import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from google.genai import types
from google.genai.errors import ClientError, ServerError
from httpx import ReadTimeout
from pypdf.errors import PdfReadError

from app.api import resume as resume_api
from app.main import app
from app.services import resume as resume_service


class FakeGeminiModels:
    def __init__(self):
        self.requests = []
        self.count_requests = []
        self.embed_errors = []
        self.response_embeddings = None
        self.response_missing_embeddings = False

    def count_tokens(self, model, contents):
        self.count_requests.append({"model": model, "contents": contents})
        token_count = sum(
            max(1, len(content.parts[0].text) // 4) for content in contents
        )
        return SimpleNamespace(total_tokens=token_count)

    def embed_content(self, model, contents, config):
        self.requests.append(
            {"model": model, "contents": contents, "config": config}
        )
        if self.embed_errors:
            raise self.embed_errors.pop(0)
        count = len(contents) if isinstance(contents, list) else 1
        embeddings = self.response_embeddings
        if embeddings is None:
            embeddings = [
                SimpleNamespace(values=[3.0, 4.0] + [0.0] * 766)
                for _ in range(count)
            ]
        if self.response_missing_embeddings:
            return SimpleNamespace()
        return SimpleNamespace(embeddings=embeddings)


class FakeGeminiClient:
    def __init__(self):
        self.models = FakeGeminiModels()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class FakeCollection:
    def __init__(self, metadata=None):
        self.metadata = metadata
        self.documents = []
        self.last_query_embeddings = None

    def add(self, ids, documents, embeddings, metadatas):
        self.documents.extend(
            {
                "id": item_id,
                "text": text,
                "metadata": metadata,
                "embedding": embedding,
            }
            for item_id, text, metadata, embedding in zip(
                ids, documents, metadatas, embeddings
            )
        )

    def count(self):
        return len(self.documents)

    def get(self, limit=None, include=None, ids=None):
        records = self.documents
        if ids is not None:
            requested = set(ids)
            records = [record for record in records if record["id"] in requested]
        if limit is not None:
            records = records[:limit]
        return {
            "ids": [record["id"] for record in records],
            "embeddings": [record["embedding"] for record in records]
            if include and "embeddings" in include
            else None
        }

    def upsert(self, ids, documents, embeddings, metadatas):
        records = {record["id"]: record for record in self.documents}
        for item_id, text, embedding, metadata in zip(
            ids, documents, embeddings, metadatas
        ):
            records[item_id] = {
                "id": item_id,
                "text": text,
                "metadata": metadata,
                "embedding": embedding,
            }
        self.documents = list(records.values())

    def query(self, query_embeddings, n_results, where, include):
        self.last_query_embeddings = query_embeddings
        records = self.documents
        if where:
            records = [
                record
                for record in records
                if record["metadata"]["section"] == where["section"]
            ]
        records = records[:n_results]
        return {
            "documents": [[record["text"] for record in records]],
            "metadatas": [[record["metadata"] for record in records]],
            "distances": [[0.1 for _ in records]],
        }


class FakeChromaClient:
    def __init__(self):
        self.collections = {}

    def get_collection(self, name, embedding_function):
        if name not in self.collections:
            raise resume_service.NotFoundError(name)
        return self.collections[name]

    def create_collection(self, name, metadata, embedding_function):
        collection = FakeCollection(metadata=metadata)
        self.collections[name] = collection
        return collection

    def delete_collection(self, name):
        del self.collections[name]


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.chroma = FakeChromaClient()
        self.gemini = FakeGeminiClient()
        self.patches = [
            patch.object(resume_service, "_get_chroma_client", return_value=self.chroma),
            patch.object(resume_service.genai, "Client", return_value=self.gemini),
            patch.object(resume_service.settings, "GEMINI_API_KEY", "test-key"),
            patch.object(resume_service.embedding_limiter, "acquire"),
        ]
        for mocked in self.patches:
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_pdf_text_extraction(self):
        page = unittest.mock.Mock()
        page.extract_text.return_value = "Resume text"
        reader = unittest.mock.Mock(pages=[page], is_encrypted=False)
        with patch.object(resume_service, "PdfReader", return_value=reader):
            self.assertEqual(
                resume_service.extract_pdf_text(b"%PDF-1.4 content"), "Resume text"
            )

    def test_rejects_invalid_corrupt_and_empty_pdfs(self):
        with self.assertRaisesRegex(resume_service.PDFProcessingError, "not a valid PDF"):
            resume_service.extract_pdf_text(b"not a PDF")

        with patch.object(resume_service, "PdfReader", side_effect=PdfReadError("bad")):
            with self.assertRaisesRegex(resume_service.PDFProcessingError, "corrupt"):
                resume_service.extract_pdf_text(b"%PDF-1.4 broken")

        page = unittest.mock.Mock()
        page.extract_text.return_value = ""
        reader = unittest.mock.Mock(pages=[page], is_encrypted=False)
        with patch.object(resume_service, "PdfReader", return_value=reader):
            with self.assertRaisesRegex(resume_service.PDFProcessingError, "no extractable text"):
                resume_service.extract_pdf_text(b"%PDF-1.4 empty")

    def test_chunks_detect_sections_and_overlap(self):
        chunks = resume_service.chunk_text(
            "Skills:\nPython Java\nEducation:\nUniversity degree\n"
            "Experience:\n" + ("Built reliable systems " * 20),
            chunk_size=60,
            overlap=10,
        )
        self.assertEqual(
            {chunk["section"] for chunk in chunks},
            {"Skills", "Education", "Experience"},
        )
        experience_chunks = [chunk["text"] for chunk in chunks if chunk["section"] == "Experience"]
        self.assertGreater(len(experience_chunks), 1)
        self.assertTrue(
            any(
                left[-10:].strip() in right
                for left, right in zip(experience_chunks, experience_chunks[1:])
            )
        )

    def test_upload_then_search_and_section_filter(self):
        content = b"%PDF-1.4 test document"
        text = "Skills:\nPython and Java\nEducation:\nComputer Science degree"
        with tempfile.TemporaryDirectory() as upload_dir:
            with (
                patch.object(resume_api, "extract_pdf_text", return_value=text),
                patch.object(resume_service.settings, "UPLOAD_DIR", upload_dir),
            ):
                upload = self.client.post(
                    "/api/resume/upload",
                    files={"file": ("resume.pdf", content, "application/pdf")},
                )
            self.assertEqual(upload.status_code, 200)
            resume_id = upload.json()["resume_id"]
            self.assertTrue((Path(upload_dir) / f"{resume_id}.pdf").exists())

            all_results = resume_service.search_resume(resume_id, "degree", top_k=5)
            self.assertEqual(len(all_results), 2)

            filtered = resume_service.search_resume(
                resume_id, "degree", section="Education"
            )
            self.assertEqual([result["section"] for result in filtered], ["Education"])

        collection = self.chroma.collections[
            resume_service._collection_name(resume_id)
        ]
        self.assertEqual(
            collection.metadata["embedding_model"], "gemini-embedding-2"
        )
        self.assertEqual(collection.metadata["embedding_dimensions"], 768)
        self.assertEqual(len(collection.documents[0]["embedding"]), 768)
        self.assertAlmostEqual(
            sum(value * value for value in collection.documents[0]["embedding"]),
            1.0,
        )
        self.assertEqual(
            [
                content.parts[0].text
                for content in self.gemini.models.requests[0]["contents"]
            ],
            [
                "title: Skills | text: Python and Java",
                "title: Education | text: Computer Science degree",
            ],
        )
        self.assertEqual(
            len(self.gemini.models.requests[0]["contents"]),
            2,
        )
        self.assertEqual(len(self.gemini.models.count_requests), 3)
        self.assertTrue(
            all(
                isinstance(content, types.Content)
                and len(content.parts) == 1
                and isinstance(content.parts[0], types.Part)
                for content in self.gemini.models.requests[0]["contents"]
            )
        )
        self.assertEqual(
            self.gemini.models.requests[0]["config"].output_dimensionality, 768
        )
        self.assertEqual(
            self.gemini.models.requests[1]["contents"][0].parts[0].text,
            "task: question answering | query: degree",
        )
        self.assertEqual(
            collection.last_query_embeddings[0],
            collection.documents[0]["embedding"],
        )

    def test_embedding_response_count_mismatch_has_safe_error(self):
        self.gemini.models.response_embeddings = [
            SimpleNamespace(values=[3.0, 4.0] + [0.0] * 766)
        ]
        with self.assertRaisesRegex(
            resume_service.ResumeEmbeddingError,
            "returned 1 embedding for 2 input texts",
        ):
            resume_service._embed_texts(["First chunk", "Second chunk"])

    def test_embedding_response_with_missing_embeddings_is_rejected(self):
        self.gemini.models.response_embeddings = []
        with self.assertRaisesRegex(
            resume_service.ResumeEmbeddingError,
            "returned 0 embeddings for 1 input text",
        ):
            resume_service._embed_texts(["A resume chunk"])

    def test_embedding_response_missing_embeddings_is_rejected(self):
        self.gemini.models.response_missing_embeddings = True
        with self.assertRaisesRegex(
            resume_service.ResumeEmbeddingError,
            "returned 0 embeddings for 2 input texts",
        ):
            resume_service._embed_texts(["First chunk", "Second chunk"])

    def test_embedding_with_wrong_dimensions_is_rejected(self):
        self.gemini.models.response_embeddings = [
            SimpleNamespace(values=[1.0, 0.0])
        ]
        with self.assertRaisesRegex(
            resume_service.ResumeEmbeddingError,
            "2 dimensions; expected 768",
        ):
            resume_service._embed_texts(["A resume chunk"])

    def test_incompatible_existing_index_is_rebuilt_on_upload(self):
        resume_id = str(uuid4())
        name = resume_service._collection_name(resume_id)
        legacy = FakeCollection(metadata={"resume_id": resume_id})
        legacy.add(
            ids=["0"],
            documents=["old document"],
            embeddings=[[0.0, 1.0]],
            metadatas=[{"resume_id": resume_id, "section": "Skills", "chunk_index": 0}],
        )
        self.chroma.collections[name] = legacy

        resume_service.index_resume(
            resume_id,
            [{"text": "New document", "section": "Skills", "chunk_index": 0}],
        )

        collection = self.chroma.collections[name]
        self.assertIsNot(collection, legacy)
        self.assertEqual(collection.metadata["embedding_model"], "gemini-embedding-2")
        self.assertEqual([record["text"] for record in collection.documents], ["New document"])

    def test_identical_chunks_reuse_persistent_embedding_cache(self):
        chunk = {"text": "Python and Java", "section": "Skills", "chunk_index": 0}

        resume_service.index_resume(str(uuid4()), [chunk])
        resume_service.index_resume(str(uuid4()), [chunk])

        self.assertEqual(len(self.gemini.models.requests), 1)
        cache = self.chroma.collections[
            resume_service._EMBEDDING_CACHE_COLLECTION
        ]
        self.assertEqual(cache.count(), 1)

    def test_changed_chunks_embed_only_new_content(self):
        first = [
            {"text": "Python", "section": "Skills", "chunk_index": 0},
            {"text": "Computer Science", "section": "Education", "chunk_index": 1},
        ]
        changed = [
            first[0],
            {"text": "New project", "section": "Projects", "chunk_index": 1},
        ]

        resume_service.index_resume(str(uuid4()), first)
        resume_service.index_resume(str(uuid4()), changed)

        self.assertEqual(len(self.gemini.models.requests), 2)
        embedded_texts = [
            content.parts[0].text
            for request in self.gemini.models.requests
            for content in request["contents"]
        ]
        self.assertEqual(
            embedded_texts,
            [
                "title: Skills | text: Python",
                "title: Education | text: Computer Science",
                "title: Projects | text: New project",
            ],
        )

    def test_concurrent_uploads_do_not_embed_the_same_chunk_twice(self):
        from concurrent.futures import ThreadPoolExecutor

        chunk = {"text": "Python and Java", "section": "Skills", "chunk_index": 0}
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda resume_id: resume_service.index_resume(resume_id, [chunk]),
                    (str(uuid4()), str(uuid4())),
                )
            )

        self.assertEqual(results, [None, None])
        self.assertEqual(len(self.gemini.models.requests), 1)

    def test_repeated_resume_upload_reuses_cached_chunk_embeddings(self):
        text = "Skills:\nPython and Java"
        with tempfile.TemporaryDirectory() as upload_dir:
            with (
                patch.object(resume_api, "extract_pdf_text", return_value=text),
                patch.object(resume_service.settings, "UPLOAD_DIR", upload_dir),
            ):
                first = self.client.post(
                    "/api/resume/upload",
                    files={"file": ("resume.pdf", b"%PDF-1.4 test", "application/pdf")},
                )
                second = self.client.post(
                    "/api/resume/upload",
                    files={"file": ("resume.pdf", b"%PDF-1.4 test", "application/pdf")},
                )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertNotEqual(first.json()["resume_id"], second.json()["resume_id"])
        self.assertEqual(len(self.gemini.models.requests), 1)

    def test_embedding_token_usage_is_sent_to_the_limiter(self):
        resume_service._embed_texts(["A resume chunk"])

        token_count = self.gemini.models.count_requests[0]["contents"][0].parts[0].text
        self.assertEqual(
            self.gemini.models.count_requests[0]["model"],
            "gemini-embedding-2",
        )
        self.assertEqual(
            resume_service.embedding_limiter.acquire.call_args_list[-1].kwargs,
            {"tokens": max(1, len(token_count) // 4)},
        )

    def test_embedding_retries_503_through_the_shared_embedding_limiter(self):
        self.gemini.models.embed_errors = [
            ServerError(
                503,
                {"error": {"code": 503, "status": "UNAVAILABLE"}},
            )
        ]
        with (
            patch.object(resume_service.embedding_limiter, "acquire") as acquire,
            patch("app.services.rate_limit.time.sleep"),
        ):
            result = resume_service._embed_texts(["A resume chunk"])

        self.assertEqual(len(result), 1)
        self.assertEqual(len(self.gemini.models.requests), 2)
        self.assertEqual(acquire.call_count, 3)
        self.assertEqual(
            [call.kwargs["tokens"] for call in acquire.call_args_list],
            [0, 3, 3],
        )

    def test_embedding_429_is_not_retried_and_has_retry_after(self):
        self.gemini.models.embed_errors = [
            ClientError(
                429,
                {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}},
            )
        ]
        with patch.object(resume_service.embedding_limiter, "acquire") as acquire:
            with self.assertRaises(resume_service.RateLimitExceeded):
                resume_service._embed_texts(["A resume chunk"])

        self.assertEqual(len(self.gemini.models.requests), 1)
        self.assertEqual(acquire.call_count, 2)

    def test_search_rejects_legacy_index_without_embedding_metadata(self):
        resume_id = str(uuid4())
        self.chroma.collections[resume_service._collection_name(resume_id)] = (
            FakeCollection(metadata={"resume_id": resume_id})
        )
        with self.assertRaises(resume_service.ResumeIndexIncompatible):
            resume_service.search_resume(resume_id, "Python")
        self.assertEqual(self.gemini.models.requests, [])

    def test_upload_maps_embedding_timeout_to_502_without_leaking_key(self):
        self.gemini.models.embed_content = unittest.mock.Mock(
            side_effect=ReadTimeout("request failed with test-key")
        )
        with patch.object(resume_api, "extract_pdf_text", return_value="Skills:\nPython"):
            response = self.client.post(
                "/api/resume/upload",
                files={"file": ("resume.pdf", b"%PDF-1.4 test", "application/pdf")},
            )
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("test-key", response.text)

    def test_missing_embedding_api_key_is_reported_as_configuration_error(self):
        with patch.object(resume_service.settings, "GEMINI_API_KEY", ""):
            with self.assertRaises(
                resume_service.ResumeEmbeddingConfigurationError
            ):
                resume_service._embed_texts(["A resume chunk"])

    def test_upload_rejects_invalid_pdf(self):
        response = self.client.post(
            "/api/resume/upload",
            files={"file": ("resume.pdf", b"not a PDF", "application/pdf")},
        )
        self.assertEqual(response.status_code, 400)

    def test_search_raises_not_found_for_missing_resume_index(self):
        with self.assertRaises(resume_service.ResumeIndexNotFound):
            resume_service.search_resume(str(uuid4()), "Python")


if __name__ == "__main__":
    unittest.main()

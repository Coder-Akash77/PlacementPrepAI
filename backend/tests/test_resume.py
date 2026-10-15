import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from pypdf.errors import PdfReadError

from app.api import resume as resume_api
from app.main import app
from app.services import resume as resume_service


class FakeEmbeddingModel:
    def encode(self, texts):
        class Embeddings(list):
            def tolist(self):
                return list(self)

        return Embeddings([[float(len(text)), 1.0] for text in texts])


class FakeCollection:
    def __init__(self):
        self.documents = []

    def add(self, ids, documents, embeddings, metadatas):
        self.documents.extend(
            {"text": text, "metadata": metadata, "embedding": embedding}
            for text, metadata, embedding in zip(documents, metadatas, embeddings)
        )

    def count(self):
        return len(self.documents)

    def query(self, query_embeddings, n_results, where, include):
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

    def get_or_create_collection(self, name, metadata, embedding_function):
        return self.collections.setdefault(name, FakeCollection())

    def get_collection(self, name, embedding_function):
        if name not in self.collections:
            raise resume_service.NotFoundError(name)
        return self.collections[name]


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.chroma = FakeChromaClient()
        self.embedding_model = FakeEmbeddingModel()
        self.patches = [
            patch.object(resume_service, "_get_chroma_client", return_value=self.chroma),
            patch.object(
                resume_service, "_get_embedding_model", return_value=self.embedding_model
            ),
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

            all_results = self.client.get(
                "/api/resume/search",
                params={"resume_id": resume_id, "query": "degree", "top_k": 5},
            )
            self.assertEqual(all_results.status_code, 200)
            self.assertEqual(len(all_results.json()["results"]), 2)

            filtered = self.client.get(
                "/api/resume/search",
                params={
                    "resume_id": resume_id,
                    "query": "degree",
                    "section": "Education",
                },
            )
            self.assertEqual(filtered.status_code, 200)
            self.assertEqual(
                [result["section"] for result in filtered.json()["results"]],
                ["Education"],
            )

    def test_upload_rejects_invalid_pdf(self):
        response = self.client.post(
            "/api/resume/upload",
            files={"file": ("resume.pdf", b"not a PDF", "application/pdf")},
        )
        self.assertEqual(response.status_code, 400)

    def test_search_returns_404_for_missing_resume_index(self):
        resume_id = str(uuid4())
        response = self.client.get(
            "/api/resume/search",
            params={"resume_id": resume_id, "query": "Python"},
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()

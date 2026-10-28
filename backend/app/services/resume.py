from __future__ import annotations

import logging
import math
import re
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

import chromadb
from chromadb.errors import NotFoundError
from google import genai
from google.genai.errors import APIError
from google.genai import types
from google.genai.types import EmbedContentConfig, HttpOptions, HttpRetryOptions
from httpx import HTTPError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.config import settings

logger = logging.getLogger(__name__)

SECTION_ALIASES = {
    "Skills": {"skills", "technical skills", "core competencies"},
    "Education": {"education", "academic background", "qualifications"},
    "Experience": {
        "experience",
        "work experience",
        "professional experience",
        "employment history",
    },
    "Projects": {"projects", "project experience", "personal projects"},
    "Achievements": {"achievements", "accomplishments", "awards", "honors"},
}
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 150
EMBEDDING_MODEL = "gemini-embedding-2"
EMBEDDING_DIMENSIONS = 768
EMBEDDING_BATCH_SIZE = 32
EMBEDDING_TIMEOUT_MS = 20_000
EMBEDDING_NORMALIZATION = "l2"
_EMBEDDING_METADATA = {
    "embedding_provider": "google",
    "embedding_model": EMBEDDING_MODEL,
    "embedding_dimensions": EMBEDDING_DIMENSIONS,
    "embedding_normalization": EMBEDDING_NORMALIZATION,
}


class PDFProcessingError(ValueError):
    """Raised when an uploaded file is not a readable, text-based PDF."""


class ResumeIndexNotFound(LookupError):
    """Raised when a resume has no stored vector index."""


class ResumeEmbeddingError(RuntimeError):
    """Raised when the hosted embedding service cannot create embeddings."""


class ResumeEmbeddingConfigurationError(ResumeEmbeddingError):
    """Raised when Gemini embeddings are not configured."""


class ResumeIndexIncompatible(RuntimeError):
    """Raised when a stored resume index uses incompatible embeddings."""


def extract_pdf_text(content: bytes) -> str:
    if not content.startswith(b"%PDF-"):
        raise PDFProcessingError("The uploaded file is not a valid PDF.")

    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise PDFProcessingError("Encrypted PDFs are not supported.")
        if not reader.pages:
            raise PDFProcessingError("The PDF contains no pages.")
        text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except PDFProcessingError:
        raise
    except (EOFError, OSError, ValueError, KeyError, PdfReadError) as exc:
        raise PDFProcessingError("The PDF is corrupt or unreadable.") from exc

    if not text:
        raise PDFProcessingError("The PDF contains no extractable text.")
    return text


def _section_for_line(line: str) -> tuple[str | None, str]:
    candidate = line.strip().strip("-•* \t")
    label, separator, remainder = candidate.partition(":")
    if not separator:
        label, separator, remainder = candidate.partition(" - ")

    normalized_label = re.sub(r"[^a-z]+", " ", label.casefold()).strip()
    for section, aliases in SECTION_ALIASES.items():
        if normalized_label in aliases:
            return section, remainder.strip() if separator else ""
    return None, ""


def _split_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, list[str]]] = [("Other", [])]
    for line in text.splitlines():
        section, remainder = _section_for_line(line)
        if section:
            sections.append((section, [remainder] if remainder else []))
        else:
            sections[-1][1].append(line)
    return [
        (section, "\n".join(lines).strip())
        for section, lines in sections
        if "\n".join(lines).strip()
    ]


def _chunk_section(
    text: str, chunk_size: int, overlap: int
) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start, end)
            if boundary > start:
                end = boundary

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(text):
            break

        start = max(start + 1, end - overlap)
        while start < len(text) and text[start].isspace():
            start += 1
    return chunks


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[dict[str, Any]]:
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size must be positive and overlap smaller than chunk_size.")

    chunks: list[dict[str, Any]] = []
    for section, content in _split_sections(text):
        for chunk in _chunk_section(content, chunk_size, overlap):
            chunks.append({"text": chunk, "section": section, "chunk_index": len(chunks)})
    return chunks


@lru_cache(maxsize=1)
def _get_chroma_client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=settings.CHROMA_DIR)


def _collection_name(resume_id: str) -> str:
    return f"resume_{resume_id.replace('-', '')}"


def _normalized_embedding(values: list[float] | None) -> list[float]:
    if values is None:
        raise ResumeEmbeddingError(
            "Gemini returned an embedding without vector values."
        )
    if len(values) != EMBEDDING_DIMENSIONS:
        raise ResumeEmbeddingError(
            f"Gemini returned an embedding with {len(values)} dimensions; "
            f"expected {EMBEDDING_DIMENSIONS}."
        )
    try:
        vector = [float(value) for value in values]
    except (TypeError, ValueError) as exc:
        raise ResumeEmbeddingError("Gemini returned an invalid embedding.") from exc
    if not all(math.isfinite(value) for value in vector):
        raise ResumeEmbeddingError("Gemini returned an invalid embedding.")
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        raise ResumeEmbeddingError("Gemini returned an invalid embedding.")
    return [value / magnitude for value in vector]


def _embed_texts(texts: list[str]) -> list[list[float]]:
    if not settings.GEMINI_API_KEY:
        raise ResumeEmbeddingConfigurationError(
            "Gemini API key is not configured."
        )
    if not texts:
        return []

    embeddings = []
    try:
        with genai.Client(
            api_key=settings.GEMINI_API_KEY,
            http_options=HttpOptions(
                timeout=EMBEDDING_TIMEOUT_MS,
                retry_options=HttpRetryOptions(
                    attempts=2,
                    initial_delay=0.2,
                    max_delay=0.5,
                    exp_base=2,
                    jitter=0.1,
                    http_status_codes=[429, 503],
                ),
            ),
        ) as client:
            for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
                batch = texts[start : start + EMBEDDING_BATCH_SIZE]
                response = client.models.embed_content(
                    model=EMBEDDING_MODEL,
                    contents=[
                        types.Content(
                            parts=[types.Part.from_text(text=text)],
                            role="user",
                        )
                        for text in batch
                    ],
                    config=EmbedContentConfig(
                        output_dimensionality=EMBEDDING_DIMENSIONS
                    ),
                )
                batch_embeddings = getattr(response, "embeddings", None) or []
                if len(batch_embeddings) != len(batch):
                    embedding_word = (
                        "embedding" if len(batch_embeddings) == 1 else "embeddings"
                    )
                    text_word = "input text" if len(batch) == 1 else "input texts"
                    raise ResumeEmbeddingError(
                        f"Gemini returned {len(batch_embeddings)} {embedding_word} "
                        f"for {len(batch)} {text_word}."
                    )
                embeddings.extend(
                    _normalized_embedding(getattr(embedding, "values", None))
                    for embedding in batch_embeddings
                )
    except (APIError, HTTPError, TimeoutError) as exc:
        logger.warning(
            "Gemini embedding request failed (type=%s, code=%s).",
            type(exc).__name__,
            getattr(exc, "code", "unknown"),
        )
        raise ResumeEmbeddingError(
            "The resume embedding service is temporarily unavailable."
        ) from exc
    return embeddings


def _collection_metadata(resume_id: str) -> dict[str, str | int]:
    return {"resume_id": resume_id, **_EMBEDDING_METADATA}


def _stored_vector_is_compatible(vector: Any) -> bool:
    if vector is None or len(vector) != EMBEDDING_DIMENSIONS:
        return False
    try:
        values = [float(value) for value in vector]
    except (TypeError, ValueError):
        return False
    if not all(math.isfinite(value) for value in values):
        return False
    magnitude = math.sqrt(sum(value * value for value in values))
    return math.isclose(magnitude, 1.0, rel_tol=1e-3, abs_tol=1e-3)


def _collection_is_compatible(collection: Any, resume_id: str) -> bool:
    metadata = collection.metadata or {}
    if any(
        metadata.get(key) != value
        for key, value in _collection_metadata(resume_id).items()
    ):
        return False
    if not collection.count():
        return True
    stored = collection.get(limit=1, include=["embeddings"]).get("embeddings")
    return (
        stored is not None
        and len(stored) > 0
        and _stored_vector_is_compatible(stored[0])
    )


def _new_collection(client: chromadb.ClientAPI, resume_id: str) -> Any:
    return client.create_collection(
        name=_collection_name(resume_id),
        metadata=_collection_metadata(resume_id),
        embedding_function=None,
    )


def index_resume(resume_id: str, chunks: list[dict[str, Any]]) -> None:
    texts = [
        f"title: {chunk['section']} | text: {chunk['text']}" for chunk in chunks
    ]
    embeddings = _embed_texts(texts)
    client = _get_chroma_client()
    name = _collection_name(resume_id)
    try:
        collection = client.get_collection(name=name, embedding_function=None)
    except NotFoundError:
        collection = _new_collection(client, resume_id)
    else:
        if not _collection_is_compatible(collection, resume_id):
            client.delete_collection(name=name)
            collection = _new_collection(client, resume_id)

    collection.add(
        ids=[str(chunk["chunk_index"]) for chunk in chunks],
        documents=[chunk["text"] for chunk in chunks],
        embeddings=embeddings,
        metadatas=[
            {
                "resume_id": resume_id,
                "section": chunk["section"],
                "chunk_index": chunk["chunk_index"],
            }
            for chunk in chunks
        ],
    )


def search_resume(
    resume_id: str,
    query: str,
    top_k: int = 5,
    section: str | None = None,
) -> list[dict[str, Any]]:
    try:
        collection = _get_chroma_client().get_collection(
            name=_collection_name(resume_id),
            embedding_function=None,
        )
    except NotFoundError as exc:
        raise ResumeIndexNotFound(
            f"No resume index exists for resume_id '{resume_id}'."
        ) from exc

    if not _collection_is_compatible(collection, resume_id):
        raise ResumeIndexIncompatible(
            "This resume index uses incompatible embeddings. Please upload the resume again."
        )

    count = collection.count()
    if not count:
        return []

    query_embedding = _embed_texts([f"task: question answering | query: {query}"])
    where = {"section": section} if section else None
    matches = collection.query(
        query_embeddings=query_embedding,
        n_results=min(top_k, count),
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    documents = matches["documents"][0]
    metadatas = matches["metadatas"][0]
    distances = matches["distances"][0]
    return [
        {
            "text": document,
            "section": metadata["section"],
            "chunk_index": metadata["chunk_index"],
            "distance": distance,
        }
        for document, metadata, distance in zip(documents, metadatas, distances)
    ]


def save_uploaded_pdf(resume_id: str, content: bytes) -> None:
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / f"{resume_id}.pdf").write_bytes(content)

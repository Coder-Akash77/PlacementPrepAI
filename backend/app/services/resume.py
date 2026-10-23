from __future__ import annotations

import re
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

import chromadb
from chromadb.errors import NotFoundError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.config import settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

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


class PDFProcessingError(ValueError):
    """Raised when an uploaded file is not a readable, text-based PDF."""


class ResumeIndexNotFound(LookupError):
    """Raised when a resume has no stored vector index."""


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
def _get_embedding_model() -> SentenceTransformer:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(settings.EMBEDDING_MODEL)


@lru_cache(maxsize=1)
def _get_chroma_client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=settings.CHROMA_DIR)


def _collection_name(resume_id: str) -> str:
    return f"resume_{resume_id.replace('-', '')}"


def index_resume(resume_id: str, chunks: list[dict[str, Any]]) -> None:
    model = _get_embedding_model()
    embeddings = model.encode([chunk["text"] for chunk in chunks]).tolist()
    collection = _get_chroma_client().get_or_create_collection(
        name=_collection_name(resume_id),
        metadata={"resume_id": resume_id},
        embedding_function=None,
    )
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

    count = collection.count()
    if not count:
        return []

    query_embedding = _get_embedding_model().encode([query]).tolist()
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

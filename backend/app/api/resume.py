from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.services.resume import (
    PDFProcessingError,
    ResumeEmbeddingConfigurationError,
    ResumeEmbeddingError,
    ResumeIndexIncompatible,
    ResumeIndexNotFound,
    chunk_text,
    extract_pdf_text,
    index_resume,
    save_uploaded_pdf,
    search_resume,
)

router = APIRouter(prefix="/resume", tags=["resume"])
SectionName = Literal[
    "Skills", "Education", "Experience", "Projects", "Achievements", "Other"
]


@router.post("/upload")
async def upload_resume(file: UploadFile = File(...)):
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported.")

    content = await file.read()
    try:
        text = extract_pdf_text(content)
    except PDFProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    chunks = chunk_text(text)
    resume_id = str(uuid4())
    try:
        index_resume(resume_id, chunks)
    except ResumeEmbeddingConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ResumeEmbeddingError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    save_uploaded_pdf(resume_id, content)
    return {
        "resume_id": resume_id,
        "filename": file.filename,
        "chunks_count": len(chunks),
    }


@router.get("/search")
def search_resumes(
    resume_id: UUID,
    query: str = Query(min_length=1, max_length=2000),
    section: SectionName | None = None,
    top_k: int = Query(default=5, ge=1, le=50),
):
    try:
        results = search_resume(str(resume_id), query, top_k=top_k, section=section)
    except ResumeIndexNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ResumeIndexIncompatible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ResumeEmbeddingConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ResumeEmbeddingError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"resume_id": str(resume_id), "query": query, "results": results}

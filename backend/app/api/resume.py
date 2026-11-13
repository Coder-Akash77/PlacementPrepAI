from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.api.quota import enforce_user_quota
from app.services.resume import (
    MAX_PDF_BYTES,
    PDFProcessingError,
    ResumeEmbeddingConfigurationError,
    ResumeEmbeddingError,
    chunk_text,
    extract_pdf_text,
    index_resume,
    save_uploaded_pdf,
)

router = APIRouter(prefix="/resume", tags=["resume"])


@router.post("/upload", dependencies=[Depends(enforce_user_quota)])
async def upload_resume(file: UploadFile = File(...)):
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported.")

    content = await file.read(MAX_PDF_BYTES + 1)
    if len(content) > MAX_PDF_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"The PDF is too large. The maximum size is {MAX_PDF_BYTES // (1024 * 1024)} MB.",
        )
    try:
        text = extract_pdf_text(content)
    except PDFProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    chunks = chunk_text(text)
    resume_id = str(uuid4())
    try:
        await run_in_threadpool(index_resume, resume_id, chunks)
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

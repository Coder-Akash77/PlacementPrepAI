from fastapi import APIRouter, UploadFile, File, HTTPException

from app.services.upload_service import save_uploaded_file

router = APIRouter(
    prefix="/upload",
    tags=["Upload"]
)


@router.post("/")
def upload_resume(file: UploadFile = File(...)):

    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are allowed."
        )

    result = save_uploaded_file(file)

    return {
        "filename": file.filename,
        "message": "Resume processed successfully!",
        "chunks_created": result["chunks"]
    }
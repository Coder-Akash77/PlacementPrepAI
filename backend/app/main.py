from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.requests import Request
from fastapi.responses import JSONResponse

from app.api import auth, chat, health, interview, preparation, resume
from app.config import settings
from app.services.resume import MAX_PDF_BYTES
from app.services.rate_limit import (
    ProviderTemporarilyUnavailable,
    RateLimitExceeded,
)

app = FastAPI(title="PlacementPrepAI", version="0.1.0")

# Multipart overhead allowance on top of the PDF size limit.
_MAX_UPLOAD_BODY_BYTES = MAX_PDF_BYTES + 64 * 1024


# Registered before CORS so the 413 still carries CORS headers.
@app.middleware("http")
async def reject_oversized_resume_upload(request: Request, call_next):
    if request.url.path == "/api/resume/upload":
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > _MAX_UPLOAD_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"detail": "The PDF is too large. The maximum size is 5 MB."},
            )
    return await call_next(request)


# Allow the React frontend to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(resume.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(interview.router, prefix="/api")
app.include_router(preparation.router, prefix="/api")


@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": str(exc)},
        headers={"Retry-After": str(exc.retry_after)},
    )


@app.exception_handler(ProviderTemporarilyUnavailable)
async def provider_temporarily_unavailable(
    request: Request, exc: ProviderTemporarilyUnavailable
) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.get("/")
def root():
    return {"message": "PlacementPrepAI backend is running"}

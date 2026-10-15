from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, resume
from app.config import settings

app = FastAPI(title="PlacementPrepAI", version="0.1.0")

# Allow the React frontend to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(resume.router, prefix="/api")


@app.get("/")
def root():
    return {"message": "PlacementPrepAI backend is running"}

import os

from dotenv import load_dotenv

# Loads variables from backend/.env (if the file exists)
load_dotenv()


class Settings:
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    FRONTEND_ORIGINS: list[str] = [
        origin.strip()
        for origin in os.getenv(
            "FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ).split(",")
        if origin.strip()
    ]

    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    CHROMA_DIR: str = os.getenv("CHROMA_DIR", "./data/chroma")
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "./data/uploads")


settings = Settings()

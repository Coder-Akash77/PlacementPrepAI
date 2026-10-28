import os

from dotenv import load_dotenv

# Loads variables from backend/.env (if the file exists)
load_dotenv()


class Settings:
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

    FRONTEND_ORIGINS: list[str] = [
        origin.strip()
        for origin in os.getenv(
            "FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ).split(",")
        if origin.strip()
    ]

    CHROMA_DIR: str = os.getenv("CHROMA_DIR", "./data/chroma")
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "./data/uploads")
    HISTORY_DB_PATH: str = os.getenv("HISTORY_DB_PATH", "./data/history.db")


settings = Settings()

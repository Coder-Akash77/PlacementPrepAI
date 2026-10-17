import logging
import re

from google import genai
from google.genai.errors import APIError

from app.config import settings

logger = logging.getLogger(__name__)


class GeminiConfigurationError(RuntimeError):
    """Raised when Gemini is not configured."""


class GeminiGenerationError(RuntimeError):
    """Raised when Gemini cannot generate a response."""


def _safe_error_message(error: APIError) -> str:
    message = getattr(error, "message", "") or "No provider message."
    if settings.GEMINI_API_KEY:
        message = message.replace(settings.GEMINI_API_KEY, "[REDACTED]")
    message = re.sub(r"AIza[0-9A-Za-z_-]{20,}", "[REDACTED]", message)
    message = re.sub(
        r"(?i)(api[_ -]?key\s*[:=]\s*)[^\s,;]+",
        r"\1[REDACTED]",
        message,
    )
    return message


def answer_resume_question(question: str, resume_chunks: list[dict]) -> str:
    if not settings.GEMINI_API_KEY:
        raise GeminiConfigurationError("Gemini API key is not configured.")

    context = "\n\n".join(
        f"[{chunk['section']}]\n{chunk['text']}" for chunk in resume_chunks
    )
    prompt = (
        "Answer the user's question using only the retrieved resume context below. "
        "If the context does not contain the requested information, say that it is "
        "not available in the resume. Never invent or assume the user's "
        "qualifications, skills, or experience.\n\n"
        f"Retrieved resume context:\n{context or '(No relevant resume information found.)'}"
        f"\n\nUser question:\n{question}"
    )

    try:
        with genai.Client(api_key=settings.GEMINI_API_KEY) as client:
            response = client.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=prompt,
            )
    except APIError as exc:
        logger.warning(
            "Gemini API request failed (code=%s, status=%s): %s",
            getattr(exc, "code", "unknown"),
            getattr(exc, "status", "unknown"),
            _safe_error_message(exc),
        )
        raise GeminiGenerationError("Gemini could not generate a response.") from exc

    answer = response.text
    if not answer or not answer.strip():
        raise GeminiGenerationError("Gemini returned an empty response.")
    return answer.strip()

from google import genai
from google.genai.errors import APIError

from app.config import settings


class GeminiConfigurationError(RuntimeError):
    """Raised when Gemini is not configured."""


class GeminiGenerationError(RuntimeError):
    """Raised when Gemini cannot generate a response."""


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
        response = genai.Client(api_key=settings.GEMINI_API_KEY).models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=prompt,
        )
    except APIError as exc:
        raise GeminiGenerationError("Gemini could not generate a response.") from exc

    answer = response.text
    if not answer or not answer.strip():
        raise GeminiGenerationError("Gemini returned an empty response.")
    return answer.strip()

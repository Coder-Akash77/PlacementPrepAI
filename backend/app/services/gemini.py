import json
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


def generate_interview_question(
    resume_chunks: list[dict], previous_questions: list[str]
) -> str:
    context = "\n\n".join(
        f"[{chunk['section']}]\n{chunk['text']}" for chunk in resume_chunks
    )
    prompt = (
        "Generate exactly one concise mock interview question at a time. "
        "Base it on the candidate's retrieved resume context and do not assume "
        "qualifications that are not present. Do not answer the question or add "
        "numbering or commentary. Avoid repeating prior questions.\n\n"
        f"Retrieved resume context:\n{context or '(No relevant resume information found.)'}"
        f"\n\nPrior questions:\n{json.dumps(previous_questions)}"
    )
    return _generate_text(prompt)


def evaluate_interview_answer(
    question: str, answer: str, resume_chunks: list[dict]
) -> dict:
    context = "\n\n".join(
        f"[{chunk['section']}]\n{chunk['text']}" for chunk in resume_chunks
    )
    prompt = (
        "Evaluate the candidate's interview answer. Ground resume-specific "
        "feedback in the retrieved resume context; never invent qualifications "
        "or experience. Return only a JSON object with exactly these fields: "
        '"score" (integer 0-100), "strengths" (non-empty array of strings), '
        '"improvements" (non-empty array of strings), and '
        '"improved_sample_answer" (string). The sample may improve framing but '
        "must not add unsupported personal facts.\n\n"
        f"Retrieved resume context:\n{context or '(No relevant resume information found.)'}"
        f"\n\nInterview question:\n{question}\n\nCandidate answer:\n{answer}"
    )
    raw_response = _generate_text(prompt, response_mime_type="application/json")
    try:
        evaluation = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise GeminiGenerationError("Gemini returned invalid evaluation JSON.") from exc
    if not isinstance(evaluation, dict):
        raise GeminiGenerationError("Gemini returned an invalid evaluation.")
    return evaluation


def generate_preparation_question(mode: str, topic: str | None = None) -> str:
    mode_instructions = {
        "dsa": "Focus on algorithms and data structures. Ask one problem-solving question.",
        "sql": "Focus on SQL concepts or query-writing. Ask one clear question.",
        "hr": "Focus on behavioral and interpersonal interview preparation. Ask one behavioral question.",
    }
    if mode not in mode_instructions:
        raise ValueError("Unsupported preparation mode.")

    prompt = (
        f"{mode_instructions[mode]} Generate exactly one question with no answer "
        "or extra commentary."
    )
    if topic:
        prompt += f"\nFocus topic: {topic}"
    return _generate_text(prompt)


def evaluate_preparation_answer(mode: str, question: str, answer: str) -> dict:
    mode_instructions = {
        "dsa": "Assess algorithmic correctness, data-structure choices, and complexity.",
        "sql": "Assess SQL correctness, query reasoning, and relevant database concepts.",
        "hr": "Assess behavioral response clarity, relevance, and communication.",
    }
    if mode not in mode_instructions:
        raise ValueError("Unsupported preparation mode.")

    prompt = (
        f"{mode_instructions[mode]} Evaluate the submitted answer to the question. "
        "Return only a JSON object with exactly these fields: "
        '"score" (integer from 0 to 100), "strengths" (non-empty array of strings), '
        '"improvements" (non-empty array of strings), and '
        '"suggested_answer" (string containing a model answer or explanation). '
        "For HR, do not invent personal experience; make clear that the answer "
        "is a template for the user to adapt.\n\n"
        f"Question:\n{question}\n\nSubmitted answer:\n{answer}"
    )
    raw_response = _generate_text(prompt, response_mime_type="application/json")
    try:
        evaluation = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise GeminiGenerationError("Gemini returned invalid evaluation JSON.") from exc
    if not isinstance(evaluation, dict):
        raise GeminiGenerationError("Gemini returned an invalid evaluation.")
    return evaluation


def _generate_text(prompt: str, response_mime_type: str | None = None) -> str:
    if not settings.GEMINI_API_KEY:
        raise GeminiConfigurationError("Gemini API key is not configured.")

    config = {"response_mime_type": response_mime_type} if response_mime_type else None
    try:
        with genai.Client(api_key=settings.GEMINI_API_KEY) as client:
            response = client.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=prompt,
                config=config,
            )
    except APIError as exc:
        logger.warning(
            "Gemini API request failed (code=%s, status=%s): %s",
            getattr(exc, "code", "unknown"),
            getattr(exc, "status", "unknown"),
            _safe_error_message(exc),
        )
        raise GeminiGenerationError("Gemini could not generate a response.") from exc

    text = response.text
    if not text or not text.strip():
        raise GeminiGenerationError("Gemini returned an empty response.")
    return text.strip()

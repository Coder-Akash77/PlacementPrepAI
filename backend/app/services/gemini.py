import json
import logging
import re
from time import perf_counter

from google import genai
from google.genai.errors import APIError
from google.genai.types import HttpOptions, HttpRetryOptions
from httpx import Request, Response

from app.config import settings

logger = logging.getLogger(__name__)

_GEMINI_RETRY_OPTIONS = {
    "attempts": 3,
    "initial_delay": 0.2,
    "max_delay": 0.5,
    "exp_base": 2,
    "jitter": 0.1,
    "http_status_codes": [503],
}


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


def _gemini_http_options() -> HttpOptions:
    return HttpOptions(retry_options=HttpRetryOptions(**_GEMINI_RETRY_OPTIONS))


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
        with genai.Client(
            api_key=settings.GEMINI_API_KEY,
            http_options=_gemini_http_options(),
        ) as client:
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
        "Generate exactly one clear, realistic placement-level mock interview "
        "question at a time. Use understandable wording suitable for both "
        "beginners and placement-ready candidates; adjust complexity to the "
        "candidate's retrieved resume context. Ask about a skill, project, "
        "decision, or experience supported by that context. Do not assume "
        "qualifications or experience that are not present. Do not include an "
        "answer, hints that reveal an answer, numbering, or commentary. Avoid "
        "repeating prior questions.\n\n"
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
        "Evaluate the candidate's interview answer fairly for a placement "
        "interview, using clear language suitable for both beginners and "
        "placement-ready candidates. Explain specific mistakes, unsupported "
        "claims, or important omissions in the improvements, and give concrete "
        "next steps. Ground resume-specific feedback in the retrieved resume "
        "context; never invent qualifications or experience. The improved "
        "sample should demonstrate stronger structure and detail without adding "
        "personal facts not provided in the answer or resume context. Return "
        "only a JSON object with exactly these fields: "
        '"score" (integer 0-100), "strengths" (non-empty array of strings), '
        '"improvements" (non-empty array of strings), and '
        '"improved_sample_answer" (string).\n\n'
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
    prompt_started = perf_counter()
    mode_instructions = {
        "dsa": (
            "Ask one realistic algorithms or data-structures placement problem. "
            "State the task clearly and include relevant input constraints plus "
            "a small input/output example when useful. Explain any ambiguous "
            "terms briefly. Do not include the algorithm, solution, hints, or "
            "answer; the candidate must attempt it first."
        ),
        "sql": (
            "Ask one realistic SQL placement question. Include a sufficient "
            "table schema with column names and relevant types or relationships, "
            "the exact task, and the expected result columns or format when "
            "useful. Do not include a query or solution."
        ),
        "hr": (
            "Ask one specific, understandable behavioral or situational "
            "placement-interview question. Give enough scenario context to make "
            "clear what the candidate should address, without suggesting an "
            "answer."
        ),
    }
    if mode not in mode_instructions:
        raise ValueError("Unsupported preparation mode.")

    prompt = (
        "Use clear wording and a reasonable difficulty for both beginners and "
        "placement-ready students; make the task realistic without needless "
        "trickiness. Generate exactly one question and no extra commentary. "
        f"{mode_instructions[mode]}"
    )
    if topic:
        prompt += f"\nFocus topic: {topic}"
    logger.info(
        "Preparation prompt assembled mode=%s duration_ms=%.1f",
        mode,
        (perf_counter() - prompt_started) * 1000,
    )
    return _generate_text(prompt, log_http_attempts=True)


def evaluate_preparation_answer(mode: str, question: str, answer: str) -> dict:
    mode_instructions = {
        "dsa": (
            "Assess correctness, algorithm and data-structure choices, edge "
            "cases, and time/space complexity when relevant. Explain each "
            "important mistake or omission in beginner-friendly terms."
        ),
        "sql": (
            "Assess whether the query or explanation satisfies the task and "
            "schema, including joins, filtering, grouping, and edge cases when "
            "relevant. Explain specific SQL mistakes or omissions clearly."
        ),
        "hr": (
            "Assess whether the behavioral answer addresses the scenario, "
            "explains the candidate's actions, and communicates outcomes "
            "clearly. Identify missing details and practical improvements."
        ),
    }
    if mode not in mode_instructions:
        raise ValueError("Unsupported preparation mode.")

    prompt = (
        "Evaluate the submitted answer fairly using clear language suitable "
        "for both beginners and placement-ready students. "
        f"{mode_instructions[mode]} Give specific, actionable next steps in "
        "improvements and a useful improved answer or explanation that "
        "corrects the identified issues. Never invent personal experience or "
        "facts; for HR, clearly present any example as a template to adapt. "
        "Evaluate only after the submitted attempt; do not treat the suggested "
        "answer as part of the question. "
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


def _generate_text(
    prompt: str,
    response_mime_type: str | None = None,
    log_http_attempts: bool = False,
) -> str:
    generation_started = perf_counter()
    outcome = "error"
    try:
        if not settings.GEMINI_API_KEY:
            raise GeminiConfigurationError("Gemini API key is not configured.")

        config = (
            {"response_mime_type": response_mime_type}
            if response_mime_type
            else None
        )
        http_options = _gemini_http_options()
        if log_http_attempts:
            client_args = dict(http_options.client_args or {})
            event_hooks = dict(client_args.get("event_hooks") or {})
            event_hooks["request"] = [
                *event_hooks.get("request", []),
                _log_gemini_attempt_start,
            ]
            event_hooks["response"] = [
                *event_hooks.get("response", []),
                _log_gemini_attempt_response,
            ]
            client_args["event_hooks"] = event_hooks
            http_options.client_args = client_args

        client_started = perf_counter()
        try:
            client_context = genai.Client(
                api_key=settings.GEMINI_API_KEY,
                http_options=http_options,
            )
        finally:
            logger.info(
                "Gemini client initialization finished duration_ms=%.1f",
                (perf_counter() - client_started) * 1000,
            )

        call_started = perf_counter()
        try:
            with client_context as client:
                response = client.models.generate_content(
                    model=settings.GEMINI_MODEL,
                    contents=prompt,
                    config=config,
                )
        finally:
            logger.info(
                "Gemini generate_content call finished duration_ms=%.1f",
                (perf_counter() - call_started) * 1000,
            )

        validation_started = perf_counter()
        try:
            text = response.text
            if not text or not text.strip():
                raise GeminiGenerationError("Gemini returned an empty response.")
            outcome = "success"
            return text.strip()
        finally:
            logger.info(
                "Gemini response validation finished duration_ms=%.1f",
                (perf_counter() - validation_started) * 1000,
            )
    except APIError as exc:
        logger.warning(
            "Gemini API request failed (code=%s, status=%s): %s",
            getattr(exc, "code", "unknown"),
            getattr(exc, "status", "unknown"),
            _safe_error_message(exc),
        )
        raise GeminiGenerationError("Gemini could not generate a response.") from exc
    finally:
        logger.info(
            "Gemini text generation finished outcome=%s duration_ms=%.1f",
            outcome,
            (perf_counter() - generation_started) * 1000,
        )


def _log_gemini_attempt_start(request: Request) -> None:
    attempt_number = request.extensions.get("placementprep_attempt_number", 0) + 1
    request.extensions["placementprep_attempt_number"] = attempt_number
    request.extensions["placementprep_attempt_started"] = perf_counter()
    logger.info("Gemini HTTP attempt started attempt=%d", attempt_number)


def _log_gemini_attempt_response(response: Response) -> None:
    request = response.request
    attempt_number = request.extensions.get("placementprep_attempt_number")
    attempt_started = request.extensions.get("placementprep_attempt_started")
    if attempt_number is None or attempt_started is None:
        return
    logger.info(
        "Gemini HTTP attempt finished attempt=%d status_code=%d duration_ms=%.1f",
        attempt_number,
        response.status_code,
        (perf_counter() - attempt_started) * 1000,
    )

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.services.gemini import (
    GeminiConfigurationError,
    GeminiGenerationError,
    evaluate_preparation_answer,
    generate_preparation_question,
)
from app.services import history

router = APIRouter(prefix="/preparation", tags=["preparation"])
PreparationMode = Literal["dsa", "sql", "hr"]


class QuestionRequest(BaseModel):
    mode: PreparationMode
    topic: str | None = Field(default=None, min_length=1, max_length=200)
    session_id: UUID | None = None

    @field_validator("topic")
    @classmethod
    def topic_must_not_be_blank(cls, topic: str | None) -> str | None:
        if topic is None:
            return None
        topic = topic.strip()
        if not topic:
            raise ValueError("Topic must not be empty.")
        return topic


class EvaluateRequest(BaseModel):
    mode: PreparationMode
    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=5000)
    session_id: UUID | None = None
    question_id: UUID | None = None

    @field_validator("question", "answer")
    @classmethod
    def text_must_not_be_blank(cls, text: str) -> str:
        text = text.strip()
        if not text:
            raise ValueError("Question and answer must not be empty.")
        return text


class PreparationFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=0, le=100)
    strengths: list[str] = Field(min_length=1)
    improvements: list[str] = Field(min_length=1)
    suggested_answer: str = Field(min_length=1)

    @field_validator("strengths", "improvements")
    @classmethod
    def feedback_items_must_not_be_blank(cls, items: list[str]) -> list[str]:
        cleaned = [item.strip() for item in items]
        if any(not item for item in cleaned):
            raise ValueError("Feedback items must not be blank.")
        return cleaned

    @field_validator("suggested_answer")
    @classmethod
    def suggested_answer_must_not_be_blank(cls, answer: str) -> str:
        answer = answer.strip()
        if not answer:
            raise ValueError("Suggested answer must not be blank.")
        return answer


def _gemini_http_error(exc: GeminiConfigurationError | GeminiGenerationError):
    if isinstance(exc, GeminiConfigurationError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


def _history_http_error(exc: Exception):
    if isinstance(exc, history.HistoryRecordNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, history.HistoryConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=503, detail="Persistent history is unavailable.")


@router.post("/question")
def create_preparation_question(request: QuestionRequest):
    try:
        question = generate_preparation_question(request.mode, request.topic)
    except (GeminiConfigurationError, GeminiGenerationError) as exc:
        raise _gemini_http_error(exc) from exc
    try:
        session_id = history.create_preparation_session(
            request.mode, str(request.session_id) if request.session_id else None
        )
        question_id = history.add_preparation_question(
            session_id, request.mode, question, request.topic
        )
    except (
        history.HistoryRecordNotFound,
        history.HistoryConflictError,
        history.HistoryStorageError,
    ) as exc:
        raise _history_http_error(exc) from exc
    return {
        "mode": request.mode,
        "question": question,
        "session_id": session_id,
        "question_id": question_id,
    }


@router.post("/evaluate")
def evaluate_preparation_response(request: EvaluateRequest):
    try:
        raw_feedback = evaluate_preparation_answer(
            request.mode, request.question, request.answer
        )
        feedback = PreparationFeedback.model_validate(raw_feedback)
    except GeminiConfigurationError as exc:
        raise _gemini_http_error(exc) from exc
    except GeminiGenerationError as exc:
        raise _gemini_http_error(exc) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=502, detail="Gemini returned an invalid evaluation."
        ) from exc
    try:
        if request.question_id is not None:
            session_id = str(request.session_id) if request.session_id else ""
        else:
            session_id = history.create_preparation_session(
                request.mode,
                str(request.session_id) if request.session_id else None,
            )
        session_id, question_id, answer_id, evaluation_id = history.record_preparation_response(
            session_id,
            request.mode,
            request.question,
            request.answer,
            feedback.model_dump(),
            str(request.question_id) if request.question_id else None,
        )
    except (
        history.HistoryRecordNotFound,
        history.HistoryConflictError,
        history.HistoryStorageError,
    ) as exc:
        raise _history_http_error(exc) from exc
    return {
        "mode": request.mode,
        **feedback.model_dump(),
        "session_id": session_id,
        "question_id": question_id,
        "answer_id": answer_id,
        "evaluation_id": evaluation_id,
    }


@router.get("/history/{session_id}")
def preparation_history(session_id: UUID):
    try:
        return history.get_preparation_session(str(session_id))
    except (history.HistoryRecordNotFound, history.HistoryStorageError) as exc:
        raise _history_http_error(exc) from exc


@router.get("/history/questions/{question_id}")
def preparation_question_detail(question_id: UUID):
    try:
        return history.get_preparation_question(str(question_id))
    except (history.HistoryRecordNotFound, history.HistoryStorageError) as exc:
        raise _history_http_error(exc) from exc


class HistoricalAnswerRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=5000)

    @field_validator("answer")
    @classmethod
    def answer_must_not_be_blank(cls, answer: str) -> str:
        answer = answer.strip()
        if not answer:
            raise ValueError("Answer must not be empty.")
        return answer


@router.post("/history/questions/{question_id}/answers")
def answer_historical_preparation_question(
    question_id: UUID, request: HistoricalAnswerRequest
):
    try:
        answer_id = history.add_preparation_answer(
            str(question_id), request.answer
        )
    except (history.HistoryRecordNotFound, history.HistoryStorageError) as exc:
        raise _history_http_error(exc) from exc
    return {"question_id": str(question_id), "answer_id": answer_id}


@router.post("/history/answers/{answer_id}/evaluate")
def reevaluate_historical_preparation_answer(answer_id: UUID):
    try:
        answer = history.get_preparation_answer(str(answer_id))
    except (history.HistoryRecordNotFound, history.HistoryStorageError) as exc:
        raise _history_http_error(exc) from exc
    try:
        raw_feedback = evaluate_preparation_answer(
            answer["mode"], answer["question"], answer["answer"]
        )
        feedback = PreparationFeedback.model_validate(raw_feedback)
    except (GeminiConfigurationError, GeminiGenerationError) as exc:
        raise _gemini_http_error(exc) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=502, detail="Gemini returned an invalid evaluation."
        ) from exc
    try:
        evaluation_id = history.add_preparation_evaluation(
            str(answer_id), feedback.model_dump()
        )
    except (history.HistoryRecordNotFound, history.HistoryStorageError) as exc:
        raise _history_http_error(exc) from exc
    return {
        "answer_id": str(answer_id),
        "evaluation_id": evaluation_id,
        "mode": answer["mode"],
        **feedback.model_dump(),
    }

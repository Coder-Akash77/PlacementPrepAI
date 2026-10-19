from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.services import history
from app.services.gemini import (
    GeminiConfigurationError,
    GeminiGenerationError,
    evaluate_interview_answer,
    generate_interview_question,
)
from app.services.interview import (
    InvalidInterviewState,
    InterviewSessionNotFound,
    create_session,
    get_session,
    latest_answer,
    previous_questions,
    set_next_question,
    submit_answer,
)
from app.services.resume import ResumeIndexNotFound, search_resume

router = APIRouter(prefix="/interview", tags=["interview"])


class InterviewStartRequest(BaseModel):
    resume_id: UUID


class InterviewAnswerRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=5000)

    @field_validator("answer")
    @classmethod
    def answer_must_not_be_blank(cls, answer: str) -> str:
        answer = answer.strip()
        if not answer:
            raise ValueError("Answer must not be empty.")
        return answer


class InterviewEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=0, le=100)
    strengths: list[str] = Field(min_length=1)
    improvements: list[str] = Field(min_length=1)
    improved_sample_answer: str = Field(min_length=1)

    @field_validator("strengths", "improvements")
    @classmethod
    def feedback_items_must_not_be_blank(cls, items: list[str]) -> list[str]:
        cleaned = [item.strip() for item in items]
        if any(not item for item in cleaned):
            raise ValueError("Feedback items must not be blank.")
        return cleaned

    @field_validator("improved_sample_answer")
    @classmethod
    def sample_answer_must_not_be_blank(cls, answer: str) -> str:
        answer = answer.strip()
        if not answer:
            raise ValueError("Improved sample answer must not be blank.")
        return answer


def _get_session(session_id: UUID):
    try:
        return get_session(str(session_id))
    except InterviewSessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except history.HistoryStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _history_error(exc: Exception):
    if isinstance(exc, history.HistoryRecordNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, history.HistoryConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=503, detail="Persistent history is unavailable.")


def _evaluate(question: str, answer: str, resume_id: str) -> InterviewEvaluation:
    try:
        chunks = search_resume(resume_id, question)
    except ResumeIndexNotFound as exc:
        raise HTTPException(status_code=404, detail="Resume not found.") from exc

    try:
        raw_evaluation = evaluate_interview_answer(question, answer, chunks)
        return InterviewEvaluation.model_validate(raw_evaluation)
    except GeminiConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (GeminiGenerationError, ValidationError) as exc:
        raise HTTPException(
            status_code=502, detail="Gemini returned an invalid evaluation."
        ) from exc


def _generate_question(resume_id: str, questions: list[str]) -> str:
    retrieval_query = "Resume skills, education, experience, and projects for interview"
    try:
        chunks = search_resume(resume_id, retrieval_query)
    except ResumeIndexNotFound as exc:
        raise HTTPException(status_code=404, detail="Resume not found.") from exc

    try:
        return generate_interview_question(chunks, questions)
    except GeminiConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GeminiGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/start")
def start_interview(request: InterviewStartRequest):
    first_question = _generate_question(str(request.resume_id), [])
    try:
        session = create_session(str(request.resume_id), first_question)
    except history.HistoryStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "session_id": session.session_id,
        "resume_id": session.resume_id,
        "question": session.current_question,
        "question_id": session.current_question_id,
    }


@router.post("/{session_id}/answer")
def answer_interview_question(
    session_id: UUID, request: InterviewAnswerRequest
):
    try:
        submitted = submit_answer(str(session_id), request.answer)
    except InterviewSessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidInterviewState as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (history.HistoryStorageError, history.HistoryRecordNotFound) as exc:
        raise _history_error(exc) from exc

    return {
        "session_id": str(session_id),
        "question": submitted["question"],
        "answer_recorded": True,
    }


@router.post("/{session_id}/next")
def next_interview_question(session_id: UUID):
    session = _get_session(session_id)
    try:
        questions = previous_questions(str(session_id))
    except InterviewSessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidInterviewState as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    question = _generate_question(session.resume_id, questions)
    try:
        session = set_next_question(str(session_id), question)
    except InterviewSessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidInterviewState as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (history.HistoryStorageError, history.HistoryRecordNotFound) as exc:
        raise _history_error(exc) from exc
    return {
        "session_id": str(session_id),
        "question": session.current_question,
        "question_id": session.current_question_id,
    }


@router.post("/{session_id}/evaluate")
def evaluate_submitted_answer(session_id: UUID):
    session = _get_session(session_id)
    try:
        submitted = latest_answer(str(session_id))
    except InterviewSessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidInterviewState as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    evaluation = _evaluate(
        submitted["question"], submitted["answer"], session.resume_id
    )
    try:
        history.add_interview_evaluation(
            submitted["answer_id"], evaluation.model_dump()
        )
    except (history.HistoryStorageError, history.HistoryRecordNotFound) as exc:
        raise _history_error(exc) from exc

    return {
        "session_id": str(session_id),
        "question": submitted["question"],
        **evaluation.model_dump(),
    }


@router.get("/history")
def interview_history(resume_id: UUID):
    try:
        return {"sessions": history.list_interview_sessions(str(resume_id))}
    except history.HistoryStorageError as exc:
        raise _history_error(exc) from exc


@router.get("/history/{session_id}")
def interview_history_detail(session_id: UUID):
    try:
        return history.get_interview_session(str(session_id))
    except (history.HistoryRecordNotFound, history.HistoryStorageError) as exc:
        raise _history_error(exc) from exc


@router.get("/history/questions/{question_id}")
def interview_question_detail(question_id: UUID):
    try:
        return history.get_interview_question(str(question_id))
    except (history.HistoryRecordNotFound, history.HistoryStorageError) as exc:
        raise _history_error(exc) from exc


@router.post("/history/questions/{question_id}/answers")
def answer_historical_interview_question(
    question_id: UUID, request: InterviewAnswerRequest
):
    try:
        answer_id = history.add_interview_answer(str(question_id), request.answer)
    except (history.HistoryRecordNotFound, history.HistoryStorageError) as exc:
        raise _history_error(exc) from exc
    return {"question_id": str(question_id), "answer_id": answer_id}


@router.post("/history/answers/{answer_id}/evaluate")
def reevaluate_historical_interview_answer(answer_id: UUID):
    try:
        submitted = history.get_interview_answer(str(answer_id))
    except (history.HistoryRecordNotFound, history.HistoryStorageError) as exc:
        raise _history_error(exc) from exc
    evaluation = _evaluate(
        submitted["question"], submitted["answer"], submitted["resume_id"]
    )
    try:
        evaluation_id = history.add_interview_evaluation(
            str(answer_id), evaluation.model_dump()
        )
    except (history.HistoryRecordNotFound, history.HistoryStorageError) as exc:
        raise _history_error(exc) from exc
    return {
        "answer_id": str(answer_id),
        "evaluation_id": evaluation_id,
        **evaluation.model_dump(),
    }

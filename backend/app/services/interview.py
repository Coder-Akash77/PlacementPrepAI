from dataclasses import dataclass, field
from threading import Lock
from uuid import uuid4

from app.services.history import (
    add_interview_answer,
    add_interview_question,
    create_interview_session,
)


class InterviewSessionNotFound(LookupError):
    """Raised when an interview session does not exist."""


class InvalidInterviewState(ValueError):
    """Raised when an operation is invalid for the session's current state."""


@dataclass
class InterviewSession:
    session_id: str
    resume_id: str
    current_question: str
    current_question_id: str
    history: list[dict[str, str]] = field(default_factory=list)
    answer_submitted: bool = False


_sessions: dict[str, InterviewSession] = {}
_sessions_lock = Lock()


def create_session(resume_id: str, first_question: str) -> InterviewSession:
    session_id = str(uuid4())
    question_id = create_interview_session(session_id, resume_id, first_question)
    session = InterviewSession(
        session_id=session_id,
        resume_id=resume_id,
        current_question=first_question,
        current_question_id=question_id,
    )
    with _sessions_lock:
        _sessions[session.session_id] = session
    return session


def get_session(session_id: str) -> InterviewSession:
    with _sessions_lock:
        session = _sessions.get(session_id)
        if session is None:
            raise InterviewSessionNotFound("Interview session not found.")
        return session


def submit_answer(session_id: str, answer: str) -> dict[str, str]:
    with _sessions_lock:
        session = _sessions.get(session_id)
        if session is None:
            raise InterviewSessionNotFound("Interview session not found.")
        if session.answer_submitted:
            raise InvalidInterviewState(
                "Request the next question before submitting another answer."
            )

        answer_id = add_interview_answer(session.current_question_id, answer)
        submitted = {
            "question": session.current_question,
            "answer": answer,
            "question_id": session.current_question_id,
            "answer_id": answer_id,
        }
        session.history.append(submitted)
        session.answer_submitted = True
        return submitted.copy()


def set_next_question(session_id: str, question: str) -> InterviewSession:
    with _sessions_lock:
        session = _sessions.get(session_id)
        if session is None:
            raise InterviewSessionNotFound("Interview session not found.")
        if not session.answer_submitted:
            raise InvalidInterviewState(
                "Submit an answer before requesting the next question."
            )

        question_id = add_interview_question(session_id, question)
        session.current_question = question
        session.current_question_id = question_id
        session.answer_submitted = False
        return session


def latest_answer(session_id: str) -> dict[str, str]:
    with _sessions_lock:
        session = _sessions.get(session_id)
        if session is None:
            raise InterviewSessionNotFound("Interview session not found.")
        if not session.answer_submitted or not session.history:
            raise InvalidInterviewState(
                "Submit an answer before requesting an evaluation."
            )
        return session.history[-1].copy()


def previous_questions(session_id: str) -> list[str]:
    with _sessions_lock:
        session = _sessions.get(session_id)
        if session is None:
            raise InterviewSessionNotFound("Interview session not found.")
        if not session.answer_submitted:
            raise InvalidInterviewState(
                "Submit an answer before requesting the next question."
            )
        return [item["question"] for item in session.history]

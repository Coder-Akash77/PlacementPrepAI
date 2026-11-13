"""Smoke-test the history database configured by DATABASE_URL (or SQLite).

Run from the backend folder:  python scripts/check_history_db.py
Creates one throwaway practice session for a fake user, reads it back, and
confirms another user cannot see it. The test data is always deleted afterwards.
"""

import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.services import history  # noqa: E402


def delete_test_data(user_ids: list[str]) -> None:
    """Remove everything owned by the given throwaway users, children first."""
    marks = ", ".join("?" for _ in user_ids)
    connection = history._connect()
    try:
        history._initialize(connection)
        sessions = f"SELECT session_id FROM preparation_sessions WHERE user_id IN ({marks})"
        questions = f"SELECT question_id FROM preparation_questions WHERE session_id IN ({sessions})"
        answers = f"SELECT answer_id FROM preparation_answers WHERE question_id IN ({questions})"
        for statement in (
            f"DELETE FROM preparation_evaluations WHERE answer_id IN ({answers})",
            f"DELETE FROM preparation_answers WHERE question_id IN ({questions})",
            f"DELETE FROM preparation_questions WHERE session_id IN ({sessions})",
            f"DELETE FROM preparation_sessions WHERE user_id IN ({marks})",
        ):
            connection.execute(statement, tuple(user_ids) * statement.count(marks))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def verify(owner: str, stranger: str) -> None:
    session_id = history.create_preparation_session("dsa", user_id=owner)
    question_id = history.add_preparation_question(
        session_id, "dsa", "Smoke-test question?", user_id=owner
    )
    answer_id = history.add_preparation_answer(question_id, "answer", user_id=owner)
    history.add_preparation_evaluation(
        answer_id,
        {"score": 1, "strengths": ["a"], "improvements": ["b"], "suggested_answer": "c"},
        user_id=owner,
    )

    sessions = history.list_preparation_sessions(user_id=owner)
    assert [s["session_id"] for s in sessions] == [session_id], sessions
    detail = history.get_preparation_question(question_id, user_id=owner)
    assert len(detail["answers"][0]["evaluations"]) == 1, detail
    assert history.list_preparation_sessions(user_id=stranger) == []
    try:
        history.get_preparation_session(session_id, user_id=stranger)
    except history.HistoryRecordNotFound:
        pass
    else:
        raise AssertionError("another user could read this session")


def main() -> int:
    backend = "Postgres" if settings.DATABASE_URL else f"SQLite ({settings.HISTORY_DB_PATH})"
    print(f"Checking history storage: {backend}")
    owner, stranger = f"check-{uuid4()}", f"check-{uuid4()}"
    try:
        verify(owner, stranger)
    finally:
        delete_test_data([owner, stranger])
        print("Test data cleaned up.")

    print("OK: write, read-back and per-user isolation all work.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import settings


class HistoryRecordNotFound(LookupError):
    """Raised when a requested history record does not exist."""


class HistoryStorageError(RuntimeError):
    """Raised when persistent history cannot be read or written."""


class HistoryConflictError(RuntimeError):
    """Raised when a history record conflicts with the requested operation."""


def _connect() -> sqlite3.Connection:
    path = settings.HISTORY_DB_PATH
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS interview_sessions (
            session_id TEXT PRIMARY KEY,
            resume_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS interview_questions (
            question_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES interview_sessions(session_id),
            question TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS interview_answers (
            answer_id TEXT PRIMARY KEY,
            question_id TEXT NOT NULL REFERENCES interview_questions(question_id),
            attempt_no INTEGER NOT NULL,
            answer TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(question_id, attempt_no)
        );
        CREATE TABLE IF NOT EXISTS interview_evaluations (
            evaluation_id TEXT PRIMARY KEY,
            answer_id TEXT NOT NULL REFERENCES interview_answers(answer_id),
            score INTEGER NOT NULL,
            feedback_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS preparation_sessions (
            session_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL CHECK(mode IN ('dsa', 'sql', 'hr')),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS preparation_questions (
            question_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES preparation_sessions(session_id),
            mode TEXT NOT NULL CHECK(mode IN ('dsa', 'sql', 'hr')),
            topic TEXT,
            question TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS preparation_answers (
            answer_id TEXT PRIMARY KEY,
            question_id TEXT NOT NULL REFERENCES preparation_questions(question_id),
            attempt_no INTEGER NOT NULL,
            answer TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(question_id, attempt_no)
        );
        CREATE TABLE IF NOT EXISTS preparation_evaluations (
            evaluation_id TEXT PRIMARY KEY,
            answer_id TEXT NOT NULL REFERENCES preparation_answers(answer_id),
            score INTEGER NOT NULL,
            feedback_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS interview_questions_session_idx
            ON interview_questions(session_id, ordinal);
        CREATE INDEX IF NOT EXISTS preparation_questions_session_idx
            ON preparation_questions(session_id, ordinal);
        """
    )


def _run(write: bool, operation):
    connection = None
    try:
        connection = _connect()
        _initialize(connection)
        if write:
            connection.execute("BEGIN IMMEDIATE")
        result = operation(connection)
        if write:
            connection.commit()
        return result
    except HistoryRecordNotFound:
        raise
    except HistoryConflictError:
        raise
    except (sqlite3.Error, OSError, TypeError, ValueError) as exc:
        if connection is not None:
            connection.rollback()
        raise HistoryStorageError("Persistent history is unavailable.") from exc
    finally:
        if connection is not None:
            connection.close()


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def create_interview_session(
    session_id: str, resume_id: str, first_question: str
) -> str:
    question_id = str(uuid4())

    def operation(connection):
        now = _timestamp()
        connection.execute(
            "INSERT INTO interview_sessions VALUES (?, ?, ?)",
            (session_id, resume_id, now),
        )
        connection.execute(
            "INSERT INTO interview_questions VALUES (?, ?, ?, ?, ?)",
            (question_id, session_id, first_question, 1, now),
        )
        return question_id

    return _run(True, operation)


def add_interview_question(session_id: str, question: str) -> str:
    question_id = str(uuid4())

    def operation(connection):
        if not connection.execute(
            "SELECT 1 FROM interview_sessions WHERE session_id = ?", (session_id,)
        ).fetchone():
            raise HistoryRecordNotFound("Interview session not found.")
        ordinal = connection.execute(
            "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM interview_questions WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO interview_questions VALUES (?, ?, ?, ?, ?)",
            (question_id, session_id, question, ordinal, _timestamp()),
        )
        return question_id

    return _run(True, operation)


def add_interview_answer(question_id: str, answer: str) -> str:
    answer_id = str(uuid4())

    def operation(connection):
        if not connection.execute(
            "SELECT 1 FROM interview_questions WHERE question_id = ?", (question_id,)
        ).fetchone():
            raise HistoryRecordNotFound("Interview question not found.")
        attempt_no = connection.execute(
            "SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM interview_answers WHERE question_id = ?",
            (question_id,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO interview_answers VALUES (?, ?, ?, ?, ?)",
            (answer_id, question_id, attempt_no, answer, _timestamp()),
        )
        return answer_id

    return _run(True, operation)


def add_interview_evaluation(answer_id: str, feedback: dict[str, Any]) -> str:
    evaluation_id = str(uuid4())

    def operation(connection):
        if not connection.execute(
            "SELECT 1 FROM interview_answers WHERE answer_id = ?", (answer_id,)
        ).fetchone():
            raise HistoryRecordNotFound("Interview answer not found.")
        connection.execute(
            "INSERT INTO interview_evaluations VALUES (?, ?, ?, ?, ?)",
            (
                evaluation_id,
                answer_id,
                feedback["score"],
                json.dumps(feedback),
                _timestamp(),
            ),
        )
        return evaluation_id

    return _run(True, operation)


def list_interview_sessions(resume_id: str) -> list[dict[str, Any]]:
    def operation(connection):
        rows = connection.execute(
            """
            SELECT s.session_id, s.resume_id, s.created_at,
                   COUNT(DISTINCT q.question_id) AS question_count,
                   COUNT(DISTINCT a.answer_id) AS answer_count
            FROM interview_sessions s
            LEFT JOIN interview_questions q ON q.session_id = s.session_id
            LEFT JOIN interview_answers a ON a.question_id = q.question_id
            WHERE s.resume_id = ?
            GROUP BY s.session_id
            ORDER BY s.created_at DESC
            """,
            (resume_id,),
        ).fetchall()
        return [_dict(row) for row in rows]

    return _run(False, operation)


def get_interview_session(session_id: str) -> dict[str, Any]:
    def operation(connection):
        session = connection.execute(
            "SELECT * FROM interview_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if session is None:
            raise HistoryRecordNotFound("Interview session not found.")
        questions = connection.execute(
            """
            SELECT question_id, question, ordinal, created_at
            FROM interview_questions WHERE session_id = ? ORDER BY ordinal
            """,
            (session_id,),
        ).fetchall()
        return {**_dict(session), "questions": [_dict(row) for row in questions]}

    return _run(False, operation)


def get_interview_question(question_id: str) -> dict[str, Any]:
    def operation(connection):
        row = connection.execute(
            """
            SELECT q.*, s.resume_id FROM interview_questions q
            JOIN interview_sessions s USING(session_id) WHERE q.question_id = ?
            """,
            (question_id,),
        ).fetchone()
        if row is None:
            raise HistoryRecordNotFound("Interview question not found.")
        result = _dict(row)
        answers = connection.execute(
            "SELECT * FROM interview_answers WHERE question_id = ? ORDER BY attempt_no",
            (question_id,),
        ).fetchall()
        result["answers"] = []
        for answer_row in answers:
            answer = _dict(answer_row)
            evaluations = connection.execute(
                "SELECT * FROM interview_evaluations WHERE answer_id = ? ORDER BY created_at",
                (answer["answer_id"],),
            ).fetchall()
            answer["evaluations"] = [
                {**_dict(evaluation), "feedback": json.loads(evaluation["feedback_json"])}
                for evaluation in evaluations
            ]
            result["answers"].append(answer)
        return result

    return _run(False, operation)


def get_interview_answer(answer_id: str) -> dict[str, Any]:
    def operation(connection):
        row = connection.execute(
            """
            SELECT a.*, q.question_id, q.question, s.session_id, s.resume_id
            FROM interview_answers a
            JOIN interview_questions q USING(question_id)
            JOIN interview_sessions s USING(session_id)
            WHERE a.answer_id = ?
            """,
            (answer_id,),
        ).fetchone()
        if row is None:
            raise HistoryRecordNotFound("Interview answer not found.")
        result = _dict(row)
        result["evaluations"] = [
            {
                **_dict(evaluation),
                "feedback": json.loads(evaluation["feedback_json"]),
            }
            for evaluation in connection.execute(
                "SELECT * FROM interview_evaluations WHERE answer_id = ? ORDER BY created_at",
                (answer_id,),
            ).fetchall()
        ]
        return result

    return _run(False, operation)


def create_preparation_session(mode: str, session_id: str | None = None) -> str:
    session_id = session_id or str(uuid4())

    def operation(connection):
        row = connection.execute(
            "SELECT mode FROM preparation_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is not None:
            if row["mode"] != mode:
                raise HistoryConflictError("Practice session mode does not match.")
            return session_id
        connection.execute(
            "INSERT INTO preparation_sessions VALUES (?, ?, ?)",
            (session_id, mode, _timestamp()),
        )
        return session_id

    return _run(True, operation)


def add_preparation_question(
    session_id: str, mode: str, question: str, topic: str | None = None
) -> str:
    question_id = str(uuid4())

    def operation(connection):
        session = connection.execute(
            "SELECT mode FROM preparation_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if session is None:
            raise HistoryRecordNotFound("Practice session not found.")
        if session["mode"] != mode:
            raise HistoryConflictError("Practice session mode does not match.")
        ordinal = connection.execute(
            "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM preparation_questions WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO preparation_questions VALUES (?, ?, ?, ?, ?, ?, ?)",
            (question_id, session_id, mode, topic, question, ordinal, _timestamp()),
        )
        return question_id

    return _run(True, operation)


def get_preparation_session(session_id: str) -> dict[str, Any]:
    def operation(connection):
        session = connection.execute(
            "SELECT * FROM preparation_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if session is None:
            raise HistoryRecordNotFound("Practice session not found.")
        questions = connection.execute(
            """
            SELECT question_id, mode, topic, question, ordinal, created_at
            FROM preparation_questions WHERE session_id = ? ORDER BY ordinal
            """,
            (session_id,),
        ).fetchall()
        return {**_dict(session), "questions": [_dict(row) for row in questions]}

    return _run(False, operation)


def get_preparation_question(question_id: str) -> dict[str, Any]:
    def operation(connection):
        row = connection.execute(
            "SELECT * FROM preparation_questions WHERE question_id = ?",
            (question_id,),
        ).fetchone()
        if row is None:
            raise HistoryRecordNotFound("Practice question not found.")
        result = _dict(row)
        answers = connection.execute(
            "SELECT * FROM preparation_answers WHERE question_id = ? ORDER BY attempt_no",
            (question_id,),
        ).fetchall()
        result["answers"] = []
        for answer_row in answers:
            answer = _dict(answer_row)
            evaluations = connection.execute(
                "SELECT * FROM preparation_evaluations WHERE answer_id = ? ORDER BY created_at",
                (answer["answer_id"],),
            ).fetchall()
            answer["evaluations"] = [
                {**_dict(evaluation), "feedback": json.loads(evaluation["feedback_json"])}
                for evaluation in evaluations
            ]
            result["answers"].append(answer)
        return result

    return _run(False, operation)


def add_preparation_answer(question_id: str, answer: str) -> str:
    answer_id = str(uuid4())

    def operation(connection):
        if not connection.execute(
            "SELECT 1 FROM preparation_questions WHERE question_id = ?",
            (question_id,),
        ).fetchone():
            raise HistoryRecordNotFound("Practice question not found.")
        attempt_no = connection.execute(
            "SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM preparation_answers WHERE question_id = ?",
            (question_id,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO preparation_answers VALUES (?, ?, ?, ?, ?)",
            (answer_id, question_id, attempt_no, answer, _timestamp()),
        )
        return answer_id

    return _run(True, operation)


def record_preparation_response(
    session_id: str,
    mode: str,
    question: str,
    answer: str,
    feedback: dict[str, Any],
    question_id: str | None = None,
    topic: str | None = None,
) -> tuple[str, str, str, str]:
    requested_session_id = session_id
    requested_question_id = question_id

    def operation(connection):
        session_id = requested_session_id
        question_id = requested_question_id
        if question_id is not None:
            row = connection.execute(
                "SELECT session_id, mode, question FROM preparation_questions WHERE question_id = ?",
                (question_id,),
            ).fetchone()
            if row is None:
                raise HistoryRecordNotFound("Practice question not found.")
            if row["mode"] != mode or row["question"] != question:
                raise HistoryConflictError("Practice question does not match request.")
            if session_id and row["session_id"] != session_id:
                raise HistoryConflictError("Practice question does not match session.")
            session_id = row["session_id"]
        else:
            session = connection.execute(
                "SELECT mode FROM preparation_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if session is None or session["mode"] != mode:
                raise HistoryRecordNotFound("Practice session not found.")
            question_id = str(uuid4())
            ordinal = connection.execute(
                "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM preparation_questions WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO preparation_questions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (question_id, session_id, mode, topic, question, ordinal, _timestamp()),
            )

        answer_id = str(uuid4())
        attempt_no = connection.execute(
            "SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM preparation_answers WHERE question_id = ?",
            (question_id,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO preparation_answers VALUES (?, ?, ?, ?, ?)",
            (answer_id, question_id, attempt_no, answer, _timestamp()),
        )
        evaluation_id = str(uuid4())
        connection.execute(
            "INSERT INTO preparation_evaluations VALUES (?, ?, ?, ?, ?)",
            (
                evaluation_id,
                answer_id,
                feedback["score"],
                json.dumps(feedback),
                _timestamp(),
            ),
        )
        return session_id, question_id, answer_id, evaluation_id

    return _run(True, operation)


def get_preparation_answer(answer_id: str) -> dict[str, Any]:
    def operation(connection):
        row = connection.execute(
            """
            SELECT a.*, q.question_id, q.mode, q.question, q.session_id
            FROM preparation_answers a
            JOIN preparation_questions q USING(question_id)
            WHERE a.answer_id = ?
            """,
            (answer_id,),
        ).fetchone()
        if row is None:
            raise HistoryRecordNotFound("Practice answer not found.")
        return _dict(row)

    return _run(False, operation)


def add_preparation_evaluation(answer_id: str, feedback: dict[str, Any]) -> str:
    evaluation_id = str(uuid4())

    def operation(connection):
        if not connection.execute(
            "SELECT 1 FROM preparation_answers WHERE answer_id = ?", (answer_id,)
        ).fetchone():
            raise HistoryRecordNotFound("Practice answer not found.")
        connection.execute(
            "INSERT INTO preparation_evaluations VALUES (?, ?, ?, ?, ?)",
            (
                evaluation_id,
                answer_id,
                feedback["score"],
                json.dumps(feedback),
                _timestamp(),
            ),
        )
        return evaluation_id

    return _run(True, operation)

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg

from app.config import settings


class HistoryRecordNotFound(LookupError):
    """Raised when a requested history record does not exist."""


class HistoryStorageError(RuntimeError):
    """Raised when persistent history cannot be read or written."""


class HistoryConflictError(RuntimeError):
    """Raised when a history record conflicts with the requested operation."""


class _Row(dict):
    """Dict row that also supports positional access, like sqlite3.Row."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _pg_row_factory(cursor):
    names = [column.name for column in cursor.description or []]

    def make_row(values):
        return _Row(zip(names, values))

    return make_row


class _PostgresConnection:
    """Gives a psycopg connection the sqlite-style `?` placeholder API."""

    def __init__(self, connection):
        self._connection = connection

    def execute(self, sql: str, params=()):
        return self._connection.execute(sql.replace("?", "%s"), params)

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()


def _connect():
    if settings.DATABASE_URL:
        return _PostgresConnection(
            psycopg.connect(
                settings.DATABASE_URL,
                row_factory=_pg_row_factory,
                prepare_threshold=None,  # required behind Supabase's pooler
                connect_timeout=10,
            )
        )
    path = settings.HISTORY_DB_PATH
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS interview_sessions (
    session_id TEXT PRIMARY KEY,
    resume_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT ''
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
    created_at TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT ''
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
_USER_INDEXES = (
    "CREATE INDEX IF NOT EXISTS interview_sessions_user_idx "
    "ON interview_sessions(user_id, created_at)",
    "CREATE INDEX IF NOT EXISTS preparation_sessions_user_idx "
    "ON preparation_sessions(user_id, created_at)",
)
_USER_SCOPED_TABLES = ("interview_sessions", "preparation_sessions")
_initialized_targets: set[str] = set()
_initialize_lock = threading.Lock()


def _initialize(connection) -> None:
    target = settings.DATABASE_URL or settings.HISTORY_DB_PATH
    cacheable = target != ":memory:"
    if cacheable and target in _initialized_targets:
        return
    with _initialize_lock:
        if cacheable and target in _initialized_targets:
            return
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                connection.execute(statement)
        if not settings.DATABASE_URL:
            # Databases created before user accounts lack the user_id column.
            for table in _USER_SCOPED_TABLES:
                columns = {
                    row["name"]
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                if "user_id" not in columns:
                    connection.execute(
                        f"ALTER TABLE {table} "
                        "ADD COLUMN user_id TEXT NOT NULL DEFAULT ''"
                    )
        for statement in _USER_INDEXES:
            connection.execute(statement)
        connection.commit()
        if cacheable:
            _initialized_targets.add(target)


def _run(write: bool, operation):
    connection = None
    try:
        connection = _connect()
        _initialize(connection)
        if write and not settings.DATABASE_URL:
            connection.execute("BEGIN IMMEDIATE")
        result = operation(connection)
        if write:
            connection.commit()
        return result
    except HistoryRecordNotFound:
        raise
    except HistoryConflictError:
        raise
    except (sqlite3.Error, psycopg.Error, OSError, TypeError, ValueError) as exc:
        if connection is not None:
            connection.rollback()
        raise HistoryStorageError("Persistent history is unavailable.") from exc
    finally:
        if connection is not None:
            connection.close()


def _dict(row) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result.pop("user_id", None)
    return result


def _owned_interview_question(connection, question_id: str, user_id: str):
    return connection.execute(
        """
        SELECT q.question_id FROM interview_questions q
        JOIN interview_sessions s ON s.session_id = q.session_id
        WHERE q.question_id = ? AND s.user_id = ?
        """,
        (question_id, user_id),
    ).fetchone()


def _owned_interview_answer(connection, answer_id: str, user_id: str):
    return connection.execute(
        """
        SELECT a.answer_id FROM interview_answers a
        JOIN interview_questions q ON q.question_id = a.question_id
        JOIN interview_sessions s ON s.session_id = q.session_id
        WHERE a.answer_id = ? AND s.user_id = ?
        """,
        (answer_id, user_id),
    ).fetchone()


def _owned_preparation_question(connection, question_id: str, user_id: str):
    return connection.execute(
        """
        SELECT q.question_id FROM preparation_questions q
        JOIN preparation_sessions s ON s.session_id = q.session_id
        WHERE q.question_id = ? AND s.user_id = ?
        """,
        (question_id, user_id),
    ).fetchone()


def _owned_preparation_answer(connection, answer_id: str, user_id: str):
    return connection.execute(
        """
        SELECT a.answer_id FROM preparation_answers a
        JOIN preparation_questions q ON q.question_id = a.question_id
        JOIN preparation_sessions s ON s.session_id = q.session_id
        WHERE a.answer_id = ? AND s.user_id = ?
        """,
        (answer_id, user_id),
    ).fetchone()


def create_interview_session(
    session_id: str, resume_id: str, first_question: str, *, user_id: str
) -> str:
    question_id = str(uuid4())

    def operation(connection):
        now = _timestamp()
        connection.execute(
            "INSERT INTO interview_sessions (session_id, resume_id, created_at, user_id) "
            "VALUES (?, ?, ?, ?)",
            (session_id, resume_id, now, user_id),
        )
        connection.execute(
            "INSERT INTO interview_questions VALUES (?, ?, ?, ?, ?)",
            (question_id, session_id, first_question, 1, now),
        )
        return question_id

    return _run(True, operation)


def add_interview_question(session_id: str, question: str, *, user_id: str) -> str:
    question_id = str(uuid4())

    def operation(connection):
        if not connection.execute(
            "SELECT 1 FROM interview_sessions WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
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


def add_interview_answer(question_id: str, answer: str, *, user_id: str) -> str:
    answer_id = str(uuid4())

    def operation(connection):
        if not _owned_interview_question(connection, question_id, user_id):
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


def add_interview_evaluation(
    answer_id: str, feedback: dict[str, Any], *, user_id: str
) -> str:
    evaluation_id = str(uuid4())

    def operation(connection):
        if not _owned_interview_answer(connection, answer_id, user_id):
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


def list_interview_sessions(
    *, user_id: str, resume_id: str | None = None
) -> list[dict[str, Any]]:
    def operation(connection):
        filters, params = ["s.user_id = ?"], [user_id]
        if resume_id is not None:
            filters.append("s.resume_id = ?")
            params.append(resume_id)
        rows = connection.execute(
            f"""
            SELECT s.session_id, s.resume_id, s.created_at,
                   COUNT(DISTINCT q.question_id) AS question_count,
                   COUNT(DISTINCT a.answer_id) AS answer_count
            FROM interview_sessions s
            LEFT JOIN interview_questions q ON q.session_id = s.session_id
            LEFT JOIN interview_answers a ON a.question_id = q.question_id
            WHERE {" AND ".join(filters)}
            GROUP BY s.session_id
            ORDER BY s.created_at DESC
            """,
            tuple(params),
        ).fetchall()
        return [_dict(row) for row in rows]

    return _run(False, operation)


def get_interview_session(session_id: str, *, user_id: str) -> dict[str, Any]:
    def operation(connection):
        session = connection.execute(
            "SELECT * FROM interview_sessions WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
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


def _with_evaluations(
    connection, table: str, answer: dict[str, Any]
) -> dict[str, Any]:
    evaluations = connection.execute(
        f"SELECT * FROM {table} WHERE answer_id = ? ORDER BY created_at",
        (answer["answer_id"],),
    ).fetchall()
    answer["evaluations"] = [
        {**_dict(evaluation), "feedback": json.loads(evaluation["feedback_json"])}
        for evaluation in evaluations
    ]
    return answer


def get_interview_question(question_id: str, *, user_id: str) -> dict[str, Any]:
    def operation(connection):
        row = connection.execute(
            """
            SELECT q.*, s.resume_id FROM interview_questions q
            JOIN interview_sessions s ON s.session_id = q.session_id
            WHERE q.question_id = ? AND s.user_id = ?
            """,
            (question_id, user_id),
        ).fetchone()
        if row is None:
            raise HistoryRecordNotFound("Interview question not found.")
        result = _dict(row)
        answers = connection.execute(
            "SELECT * FROM interview_answers WHERE question_id = ? ORDER BY attempt_no",
            (question_id,),
        ).fetchall()
        result["answers"] = [
            _with_evaluations(connection, "interview_evaluations", _dict(answer_row))
            for answer_row in answers
        ]
        return result

    return _run(False, operation)


def get_interview_answer(answer_id: str, *, user_id: str) -> dict[str, Any]:
    def operation(connection):
        row = connection.execute(
            """
            SELECT a.*, q.question, s.session_id, s.resume_id
            FROM interview_answers a
            JOIN interview_questions q ON q.question_id = a.question_id
            JOIN interview_sessions s ON s.session_id = q.session_id
            WHERE a.answer_id = ? AND s.user_id = ?
            """,
            (answer_id, user_id),
        ).fetchone()
        if row is None:
            raise HistoryRecordNotFound("Interview answer not found.")
        result = _dict(row)
        evaluations = connection.execute(
            "SELECT * FROM interview_evaluations WHERE answer_id = ? ORDER BY created_at",
            (answer_id,),
        ).fetchall()
        result["evaluations"] = [
            {**_dict(evaluation), "feedback": json.loads(evaluation["feedback_json"])}
            for evaluation in evaluations
        ]
        return result

    return _run(False, operation)


def create_preparation_session(
    mode: str, session_id: str | None = None, *, user_id: str
) -> str:
    session_id = session_id or str(uuid4())

    def operation(connection):
        row = connection.execute(
            "SELECT mode, user_id FROM preparation_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is not None:
            if row["user_id"] != user_id:
                raise HistoryRecordNotFound("Practice session not found.")
            if row["mode"] != mode:
                raise HistoryConflictError("Practice session mode does not match.")
            return session_id
        connection.execute(
            "INSERT INTO preparation_sessions (session_id, mode, created_at, user_id) "
            "VALUES (?, ?, ?, ?)",
            (session_id, mode, _timestamp(), user_id),
        )
        return session_id

    return _run(True, operation)


def add_preparation_question(
    session_id: str,
    mode: str,
    question: str,
    topic: str | None = None,
    *,
    user_id: str,
) -> str:
    question_id = str(uuid4())

    def operation(connection):
        session = connection.execute(
            "SELECT mode FROM preparation_sessions WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
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


def list_preparation_sessions(*, user_id: str) -> list[dict[str, Any]]:
    def operation(connection):
        rows = connection.execute(
            """
            SELECT s.session_id, s.mode, s.created_at,
                   COUNT(q.question_id) AS question_count
            FROM preparation_sessions s
            LEFT JOIN preparation_questions q ON q.session_id = s.session_id
            WHERE s.user_id = ?
            GROUP BY s.session_id
            ORDER BY s.created_at DESC
            """,
            (user_id,),
        ).fetchall()
        return [_dict(row) for row in rows]

    return _run(False, operation)


def get_preparation_session(session_id: str, *, user_id: str) -> dict[str, Any]:
    def operation(connection):
        session = connection.execute(
            "SELECT * FROM preparation_sessions WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
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


def get_preparation_question(question_id: str, *, user_id: str) -> dict[str, Any]:
    def operation(connection):
        row = connection.execute(
            """
            SELECT q.* FROM preparation_questions q
            JOIN preparation_sessions s ON s.session_id = q.session_id
            WHERE q.question_id = ? AND s.user_id = ?
            """,
            (question_id, user_id),
        ).fetchone()
        if row is None:
            raise HistoryRecordNotFound("Practice question not found.")
        result = _dict(row)
        answers = connection.execute(
            "SELECT * FROM preparation_answers WHERE question_id = ? ORDER BY attempt_no",
            (question_id,),
        ).fetchall()
        result["answers"] = [
            _with_evaluations(connection, "preparation_evaluations", _dict(answer_row))
            for answer_row in answers
        ]
        return result

    return _run(False, operation)


def add_preparation_answer(question_id: str, answer: str, *, user_id: str) -> str:
    answer_id = str(uuid4())

    def operation(connection):
        if not _owned_preparation_question(connection, question_id, user_id):
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
    *,
    user_id: str,
) -> tuple[str, str, str, str]:
    requested_session_id = session_id
    requested_question_id = question_id

    def operation(connection):
        session_id = requested_session_id
        question_id = requested_question_id
        if question_id is not None:
            row = connection.execute(
                """
                SELECT q.session_id, q.mode, q.question
                FROM preparation_questions q
                JOIN preparation_sessions s ON s.session_id = q.session_id
                WHERE q.question_id = ? AND s.user_id = ?
                """,
                (question_id, user_id),
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
                "SELECT mode FROM preparation_sessions WHERE session_id = ? AND user_id = ?",
                (session_id, user_id),
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


def get_preparation_answer(answer_id: str, *, user_id: str) -> dict[str, Any]:
    def operation(connection):
        row = connection.execute(
            """
            SELECT a.*, q.mode, q.question, q.session_id
            FROM preparation_answers a
            JOIN preparation_questions q ON q.question_id = a.question_id
            JOIN preparation_sessions s ON s.session_id = q.session_id
            WHERE a.answer_id = ? AND s.user_id = ?
            """,
            (answer_id, user_id),
        ).fetchone()
        if row is None:
            raise HistoryRecordNotFound("Practice answer not found.")
        return _dict(row)

    return _run(False, operation)


def add_preparation_evaluation(
    answer_id: str, feedback: dict[str, Any], *, user_id: str
) -> str:
    evaluation_id = str(uuid4())

    def operation(connection):
        if not _owned_preparation_answer(connection, answer_id, user_id):
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

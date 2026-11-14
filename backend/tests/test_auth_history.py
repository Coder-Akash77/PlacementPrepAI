import sqlite3
import time
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api.auth import get_current_user_id
from app.config import settings
from app.main import app
from app.services import history
from app.services.auth import InvalidCredential


@pytest.fixture
def db_path(tmp_path):
    old_path = settings.HISTORY_DB_PATH
    settings.HISTORY_DB_PATH = str(tmp_path / "history.db")
    yield settings.HISTORY_DB_PATH
    settings.HISTORY_DB_PATH = old_path


@pytest.fixture
def client(db_path):
    return TestClient(app)


@pytest.fixture
def secret():
    old = (settings.SESSION_SECRET, settings.GOOGLE_CLIENT_ID)
    settings.SESSION_SECRET = "test-secret-that-is-long-enough-for-hs256"
    settings.GOOGLE_CLIENT_ID = "client-id.apps.googleusercontent.com"
    yield settings.SESSION_SECRET
    settings.SESSION_SECRET, settings.GOOGLE_CLIENT_ID = old


def as_user(user_id):
    app.dependency_overrides[get_current_user_id] = lambda: user_id


FEEDBACK = {
    "score": 70,
    "strengths": ["ok"],
    "improvements": ["more"],
    "suggested_answer": "better",
}


def test_google_login_issues_working_session_token(client, secret):
    app.dependency_overrides.pop(get_current_user_id)
    claims = {"sub": "google-123", "name": "Ada", "email": "ada@example.com"}
    with patch("app.api.auth.verify_google_credential", return_value=claims):
        login = client.post("/api/auth/google", json={"credential": "google-id-token"})
    assert login.status_code == 200
    body = login.json()
    assert body["user"]["email"] == "ada@example.com"
    assert "sub" not in body["user"]

    me = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {body['token']}"}
    )
    assert me.status_code == 200
    assert me.json()["user"]["name"] == "Ada"


def test_invalid_google_credential_is_rejected(client, secret):
    with patch(
        "app.api.auth.verify_google_credential",
        side_effect=InvalidCredential("Invalid Google credential."),
    ):
        response = client.post("/api/auth/google", json={"credential": "bad"})
    assert response.status_code == 401


def test_google_login_without_configuration_returns_503(client):
    old = settings.GOOGLE_CLIENT_ID
    settings.GOOGLE_CLIENT_ID = ""
    try:
        response = client.post("/api/auth/google", json={"credential": "x"})
    finally:
        settings.GOOGLE_CLIENT_ID = old
    assert response.status_code == 503


def test_protected_routes_require_a_valid_token(client, secret):
    app.dependency_overrides.pop(get_current_user_id)
    assert client.get("/api/preparation/history").status_code == 401
    assert (
        client.get(
            "/api/preparation/history", headers={"Authorization": "Bearer junk"}
        ).status_code
        == 401
    )
    forged = jwt.encode(
        {"sub": "u", "exp": int(time.time()) + 60}, "another-secret-entirely-xxxxxxxx", "HS256"
    )
    assert (
        client.get(
            "/api/preparation/history", headers={"Authorization": f"Bearer {forged}"}
        ).status_code
        == 401
    )
    expired = jwt.encode(
        {"sub": "u", "exp": int(time.time()) - 60}, secret, "HS256"
    )
    assert (
        client.get(
            "/api/preparation/history", headers={"Authorization": f"Bearer {expired}"}
        ).status_code
        == 401
    )


def test_practice_history_is_listed_per_user_and_persists(client):
    as_user("alice")
    with patch(
        "app.api.preparation.generate_preparation_question", return_value="Q1?"
    ):
        created = client.post("/api/preparation/question", json={"mode": "dsa"}).json()

    listing = client.get("/api/preparation/history").json()["sessions"]
    assert [s["session_id"] for s in listing] == [created["session_id"]]
    assert listing[0]["mode"] == "dsa"
    assert listing[0]["question_count"] == 1
    assert "user_id" not in listing[0]

    # Later login (new request, same user) still sees it; other users do not.
    assert client.get("/api/preparation/history").json()["sessions"] == listing
    as_user("bob")
    assert client.get("/api/preparation/history").json()["sessions"] == []


def test_users_cannot_read_or_modify_each_others_history(client):
    as_user("alice")
    with patch(
        "app.api.preparation.generate_preparation_question", return_value="Q1?"
    ):
        created = client.post("/api/preparation/question", json={"mode": "sql"}).json()
    session_id, question_id = created["session_id"], created["question_id"]

    as_user("bob")
    assert client.get(f"/api/preparation/history/{session_id}").status_code == 404
    assert (
        client.get(f"/api/preparation/history/questions/{question_id}").status_code
        == 404
    )
    assert (
        client.post(
            f"/api/preparation/history/questions/{question_id}/answers",
            json={"answer": "select 1"},
        ).status_code
        == 404
    )
    # Bob cannot attach questions to Alice's session by reusing its id.
    with patch(
        "app.api.preparation.generate_preparation_question", return_value="Q2?"
    ):
        hijack = client.post(
            "/api/preparation/question",
            json={"mode": "sql", "session_id": session_id},
        )
    assert hijack.status_code == 404

    as_user("alice")
    assert client.get(f"/api/preparation/history/{session_id}").status_code == 200
    assert len(client.get(f"/api/preparation/history/{session_id}").json()["questions"]) == 1


def test_interview_history_is_scoped_and_lists_without_resume_filter(db_path):
    history.create_interview_session("s1", "resume-a", "Tell me about X", user_id="alice")
    history.create_interview_session("s2", "resume-b", "Tell me about Y", user_id="bob")

    alice = history.list_interview_sessions(user_id="alice")
    assert [s["session_id"] for s in alice] == ["s1"]
    assert history.list_interview_sessions(user_id="alice", resume_id="resume-b") == []
    with pytest.raises(history.HistoryRecordNotFound):
        history.get_interview_session("s2", user_id="alice")


def test_existing_database_without_user_column_is_migrated(db_path):
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE preparation_sessions (
            session_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL CHECK(mode IN ('dsa', 'sql', 'hr')),
            created_at TEXT NOT NULL
        );
        INSERT INTO preparation_sessions VALUES ('old', 'dsa', '2024-01-01');
        """
    )
    connection.commit()
    connection.close()
    history._initialized_targets.discard(db_path)

    new_id = history.create_preparation_session("hr", user_id="alice")
    sessions = history.list_preparation_sessions(user_id="alice")
    assert [s["session_id"] for s in sessions] == [new_id]
    # Pre-account rows are kept but belong to nobody.
    with pytest.raises(history.HistoryRecordNotFound):
        history.get_preparation_session("old", user_id="alice")


# ------------------------------------------------ storage failures are diagnosable


def test_storage_failure_is_logged_without_leaking_the_connection_string(caplog):
    """A Postgres failure used to surface only as a generic 503 with no log line."""
    import psycopg

    password = "s3cr3t-pa55"
    url = f"postgresql://postgres.abc:{password}@aws-0-eu.pooler.supabase.com:5432/postgres"
    old = settings.DATABASE_URL
    settings.DATABASE_URL = url
    error = psycopg.OperationalError(
        f'connection failed: FATAL: Tenant or user not found (dsn {url})'
    )
    try:
        with patch.object(history, "_connect", side_effect=error), caplog.at_level(
            "ERROR", logger="app.services.history"
        ):
            with pytest.raises(history.HistoryStorageError) as raised:
                history.list_preparation_sessions(user_id="alice")
    finally:
        settings.DATABASE_URL = old

    assert str(raised.value) == "Persistent history is unavailable."
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "backend=postgres" in logged
    assert "OperationalError" in logged
    assert "Tenant or user not found" in logged
    assert password not in logged
    assert url not in logged


def test_history_endpoint_returns_503_and_logs_when_storage_is_down(client, caplog):
    as_user("alice")
    with patch.object(history, "_connect", side_effect=OSError("disk unavailable")), caplog.at_level(
        "ERROR", logger="app.services.history"
    ):
        response = client.get("/api/preparation/history")
    assert response.status_code == 503
    assert response.json()["detail"] == "Persistent history is unavailable."
    assert any("History storage failed" in r.getMessage() for r in caplog.records)


def test_malformed_password_fragments_never_reach_the_logs(caplog):
    """A password containing '@' or brackets makes libpq report part of it as the host."""
    import psycopg

    url = "postgresql://postgres.abc:[Xy9@Zq778]@aws-1-ap-southeast-2.pooler.supabase.com:5432/postgres"
    error = psycopg.OperationalError(
        "failed to resolve host 'Zq778]@aws-1-ap-southeast-2.pooler.supabase.com': "
        "[Errno -2] Name or service not known"
    )
    old = settings.DATABASE_URL
    settings.DATABASE_URL = url
    try:
        with patch.object(history, "_connect", side_effect=error), caplog.at_level(
            "ERROR", logger="app.services.history"
        ):
            with pytest.raises(history.HistoryStorageError):
                history.list_preparation_sessions(user_id="alice")
    finally:
        settings.DATABASE_URL = old
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "Zq778" not in logged and "Xy9" not in logged
    # The host stays visible, which is what makes the log useful for diagnosis.
    assert "aws-1-ap-southeast-2.pooler.supabase.com" in logged
    assert "Name or service not known" in logged


def test_url_encoded_and_unrelated_credentials_are_redacted(caplog):
    import psycopg

    old = settings.DATABASE_URL
    settings.DATABASE_URL = "postgresql://postgres.abc:p%40ss%2Fw0rd@host.example:5432/postgres"
    error = psycopg.OperationalError(
        'connection to "p@ss/w0rd" failed; also postgresql://other:hunter22@elsewhere/db'
    )
    try:
        with patch.object(history, "_connect", side_effect=error), caplog.at_level(
            "ERROR", logger="app.services.history"
        ):
            with pytest.raises(history.HistoryStorageError):
                history.list_preparation_sessions(user_id="alice")
    finally:
        settings.DATABASE_URL = old
    logged = " ".join(record.getMessage() for record in caplog.records)
    for secret in ("p@ss", "w0rd", "hunter22", "p%40ss"):
        assert secret not in logged


def test_secret_straddling_the_truncation_limit_is_still_redacted(caplog):
    """Redaction must happen before the message is shortened, not after."""
    import psycopg

    password = "StraddleSecret99"
    url = f"postgresql://postgres.abc:{password}@host.example:5432/postgres"
    for padding in range(280, 300):  # slide the secret across the old 300-char cut
        caplog.clear()
        error = psycopg.OperationalError("x" * padding + f" password={password} tail")
        old = settings.DATABASE_URL
        settings.DATABASE_URL = url
        try:
            with patch.object(history, "_connect", side_effect=error), caplog.at_level(
                "ERROR", logger="app.services.history"
            ):
                with pytest.raises(history.HistoryStorageError):
                    history.list_preparation_sessions(user_id="alice")
        finally:
            settings.DATABASE_URL = old
        logged = " ".join(record.getMessage() for record in caplog.records)
        for piece in ("Straddle", "Secret99", "StraddleSecret99", "ecret9"):
            assert piece not in logged, (padding, piece)


# ------------------------------------------------ bounded connection retry


@pytest.fixture
def postgres_url():
    old = settings.DATABASE_URL
    settings.DATABASE_URL = "postgresql://postgres.abc:pw12345@host.example:5432/postgres"
    yield settings.DATABASE_URL
    settings.DATABASE_URL = old


def test_connect_retries_once_after_a_transient_network_failure(postgres_url):
    import psycopg
    from unittest.mock import MagicMock

    flaky = psycopg.OperationalError("connection failed: Network is unreachable")
    with patch.object(history.psycopg, "connect", side_effect=[flaky, MagicMock()]) as connect, patch.object(
        history.time, "sleep"
    ) as sleep:
        connection = history._connect()
    assert connect.call_count == 2
    sleep.assert_called_once_with(history._CONNECT_RETRY_DELAY_SECONDS)
    assert connection is not None


def test_connect_gives_up_after_the_strict_attempt_limit(postgres_url):
    import psycopg

    down = psycopg.OperationalError("connection failed: Network is unreachable")
    with patch.object(history.psycopg, "connect", side_effect=down) as connect, patch.object(
        history.time, "sleep"
    ) as sleep:
        with pytest.raises(history.HistoryStorageError):
            history.list_preparation_sessions(user_id="alice")
    assert connect.call_count == history._CONNECT_ATTEMPTS == 2
    assert sleep.call_count == 1


def test_connect_does_not_retry_rejections_such_as_a_wrong_password(postgres_url):
    import psycopg

    rejected = psycopg.errors.InvalidPassword("password authentication failed")
    with patch.object(history.psycopg, "connect", side_effect=rejected) as connect, patch.object(
        history.time, "sleep"
    ) as sleep:
        with pytest.raises(history.HistoryStorageError):
            history.list_preparation_sessions(user_id="alice")
    assert connect.call_count == 1
    sleep.assert_not_called()


def test_retry_warning_does_not_leak_credentials(postgres_url, caplog):
    import psycopg
    from unittest.mock import MagicMock

    flaky = psycopg.OperationalError(f"failed to connect to {postgres_url}")
    with patch.object(history.psycopg, "connect", side_effect=[flaky, MagicMock()]), patch.object(
        history.time, "sleep"
    ), caplog.at_level("WARNING", logger="app.services.history"):
        history._connect()
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "retrying" in logged
    assert "pw12345" not in logged


def test_a_retry_never_replays_a_write(db_path):
    """Retrying only covers the connect step, so a failing statement runs once."""
    calls = []

    def operation(connection):
        calls.append(1)
        raise sqlite3.OperationalError("disk I/O error")

    with pytest.raises(history.HistoryStorageError):
        history._run(True, operation)
    assert calls == [1]

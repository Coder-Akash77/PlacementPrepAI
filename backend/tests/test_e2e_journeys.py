"""End-to-end user journeys over HTTP with real session tokens.

Uses a temporary SQLite history database and mocked Gemini/embedding calls, so
no real provider or production database is touched.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api.auth import get_current_user_id
from app.config import settings
from app.main import app
from app.services import interview as interview_service
from app.services.auth import issue_session_token

RESUME_ID = "8f3a5f0e-6d3c-4f43-9a52-0d7f6a1c2b11"
FEEDBACK = {
    "score": 70,
    "strengths": ["clear"],
    "improvements": ["edge cases"],
    "suggested_answer": "model answer",
}
INTERVIEW_FEEDBACK = {
    "score": 80,
    "strengths": ["clear"],
    "improvements": ["detail"],
    "improved_sample_answer": "better answer",
}


@pytest.fixture
def api(tmp_path):
    """A client that uses the real token check against a temp database."""
    old = (settings.HISTORY_DB_PATH, settings.SESSION_SECRET)
    settings.HISTORY_DB_PATH = str(tmp_path / "history.db")
    settings.SESSION_SECRET = "test-secret-that-is-long-enough-for-hs256"
    app.dependency_overrides.pop(get_current_user_id, None)
    interview_service._sessions.clear()
    with patch(
        "app.api.preparation.generate_preparation_question", return_value="Q1?"
    ), patch(
        "app.api.preparation.evaluate_preparation_answer", return_value=FEEDBACK
    ), patch("app.api.interview.search_resume", return_value=[]), patch(
        "app.api.interview.generate_interview_question", side_effect=["I1?", "I2?"]
    ), patch(
        "app.api.interview.evaluate_interview_answer", return_value=INTERVIEW_FEEDBACK
    ):
        yield TestClient(app)
    interview_service._sessions.clear()
    settings.HISTORY_DB_PATH, settings.SESSION_SECRET = old


def login(sub):
    """A fresh sign-in: a new token for the same Google account."""
    token, _ = issue_session_token({"sub": sub, "name": sub, "email": f"{sub}@x.test"})
    return {"Authorization": f"Bearer {token}"}


def test_practice_history_survives_refresh_and_logout_login_and_is_private(api):
    alice = login("alice")
    question = api.post("/api/preparation/question", json={"mode": "dsa"}, headers=alice).json()
    evaluated = api.post(
        "/api/preparation/evaluate",
        json={
            "mode": "dsa",
            "question": question["question"],
            "answer": "my answer",
            "session_id": question["session_id"],
            "question_id": question["question_id"],
        },
        headers=alice,
    )
    assert evaluated.status_code == 200

    # Page refresh (same token) and logout/login (new token, same account).
    for headers in (alice, login("alice")):
        sessions = api.get("/api/preparation/history", headers=headers).json()["sessions"]
        assert [s["session_id"] for s in sessions] == [question["session_id"]]
        detail = api.get(
            f"/api/preparation/history/questions/{question['question_id']}", headers=headers
        ).json()
        assert detail["answers"][0]["evaluations"][0]["score"] == 70

    # Another account sees nothing and cannot reach Alice's records.
    bob = login("bob")
    assert api.get("/api/preparation/history", headers=bob).json()["sessions"] == []
    for path in (
        f"/api/preparation/history/{question['session_id']}",
        f"/api/preparation/history/questions/{question['question_id']}",
    ):
        assert api.get(path, headers=bob).status_code == 404
    assert (
        api.post(
            f"/api/preparation/history/questions/{question['question_id']}/answers",
            json={"answer": "x"},
            headers=bob,
        ).status_code
        == 404
    )
    # Bob cannot attach a question to Alice's session either.
    assert (
        api.post(
            "/api/preparation/question",
            json={"mode": "dsa", "session_id": question["session_id"]},
            headers=bob,
        ).status_code
        == 404
    )


def test_interview_journey_history_isolation_and_server_restart(api):
    alice, bob = login("alice"), login("bob")
    started = api.post("/api/interview/start", json={"resume_id": RESUME_ID}, headers=alice)
    assert started.status_code == 200
    session_id = started.json()["session_id"]
    first_question_id = started.json()["question_id"]

    # Bob cannot drive or read Alice's live session.
    assert api.post(f"/api/interview/{session_id}/answer", json={"answer": "x"}, headers=bob).status_code == 404
    assert api.post(f"/api/interview/{session_id}/next", headers=bob).status_code == 404

    assert api.post(f"/api/interview/{session_id}/answer", json={"answer": "mine"}, headers=alice).status_code == 200
    assert api.post(f"/api/interview/{session_id}/evaluate", headers=alice).json()["score"] == 80
    assert api.post(f"/api/interview/{session_id}/next", headers=alice).json()["question"] == "I2?"

    # The session shows up in history immediately, with both questions.
    listed = api.get("/api/interview/history", headers=login("alice")).json()["sessions"]
    assert [s["question_count"] for s in listed] == [2]
    assert api.get("/api/interview/history", headers=bob).json()["sessions"] == []
    assert api.get(f"/api/interview/history/{session_id}", headers=bob).status_code == 404

    # A backend restart drops live sessions (they are held in memory) ...
    interview_service._sessions.clear()
    gone = api.post(f"/api/interview/{session_id}/answer", json={"answer": "x"}, headers=alice)
    assert gone.status_code == 404
    assert "session not found" in gone.json()["detail"].lower()

    # ... but saved history is intact and can still be answered and re-evaluated.
    detail = api.get(f"/api/interview/history/questions/{first_question_id}", headers=alice).json()
    assert detail["answers"][0]["answer"] == "mine"
    again = api.post(
        f"/api/interview/history/questions/{first_question_id}/answers",
        json={"answer": "second try"},
        headers=alice,
    ).json()
    reevaluated = api.post(
        f"/api/interview/history/answers/{again['answer_id']}/evaluate", headers=alice
    )
    assert reevaluated.status_code == 200
    assert (
        api.post(
            f"/api/interview/history/answers/{again['answer_id']}/evaluate", headers=bob
        ).status_code
        == 404
    )


def test_missing_resume_after_restart_is_a_clear_404(api):
    from app.services.resume import ResumeIndexNotFound

    with patch("app.api.interview.search_resume", side_effect=ResumeIndexNotFound("x")), patch(
        "app.api.chat.search_resume", side_effect=ResumeIndexNotFound("x")
    ):
        alice = login("alice")
        start = api.post("/api/interview/start", json={"resume_id": RESUME_ID}, headers=alice)
        chat = api.post("/api/chat", json={"resume_id": RESUME_ID, "question": "Hi"}, headers=alice)
    for response in (start, chat):
        assert response.status_code == 404
        assert response.json()["detail"] == "Resume not found."


def test_expired_or_tampered_session_is_rejected(api):
    headers = login("alice")
    tampered = {"Authorization": headers["Authorization"][:-3] + "abc"}
    assert api.get("/api/preparation/history", headers=tampered).status_code == 401
    assert api.get("/api/auth/me", headers=headers).json()["user"]["email"] == "alice@x.test"
    assert "sub" not in api.get("/api/auth/me", headers=headers).json()["user"]


@pytest.mark.parametrize("mode", ["dsa", "sql", "hr"])
def test_every_practice_mode_generates_evaluates_reanswers_and_persists(api, mode):
    alice = login("alice")
    generated = api.post(
        "/api/preparation/question", json={"mode": mode, "topic": "joins"}, headers=alice
    )
    assert generated.status_code == 200 and generated.json()["mode"] == mode
    question = generated.json()
    evaluated = api.post(
        "/api/preparation/evaluate",
        json={
            "mode": mode,
            "question": question["question"],
            "answer": "first try",
            "session_id": question["session_id"],
            "question_id": question["question_id"],
        },
        headers=alice,
    )
    assert evaluated.status_code == 200 and evaluated.json()["score"] == 70

    # A second attempt from history is saved and re-evaluated.
    attempt = api.post(
        f"/api/preparation/history/questions/{question['question_id']}/answers",
        json={"answer": "second try"},
        headers=alice,
    ).json()
    again = api.post(
        f"/api/preparation/history/answers/{attempt['answer_id']}/evaluate", headers=alice
    )
    assert again.status_code == 200 and again.json()["mode"] == mode

    fresh_login = login("alice")
    sessions = api.get("/api/preparation/history", headers=fresh_login).json()["sessions"]
    assert [(s["session_id"], s["mode"]) for s in sessions] == [(question["session_id"], mode)]
    detail = api.get(
        f"/api/preparation/history/questions/{question['question_id']}", headers=fresh_login
    ).json()
    assert [len(a["evaluations"]) for a in detail["answers"]] == [1, 1]
    assert api.get("/api/preparation/history", headers=login("bob")).json()["sessions"] == []

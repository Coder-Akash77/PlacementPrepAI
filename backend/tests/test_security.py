import time
from io import BytesIO
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.api import resume as resume_api
from app.api.auth import get_current_user_id
from app.config import settings
from app.main import app
from app.services import rate_limit
from app.services import resume as resume_service
from app.services.rate_limit import PerUserRateLimiter, RateLimitExceeded

RESUME_ID = "8f3a5f0e-6d3c-4f43-9a52-0d7f6a1c2b11"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def real_auth():
    """Remove the test auth override so the real token check runs."""
    app.dependency_overrides.pop(get_current_user_id, None)
    old = settings.SESSION_SECRET
    settings.SESSION_SECRET = "test-secret-that-is-long-enough-for-hs256"
    yield
    settings.SESSION_SECRET = old


def as_user(user_id):
    app.dependency_overrides[get_current_user_id] = lambda: user_id


def pdf_with_pages(count: int) -> bytes:
    writer = PdfWriter()
    for _ in range(count):
        writer.add_blank_page(width=200, height=200)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------- authentication


def test_upload_and_chat_require_a_session(client, real_auth):
    upload = client.post(
        "/api/resume/upload",
        files={"file": ("resume.pdf", b"%PDF-1.4", "application/pdf")},
    )
    chat = client.post("/api/chat", json={"resume_id": RESUME_ID, "question": "Hi"})
    assert upload.status_code == 401
    assert chat.status_code == 401

    bad = {"Authorization": "Bearer not-a-real-token"}
    assert client.post("/api/chat", json={"resume_id": RESUME_ID, "question": "Hi"}, headers=bad).status_code == 401


def test_search_endpoint_is_removed(client):
    response = client.get("/api/resume/search", params={"resume_id": RESUME_ID, "query": "x"})
    assert response.status_code == 404


def test_every_gemini_backed_route_requires_a_session(client, real_auth):
    q = "00000000-0000-4000-8000-000000000000"
    calls = [
        ("post", "/api/resume/upload", {"files": {"file": ("r.pdf", b"%PDF-1.4", "application/pdf")}}),
        ("post", "/api/chat", {"json": {"resume_id": RESUME_ID, "question": "Hi"}}),
        ("post", "/api/preparation/question", {"json": {"mode": "dsa"}}),
        ("post", "/api/preparation/evaluate", {"json": {"mode": "dsa", "question": "q", "answer": "a"}}),
        ("post", f"/api/preparation/history/answers/{q}/evaluate", {}),
        ("post", "/api/interview/start", {"json": {"resume_id": RESUME_ID}}),
        ("post", f"/api/interview/{q}/next", {}),
        ("post", f"/api/interview/{q}/evaluate", {}),
        ("post", f"/api/interview/history/answers/{q}/evaluate", {}),
    ]
    for method, path, kwargs in calls:
        assert getattr(client, method)(path, **kwargs).status_code == 401, path


# ---------------------------------------------------------------- per-user limits


def test_user_limiter_enforces_minute_window_per_user():
    limiter = PerUserRateLimiter(requests_per_minute=2, requests_per_day=100)
    limiter.check("a")
    limiter.check("a")
    with pytest.raises(RateLimitExceeded) as exc:
        limiter.check("a")
    assert 1 <= exc.value.retry_after <= 60
    limiter.check("b")  # other users are unaffected


def test_user_limiter_enforces_daily_cap_and_recovers():
    limiter = PerUserRateLimiter(requests_per_minute=100, requests_per_day=3)
    start = time.monotonic()
    with patch("app.services.rate_limit.time.monotonic", return_value=start):
        for _ in range(3):
            limiter.check("a")
        with pytest.raises(RateLimitExceeded) as exc:
            limiter.check("a")
    assert exc.value.retry_after > 60
    with patch("app.services.rate_limit.time.monotonic", return_value=start + 86401):
        limiter.check("a")


def test_user_limiter_minute_window_expires():
    limiter = PerUserRateLimiter(requests_per_minute=1, requests_per_day=100)
    start = time.monotonic()
    with patch("app.services.rate_limit.time.monotonic", return_value=start):
        limiter.check("a")
        with pytest.raises(RateLimitExceeded):
            limiter.check("a")
    with patch("app.services.rate_limit.time.monotonic", return_value=start + 61):
        limiter.check("a")


def test_user_limiter_evicts_idle_users():
    limiter = PerUserRateLimiter(requests_per_minute=5, requests_per_day=5, max_tracked_users=2)
    start = time.monotonic()
    with patch("app.services.rate_limit.time.monotonic", return_value=start):
        limiter.check("a")
        limiter.check("b")
    with patch("app.services.rate_limit.time.monotonic", return_value=start + 90000):
        limiter.check("c")
    assert set(limiter._events) == {"c"}


def test_chat_returns_429_with_retry_after_when_user_quota_exceeded(client):
    small = PerUserRateLimiter(requests_per_minute=2, requests_per_day=100)
    body = {"resume_id": RESUME_ID, "question": "Hi"}
    with patch.object(rate_limit, "user_limiter", small), patch(
        "app.api.chat.search_resume", return_value=[]
    ), patch("app.api.chat.answer_resume_question", return_value="ok"):
        as_user("alice")
        assert client.post("/api/chat", json=body).status_code == 200
        assert client.post("/api/chat", json=body).status_code == 200
        limited = client.post("/api/chat", json=body)
        assert limited.status_code == 429
        assert int(limited.headers["Retry-After"]) >= 1
        # A different user still has quota.
        as_user("bob")
        assert client.post("/api/chat", json=body).status_code == 200


def test_user_quota_applies_to_practice_generation_but_not_history_reads(client):
    small = PerUserRateLimiter(requests_per_minute=1, requests_per_day=100)
    with patch.object(rate_limit, "user_limiter", small), patch(
        "app.api.preparation.generate_preparation_question", return_value="Q?"
    ):
        assert client.post("/api/preparation/question", json={"mode": "dsa"}).status_code == 200
        assert client.post("/api/preparation/question", json={"mode": "dsa"}).status_code == 429
        assert client.get("/api/preparation/history").status_code == 200


def test_global_limiters_are_still_in_place():
    assert rate_limit.generation_limiter.requests_per_minute == 15
    assert rate_limit.embedding_limiter.requests_per_day == 1000


# ---------------------------------------------------------------- upload limits


def test_pdf_over_page_limit_is_rejected_but_limit_itself_is_allowed():
    with pytest.raises(resume_service.PDFProcessingError, match="too many pages"):
        resume_service.extract_pdf_text(pdf_with_pages(resume_service.MAX_PDF_PAGES + 1))
    # Exactly 20 pages passes the page check (blank pages then fail on "no text").
    with pytest.raises(resume_service.PDFProcessingError, match="no extractable text"):
        resume_service.extract_pdf_text(pdf_with_pages(resume_service.MAX_PDF_PAGES))


def test_upload_over_page_limit_returns_400(client):
    response = client.post(
        "/api/resume/upload",
        files={"file": ("r.pdf", pdf_with_pages(21), "application/pdf")},
    )
    assert response.status_code == 400
    assert "too many pages" in response.json()["detail"]


def test_upload_just_over_size_limit_returns_413_without_processing(client):
    oversized = b"%PDF-1.4 " + b"0" * resume_service.MAX_PDF_BYTES
    with patch.object(resume_api, "extract_pdf_text") as extract:
        response = client.post(
            "/api/resume/upload",
            files={"file": ("r.pdf", oversized, "application/pdf")},
        )
    assert response.status_code == 413
    assert "5 MB" in response.json()["detail"]
    extract.assert_not_called()


def test_upload_far_over_size_limit_is_rejected_from_content_length(client):
    huge = b"0" * (resume_service.MAX_PDF_BYTES + 200_000)
    with patch.object(resume_api, "extract_pdf_text") as extract:
        response = client.post(
            "/api/resume/upload",
            files={"file": ("r.pdf", huge, "application/pdf")},
        )
    assert response.status_code == 413
    extract.assert_not_called()


def test_upload_at_exact_size_limit_is_not_rejected_for_size(client):
    at_limit = b"%PDF-1.4 " + b"0" * (resume_service.MAX_PDF_BYTES - 9)
    assert len(at_limit) == resume_service.MAX_PDF_BYTES
    response = client.post(
        "/api/resume/upload",
        files={"file": ("r.pdf", at_limit, "application/pdf")},
    )
    assert response.status_code == 400  # fails PDF parsing, not the size check
    assert "too large" not in response.json()["detail"]

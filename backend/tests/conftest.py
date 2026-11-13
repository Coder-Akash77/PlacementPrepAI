import pytest

from app.api.auth import get_current_user_id
from app.config import settings
from app.main import app
from app.services.rate_limit import user_limiter

TEST_USER_ID = "test-user"


@pytest.fixture(autouse=True)
def signed_in_user_and_local_history():
    """Authenticate every request as TEST_USER_ID and never touch a real database."""
    previous_database_url = settings.DATABASE_URL
    settings.DATABASE_URL = ""
    user_limiter.reset()
    app.dependency_overrides[get_current_user_id] = lambda: TEST_USER_ID
    yield
    app.dependency_overrides.pop(get_current_user_id, None)
    settings.DATABASE_URL = previous_database_url

from fastapi import Depends

from app.api.auth import get_current_user_id
from app.services import rate_limit


def enforce_user_quota(user_id: str = Depends(get_current_user_id)) -> str:
    """Authenticate the caller and charge one AI request to their personal quota."""
    rate_limit.user_limiter.check(user_id)
    return user_id

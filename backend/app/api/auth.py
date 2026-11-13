from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.services.auth import (
    AuthConfigurationError,
    InvalidCredential,
    decode_session_token,
    issue_session_token,
    verify_google_credential,
)

router = APIRouter(prefix="/auth", tags=["auth"])
_bearer = HTTPBearer(auto_error=False)


class GoogleLoginRequest(BaseModel):
    credential: str = Field(min_length=1, max_length=8192)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Please sign in to continue.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return decode_session_token(credentials.credentials)
    except AuthConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except InvalidCredential as exc:
        raise HTTPException(
            status_code=401,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_current_user_id(user: dict = Depends(get_current_user)) -> str:
    return user["sub"]


@router.post("/google")
def login_with_google(request: GoogleLoginRequest):
    try:
        claims = verify_google_credential(request.credential)
        token, user = issue_session_token(claims)
    except AuthConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except InvalidCredential as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {"token": token, "user": {k: v for k, v in user.items() if k != "sub"}}


@router.get("/me")
def current_user(user: dict = Depends(get_current_user)):
    return {"user": {k: v for k, v in user.items() if k != "sub"}}

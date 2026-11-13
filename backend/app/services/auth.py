import time

import jwt
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from app.config import settings

_ALGORITHM = "HS256"


class AuthConfigurationError(RuntimeError):
    """Raised when Google sign-in or session signing is not configured."""


class InvalidCredential(ValueError):
    """Raised when a Google credential or session token is invalid."""


def verify_google_credential(credential: str) -> dict:
    if not settings.GOOGLE_CLIENT_ID:
        raise AuthConfigurationError("Google sign-in is not configured.")
    try:
        claims = id_token.verify_oauth2_token(
            credential, google_requests.Request(), settings.GOOGLE_CLIENT_ID
        )
    except ValueError as exc:
        raise InvalidCredential("Invalid Google credential.") from exc
    if not claims.get("sub"):
        raise InvalidCredential("Invalid Google credential.")
    return claims


def issue_session_token(claims: dict) -> tuple[str, dict]:
    if not settings.SESSION_SECRET:
        raise AuthConfigurationError("Session signing is not configured.")
    user = {
        "sub": claims["sub"],
        "name": claims.get("name") or "",
        "email": claims.get("email") or "",
        "picture": claims.get("picture") or "",
    }
    now = int(time.time())
    token = jwt.encode(
        {**user, "iat": now, "exp": now + settings.SESSION_TTL_SECONDS},
        settings.SESSION_SECRET,
        algorithm=_ALGORITHM,
    )
    return token, user


def decode_session_token(token: str) -> dict:
    if not settings.SESSION_SECRET:
        raise AuthConfigurationError("Session signing is not configured.")
    try:
        claims = jwt.decode(
            token,
            settings.SESSION_SECRET,
            algorithms=[_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidCredential("Invalid or expired session.") from exc
    return {key: claims.get(key, "") for key in ("sub", "name", "email", "picture")}

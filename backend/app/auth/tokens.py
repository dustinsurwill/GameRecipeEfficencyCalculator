"""
JWT access + refresh tokens.

Access token  — short-lived (60 min default), carries user_id + roles inline.
                Stateless: no DB lookup needed to authenticate a request.

Refresh token — long-lived (30 days default), stored in DB so it can be
                revoked (logout, suspicious activity, password change).
                Sent as HttpOnly cookie to prevent JS access.
"""
import uuid
from datetime import datetime, timedelta, timezone
from enum import StrEnum

import jwt
from fastapi import Cookie, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.db import get_session

settings = get_settings()


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


# ── Token creation ────────────────────────────────────────────────────────────

def _make_token(
    subject: str,
    token_type: TokenType,
    expires_delta: timedelta,
    extra_claims: dict | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,          # user UUID as string
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        "jti": str(uuid.uuid4()),  # unique token ID — useful for revocation
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(
    user_id: uuid.UUID,
    is_curator: bool = False,
    is_superuser: bool = False,
) -> str:
    """
    Short-lived stateless token. Carries site-level roles so endpoints
    don't need a DB lookup for every request.
    Scoped admin roles (mod/modpack admin) are NOT in the token — they are
    checked against the DB on write operations to stay current.
    """
    return _make_token(
        subject=str(user_id),
        token_type=TokenType.ACCESS,
        expires_delta=timedelta(minutes=settings.jwt_access_token_expire_minutes),
        extra_claims={"curator": is_curator, "superuser": is_superuser},
    )


def create_refresh_token(user_id: uuid.UUID) -> tuple[str, str]:
    """
    Returns (token_string, jti) — caller should persist jti in DB so
    the token can be revoked on logout.
    """
    jti = str(uuid.uuid4())
    token = _make_token(
        subject=str(user_id),
        token_type=TokenType.REFRESH,
        expires_delta=timedelta(days=settings.jwt_refresh_token_expire_days),
        extra_claims={"jti": jti},  # override the one _make_token would set
    )
    # re-decode to get the jti _make_token embedded
    decoded = jwt.decode(
        token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
    )
    return token, decoded["jti"]


# ── Token verification ────────────────────────────────────────────────────────

class TokenData:
    def __init__(self, payload: dict):
        self.user_id: uuid.UUID = uuid.UUID(payload["sub"])
        self.token_type: TokenType = TokenType(payload["type"])
        self.jti: str = payload["jti"]
        self.is_curator: bool = payload.get("curator", False)
        self.is_superuser: bool = payload.get("superuser", False)


def decode_token(token: str) -> TokenData:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    return TokenData(payload)


# ── FastAPI dependencies ──────────────────────────────────────────────────────

from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> uuid.UUID:
    """
    Dependency: extracts and validates the Bearer access token.
    Returns the user's UUID. Does NOT hit the database.
    Use this for endpoints where you only need to know who is calling.
    """
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    token_data = decode_token(credentials.credentials)
    if token_data.token_type != TokenType.ACCESS:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Expected access token")
    return token_data.user_id


async def get_current_token_data(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> TokenData:
    """Like get_current_user_id but also exposes role claims (curator, etc.)."""
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    token_data = decode_token(credentials.credentials)
    if token_data.token_type != TokenType.ACCESS:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Expected access token")
    return token_data


def require_curator(token: TokenData = Depends(get_current_token_data)) -> TokenData:
    """Dependency: gate an endpoint to curators only."""
    if not token.is_curator:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Curator role required")
    return token

"""FastAPI dependencies for the HTML site: scope, index, cookie session, CSRF."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

import jwt
from fastapi import Depends, HTTPException, Request, Response
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.refresh import get_refresh_token, revoke_refresh_token, store_refresh_token
from app.auth.tokens import TokenData, TokenType, create_refresh_token
from app.config import get_settings
from app.db import get_session
from app.discovery.index import DiscoveryIndex, get_index
from app.discovery.scope import Scope, ScopeNotFound, resolve_scope
from app.models.user import User

settings = get_settings()
SESSION_COOKIE = "rb_session"


@dataclass(slots=True)
class WebUser:
    id: uuid.UUID
    display_name: str
    avatar_url: str | None
    is_superuser: bool
    is_curator: bool

    @property
    def token(self) -> TokenData:
        """Equivalent of a decoded access token, so API handlers can be called directly."""
        return TokenData({
            "sub": str(self.id), "type": TokenType.ACCESS, "jti": "web",
            "curator": self.is_curator, "superuser": self.is_superuser,
        })


# ── Session cookie ────────────────────────────────────────────────────────────
# The cookie holds the same DB-backed refresh token the JSON API uses, so a session can be
# revoked server-side (logout, password change) and expires on the same schedule.

async def start_session(response: Response, session: AsyncSession, user: User, request: Request) -> None:
    token, jti = create_refresh_token(user.id)
    await store_refresh_token(
        session, jti=jti, user_id=user.id,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, secure=not settings.debug, samesite="lax",
        max_age=settings.jwt_refresh_token_expire_days * 86400, path="/",
    )


async def end_session(request: Request, response: Response, session: AsyncSession) -> None:
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        try:
            payload = jwt.decode(raw, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            await revoke_refresh_token(session, payload["jti"])
        except (jwt.InvalidTokenError, KeyError):
            pass
    response.delete_cookie(SESSION_COOKIE, path="/")


async def optional_user(request: Request, session: AsyncSession = Depends(get_session)) -> WebUser | None:
    cached = getattr(request.state, "user", False)
    if cached is not False:
        return cached
    user = None
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        try:
            payload = jwt.decode(raw, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            if payload.get("type") == TokenType.REFRESH:
                stored = await get_refresh_token(session, payload["jti"])
                fresh = stored and stored.expires_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
                row = await session.get(User, uuid.UUID(payload["sub"])) if fresh else None
                if row and row.is_active:
                    user = WebUser(row.id, row.display_name, row.avatar_url, row.is_superuser, row.is_curator)
        except (jwt.InvalidTokenError, KeyError, ValueError):
            pass
    request.state.user = user
    return user


class LoginRequired(Exception):
    def __init__(self, next_url: str):
        self.next_url = next_url


async def require_user(request: Request, user: WebUser | None = Depends(optional_user)) -> WebUser:
    if user is None:
        raise LoginRequired(request.url.path + (f"?{request.url.query}" if request.url.query else ""))
    return user


async def require_staff(user: WebUser = Depends(require_user)) -> WebUser:
    """Any signed-in user may open /admin; per-scope permission is enforced by each action."""
    return user


# ── CSRF ──────────────────────────────────────────────────────────────────────
# Cookies are SameSite=Lax (not sent on cross-site POSTs); as a second layer, reject any
# state-changing request whose Origin/Referer names a different host.

async def same_origin(request: Request) -> None:
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return
    if urlsplit(source).netloc != request.headers.get("host", ""):
        raise HTTPException(403, "Cross-site request blocked")


# ── Scope ─────────────────────────────────────────────────────────────────────

async def web_scope(
    request: Request, game: str, pack: str, version: str, session: AsyncSession = Depends(get_session),
) -> Scope:
    try:
        scope = await resolve_scope(session, game, pack, version)
    except ScopeNotFound as e:
        raise HTTPException(404, str(e))
    request.state.scope = scope
    return scope


async def web_index(scope: Scope = Depends(web_scope), session: AsyncSession = Depends(get_session)) -> DiscoveryIndex:
    return await get_index(session, scope)

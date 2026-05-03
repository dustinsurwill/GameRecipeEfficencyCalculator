"""
Auth routes:

  POST /auth/register          — email + password signup
  POST /auth/login             — email + password login
  POST /auth/refresh           — rotate refresh token (cookie)
  POST /auth/logout            — revoke refresh token
  GET  /auth/oauth/{provider}  — start OAuth flow
  GET  /auth/oauth/{provider}/callback — OAuth callback
  POST /auth/oauth/{provider}/connect  — link OAuth to existing account
                                          (requires auth)
"""
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.oauth import (
    PROFILE_FETCHERS,
    PROVIDER_META,
    OAuthProviderProfile,
    get_enabled_providers,
)
from app.auth.password import hash_password, verify_and_update
from app.auth.refresh import (
    get_refresh_token,
    revoke_refresh_token,
    store_refresh_token,
)
from app.auth.tokens import (
    TokenData,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_token_data,
    get_current_user_id,
)
from app.config import get_settings
from app.db import get_session
from sqlmodel import SQLModel
from app.models.user import (
    OAuthAccount,
    User,
    UserCreate,
    UserRead,
    UserReadWithAccounts,
)

settings = get_settings()
router = APIRouter(prefix="/auth", tags=["auth"])

Session = Annotated[AsyncSession, Depends(get_session)]

REFRESH_COOKIE = "refresh_token"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        httponly=True,       # JS cannot read this
        secure=True,         # HTTPS only in production
        samesite="lax",
        max_age=settings.jwt_refresh_token_expire_days * 86400,
        path="/auth/refresh",  # cookie only sent to the refresh endpoint
    )


async def _issue_tokens(
    session: AsyncSession,
    response: Response,
    user: User,
    request: Request,
) -> dict:
    """Create access + refresh token pair, persist refresh in DB, set cookie."""
    access = create_access_token(
        user.id, is_curator=user.is_curator, is_superuser=user.is_superuser
    )
    refresh_str, jti = create_refresh_token(user.id)

    await store_refresh_token(
        session,
        jti=jti,
        user_id=user.id,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    _set_refresh_cookie(response, refresh_str)
    return {"access_token": access, "token_type": "bearer"}


async def _get_user_by_email(session: AsyncSession, email: str) -> User | None:
    # .scalars() ensures we get ORM objects, not Row tuples, across all
    # execution contexts (including TestClient's anyio bridge)
    result = await session.exec(select(User).where(User.email == email))
    return result.scalars().one_or_none()


async def _get_oauth_account(
    session: AsyncSession, provider: str, provider_user_id: str
) -> OAuthAccount | None:
    result = await session.exec(
        select(OAuthAccount).where(
            OAuthAccount.provider == provider,
            OAuthAccount.provider_user_id == provider_user_id,
        )
    )
    return result.scalars().one_or_none()


# ── Email + Password ──────────────────────────────────────────────────────────

@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(body: UserCreate, session: Session) -> User:
    existing = await _get_user_by_email(session, body.email)
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    user = User(
        display_name=body.display_name,
        email=body.email,
        hashed_password=hash_password(body.password),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


class LoginRequest(SQLModel):
    email: str
    password: str


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: Session,
) -> dict:
    user = await _get_user_by_email(session, body.email)
    if not user or not user.hashed_password:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account deactivated")

    # verify_and_update checks password AND returns an upgraded hash if needed
    is_valid, updated_hash = verify_and_update(body.password, user.hashed_password)
    if not is_valid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    # Opportunistically persist upgraded hash if Argon2 params changed
    if updated_hash:
        user.hashed_password = updated_hash
        session.add(user)
        await session.commit()

    return await _issue_tokens(session, response, user, request)


@router.post("/refresh")
async def refresh_token(
    request: Request,
    response: Response,
    session: Session,
) -> dict:
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No refresh token")

    token_data = decode_token(raw)
    if token_data.token_type != TokenType.REFRESH:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Expected refresh token")

    stored = await get_refresh_token(session, token_data.jti)
    if not stored:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token revoked")
    if stored.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        await revoke_refresh_token(session, token_data.jti)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")

    # Token rotation — revoke old, issue new
    await revoke_refresh_token(session, token_data.jti)

    result = await session.get(User, token_data.user_id)
    if not result or not result.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")

    return await _issue_tokens(session, response, result, request)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, session: Session) -> None:
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        try:
            token_data = decode_token(raw)
            await revoke_refresh_token(session, token_data.jti)
        except HTTPException:
            pass  # already expired/invalid — nothing to revoke
    response.delete_cookie(REFRESH_COOKIE, path="/auth/refresh")


# ── OAuth flows ───────────────────────────────────────────────────────────────

@router.get("/oauth/{provider}")
async def oauth_redirect(provider: str, request: Request) -> dict:
    """Step 1: return the URL the frontend should redirect the user to."""
    if provider not in PROVIDER_META:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown provider: {provider}")
    if provider not in get_enabled_providers():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"{provider} not configured")

    meta = PROVIDER_META[provider]
    client = meta["client_factory"]()
    redirect_uri = str(request.url_for("oauth_callback", provider=provider))

    # State is a random value stored in session to prevent CSRF
    # For a full implementation, store this in a short-lived server-side session
    # or a signed cookie. Simplified here for clarity.
    uri, state = client.create_authorization_url(
        meta["authorize_url"],
        redirect_uri=redirect_uri,
    )
    return {"authorization_url": uri, "state": state}


@router.get("/oauth/{provider}/callback", name="oauth_callback")
async def oauth_callback(
    provider: str,
    code: str,
    state: str,
    request: Request,
    response: Response,
    session: Session,
) -> dict:
    """Step 2: exchange code for tokens, find or create user, issue JWT."""
    if provider not in PROVIDER_META:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown provider: {provider}")

    meta = PROVIDER_META[provider]
    client = meta["client_factory"]()
    redirect_uri = str(request.url_for("oauth_callback", provider=provider))

    try:
        token = await client.fetch_token(
            meta["token_url"],
            code=code,
            redirect_uri=redirect_uri,
        )
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Token exchange failed: {e}")

    fetcher = PROFILE_FETCHERS[provider]
    profile = await fetcher(client, token)

    return await _oauth_find_or_create(session, response, request, profile)


@router.post("/oauth/{provider}/connect")
async def oauth_connect(
    provider: str,
    code: str,
    state: str,
    request: Request,
    response: Response,
    session: Session,
    current_user_id: uuid.UUID = Depends(get_current_user_id),
) -> UserReadWithAccounts:
    """
    Link a new OAuth account to an already-authenticated user.
    This is the ONLY way accounts get linked — no silent email matching.
    """
    if provider not in PROVIDER_META:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown provider: {provider}")

    meta = PROVIDER_META[provider]
    client = meta["client_factory"]()
    redirect_uri = str(request.url_for("oauth_callback", provider=provider))

    token = await client.fetch_token(meta["token_url"], code=code, redirect_uri=redirect_uri)
    fetcher = PROFILE_FETCHERS[provider]
    profile = await fetcher(client, token)

    # Check not already linked to ANOTHER user
    existing = await _get_oauth_account(session, provider, profile.provider_user_id)
    if existing and existing.user_id != current_user_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This OAuth account is already linked to a different user",
        )

    user = await session.get(User, current_user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    if not existing:
        oauth = _make_oauth_account(profile, current_user_id)
        session.add(oauth)
    else:
        # Update tokens on the existing link
        existing.access_token = profile.access_token
        existing.refresh_token = profile.refresh_token
        existing.last_used_at = datetime.now(timezone.utc)
        session.add(existing)

    await session.commit()
    await session.refresh(user)
    return UserReadWithAccounts.model_validate(user)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _make_oauth_account(profile: OAuthProviderProfile, user_id: uuid.UUID) -> OAuthAccount:
    return OAuthAccount(
        user_id=user_id,
        provider=profile.provider,
        provider_user_id=profile.provider_user_id,
        provider_email=profile.provider_email,
        access_token=profile.access_token,
        refresh_token=profile.refresh_token,
        token_expires_at=(
            datetime.fromtimestamp(profile.token_expires_at, tz=timezone.utc)
            if profile.token_expires_at else None
        ),
    )


async def _oauth_find_or_create(
    session: AsyncSession,
    response: Response,
    request: Request,
    profile: OAuthProviderProfile,
) -> dict:
    """
    Find existing OAuthAccount → get its User → issue tokens.
    If no OAuthAccount exists → create new User + OAuthAccount.

    Deliberately does NOT match on email to avoid account hijacking.
    """
    existing_oauth = await _get_oauth_account(
        session, profile.provider, profile.provider_user_id
    )

    if existing_oauth:
        # Update tokens and last_used
        existing_oauth.access_token = profile.access_token
        existing_oauth.refresh_token = profile.refresh_token
        existing_oauth.last_used_at = datetime.now(timezone.utc)
        session.add(existing_oauth)

        user = await session.get(User, existing_oauth.user_id)
        if not user or not user.is_active:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Account deactivated")
    else:
        # New OAuth login — create a fresh user
        # Use provider display name as default, user can change in profile
        user = User(
            display_name=profile.display_name or profile.provider_email or "New User",
            avatar_url=profile.avatar_url,
            # Do NOT set email from OAuth — user must explicitly set it in profile
        )
        session.add(user)
        await session.flush()  # get user.id before creating OAuthAccount

        oauth = _make_oauth_account(profile, user.id)
        session.add(oauth)

    await session.commit()
    await session.refresh(user)
    # Explicitly load oauth_accounts for the response
    await session.exec(
        select(User).where(User.id == user.id).options(selectinload(User.oauth_accounts))
    )
    return await _issue_tokens(session, response, user, request)

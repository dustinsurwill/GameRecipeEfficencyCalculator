"""Sign-in, registration and profile pages (cookie session; see app/web/deps.py)."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.v1.routes.auth import set_state_cookie, start_oauth
from app.auth.oauth import PROVIDER_META, get_enabled_providers
from app.auth.password import hash_password, verify_and_update
from app.db import get_session
from app.models.user import OAuthAccount, User, UserCreate, UserUpdate
from app.web.deps import WebUser, end_session, optional_user, require_user, same_origin, start_session
from app.web.templating import render

router = APIRouter(dependencies=[Depends(optional_user)])
Session = Annotated[AsyncSession, Depends(get_session)]


def _safe_next(value: str | None) -> str:
    """Only allow same-site relative redirects (blocks open-redirect via ?next=)."""
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/"


def _providers() -> list[dict]:
    return [{"id": p, "name": p.title()} for p in get_enabled_providers()]


# ── Login / logout ────────────────────────────────────────────────────────────

@router.get("/login")
async def login_form(request: Request, next: str = "", user: WebUser | None = Depends(optional_user)):
    if user:
        return RedirectResponse(_safe_next(next), status_code=303)
    return render(request, "login.html", next=_safe_next(next) if next else "", providers=_providers())


@router.post("/login", dependencies=[Depends(same_origin)])
async def login(
    request: Request, session: Session,
    email: str = Form(...), password: str = Form(...), next: str = Form(""),
):
    row = (await session.exec(select(User).where(User.email == email.strip()))).scalars().one_or_none()
    ok, upgraded = (False, None)
    if row and row.hashed_password:
        ok, upgraded = verify_and_update(password, row.hashed_password)
    if not ok:
        # one message for unknown email and wrong password, so accounts can't be enumerated
        return render(request, "login.html", status_code=401, next=next, email=email,
                      error="That email and password don’t match an account.", providers=_providers())
    if not row.is_active:
        return render(request, "login.html", status_code=403, next=next, email=email,
                      error="This account has been deactivated.", providers=_providers())
    if upgraded:
        row.hashed_password = upgraded
        session.add(row)
        await session.commit()
    resp = RedirectResponse(_safe_next(next), status_code=303)
    await start_session(resp, session, row, request)
    return resp


@router.post("/logout", dependencies=[Depends(same_origin)])
async def logout(request: Request, session: Session):
    resp = RedirectResponse("/", status_code=303)
    await end_session(request, resp, session)
    return resp


# ── Registration ──────────────────────────────────────────────────────────────

@router.get("/register")
async def register_form(request: Request, user: WebUser | None = Depends(optional_user)):
    if user:
        return RedirectResponse("/", status_code=303)
    return render(request, "register.html", providers=_providers())


@router.post("/register", dependencies=[Depends(same_origin)])
async def register(
    request: Request, session: Session,
    display_name: str = Form(...), email: str = Form(...), password: str = Form(...),
):
    form = {"display_name": display_name, "email": email}

    def fail(msg: str, code: int = 422):
        return render(request, "register.html", status_code=code, error=msg, form=form, providers=_providers())

    try:
        body = UserCreate(display_name=display_name.strip(), email=email.strip(), password=password)
    except ValidationError as e:
        first = e.errors()[0]
        field = str(first["loc"][-1]).replace("_", " ")
        return fail(f"Check your {field}: {first['msg'].lower()}.")
    if "@" not in body.email:
        return fail("Enter a valid email address.")
    if (await session.exec(select(User).where(User.email == body.email))).scalars().first():
        return fail("That email is already registered. Try logging in instead.", 409)

    user = User(display_name=body.display_name, email=body.email, hashed_password=hash_password(body.password))
    session.add(user)
    await session.commit()
    await session.refresh(user)
    resp = RedirectResponse("/", status_code=303)
    await start_session(resp, session, user, request)
    return resp


# ── OAuth ─────────────────────────────────────────────────────────────────────

@router.get("/auth/oauth/{provider}")
async def oauth_start(provider: str, request: Request):
    if provider not in PROVIDER_META:
        raise HTTPException(404, f"Unknown provider: {provider}")
    url, state = start_oauth(provider, request)
    resp = RedirectResponse(url, status_code=303)
    set_state_cookie(resp, state)
    return resp


# ── Profile ───────────────────────────────────────────────────────────────────

async def _load_profile(session: AsyncSession, user_id: uuid.UUID) -> User:
    row = (await session.exec(
        select(User).where(User.id == user_id).options(selectinload(User.oauth_accounts))
    )).scalars().one_or_none()
    if not row:
        raise HTTPException(404, "User not found")
    return row


@router.get("/profile")
async def profile(request: Request, session: Session, user: WebUser = Depends(require_user), saved: str = ""):
    return render(request, "profile.html", profile=await _load_profile(session, user.id), saved=bool(saved))


@router.post("/profile", dependencies=[Depends(same_origin)])
async def profile_save(
    request: Request, session: Session, user: WebUser = Depends(require_user),
    display_name: str = Form(""), email: str = Form(""), bio: str = Form(""), avatar_url: str = Form(""),
):
    row = await _load_profile(session, user.id)

    def fail(msg: str, code: int = 422):
        return render(request, "profile.html", status_code=code, profile=row, error=msg,
                      form={"display_name": display_name, "email": email, "bio": bio, "avatar_url": avatar_url})

    avatar = avatar_url.strip() or None
    if avatar and not avatar.startswith(("https://", "http://")):
        return fail("The avatar must be a web address starting with https://.")
    try:
        body = UserUpdate(display_name=display_name.strip() or None, email=email.strip() or None,
                          bio=bio.strip() or None, avatar_url=avatar)
    except ValidationError as e:
        field = str(e.errors()[0]["loc"][-1]).replace("_", " ")
        return fail(f"Check your {field}: {e.errors()[0]['msg'].lower()}.")

    data = body.model_dump(exclude_unset=True)
    if data.get("email") and data["email"] != row.email:
        taken = (await session.exec(select(User).where(User.email == data["email"], User.id != row.id))).scalars().first()
        if taken:
            return fail("That email is already in use.", 409)
        data["email_verified"] = False
    for k, v in data.items():
        setattr(row, k, v)
    session.add(row)
    await session.commit()
    return RedirectResponse("/profile?saved=1", status_code=303)


@router.post("/profile/oauth/{account_id}/unlink", dependencies=[Depends(same_origin)])
async def unlink(request: Request, account_id: uuid.UUID, session: Session, user: WebUser = Depends(require_user)):
    row = await _load_profile(session, user.id)
    acct = await session.get(OAuthAccount, account_id)
    if not acct or acct.user_id != user.id:
        raise HTTPException(404, "Account not found")
    if not row.hashed_password and len(row.oauth_accounts) <= 1:
        return render(request, "profile.html", status_code=409, profile=row,
                      error="You can’t unlink your only way to sign in. Add a password or another account first.")
    await session.delete(acct)
    await session.commit()
    return RedirectResponse("/profile", status_code=303)

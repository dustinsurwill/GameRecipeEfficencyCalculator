"""Admin pages: recipe review queue, modpack/mod version publishing, member management.

Each action calls the same handler the JSON API uses (app/api/v1/routes/admin.py, recipes.py)
with an identity derived from the session cookie, so permission rules live in one place.
"""
from __future__ import annotations

import uuid
from typing import Annotated, Awaitable, Callable
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.v1.routes import admin as admin_api
from app.api.v1.routes import recipes as recipes_api
from app.db import get_session
from app.models.admin import AdminGroupCreate, AdminRole, AdminScopeType, get_user_mod_scopes
from app.models.status import RecipeReviewCreate
from app.models.versioning import Mod, ModPackIdentity, ModPackVersion, ModVersion
from app.web.deps import WebUser, optional_user, require_staff, same_origin
from app.web.templating import render

router = APIRouter(prefix="/admin", dependencies=[Depends(optional_user)])
Session = Annotated[AsyncSession, Depends(get_session)]
PAGE = 20


def _back(url: str, *, msg: str = "", error: str = "") -> RedirectResponse:
    sep = "&" if "?" in url else "?"
    extra = f"{sep}msg={quote(msg)}" if msg else f"{sep}error={quote(error)}" if error else ""
    return RedirectResponse(url + extra, status_code=303)


async def _act(url: str, ok: str, fn: Callable[[], Awaitable]) -> RedirectResponse:
    """Run an API handler; turn its HTTPException into a message on the redirect target."""
    try:
        await fn()
    except HTTPException as e:
        return _back(url, error=str(e.detail))
    return _back(url, msg=ok)


# ── Home ──────────────────────────────────────────────────────────────────────

@router.get("")
async def admin_home(request: Request, session: Session, user: WebUser = Depends(require_staff)):
    if user.is_superuser:
        packs = (await session.exec(select(ModPackIdentity).order_by(ModPackIdentity.name))).scalars().all()
        mods = (await session.exec(select(Mod).order_by(Mod.name))).scalars().all()
    else:
        pack_ids = await get_user_mod_scopes(session, user.id, AdminScopeType.MODPACK)
        mod_ids = await get_user_mod_scopes(session, user.id, AdminScopeType.MOD)
        packs = (await session.exec(select(ModPackIdentity).where(ModPackIdentity.id.in_(pack_ids)))).scalars().all() if pack_ids else []
        mods = (await session.exec(select(Mod).where(Mod.id.in_(mod_ids)))).scalars().all() if mod_ids else []
    can_review = user.is_superuser or user.is_curator or bool(packs or mods)
    return render(request, "admin/home.html", packs=packs, mods=mods, can_review=can_review,
                  msg=request.query_params.get("msg"), error=request.query_params.get("error"))


# ── Recipe review queue ───────────────────────────────────────────────────────

@router.get("/recipes")
async def review_queue(request: Request, session: Session, user: WebUser = Depends(require_staff), offset: int = 0):
    offset = max(0, offset)
    try:
        rows = await recipes_api.list_pending_recipes(
            session=session, token=user.token, modpack_id=None, limit=PAGE + 1, offset=offset
        )
    except HTTPException as e:
        raise HTTPException(e.status_code, "You don’t have access to the review queue.")
    return render(request, "admin/recipes.html", recipes=rows[:PAGE], offset=offset,
                  has_more=len(rows) > PAGE, msg=request.query_params.get("msg"), error=request.query_params.get("error"))


@router.post("/recipes/{recipe_id}/review", dependencies=[Depends(same_origin)])
async def review(
    recipe_id: uuid.UUID, session: Session, user: WebUser = Depends(require_staff),
    decision: str = Form(...), reason: str = Form(""), offset: int = Form(0),
):
    body = RecipeReviewCreate(decision=decision, reason=reason.strip() or None)
    return await _act(
        f"/admin/recipes?offset={offset}", f"Recipe {decision}.",
        lambda: recipes_api.review_recipe(recipe_id, body, session, user.token),
    )


# ── Modpacks ──────────────────────────────────────────────────────────────────

@router.get("/modpacks/{identity_id}")
async def modpack_admin(request: Request, identity_id: uuid.UUID, session: Session, user: WebUser = Depends(require_staff)):
    identity = await session.get(ModPackIdentity, identity_id)
    if not identity:
        raise HTTPException(404, "Modpack not found")
    versions = (await session.exec(
        select(ModPackVersion).where(ModPackVersion.identity_id == identity_id).order_by(ModPackVersion.created_at.desc())
    )).scalars().all()
    try:
        members = await admin_api.list_modpack_members(identity_id, session, user.token)
    except HTTPException as e:
        raise HTTPException(e.status_code, "You don’t manage this modpack.")
    return render(request, "admin/scope.html", kind="modpack", title=identity.name, subtitle=identity.slug,
                  versions=versions, members=members, base=f"/admin/modpacks/{identity_id}",
                  msg=request.query_params.get("msg"), error=request.query_params.get("error"))


@router.post("/modpacks/{identity_id}/versions/{version_id}/{action}", dependencies=[Depends(same_origin)])
async def modpack_version_action(
    identity_id: uuid.UUID, version_id: uuid.UUID, action: str, session: Session, user: WebUser = Depends(require_staff),
):
    fn = {"publish": admin_api.publish_modpack, "deprecate": admin_api.deprecate_modpack}.get(action)
    if not fn:
        raise HTTPException(404, "Unknown action")
    return await _act(f"/admin/modpacks/{identity_id}", f"Version {action}d.", lambda: fn(version_id, session, user.token))


@router.post("/modpacks/{identity_id}/members", dependencies=[Depends(same_origin)])
async def modpack_add_member(
    identity_id: uuid.UUID, session: Session, user: WebUser = Depends(require_staff),
    user_id: str = Form(...), role: str = Form(AdminRole.MAINTAINER),
):
    url = f"/admin/modpacks/{identity_id}"
    try:
        body = AdminGroupCreate(user_id=uuid.UUID(user_id.strip()), scope_type=AdminScopeType.MODPACK, scope_id=identity_id, role=role)
    except ValueError:
        return _back(url, error="Enter the member’s user ID (a UUID).")
    return await _act(url, "Member added.", lambda: admin_api.grant_modpack_member(identity_id, body, session, user.token))


@router.post("/modpacks/{identity_id}/members/{member_id}/revoke", dependencies=[Depends(same_origin)])
async def modpack_revoke_member(identity_id: uuid.UUID, member_id: uuid.UUID, session: Session, user: WebUser = Depends(require_staff)):
    return await _act(f"/admin/modpacks/{identity_id}", "Member removed.",
                      lambda: admin_api.revoke_modpack_member(identity_id, member_id, session, user.token))


# ── Mods ──────────────────────────────────────────────────────────────────────

@router.get("/mods/{mod_id}")
async def mod_admin(request: Request, mod_id: uuid.UUID, session: Session, user: WebUser = Depends(require_staff)):
    mod = await session.get(Mod, mod_id)
    if not mod:
        raise HTTPException(404, "Mod not found")
    versions = (await session.exec(
        select(ModVersion).where(ModVersion.mod_id == mod_id).order_by(ModVersion.created_at.desc())
    )).scalars().all()
    try:
        members = await admin_api.list_mod_members(mod_id, session, user.token)
    except HTTPException as e:
        raise HTTPException(e.status_code, "You don’t manage this mod.")
    return render(request, "admin/scope.html", kind="mod", title=mod.name, subtitle=mod.namespace,
                  versions=versions, members=members, base=f"/admin/mods/{mod_id}",
                  msg=request.query_params.get("msg"), error=request.query_params.get("error"))


@router.post("/mods/{mod_id}/versions/{version_id}/{action}", dependencies=[Depends(same_origin)])
async def mod_version_action(
    mod_id: uuid.UUID, version_id: uuid.UUID, action: str, session: Session, user: WebUser = Depends(require_staff),
):
    fn = {"publish": admin_api.publish_mod_version, "deprecate": admin_api.deprecate_mod_version}.get(action)
    if not fn:
        raise HTTPException(404, "Unknown action")
    return await _act(f"/admin/mods/{mod_id}", f"Version {action}d.", lambda: fn(version_id, session, user.token))


@router.post("/mods/{mod_id}/members", dependencies=[Depends(same_origin)])
async def mod_add_member(
    mod_id: uuid.UUID, session: Session, user: WebUser = Depends(require_staff),
    user_id: str = Form(...), role: str = Form(AdminRole.MAINTAINER),
):
    url = f"/admin/mods/{mod_id}"
    try:
        body = AdminGroupCreate(user_id=uuid.UUID(user_id.strip()), scope_type=AdminScopeType.MOD, scope_id=mod_id, role=role)
    except ValueError:
        return _back(url, error="Enter the member’s user ID (a UUID).")
    return await _act(url, "Member added.", lambda: admin_api.grant_mod_member(mod_id, body, session, user.token))


@router.post("/mods/{mod_id}/members/{member_id}/revoke", dependencies=[Depends(same_origin)])
async def mod_revoke_member(mod_id: uuid.UUID, member_id: uuid.UUID, session: Session, user: WebUser = Depends(require_staff)):
    return await _act(f"/admin/mods/{mod_id}", "Member removed.",
                      lambda: admin_api.revoke_mod_member(mod_id, member_id, session, user.token))

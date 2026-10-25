"""
Admin management routes:

  GET    /admin/mods/{mod_id}/members          — list mod admins
  POST   /admin/mods/{mod_id}/members          — grant role to user
  DELETE /admin/mods/{mod_id}/members/{user_id} — revoke role

  GET    /admin/modpacks/{modpack_id}/members
  POST   /admin/modpacks/{modpack_id}/members
  DELETE /admin/modpacks/{modpack_id}/members/{user_id}

  POST   /admin/mod-versions/{id}/publish      — draft → published
  POST   /admin/mod-versions/{id}/deprecate    — published → deprecated
  POST   /admin/modpacks/{id}/publish
  POST   /admin/modpacks/{id}/deprecate

All routes require at least ADMIN role on the relevant scope, or superuser.
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.tokens import TokenData, get_current_token_data
from app.db import get_session
from app.models.admin import (
    AdminGroup,
    AdminGroupCreate,
    AdminGroupRead,
    AdminRole,
    AdminScopeType,
    is_mod_admin,
    is_modpack_admin,
)
from app.models.status import PublicationStatus
from app.models.versioning import ModPackVersion, ModVersion

router = APIRouter(prefix="/admin", tags=["admin"])
Session = Annotated[AsyncSession, Depends(get_session)]
Token = Annotated[TokenData, Depends(get_current_token_data)]


# ── Shared helpers ────────────────────────────────────────────────────────────

async def _require_scope_admin(
    session: AsyncSession,
    token: TokenData,
    scope_type: AdminScopeType,
    scope_id: uuid.UUID,
    min_role: AdminRole = AdminRole.ADMIN,  # member management requires ADMIN
) -> None:
    if token.is_superuser:
        return
    if scope_type == AdminScopeType.MOD:
        ok = await is_mod_admin(session, token.user_id, scope_id, min_role)
    else:
        ok = await is_modpack_admin(session, token.user_id, scope_id, min_role)
    if not ok:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")


async def _list_members(
    session: AsyncSession,
    scope_type: AdminScopeType,
    scope_id: uuid.UUID,
) -> list[AdminGroup]:
    result = await session.exec(
        select(AdminGroup).where(
            and_(
                AdminGroup.scope_type == scope_type,
                AdminGroup.scope_id == scope_id,
            )
        )
    )
    return list(result.scalars().all())


async def _grant_member(
    session: AsyncSession,
    token: TokenData,
    scope_type: AdminScopeType,
    scope_id: uuid.UUID,
    body: AdminGroupCreate,
) -> AdminGroup:
    # Check the target user isn't already a member
    existing = await session.exec(
        select(AdminGroup).where(
            and_(
                AdminGroup.user_id == body.user_id,
                AdminGroup.scope_type == scope_type,
                AdminGroup.scope_id == scope_id,
            )
        )
    )
    if existing.scalars().one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "User already has a role in this scope")

    group = AdminGroup(
        user_id=body.user_id,
        scope_type=scope_type,
        scope_id=scope_id,
        role=body.role,
        granted_by_id=token.user_id,
    )
    session.add(group)
    await session.commit()
    await session.refresh(group)
    return group


async def _revoke_member(
    session: AsyncSession,
    token: TokenData,
    scope_type: AdminScopeType,
    scope_id: uuid.UUID,
    target_user_id: uuid.UUID,
) -> None:
    result = await session.exec(
        select(AdminGroup).where(
            and_(
                AdminGroup.user_id == target_user_id,
                AdminGroup.scope_type == scope_type,
                AdminGroup.scope_id == scope_id,
            )
        )
    )
    group = result.scalars().one_or_none()
    if not group:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membership not found")

    # Prevent removing the last ADMIN (would leave the scope orphaned)
    if group.role == AdminRole.ADMIN:
        all_admins = await session.exec(
            select(AdminGroup).where(
                and_(
                    AdminGroup.scope_type == scope_type,
                    AdminGroup.scope_id == scope_id,
                    AdminGroup.role == AdminRole.ADMIN,
                )
            )
        )
        if len(list(all_admins.scalars().all())) <= 1 and not token.is_superuser:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Cannot remove the last admin. Promote another user first.",
            )

    await session.delete(group)
    await session.commit()


# ── Mod member management ─────────────────────────────────────────────────────

@router.get("/mods/{mod_id}/members", response_model=list[AdminGroupRead])
async def list_mod_members(
    mod_id: uuid.UUID,
    session: Session,
    token: Token,
) -> list[AdminGroup]:
    await _require_scope_admin(
        session, token, AdminScopeType.MOD, mod_id, min_role=AdminRole.MAINTAINER
    )
    return await _list_members(session, AdminScopeType.MOD, mod_id)


@router.post("/mods/{mod_id}/members", response_model=AdminGroupRead,
             status_code=status.HTTP_201_CREATED)
async def grant_mod_member(
    mod_id: uuid.UUID,
    body: AdminGroupCreate,
    session: Session,
    token: Token,
) -> AdminGroup:
    await _require_scope_admin(session, token, AdminScopeType.MOD, mod_id)
    body.scope_type = AdminScopeType.MOD
    body.scope_id = mod_id
    return await _grant_member(session, token, AdminScopeType.MOD, mod_id, body)


@router.delete("/mods/{mod_id}/members/{user_id}",
               status_code=status.HTTP_204_NO_CONTENT)
async def revoke_mod_member(
    mod_id: uuid.UUID,
    user_id: uuid.UUID,
    session: Session,
    token: Token,
) -> None:
    await _require_scope_admin(session, token, AdminScopeType.MOD, mod_id)
    await _revoke_member(session, token, AdminScopeType.MOD, mod_id, user_id)


# ── ModPack member management ─────────────────────────────────────────────────

@router.get("/modpacks/{modpack_id}/members", response_model=list[AdminGroupRead])
async def list_modpack_members(
    modpack_id: uuid.UUID,
    session: Session,
    token: Token,
) -> list[AdminGroup]:
    await _require_scope_admin(
        session, token, AdminScopeType.MODPACK, modpack_id, min_role=AdminRole.MAINTAINER
    )
    return await _list_members(session, AdminScopeType.MODPACK, modpack_id)


@router.post("/modpacks/{modpack_id}/members", response_model=AdminGroupRead,
             status_code=status.HTTP_201_CREATED)
async def grant_modpack_member(
    modpack_id: uuid.UUID,
    body: AdminGroupCreate,
    session: Session,
    token: Token,
) -> AdminGroup:
    await _require_scope_admin(session, token, AdminScopeType.MODPACK, modpack_id)
    body.scope_type = AdminScopeType.MODPACK
    body.scope_id = modpack_id
    return await _grant_member(session, token, AdminScopeType.MODPACK, modpack_id, body)


@router.delete("/modpacks/{modpack_id}/members/{user_id}",
               status_code=status.HTTP_204_NO_CONTENT)
async def revoke_modpack_member(
    modpack_id: uuid.UUID,
    user_id: uuid.UUID,
    session: Session,
    token: Token,
) -> None:
    await _require_scope_admin(session, token, AdminScopeType.MODPACK, modpack_id)
    await _revoke_member(session, token, AdminScopeType.MODPACK, modpack_id, user_id)


# ── Version publication ───────────────────────────────────────────────────────

async def _transition_mod_version(
    session: AsyncSession,
    token: TokenData,
    version_id: uuid.UUID,
    target_status: PublicationStatus,
    allowed_from: list[PublicationStatus],
) -> ModVersion:
    mv = await session.get(ModVersion, version_id)
    if not mv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ModVersion not found")

    await _require_scope_admin(session, token, AdminScopeType.MOD, mv.mod_id)

    if mv.status not in allowed_from:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot transition from '{mv.status}' to '{target_status}'",
        )
    mv.status = target_status
    session.add(mv)
    await session.commit()
    await session.refresh(mv)
    return mv


async def _transition_modpack(
    session: AsyncSession,
    token: TokenData,
    modpack_id: uuid.UUID,
    target_status: PublicationStatus,
    allowed_from: list[PublicationStatus],
) -> ModPackVersion:
    mp = await session.get(ModPackVersion, modpack_id)
    if not mp:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ModPack not found")

    # Memberships are granted on the modpack identity (see the members endpoints), not on one version
    await _require_scope_admin(session, token, AdminScopeType.MODPACK, mp.identity_id)

    if mp.status not in allowed_from:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot transition from '{mp.status}' to '{target_status}'",
        )
    mp.status = target_status
    session.add(mp)
    await session.commit()
    await session.refresh(mp)
    return mp


@router.post("/mod-versions/{version_id}/publish")
async def publish_mod_version(
    version_id: uuid.UUID, session: Session, token: Token
) -> dict:
    mv = await _transition_mod_version(
        session, token, version_id,
        target_status=PublicationStatus.PUBLISHED,
        allowed_from=[PublicationStatus.DRAFT],
    )
    return {"id": str(mv.id), "status": mv.status}


@router.post("/mod-versions/{version_id}/deprecate")
async def deprecate_mod_version(
    version_id: uuid.UUID, session: Session, token: Token
) -> dict:
    mv = await _transition_mod_version(
        session, token, version_id,
        target_status=PublicationStatus.DEPRECATED,
        allowed_from=[PublicationStatus.PUBLISHED],
    )
    return {"id": str(mv.id), "status": mv.status}


@router.post("/modpacks/{modpack_id}/publish")
async def publish_modpack(
    modpack_id: uuid.UUID, session: Session, token: Token
) -> dict:
    mp = await _transition_modpack(
        session, token, modpack_id,
        target_status=PublicationStatus.PUBLISHED,
        allowed_from=[PublicationStatus.DRAFT],
    )
    return {"id": str(mp.id), "status": mp.status}


@router.post("/modpacks/{modpack_id}/deprecate")
async def deprecate_modpack(
    modpack_id: uuid.UUID, session: Session, token: Token
) -> dict:
    mp = await _transition_modpack(
        session, token, modpack_id,
        target_status=PublicationStatus.DEPRECATED,
        allowed_from=[PublicationStatus.PUBLISHED],
    )
    return {"id": str(mp.id), "status": mp.status}

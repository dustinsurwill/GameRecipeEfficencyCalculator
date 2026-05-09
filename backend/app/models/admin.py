"""
Admin groups — scoped permissions for mod and modpack administration.

Permission hierarchy (highest to lowest):
  superuser     — site-wide, set on User.is_superuser
  curator       — can approve community recipes for any mod/pack
  modpack_admin — can publish ModPack versions, approve recipes for their pack
  mod_admin     — can publish ModVersions, approve recipes for their mod

A user can be an admin of multiple mods and/or modpacks simultaneously.

Design notes:
- We deliberately do NOT embed scoped roles in JWTs. Mod admin status changes
  (gaining/losing rights, a mod gaining new admins) should take effect
  immediately, not at next token refresh. So we do a single DB lookup on
  write operations. Reads (viewing drafts) are fast indexed queries.
- The scope_type + scope_id pair is a "polymorphic FK" pattern — cleaner
  than separate ModAdmin and ModPackAdmin tables given that the permission
  logic is identical.
"""
import uuid
from enum import StrEnum

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from app.db import TimestampMixin


class AdminScopeType(StrEnum):
    MOD = "mod"
    MODPACK = "modpack"


class AdminRole(StrEnum):
    ADMIN = "admin"       # full control: publish versions, approve recipes, add members
    MAINTAINER = "maintainer"  # approve recipes, edit metadata; cannot publish or add admins


class AdminGroup(TimestampMixin, table=True):
    """
    One membership record: user X is ROLE for mod/modpack Y.

    scope_type + scope_id is a soft polymorphic FK:
      scope_type="mod"     → scope_id references mods.id
      scope_type="modpack" → scope_id references modpacks.id

    We use a soft FK (no DB-level foreign key constraint) because SQLModel
    doesn't support polymorphic FK constraints cleanly. Integrity is enforced
    at the application layer in the permission helpers below.
    """
    __tablename__ = "admin_groups"
    __table_args__ = (
        UniqueConstraint("user_id", "scope_type", "scope_id",
                         name="uq_admin_group_user_scope"),
        Index("ix_admin_group_scope", "scope_type", "scope_id"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)

    scope_type: str  # AdminScopeType
    scope_id: uuid.UUID  # mod.id or modpack.id
    role: str = Field(default=AdminRole.MAINTAINER)  # AdminRole

    # Who granted this and when (audit trail)
    granted_by_id: uuid.UUID | None = Field(default=None, foreign_key="users.id")

    user: "User" = Relationship(  # type: ignore[name-defined]
        back_populates="admin_groups",
        sa_relationship_kwargs={
            "foreign_keys": "[AdminGroup.user_id]",
            "lazy": "noload",
        },
    )


# ── Permission helper functions ───────────────────────────────────────────────
# These are used in route dependencies and service functions.
# All return bool — callers raise 403 if False.

from sqlalchemy import and_, select
from sqlmodel.ext.asyncio.session import AsyncSession


async def is_mod_admin(
    session: AsyncSession,
    user_id: uuid.UUID,
    mod_id: uuid.UUID,
    min_role: AdminRole = AdminRole.MAINTAINER,
) -> bool:
    """True if user has at least min_role for the given mod."""
    result = await session.exec(
        select(AdminGroup).where(
            and_(
                AdminGroup.user_id == user_id,
                AdminGroup.scope_type == AdminScopeType.MOD,
                AdminGroup.scope_id == mod_id,
            )
        )
    )
    group = result.scalars().one_or_none()
    if not group:
        return False
    return _role_gte(group.role, min_role)


async def is_modpack_admin(
    session: AsyncSession,
    user_id: uuid.UUID,
    modpack_id: uuid.UUID,
    min_role: AdminRole = AdminRole.MAINTAINER,
) -> bool:
    """True if user has at least min_role for the given modpack."""
    result = await session.exec(
        select(AdminGroup).where(
            and_(
                AdminGroup.user_id == user_id,
                AdminGroup.scope_type == AdminScopeType.MODPACK,
                AdminGroup.scope_id == modpack_id,
            )
        )
    )
    group = result.scalars().one_or_none()
    if not group:
        return False
    return _role_gte(group.role, min_role)


async def get_user_mod_scopes(
    session: AsyncSession,
    user_id: uuid.UUID,
    scope_type: AdminScopeType,
) -> list[uuid.UUID]:
    """Return all scope_ids the user has any role for, of the given type."""
    result = await session.exec(
        select(AdminGroup.scope_id).where(
            and_(
                AdminGroup.user_id == user_id,
                AdminGroup.scope_type == scope_type,
            )
        )
    )
    return list(result.all())


def _role_gte(role: str, min_role: AdminRole) -> bool:
    """Role hierarchy: ADMIN > MAINTAINER."""
    order = {AdminRole.MAINTAINER: 0, AdminRole.ADMIN: 1}
    return order.get(role, -1) >= order[min_role]


# ── FastAPI permission dependencies ───────────────────────────────────────────

from fastapi import Depends, HTTPException, status

from app.auth.tokens import TokenData, get_current_token_data
from app.db import get_session


async def require_mod_admin(
    mod_id: uuid.UUID,
    token: TokenData = Depends(get_current_token_data),
    session: AsyncSession = Depends(get_session),
) -> TokenData:
    """
    Dependency: gate a route to mod admins or site superusers.
    Usage: Depends(lambda mod_id: require_mod_admin(mod_id))
    Better usage: inject mod_id from path and call directly in the route.
    """
    if token.is_superuser:
        return token
    allowed = await is_mod_admin(session, token.user_id, mod_id)
    if not allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mod admin role required")
    return token


async def require_modpack_admin(
    modpack_id: uuid.UUID,
    token: TokenData = Depends(get_current_token_data),
    session: AsyncSession = Depends(get_session),
) -> TokenData:
    if token.is_superuser:
        return token
    allowed = await is_modpack_admin(session, token.user_id, modpack_id)
    if not allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "ModPack admin role required")
    return token


# ── API Schemas ───────────────────────────────────────────────────────────────

class AdminGroupRead(SQLModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    user_id: uuid.UUID
    scope_type: str
    scope_id: uuid.UUID
    role: str
    granted_by_id: uuid.UUID | None


class AdminGroupCreate(SQLModel):
    user_id: uuid.UUID
    scope_type: str  # "mod" | "modpack"
    scope_id: uuid.UUID
    role: str = AdminRole.MAINTAINER

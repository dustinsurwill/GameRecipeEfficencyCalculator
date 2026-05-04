"""
Catalog routes — versioning layer CRUD.

Public read endpoints return only published records unless the caller is
a superuser or mod/modpack admin.

  GET  /games                       — list all games
  POST /games                       — create (superuser)
  GET  /games/{id}                  — get by id
  GET  /games/{id}/versions         — list game versions
  POST /games/{id}/versions         — create game version (superuser)

  GET  /mods                        — list mods (filter: ?game_id=)
  POST /mods                        — create (superuser)
  GET  /mods/{id}                   — get by id
  GET  /mods/{id}/versions          — list mod versions
  POST /mods/{id}/versions          — create mod version (superuser or mod admin)

  GET  /modpacks                    — list modpacks (filter: ?game_version_id=)
  POST /modpacks                    — create (superuser)
  GET  /modpacks/{id}               — get by id
  POST /modpacks/{id}/mod-versions  — add a mod version to a modpack (superuser or modpack admin)
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.tokens import TokenData, get_current_token_data
from app.db import get_session
from app.models.admin import AdminRole, AdminScopeType, is_mod_admin, is_modpack_admin
from app.models.status import PublicationStatus
from app.models.versioning import (
    Game, GameCreate, GameRead, GameVersion, GameVersionCreate, GameVersionRead,
    Mod, ModCreate, ModRead, ModPack, ModPackCreate, ModPackRead,
    ModPackModVersion, ModVersion, ModVersionCreate, ModVersionRead,
)

router = APIRouter(tags=["catalog"])
Session = Annotated[AsyncSession, Depends(get_session)]
Token = Annotated[TokenData, Depends(get_current_token_data)]

from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from app.auth.tokens import decode_token, TokenType

_optional_bearer = HTTPBearer(auto_error=False)


async def _optional_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
) -> TokenData | None:
    if not credentials:
        return None
    try:
        token = decode_token(credentials.credentials)
        return token if token.token_type == TokenType.ACCESS else None
    except HTTPException:
        return None


def _require_superuser(token: TokenData) -> None:
    if not token.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Superuser required")


# ── Games ─────────────────────────────────────────────────────────────────────

@router.get("/games", response_model=list[GameRead])
async def list_games(session: Session) -> list[Game]:
    result = await session.exec(select(Game).order_by(Game.name))
    return list(result.scalars().all())


@router.post("/games", response_model=GameRead, status_code=status.HTTP_201_CREATED)
async def create_game(body: GameCreate, session: Session, token: Token) -> Game:
    _require_superuser(token)
    existing = await session.exec(select(Game).where(Game.slug == body.slug))
    if existing.scalars().one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Game slug '{body.slug}' already exists")
    game = Game(**body.model_dump())
    session.add(game)
    await session.commit()
    await session.refresh(game)
    return game


@router.get("/games/{game_id}", response_model=GameRead)
async def get_game(game_id: uuid.UUID, session: Session) -> Game:
    game = await session.get(Game, game_id)
    if not game:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Game not found")
    return game


@router.get("/games/{game_id}/versions", response_model=list[GameVersionRead])
async def list_game_versions(game_id: uuid.UUID, session: Session) -> list[GameVersion]:
    game = await session.get(Game, game_id)
    if not game:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Game not found")
    result = await session.exec(
        select(GameVersion).where(GameVersion.game_id == game_id).order_by(GameVersion.version)
    )
    return list(result.scalars().all())


@router.post("/games/{game_id}/versions", response_model=GameVersionRead,
             status_code=status.HTTP_201_CREATED)
async def create_game_version(
    game_id: uuid.UUID, body: GameVersionCreate, session: Session, token: Token,
) -> GameVersion:
    _require_superuser(token)
    game = await session.get(Game, game_id)
    if not game:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Game not found")
    gv = GameVersion(game_id=game_id, **body.model_dump())
    session.add(gv)
    await session.commit()
    await session.refresh(gv)
    return gv


# ── Mods ──────────────────────────────────────────────────────────────────────

@router.get("/mods", response_model=list[ModRead])
async def list_mods(
    session: Session,
    game_id: uuid.UUID | None = None,
) -> list[Mod]:
    q = select(Mod).order_by(Mod.name)
    if game_id:
        q = q.where(Mod.game_id == game_id)
    result = await session.exec(q)
    return list(result.scalars().all())


@router.post("/mods", response_model=ModRead, status_code=status.HTTP_201_CREATED)
async def create_mod(body: ModCreate, session: Session, token: Token) -> Mod:
    _require_superuser(token)
    game = await session.get(Game, body.game_id)
    if not game:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Game not found")
    mod = Mod(**body.model_dump())
    session.add(mod)
    await session.commit()
    await session.refresh(mod)
    return mod


@router.get("/mods/{mod_id}", response_model=ModRead)
async def get_mod(mod_id: uuid.UUID, session: Session) -> Mod:
    mod = await session.get(Mod, mod_id)
    if not mod:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mod not found")
    return mod


@router.get("/mods/{mod_id}/versions", response_model=list[ModVersionRead])
async def list_mod_versions(
    mod_id: uuid.UUID,
    session: Session,
    token: TokenData | None = Depends(_optional_token),
) -> list[ModVersion]:
    mod = await session.get(Mod, mod_id)
    if not mod:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mod not found")

    q = select(ModVersion).where(ModVersion.mod_id == mod_id)

    is_admin = token and (
        token.is_superuser or await is_mod_admin(session, token.user_id, mod_id, AdminRole.MAINTAINER)
    )
    if not is_admin:
        q = q.where(ModVersion.status == PublicationStatus.PUBLISHED)

    result = await session.exec(q.order_by(ModVersion.version))
    return list(result.scalars().all())


@router.post("/mods/{mod_id}/versions", response_model=ModVersionRead,
             status_code=status.HTTP_201_CREATED)
async def create_mod_version(
    mod_id: uuid.UUID, body: ModVersionCreate, session: Session, token: Token,
) -> ModVersion:
    mod = await session.get(Mod, mod_id)
    if not mod:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mod not found")

    if not token.is_superuser:
        ok = await is_mod_admin(session, token.user_id, mod_id, AdminRole.MAINTAINER)
        if not ok:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Mod admin required")

    gv = await session.get(GameVersion, body.game_version_id)
    if not gv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "GameVersion not found")

    mv = ModVersion(mod_id=mod_id, **body.model_dump())
    session.add(mv)
    await session.commit()
    await session.refresh(mv)
    return mv


# ── ModPacks ──────────────────────────────────────────────────────────────────

@router.get("/modpacks", response_model=list[ModPackRead])
async def list_modpacks(
    session: Session,
    game_version_id: uuid.UUID | None = None,
    token: TokenData | None = Depends(_optional_token),
) -> list[ModPack]:
    q = select(ModPack).order_by(ModPack.name, ModPack.version)
    if game_version_id:
        q = q.where(ModPack.game_version_id == game_version_id)

    is_admin = token and token.is_superuser
    if not is_admin:
        q = q.where(ModPack.status == PublicationStatus.PUBLISHED)

    result = await session.exec(q)
    return list(result.scalars().all())


@router.post("/modpacks", response_model=ModPackRead, status_code=status.HTTP_201_CREATED)
async def create_modpack(body: ModPackCreate, session: Session, token: Token) -> ModPack:
    _require_superuser(token)
    gv = await session.get(GameVersion, body.game_version_id)
    if not gv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "GameVersion not found")
    mp = ModPack(**body.model_dump())
    session.add(mp)
    await session.commit()
    await session.refresh(mp)
    return mp


@router.get("/modpacks/{modpack_id}", response_model=ModPackRead)
async def get_modpack(
    modpack_id: uuid.UUID,
    session: Session,
    token: TokenData | None = Depends(_optional_token),
) -> ModPack:
    mp = await session.get(ModPack, modpack_id)
    if not mp:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ModPack not found")

    is_admin = token and (
        token.is_superuser
        or await is_modpack_admin(session, token.user_id, modpack_id, AdminRole.MAINTAINER)
    )
    if not is_admin and mp.status != PublicationStatus.PUBLISHED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ModPack not found")

    return mp


@router.post("/modpacks/{modpack_id}/mod-versions",
             status_code=status.HTTP_204_NO_CONTENT)
async def add_mod_version_to_modpack(
    modpack_id: uuid.UUID,
    mod_version_id: uuid.UUID,
    session: Session,
    token: Token,
) -> None:
    """Link a ModVersion into a ModPack."""
    mp = await session.get(ModPack, modpack_id)
    if not mp:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ModPack not found")

    if not token.is_superuser:
        ok = await is_modpack_admin(session, token.user_id, modpack_id, AdminRole.MAINTAINER)
        if not ok:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "ModPack admin required")

    mv = await session.get(ModVersion, mod_version_id)
    if not mv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ModVersion not found")

    existing = await session.get(ModPackModVersion, (modpack_id, mod_version_id))
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "ModVersion already in ModPack")

    link = ModPackModVersion(modpack_id=modpack_id, mod_version_id=mod_version_id)
    session.add(link)
    await session.commit()

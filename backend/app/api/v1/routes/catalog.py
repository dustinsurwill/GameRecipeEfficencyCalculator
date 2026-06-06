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

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.tokens import TokenData, get_current_token_data
from app.db import get_session
from app.models.admin import AdminRole, AdminScopeType, is_mod_admin, is_modpack_admin
from app.models.identity import (
    ItemAppearance, ItemAppearanceRead,
    ItemIdentity, ItemIdentityRead,
    ItemAppearanceWithIdentityRead,
    MachineIdentity, MachineIdentityRead,
)
from app.models.recipe import Recipe, RecipeModVersionLink, RecipeSlot
from app.models.status import PublicationStatus
from app.models.versioning import (
    Game, GameCreate, GameRead, GameVersion, GameVersionCreate, GameVersionRead,
    Mod, ModCreate, ModRead,
    ModPackIdentity, ModPackIdentityCreate, ModPackIdentityRead,
    ModPackVersion, ModPackVersionCreate, ModPackVersionRead,
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


@router.get("/games/{game_id}/machines", response_model=list[MachineIdentityRead])
async def list_game_machines(game_id: uuid.UUID, session: Session) -> list[MachineIdentity]:
    game = await session.get(Game, game_id)
    if not game:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Game not found")
    result = await session.exec(
        select(MachineIdentity).where(MachineIdentity.game_id == game_id).order_by(MachineIdentity.name)
    )
    return list(result.scalars().all())


@router.get("/games/{game_id}/items", response_model=list[ItemIdentityRead])
async def list_game_items(
    game_id: uuid.UUID,
    session: Session,
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ItemIdentity]:
    game = await session.get(Game, game_id)
    if not game:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Game not found")
    q = select(ItemIdentity).where(ItemIdentity.game_id == game_id)
    if search:
        q = q.where(ItemIdentity.canonical_name.icontains(search))
    result = await session.exec(q.order_by(ItemIdentity.canonical_name).offset(offset).limit(limit))
    return list(result.scalars().all())


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


# ── ModPack Identities ────────────────────────────────────────────────────────

def _build_mpv_read(mpv: ModPackVersion, identity: ModPackIdentity) -> ModPackVersionRead:
    return ModPackVersionRead(
        id=mpv.id,
        identity_id=mpv.identity_id,
        game_version_id=mpv.game_version_id,
        name=identity.name,
        slug=identity.slug,
        version=mpv.version,
        status=mpv.status,
        icon_url=mpv.icon_url or identity.icon_url,
        source_url=identity.source_url,
        description=identity.description,
    )


@router.get("/modpacks", response_model=list[ModPackIdentityRead])
async def list_modpack_identities(
    session: Session,
    game_id: uuid.UUID | None = None,
    token: TokenData | None = Depends(_optional_token),
) -> list[ModPackIdentity]:
    q = select(ModPackIdentity).order_by(ModPackIdentity.name)
    if game_id:
        q = q.where(ModPackIdentity.game_id == game_id)
    result = await session.exec(q)
    return list(result.scalars().all())


@router.post("/modpacks", response_model=ModPackIdentityRead, status_code=status.HTTP_201_CREATED)
async def create_modpack_identity(
    body: ModPackIdentityCreate, session: Session, token: Token,
) -> ModPackIdentity:
    _require_superuser(token)
    game = await session.get(Game, body.game_id)
    if not game:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Game not found")
    existing = await session.exec(
        select(ModPackIdentity).where(
            ModPackIdentity.game_id == body.game_id,
            ModPackIdentity.slug == body.slug,
        )
    )
    if existing.scalars().one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Modpack slug '{body.slug}' already exists for this game")
    identity = ModPackIdentity(**body.model_dump())
    session.add(identity)
    await session.commit()
    await session.refresh(identity)
    return identity


@router.get("/modpacks/{identity_id}", response_model=ModPackIdentityRead)
async def get_modpack_identity(identity_id: uuid.UUID, session: Session) -> ModPackIdentity:
    identity = await session.get(ModPackIdentity, identity_id)
    if not identity:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ModPack not found")
    return identity


@router.get("/modpacks/{identity_id}/versions", response_model=list[ModPackVersionRead])
async def list_modpack_versions(
    identity_id: uuid.UUID,
    session: Session,
    token: TokenData | None = Depends(_optional_token),
) -> list[ModPackVersionRead]:
    identity = await session.get(ModPackIdentity, identity_id)
    if not identity:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ModPack not found")

    q = select(ModPackVersion).where(ModPackVersion.identity_id == identity_id)
    is_admin = token and token.is_superuser
    if not is_admin:
        q = q.where(ModPackVersion.status == PublicationStatus.PUBLISHED)
    result = await session.exec(q.order_by(ModPackVersion.version))
    return [_build_mpv_read(v, identity) for v in result.scalars().all()]


@router.post("/modpacks/{identity_id}/versions",
             response_model=ModPackVersionRead,
             status_code=status.HTTP_201_CREATED)
async def create_modpack_version(
    identity_id: uuid.UUID,
    body: ModPackVersionCreate,
    session: Session,
    token: Token,
) -> ModPackVersionRead:
    _require_superuser(token)
    identity = await session.get(ModPackIdentity, identity_id)
    if not identity:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ModPack not found")
    gv = await session.get(GameVersion, body.game_version_id)
    if not gv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "GameVersion not found")
    mpv = ModPackVersion(identity_id=identity_id, **body.model_dump())
    session.add(mpv)
    await session.commit()
    await session.refresh(mpv)
    return _build_mpv_read(mpv, identity)


# ── ModPack Versions (specific release endpoints) ─────────────────────────────

async def _get_modpack_version(
    session: AsyncSession,
    modpack_version_id: uuid.UUID,
    token: TokenData | None = None,
) -> tuple[ModPackVersion, ModPackIdentity]:
    mpv = await session.get(ModPackVersion, modpack_version_id)
    if not mpv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ModPack version not found")
    identity = await session.get(ModPackIdentity, mpv.identity_id)
    is_admin = token and (
        token.is_superuser
        or await is_modpack_admin(session, token.user_id, modpack_version_id, AdminRole.MAINTAINER)
    )
    if not is_admin and mpv.status != PublicationStatus.PUBLISHED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ModPack version not found")
    return mpv, identity


@router.get("/modpack-versions/{modpack_version_id}", response_model=ModPackVersionRead)
async def get_modpack_version(
    modpack_version_id: uuid.UUID,
    session: Session,
    token: TokenData | None = Depends(_optional_token),
) -> ModPackVersionRead:
    mpv, identity = await _get_modpack_version(session, modpack_version_id, token)
    return _build_mpv_read(mpv, identity)


@router.post("/modpack-versions/{modpack_version_id}/mod-versions",
             status_code=status.HTTP_204_NO_CONTENT)
async def add_mod_version_to_modpack_version(
    modpack_version_id: uuid.UUID,
    mod_version_id: uuid.UUID,
    session: Session,
    token: Token,
) -> None:
    """Link a ModVersion into a ModPackVersion."""
    mpv = await session.get(ModPackVersion, modpack_version_id)
    if not mpv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ModPack version not found")
    if not token.is_superuser:
        ok = await is_modpack_admin(session, token.user_id, modpack_version_id, AdminRole.MAINTAINER)
        if not ok:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "ModPack admin required")
    mv = await session.get(ModVersion, mod_version_id)
    if not mv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ModVersion not found")
    existing = await session.get(ModPackModVersion, (modpack_version_id, mod_version_id))
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "ModVersion already in this ModPack version")
    session.add(ModPackModVersion(modpack_id=modpack_version_id, mod_version_id=mod_version_id))
    await session.commit()


# ── ModPack version sub-resources ─────────────────────────────────────────────

class ModPackModEntry(SQLModel):
    mod: ModRead
    mod_version: ModVersionRead


@router.get("/modpack-versions/{modpack_version_id}/mods", response_model=list[ModPackModEntry])
async def list_modpack_version_mods(
    modpack_version_id: uuid.UUID,
    session: Session,
    token: TokenData | None = Depends(_optional_token),
) -> list[ModPackModEntry]:
    mpv, _ = await _get_modpack_version(session, modpack_version_id, token)
    links_result = await session.exec(
        select(ModPackModVersion).where(ModPackModVersion.modpack_id == modpack_version_id)
    )
    entries: list[ModPackModEntry] = []
    for link in links_result.scalars().all():
        mv = await session.get(ModVersion, link.mod_version_id)
        if not mv:
            continue
        mod = await session.get(Mod, mv.mod_id)
        if not mod:
            continue
        entries.append(ModPackModEntry(
            mod=ModRead.model_validate(mod),
            mod_version=ModVersionRead.model_validate(mv),
        ))
    entries.sort(key=lambda e: e.mod.name)
    return entries


@router.get("/modpack-versions/{modpack_version_id}/machines", response_model=list[MachineIdentityRead])
async def list_modpack_version_machines(
    modpack_version_id: uuid.UUID,
    session: Session,
    token: TokenData | None = Depends(_optional_token),
) -> list[MachineIdentity]:
    mpv, _ = await _get_modpack_version(session, modpack_version_id, token)
    gv = await session.get(GameVersion, mpv.game_version_id)
    links_result = await session.exec(
        select(ModPackModVersion).where(ModPackModVersion.modpack_id == modpack_version_id)
    )
    namespaces: set[str] = set()
    for link in links_result.scalars().all():
        mv = await session.get(ModVersion, link.mod_version_id)
        if mv:
            mod = await session.get(Mod, mv.mod_id)
            if mod:
                namespaces.add(mod.namespace)
    game_machines_result = await session.exec(
        select(MachineIdentity).where(MachineIdentity.game_id == gv.game_id)
    )
    machines = [
        m for m in game_machines_result.scalars().all()
        if any(f"{ns}:" in m.recipe_types_json for ns in namespaces)
    ]
    return sorted(machines, key=lambda m: m.name)


@router.get("/modpack-versions/{modpack_version_id}/items", response_model=list[ItemAppearanceWithIdentityRead])
async def list_modpack_version_items(
    modpack_version_id: uuid.UUID,
    session: Session,
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    token: TokenData | None = Depends(_optional_token),
) -> list[ItemAppearanceWithIdentityRead]:
    mpv, _ = await _get_modpack_version(session, modpack_version_id, token)
    links_result = await session.exec(
        select(ModPackModVersion).where(ModPackModVersion.modpack_id == modpack_version_id)
    )
    mv_ids = [link.mod_version_id for link in links_result.scalars().all()]
    if not mv_ids:
        return []

    q = select(ItemAppearance).where(ItemAppearance.mod_version_id.in_(mv_ids))
    if search:
        q = q.where(ItemAppearance.item_id.icontains(search))
    appearances_result = await session.exec(
        q.order_by(ItemAppearance.item_id).offset(offset).limit(limit)
    )
    appearances = list(appearances_result.scalars().all())

    identity_ids = {a.identity_id for a in appearances}
    identities_result = await session.exec(
        select(ItemIdentity).where(ItemIdentity.id.in_(identity_ids))
    )
    identity_map = {i.id: i for i in identities_result.scalars().all()}

    entries: list[ItemAppearanceWithIdentityRead] = []
    for app in appearances:
        identity = identity_map.get(app.identity_id)
        if not identity:
            continue
        entries.append(ItemAppearanceWithIdentityRead(
            appearance=ItemAppearanceRead.model_validate(app),
            identity=ItemIdentityRead.model_validate(identity),
        ))
    return entries


# ── Mod detail endpoints ──────────────────────────────────────────────────────

@router.get("/mods/{mod_id}/machines", response_model=list[MachineIdentityRead])
async def list_mod_machines(
    mod_id: uuid.UUID,
    session: Session,
) -> list[MachineIdentity]:
    mod = await session.get(Mod, mod_id)
    if not mod:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mod not found")

    by_fk_result = await session.exec(
        select(MachineIdentity).where(MachineIdentity.mod_id == mod_id)
    )
    by_fk = list(by_fk_result.scalars().all())
    found_ids = {m.id for m in by_fk}

    game_machines_result = await session.exec(
        select(MachineIdentity).where(MachineIdentity.game_id == mod.game_id)
    )
    prefix = f"{mod.namespace}:"
    by_namespace = [
        m for m in game_machines_result.scalars().all()
        if m.id not in found_ids and prefix in m.recipe_types_json
    ]

    return sorted(by_fk + by_namespace, key=lambda m: m.name)


@router.get("/mods/{mod_id}/items", response_model=list[ItemAppearanceWithIdentityRead])
async def list_mod_items(
    mod_id: uuid.UUID,
    session: Session,
    mod_version_id: uuid.UUID | None = None,
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ItemAppearanceWithIdentityRead]:
    mod = await session.get(Mod, mod_id)
    if not mod:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mod not found")

    if mod_version_id:
        mv = await session.get(ModVersion, mod_version_id)
        if not mv or mv.mod_id != mod_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "ModVersion not found for this mod")
        target_mv_id = mod_version_id
    else:
        mv_result = await session.exec(
            select(ModVersion)
            .where(ModVersion.mod_id == mod_id, ModVersion.status == PublicationStatus.PUBLISHED)
            .order_by(ModVersion.imported_at.desc())
            .limit(1)
        )
        mv = mv_result.scalars().one_or_none()
        if not mv:
            return []
        target_mv_id = mv.id

    q = select(ItemAppearance).where(ItemAppearance.mod_version_id == target_mv_id)
    if search:
        q = q.where(ItemAppearance.item_id.icontains(search))
    appearances_result = await session.exec(
        q.order_by(ItemAppearance.item_id).offset(offset).limit(limit)
    )
    appearances = list(appearances_result.scalars().all())

    identity_ids = {a.identity_id for a in appearances}
    identities_result = await session.exec(
        select(ItemIdentity).where(ItemIdentity.id.in_(identity_ids))
    )
    identity_map = {i.id: i for i in identities_result.scalars().all()}

    entries: list[ItemAppearanceWithIdentityRead] = []
    for app in appearances:
        identity = identity_map.get(app.identity_id)
        if not identity:
            continue
        entries.append(ItemAppearanceWithIdentityRead(
            appearance=ItemAppearanceRead.model_validate(app),
            identity=ItemIdentityRead.model_validate(identity),
        ))
    return entries


# ── JEI-style item info ───────────────────────────────────────────────────────

class RecipeSlotInfo(SQLModel):
    item_id: str | None
    tag_string: str | None
    quantity: float
    is_fluid: bool
    fluid_label: str | None


class RecipeInfo(SQLModel):
    id: uuid.UUID
    recipe_type: str
    mod_namespace: str
    machine_name: str
    inputs: list["RecipeSlotInfo"]
    outputs: list["RecipeSlotInfo"]
    aux_inputs: list["RecipeSlotInfo"]


class ItemInfoRead(SQLModel):
    item_id: str
    produces: list["RecipeInfo"]
    uses: list["RecipeInfo"]


async def _load_recipe_infos(
    session: AsyncSession,
    recipe_ids: set[uuid.UUID],
    appearance_id_to_item: dict[uuid.UUID, str],
) -> list[RecipeInfo]:
    if not recipe_ids:
        return []
    from collections import defaultdict as _dd

    machine_result = await session.exec(select(MachineIdentity))
    machine_map: dict[uuid.UUID, str] = {m.id: m.name for m in machine_result.scalars().all()}

    recipes_result = await session.exec(select(Recipe).where(Recipe.id.in_(recipe_ids)))
    db_recipes = list(recipes_result.scalars().all())

    slots_result = await session.exec(select(RecipeSlot).where(RecipeSlot.recipe_id.in_(recipe_ids)))
    slots_by_recipe: dict[uuid.UUID, list[RecipeSlot]] = _dd(list)
    for s in slots_result.scalars().all():
        slots_by_recipe[s.recipe_id].append(s)

    infos: list[RecipeInfo] = []
    for db_r in db_recipes:
        inputs, outputs, aux = [], [], []
        for sl in sorted(slots_by_recipe[db_r.id], key=lambda s: s.slot_order):
            item_id = appearance_id_to_item.get(sl.item_appearance_id) if sl.item_appearance_id else None
            si = RecipeSlotInfo(
                item_id=item_id,
                tag_string=sl.tag_string,
                quantity=sl.quantity,
                is_fluid=sl.is_fluid,
                fluid_label=sl.fluid_label,
            )
            if sl.slot_type == "output":
                outputs.append(si)
            elif sl.slot_type == "input":
                inputs.append(si)
            else:
                aux.append(si)
        rt = db_r.recipe_type
        infos.append(RecipeInfo(
            id=db_r.id,
            recipe_type=rt,
            mod_namespace=rt.split(":")[0] if ":" in rt else rt,
            machine_name=machine_map.get(db_r.machine_identity_id, rt),
            inputs=inputs,
            outputs=outputs,
            aux_inputs=aux,
        ))
    return infos


@router.get("/item-info", response_model=ItemInfoRead)
async def get_item_info(
    session: Session,
    item_id: str = Query(...),
    mod_version_id: list[uuid.UUID] = Query(default=[]),
) -> ItemInfoRead:
    """Recipes that produce or consume item_id within the given mod version scope."""
    if not mod_version_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Provide at least one mod_version_id")

    appearances_result = await session.exec(
        select(ItemAppearance).where(
            ItemAppearance.item_id == item_id,
            ItemAppearance.mod_version_id.in_(mod_version_id),
        )
    )
    appearance_ids = [a.id for a in appearances_result.scalars().all()]

    all_apps_result = await session.exec(
        select(ItemAppearance).where(ItemAppearance.mod_version_id.in_(mod_version_id))
    )
    appearance_id_to_item: dict[uuid.UUID, str] = {a.id: a.item_id for a in all_apps_result.scalars().all()}

    links_result = await session.exec(
        select(RecipeModVersionLink).where(RecipeModVersionLink.mod_version_id.in_(mod_version_id))
    )
    scoped_recipe_ids = {lnk.recipe_id for lnk in links_result.scalars().all()}

    async def _by_role(role: str) -> set[uuid.UUID]:
        if not appearance_ids:
            return set()
        r = await session.exec(
            select(RecipeSlot).where(
                RecipeSlot.item_appearance_id.in_(appearance_ids),
                RecipeSlot.slot_type == role,
            )
        )
        return {s.recipe_id for s in r.scalars().all()} & scoped_recipe_ids

    produces_ids = await _by_role("output")
    uses_ids = await _by_role("input")

    infos_map = {r.id: r for r in await _load_recipe_infos(
        session, produces_ids | uses_ids, appearance_id_to_item
    )}
    return ItemInfoRead(
        item_id=item_id,
        produces=[infos_map[rid] for rid in produces_ids if rid in infos_map],
        uses=[infos_map[rid] for rid in uses_ids if rid in infos_map],
    )


@router.get("/machines/{machine_id}/recipes", response_model=list[RecipeInfo])
async def get_machine_recipes(
    machine_id: uuid.UUID,
    session: Session,
    mod_version_id: list[uuid.UUID] = Query(default=[]),
) -> list[RecipeInfo]:
    """All recipes for a machine within the given mod version scope."""
    machine = await session.get(MachineIdentity, machine_id)
    if not machine:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Machine not found")
    if not mod_version_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Provide at least one mod_version_id")

    links_result = await session.exec(
        select(RecipeModVersionLink).where(RecipeModVersionLink.mod_version_id.in_(mod_version_id))
    )
    scoped_recipe_ids = {lnk.recipe_id for lnk in links_result.scalars().all()}

    machine_recipes_result = await session.exec(
        select(Recipe).where(Recipe.machine_identity_id == machine_id)
    )
    recipe_ids = {r.id for r in machine_recipes_result.scalars().all()} & scoped_recipe_ids

    all_apps_result = await session.exec(
        select(ItemAppearance).where(ItemAppearance.mod_version_id.in_(mod_version_id))
    )
    appearance_id_to_item: dict[uuid.UUID, str] = {a.id: a.item_id for a in all_apps_result.scalars().all()}

    return await _load_recipe_infos(session, recipe_ids, appearance_id_to_item)

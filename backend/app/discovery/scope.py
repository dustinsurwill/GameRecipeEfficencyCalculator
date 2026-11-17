"""
Scope = one game + modpack + modpack version.

Everything the discovery UI shows (items, mods, machines, tags, recipes) is relative to a
scope: the set of mod versions pinned by one modpack version. Indexes and graphs are cached
per scope, so several games and several versions of a pack can be served side by side.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.recipe import RecipeModVersionLink
from app.models.status import PublicationStatus
from app.models.versioning import (
    Game, GameVersion, ModPackIdentity, ModPackModVersion, ModPackVersion,
)

LATEST = "latest"


class ScopeNotFound(Exception):
    """Unknown game, modpack or version — callers turn this into a 404."""


@dataclass(frozen=True, slots=True)
class Scope:
    game_id: uuid.UUID
    game_slug: str
    game_name: str
    pack_id: uuid.UUID
    pack_slug: str
    pack_name: str
    version_id: uuid.UUID
    version: str
    game_version: str
    mod_version_ids: frozenset[uuid.UUID]
    is_latest: bool
    recipe_metric: str = "discrete"      # "throughput" games get the factory calculator

    @property
    def key(self) -> uuid.UUID:
        return self.version_id

    def url(self, path: str = "") -> str:
        """Canonical HTML URL under this scope. `path` starts with '/' (or is empty)."""
        return f"/{self.game_slug}/{self.pack_slug}/{self.version}{path}"

    def recipe_ids(self):
        """Subquery of recipe ids that belong to this scope (not a literal list: asyncpg's
        32767-parameter limit is easily exceeded by a pack's recipe count)."""
        return select(RecipeModVersionLink.recipe_id).where(
            RecipeModVersionLink.mod_version_id.in_(self.mod_version_ids)
        )


async def list_games(session: AsyncSession) -> list[Game]:
    return list((await session.exec(select(Game).order_by(Game.name))).scalars().all())


async def get_game(session: AsyncSession, slug: str) -> Game:
    game = (await session.exec(select(Game).where(Game.slug == slug))).scalars().one_or_none()
    if not game:
        raise ScopeNotFound(f"Unknown game '{slug}'")
    return game


async def list_packs(session: AsyncSession, game_id: uuid.UUID) -> list[ModPackIdentity]:
    return list((await session.exec(
        select(ModPackIdentity).where(ModPackIdentity.game_id == game_id).order_by(ModPackIdentity.name)
    )).scalars().all())


async def list_versions(
    session: AsyncSession, pack_id: uuid.UUID, *, include_unpublished: bool = False
) -> list[ModPackVersion]:
    """Newest first. Only published versions unless asked (admins see drafts)."""
    q = select(ModPackVersion).where(ModPackVersion.identity_id == pack_id)
    if not include_unpublished:
        q = q.where(ModPackVersion.status == PublicationStatus.PUBLISHED)
    return list((await session.exec(q.order_by(ModPackVersion.created_at.desc()))).scalars().all())


async def resolve_scope(
    session: AsyncSession, game_slug: str, pack_slug: str, version: str = LATEST
) -> Scope:
    game = await get_game(session, game_slug)
    pack = (await session.exec(
        select(ModPackIdentity).where(
            ModPackIdentity.game_id == game.id, ModPackIdentity.slug == pack_slug
        )
    )).scalars().one_or_none()
    if not pack:
        raise ScopeNotFound(f"Unknown modpack '{pack_slug}' for {game.name}")

    published = await list_versions(session, pack.id)
    # a pack with nothing published yet still resolves, so a fresh import is browsable
    candidates = published or await list_versions(session, pack.id, include_unpublished=True)
    if not candidates:
        raise ScopeNotFound(f"{pack.name} has no versions")

    latest = candidates[0]
    if version == LATEST:
        mpv = latest
    else:
        mpv = next((v for v in candidates if v.version == version), None)
        if mpv is None:
            raise ScopeNotFound(f"{pack.name} has no version '{version}'")

    gv = await session.get(GameVersion, mpv.game_version_id)
    links = (await session.exec(
        select(ModPackModVersion.mod_version_id).where(ModPackModVersion.modpack_id == mpv.id)
    )).scalars().all()
    return Scope(
        game_id=game.id, game_slug=game.slug, game_name=game.name,
        pack_id=pack.id, pack_slug=pack.slug, pack_name=pack.name,
        version_id=mpv.id, version=mpv.version, game_version=gv.version if gv else "",
        mod_version_ids=frozenset(links), is_latest=mpv.id == latest.id, recipe_metric=game.recipe_metric,
    )

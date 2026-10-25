"""
Discovery JSON API, scoped to one game + modpack + version:

  /discover/{game}/{pack}/{version}/overview | search | items | item | recipes
                                    mods | mods/{ns} | machines | machines/{id} | tags | tag

`version` may be `latest`. The server-rendered site (app/web) uses the same service layer.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import get_session
from app.discovery import service
from app.discovery.index import DiscoveryIndex, get_index
from app.discovery.scope import Scope, ScopeNotFound, resolve_scope

router = APIRouter(prefix="/discover/{game}/{pack}/{version}", tags=["discover"])


async def scoped_index(
    game: str, pack: str, version: str, session: AsyncSession = Depends(get_session),
) -> DiscoveryIndex:
    try:
        scope = await resolve_scope(session, game, pack, version)
    except ScopeNotFound as e:
        raise HTTPException(404, str(e))
    return await get_index(session, scope)


Index = Depends(scoped_index)


def _nf(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except service.NotFound as e:
        raise HTTPException(404, str(e))


@router.get("/overview")
async def overview(idx: DiscoveryIndex = Index) -> dict[str, Any]:
    s: Scope = idx.scope
    return {"scope": _scope_json(s), **service.overview(idx)}


def _scope_json(s: Scope) -> dict[str, Any]:
    return {"game": s.game_slug, "pack": s.pack_slug, "pack_name": s.pack_name,
            "version": s.version, "game_version": s.game_version, "is_latest": s.is_latest}


@router.get("/search")
async def search(q: str = Query(..., min_length=1), idx: DiscoveryIndex = Index) -> dict[str, Any]:
    return service.search(idx, q)


@router.get("/items")
async def list_items(
    idx: DiscoveryIndex = Index,
    q: str | None = None,
    mod: str | None = Query(default=None, description="mod namespace"),
    tag: str | None = None,
    craftable: bool | None = Query(default=None, description="only items with a recipe that makes them"),
    sort: Literal["name", "made_by", "used_in"] = "name",
    limit: int = Query(default=48, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return service.page(idx.filter_items(q=q, mod=mod, tag=tag, craftable=craftable, sort=sort), limit, offset)


@router.get("/item")
async def get_item(
    id: str = Query(..., description="item id, e.g. minecraft:iron_ingot"),
    idx: DiscoveryIndex = Index,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        return await service.item_detail(session, idx.scope, idx, id)
    except service.NotFound as e:
        raise HTTPException(404, str(e))


@router.get("/recipes")
async def list_recipes(
    idx: DiscoveryIndex = Index,
    session: AsyncSession = Depends(get_session),
    item: str | None = Query(default=None, description="item id or #tag"),
    role: service.Role = "output",
    machine: uuid.UUID | None = None,
    mod: str | None = Query(default=None, description="recipe-type namespace"),
    limit: int = Query(default=12, ge=1, le=60),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        return await service.list_recipes(
            session, idx.scope, idx, item=item, role=role, machine=machine, mod=mod, limit=limit, offset=offset
        )
    except service.NotFound as e:
        raise HTTPException(404, str(e))


@router.get("/mods")
async def list_mods(
    idx: DiscoveryIndex = Index, q: str | None = None,
    limit: int = Query(default=60, ge=1, le=300), offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return service.page(service.list_mods(idx, q), limit, offset)


@router.get("/mods/{namespace}")
async def get_mod(namespace: str, idx: DiscoveryIndex = Index) -> dict[str, Any]:
    return _nf(service.get_mod, idx, namespace)


@router.get("/machines")
async def list_machines(idx: DiscoveryIndex = Index, q: str | None = None, mod: str | None = None) -> dict[str, Any]:
    rows = service.list_machines(idx, q, mod)
    return {"total": len(rows), "items": rows}


@router.get("/machines/{machine_id}")
async def get_machine(machine_id: uuid.UUID, idx: DiscoveryIndex = Index) -> dict[str, Any]:
    return _nf(service.get_machine, idx, machine_id)


@router.get("/tags")
async def list_tags(
    idx: DiscoveryIndex = Index, q: str | None = None,
    limit: int = Query(default=60, ge=1, le=300), offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return service.page(service.list_tags(idx, q), limit, offset)


@router.get("/tag")
async def get_tag(id: str = Query(...), idx: DiscoveryIndex = Index) -> dict[str, Any]:
    return _nf(service.get_tag, idx, id)

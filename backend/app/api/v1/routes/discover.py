"""
Discovery endpoints — the item/recipe browsing experience (JEI-style, on the web).

  GET /discover/overview            — headline numbers + top mods/machines
  GET /discover/search?q=           — typeahead across items, mods, machines, tags
  GET /discover/items               — browse/filter/sort items (paged)
  GET /discover/item?id=            — one item: meta, tags, recipe-count facets
  GET /discover/recipes             — recipes by item (made-by / used-in), machine, or mod
  GET /discover/mods                — mods with item/recipe counts
  GET /discover/machines            — machines (crafting stations) with recipe counts
  GET /discover/tags / tag?id=      — item tags and their members

Items, mods, machines and tags are served from an in-memory index (see
app/discovery/index.py); only recipe hydration hits the database per request.
"""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import get_session
from app.discovery.index import get_index, pretty_name
from app.models.identity import ItemAppearance, ItemIdentity
from app.models.recipe import Recipe, RecipeSlot

router = APIRouter(prefix="/discover", tags=["discover"])

Role = Literal["output", "input"]


def _page(rows: list[Any], limit: int, offset: int) -> dict[str, Any]:
    return {"total": len(rows), "limit": limit, "offset": offset, "items": rows[offset : offset + limit]}


# ── Overview / search ─────────────────────────────────────────────────────────

@router.get("/overview")
async def overview(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    idx = await get_index(session)
    mods = [m for m in idx.mods if m["item_count"] or m["recipe_count"]]
    return {
        "counts": {
            "items": len(idx.items),
            "recipes": idx.recipe_total,
            "mods": len(mods),
            "machines": len(idx.machines),
            "tags": len(idx.tags),
        },
        "top_mods": sorted(mods, key=lambda m: -m["recipe_count"])[:12],
        "top_machines": sorted(idx.machines, key=lambda m: -m["recipe_count"])[:12],
        "popular_items": sorted(idx.items, key=lambda i: -(i["made_by"] + i["used_in"]))[:16],
    }


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    idx = await get_index(session)
    needle = q.strip().lower()
    return {
        "items": idx.search_items(needle, 8),
        "mods": idx.search_mods(needle, 4),
        "machines": idx.search_machines(needle, 4),
        "tags": idx.search_tags(needle, 4),
    }


# ── Items ─────────────────────────────────────────────────────────────────────

@router.get("/items")
async def list_items(
    session: AsyncSession = Depends(get_session),
    q: str | None = None,
    mod: str | None = Query(default=None, description="mod namespace"),
    tag: str | None = None,
    craftable: bool | None = Query(default=None, description="only items with a recipe that makes them"),
    sort: Literal["name", "made_by", "used_in"] = "name",
    limit: int = Query(default=48, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    idx = await get_index(session)
    rows = idx.filter_items(q=q, mod=mod, tag=tag, craftable=craftable, sort=sort)
    return _page(rows, limit, offset)


@router.get("/item")
async def get_item(
    id: str = Query(..., description="item id, e.g. minecraft:iron_ingot"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    idx = await get_index(session)
    item = idx.by_item_id.get(id)
    if not item:
        raise HTTPException(404, "Item not found")
    return {
        **item,
        "tags": [
            {"id": t, "member_count": idx.tags_by_id[t]["member_count"] if t in idx.tags_by_id else 0}
            for t in item["tags"]
        ],
        "machines": {
            "made_in": await _machine_facets(session, id, "output", item["tags"], idx),
            "used_in": await _machine_facets(session, id, "input", item["tags"], idx),
        },
    }


# ── Recipes ───────────────────────────────────────────────────────────────────

def _recipe_ids_for_item(app_id: uuid.UUID, role: Role, tags: list[str]):
    """Subquery of recipe ids that make / consume an item (inputs also match via tag)."""
    if role == "output":
        cond = (RecipeSlot.slot_type == "output") & (RecipeSlot.item_appearance_id == app_id)
    else:
        match = RecipeSlot.item_appearance_id == app_id
        if tags:
            match = or_(match, RecipeSlot.tag_string.in_(tags))
        cond = RecipeSlot.slot_type.in_(("input", "aux_input")) & match
    return select(RecipeSlot.recipe_id).where(cond)


async def _machine_facets(
    session: AsyncSession, item_id: str, role: Role, tags: list[str], idx
) -> list[dict[str, Any]]:
    item = idx.by_item_id[item_id]
    sub = _recipe_ids_for_item(idx.app_ids[item_id], role, tags).scalar_subquery()
    rows = await session.exec(
        select(Recipe.machine_identity_id, func.count(func.distinct(Recipe.id)))
        .where(Recipe.id.in_(sub))
        .group_by(Recipe.machine_identity_id)
    )
    out = []
    for mid, n in rows.all():
        m = idx.machines_by_id.get(mid)
        if m:
            out.append({"id": str(mid), "name": m["name"], "namespace": m["namespace"], "count": n})
    return sorted(out, key=lambda x: -x["count"])


@router.get("/recipes")
async def list_recipes(
    session: AsyncSession = Depends(get_session),
    item: str | None = Query(default=None, description="item id or #tag"),
    role: Role = "output",
    machine: uuid.UUID | None = None,
    mod: str | None = Query(default=None, description="recipe-type namespace"),
    limit: int = Query(default=12, ge=1, le=60),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    idx = await get_index(session)
    q = select(Recipe.id)
    item_tags: list[str] = []

    if item:
        if item.startswith("#"):
            tag = item[1:]
            if role == "output":
                return {"total": 0, "limit": limit, "offset": offset, "items": []}
            sub = select(RecipeSlot.recipe_id).where(
                RecipeSlot.slot_type.in_(("input", "aux_input")), RecipeSlot.tag_string == tag
            )
            item_tags = [tag]
        else:
            it = idx.by_item_id.get(item)
            if not it:
                raise HTTPException(404, "Item not found")
            item_tags = it["tags"]
            sub = _recipe_ids_for_item(idx.app_ids[it["item_id"]], role, item_tags)
        q = q.where(Recipe.id.in_(sub.scalar_subquery()))
    if machine:
        q = q.where(Recipe.machine_identity_id == machine)
    if mod:
        cond = Recipe.recipe_type.like(f"{mod}:%")
        if mod == "minecraft":
            cond = or_(cond, Recipe.recipe_type.notlike("%:%"))
        q = q.where(cond)

    total = (await session.exec(select(func.count()).select_from(q.subquery()))).scalar_one()
    ids = (
        await session.exec(
            q.order_by(_input_count(), Recipe.recipe_type, Recipe.import_key).limit(limit).offset(offset)
        )
    ).scalars().all()
    recipes = await _hydrate(session, list(ids), idx, item_tags)
    return {"total": total, "limit": limit, "offset": offset, "items": recipes}


def _input_count():
    """Fewest-ingredient recipes first — the simple, direct ways to get something."""
    return (
        select(func.count())
        .where(RecipeSlot.recipe_id == Recipe.id, RecipeSlot.slot_type == "input")
        .scalar_subquery()
    )


async def _hydrate(
    session: AsyncSession, ids: list[uuid.UUID], idx, item_tags: list[str]
) -> list[dict[str, Any]]:
    if not ids:
        return []
    recipes = {
        r.id: r
        for r in (await session.exec(select(Recipe).where(Recipe.id.in_(ids)))).scalars().all()
    }
    slots_by_recipe: dict[uuid.UUID, list[RecipeSlot]] = defaultdict(list)
    for s in (
        await session.exec(select(RecipeSlot).where(RecipeSlot.recipe_id.in_(ids)))
    ).scalars().all():
        slots_by_recipe[s.recipe_id].append(s)

    out = []
    for rid in ids:
        r = recipes.get(rid)
        if not r:
            continue
        groups: dict[str, list[dict]] = {"input": [], "output": [], "aux_input": []}
        for s in sorted(slots_by_recipe[rid], key=lambda s: s.slot_order):
            if not (s.item_appearance_id or s.tag_string or s.fluid_label):
                continue  # unparseable ingredient from import
            it = idx.by_app_id.get(s.item_appearance_id) if s.item_appearance_id else None
            if it:
                name, item_id = it["name"], it["item_id"]
            elif s.is_fluid:
                name, item_id = pretty_name(s.fluid_label or "fluid"), None
            else:
                name, item_id = pretty_name(s.tag_string or "?"), None
            groups[s.slot_type].append({
                "item_id": item_id,
                "name": name,
                "tag": s.tag_string,
                "quantity": s.quantity,
                "is_fluid": s.is_fluid,
                "fluid_label": s.fluid_label,
                "via_tag": bool(s.tag_string and s.tag_string in item_tags),
            })
        m = idx.machines_by_id.get(r.machine_identity_id)
        out.append({
            "id": str(r.id),
            "recipe_type": r.recipe_type,
            "namespace": r.recipe_type.split(":")[0] if ":" in r.recipe_type else "minecraft",
            "machine": {"id": str(r.machine_identity_id), "name": m["name"] if m else r.recipe_type},
            "inputs": groups["input"],
            "outputs": groups["output"],
            "aux_inputs": groups["aux_input"],
            "time_ticks": r.time_ticks,
            "energy_rf": r.energy_rf,
            "modpack_override": r.source_priority >= 2,
        })
    return out


# ── Mods / machines / tags ────────────────────────────────────────────────────

@router.get("/mods")
async def list_mods(
    session: AsyncSession = Depends(get_session),
    q: str | None = None,
    limit: int = Query(default=60, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    idx = await get_index(session)
    rows = [m for m in idx.mods if m["item_count"] or m["recipe_count"]]
    if q:
        n = q.lower()
        rows = [m for m in rows if n in m["name"].lower() or n in m["namespace"]]
    rows.sort(key=lambda m: (-(m["item_count"] + m["recipe_count"]), m["name"]))
    return _page(rows, limit, offset)


@router.get("/mods/{namespace}")
async def get_mod(namespace: str, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    idx = await get_index(session)
    mod = idx.mods_by_ns.get(namespace)
    if not mod:
        raise HTTPException(404, "Mod not found")
    return {
        **mod,
        "machines": [m for m in idx.machines if m["namespace"] == namespace],
    }


@router.get("/machines")
async def list_machines(
    session: AsyncSession = Depends(get_session),
    q: str | None = None,
    mod: str | None = None,
) -> dict[str, Any]:
    idx = await get_index(session)
    rows = idx.machines
    if q:
        rows = [m for m in rows if q.lower() in m["name"].lower()]
    if mod:
        rows = [m for m in rows if m["namespace"] == mod]
    return {"total": len(rows), "items": sorted(rows, key=lambda m: -m["recipe_count"])}


@router.get("/machines/{machine_id}")
async def get_machine(machine_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    idx = await get_index(session)
    m = idx.machines_by_id.get(machine_id)
    if not m:
        raise HTTPException(404, "Machine not found")
    return m


@router.get("/tags")
async def list_tags(
    session: AsyncSession = Depends(get_session),
    q: str | None = None,
    limit: int = Query(default=60, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    idx = await get_index(session)
    rows = idx.tags
    if q:
        rows = [t for t in rows if q.lower() in t["id"].lower()]
    rows = sorted(rows, key=lambda t: (-t["recipe_count"], t["id"]))
    return _page(rows, limit, offset)


@router.get("/tag")
async def get_tag(id: str = Query(...), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    idx = await get_index(session)
    tag = idx.tags_by_id.get(id)
    if not tag:
        raise HTTPException(404, "Tag not found")
    members = idx.filter_items(tag=id, sort="name")
    return {**tag, "members": members}

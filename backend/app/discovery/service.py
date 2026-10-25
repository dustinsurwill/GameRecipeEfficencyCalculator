"""
Discovery queries shared by the JSON API and the HTML pages.

Everything takes a Scope and its DiscoveryIndex. Item/mod/machine/tag data comes from the
index (memory); only recipe lookup and hydration touch the database.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any, Literal

from sqlalchemy import func, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.discovery.index import DiscoveryIndex, pretty_name
from app.discovery.scope import Scope
from app.models.recipe import Recipe, RecipeSlot
from app.models.status import RecipeStatus

Role = Literal["output", "input"]


class NotFound(Exception):
    pass


def page(rows: list[Any], limit: int, offset: int) -> dict[str, Any]:
    return {"total": len(rows), "limit": limit, "offset": offset, "items": rows[offset : offset + limit]}


def _scoped(scope: Scope):
    return Recipe.id.in_(scope.recipe_ids()) & (Recipe.status == RecipeStatus.APPROVED)


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


def get_item(idx: DiscoveryIndex, item_id: str) -> dict[str, Any]:
    item = idx.by_item_id.get(item_id)
    if not item:
        raise NotFound("Item not found")
    return item


async def item_detail(session: AsyncSession, scope: Scope, idx: DiscoveryIndex, item_id: str) -> dict[str, Any]:
    item = get_item(idx, item_id)
    return {
        **item,
        "tags": [
            {"id": t, "member_count": idx.tags_by_id[t]["member_count"] if t in idx.tags_by_id else 0}
            for t in item["tags"]
        ],
        "machines": {
            "made_in": await machine_facets(session, scope, idx, item_id, "output"),
            "used_in": await machine_facets(session, scope, idx, item_id, "input"),
        },
    }


async def machine_facets(
    session: AsyncSession, scope: Scope, idx: DiscoveryIndex, item_id: str, role: Role
) -> list[dict[str, Any]]:
    item = idx.by_item_id[item_id]
    sub = _recipe_ids_for_item(idx.app_ids[item_id], role, item["tags"]).scalar_subquery()
    rows = await session.exec(
        select(Recipe.machine_identity_id, func.count(func.distinct(Recipe.id)))
        .where(Recipe.id.in_(sub), _scoped(scope))
        .group_by(Recipe.machine_identity_id)
    )
    out = []
    for mid, n in rows.all():
        m = idx.machines_by_id.get(mid)
        if m:
            out.append({"id": str(mid), "name": m["name"], "namespace": m["namespace"], "count": n})
    return sorted(out, key=lambda x: -x["count"])


def _input_count():
    """Fewest-ingredient recipes first — the simple, direct ways to get something."""
    return (
        select(func.count())
        .where(RecipeSlot.recipe_id == Recipe.id, RecipeSlot.slot_type == "input")
        .scalar_subquery()
    )


async def list_recipes(
    session: AsyncSession, scope: Scope, idx: DiscoveryIndex, *,
    item: str | None = None, role: Role = "output", machine: uuid.UUID | None = None,
    mod: str | None = None, limit: int = 12, offset: int = 0,
) -> dict[str, Any]:
    q = select(Recipe.id).where(_scoped(scope))
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
            it = get_item(idx, item)
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
    return {"total": total, "limit": limit, "offset": offset,
            "items": await _hydrate(session, list(ids), idx, item_tags)}


async def _hydrate(
    session: AsyncSession, ids: list[uuid.UUID], idx: DiscoveryIndex, item_tags: list[str]
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


# ── Index-only queries ───────────────────────────────────────────────────────

def overview(idx: DiscoveryIndex) -> dict[str, Any]:
    mods = [m for m in idx.mods if m["item_count"] or m["recipe_count"]]
    return {
        "counts": {
            "items": len(idx.items), "recipes": idx.recipe_total, "mods": len(mods),
            "machines": len(idx.machines), "tags": len(idx.tags),
        },
        "top_mods": sorted(mods, key=lambda m: -m["recipe_count"])[:12],
        "top_machines": sorted(idx.machines, key=lambda m: -m["recipe_count"])[:12],
        "popular_items": sorted(idx.items, key=lambda i: -(i["made_by"] + i["used_in"]))[:16],
    }


def search(idx: DiscoveryIndex, q: str, items: int = 8, others: int = 4) -> dict[str, Any]:
    needle = q.strip().lower()
    return {
        "items": idx.search_items(needle, items),
        "mods": idx.search_mods(needle, others),
        "machines": idx.search_machines(needle, others),
        "tags": idx.search_tags(needle, others),
    }


def list_mods(idx: DiscoveryIndex, q: str | None = None) -> list[dict[str, Any]]:
    rows = [m for m in idx.mods if m["item_count"] or m["recipe_count"]]
    if q:
        n = q.lower()
        rows = [m for m in rows if n in m["name"].lower() or n in m["namespace"]]
    rows.sort(key=lambda m: (-(m["item_count"] + m["recipe_count"]), m["name"]))
    return rows


def get_mod(idx: DiscoveryIndex, namespace: str) -> dict[str, Any]:
    mod = idx.mods_by_ns.get(namespace)
    if not mod:
        raise NotFound("Mod not found")
    return {**mod, "machines": [m for m in idx.machines if m["namespace"] == namespace]}


def list_machines(idx: DiscoveryIndex, q: str | None = None, mod: str | None = None) -> list[dict[str, Any]]:
    rows = idx.machines
    if q:
        rows = [m for m in rows if q.lower() in m["name"].lower()]
    if mod:
        rows = [m for m in rows if m["namespace"] == mod]
    return sorted(rows, key=lambda m: -m["recipe_count"])


def get_machine(idx: DiscoveryIndex, machine_id: uuid.UUID) -> dict[str, Any]:
    m = idx.machines_by_id.get(machine_id)
    if not m:
        raise NotFound("Machine not found")
    return m


def list_tags(idx: DiscoveryIndex, q: str | None = None) -> list[dict[str, Any]]:
    rows = idx.tags
    if q:
        rows = [t for t in rows if q.lower() in t["id"].lower()]
    return sorted(rows, key=lambda t: (-t["recipe_count"], t["id"]))


def get_tag(idx: DiscoveryIndex, tag_id: str) -> dict[str, Any]:
    tag = idx.tags_by_id.get(tag_id)
    if not tag:
        raise NotFound("Tag not found")
    return {**tag, "members": idx.filter_items(tag=tag_id, sort="name")}

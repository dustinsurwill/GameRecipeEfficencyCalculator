"""
In-memory discovery index, one per scope (game + modpack + version).

Items, mods, machines and tags are small (tens of thousands of rows) and read far
more often than they change (only on import), so they are loaded once and served
from memory with a short TTL. Recipe hydration still goes to the database.

At most MAX_INDEXES scopes are held; the oldest-built one is dropped, so memory
stays bounded no matter how many games, packs and versions exist.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app import memory
from app.discovery.scope import Scope
from app.models.identity import ItemAppearance, ItemIdentity, MachineIdentity
from app.models.recipe import Recipe, RecipeSlot
from app.models.status import RecipeStatus
from app.models.versioning import Mod, ModVersion

TTL_SECONDS = 300
MAX_INDEXES = 2


def pretty_name(ident: str) -> str:
    """'mekanism:dust_gold' / 'c:ingots/iron' -> 'Dust Gold' / 'Iron'-ish display text."""
    path = ident.split(":", 1)[-1]
    return path.replace("/", " / ").replace("_", " ").title()


def item_kind(item_id: str) -> str:
    """'item', 'fluid' or 'chemical' (fluids/chemicals are stored as 'fluid:ns:name' / 'chemical:ns:name')."""
    return item_id.split(":", 1)[0] if item_id.startswith(("fluid:", "chemical:")) else "item"


def _namespace_of(recipe_type: str) -> str:
    return recipe_type.split(":")[0] if ":" in recipe_type else "minecraft"


class DiscoveryIndex:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.by_item_id: dict[str, dict[str, Any]] = {}
        self.by_app_id: dict[uuid.UUID, dict[str, Any]] = {}
        self.app_ids: dict[str, uuid.UUID] = {}
        self.mods: list[dict[str, Any]] = []
        self.mods_by_ns: dict[str, dict[str, Any]] = {}
        self.machines: list[dict[str, Any]] = []
        self.machines_by_id: dict[uuid.UUID, dict[str, Any]] = {}
        self.tags: list[dict[str, Any]] = []
        self.tags_by_id: dict[str, dict[str, Any]] = {}
        self.recipe_total = 0
        self.scope: Scope | None = None
        self._keys: list[tuple[str, str]] = []   # (lower name, lower item_id), parallel to items

    # ── Build ────────────────────────────────────────────────────────────────

    @classmethod
    async def build(cls, session: AsyncSession, scope: Scope) -> "DiscoveryIndex":
        ix = cls()
        ix.scope = scope
        in_scope = Recipe.id.in_(scope.recipe_ids()) & (Recipe.status == RecipeStatus.APPROVED)
        slot_in_scope = RecipeSlot.recipe_id.in_(select(Recipe.id).where(in_scope))

        mod_rows = (await session.exec(
            select(Mod).where(Mod.id.in_(
                select(ModVersion.mod_id).where(ModVersion.id.in_(scope.mod_version_ids))
            ))
        )).scalars().all()
        mod_name = {m.namespace: m.name for m in mod_rows}

        # recipe counts
        ix.recipe_total = (await session.exec(
            select(func.count()).select_from(Recipe).where(in_scope)
        )).scalar_one()
        type_counts = (await session.exec(
            select(Recipe.recipe_type, func.count()).where(in_scope).group_by(Recipe.recipe_type)
        )).all()
        recipes_per_ns: dict[str, int] = {}
        for rtype, n in type_counts:
            ns = _namespace_of(rtype)
            recipes_per_ns[ns] = recipes_per_ns.get(ns, 0) + n

        machine_counts = dict((await session.exec(
            select(Recipe.machine_identity_id, func.count()).where(in_scope).group_by(Recipe.machine_identity_id)
        )).all())

        # per-appearance recipe counts
        made_by = dict((await session.exec(
            select(RecipeSlot.item_appearance_id, func.count(func.distinct(RecipeSlot.recipe_id)))
            .where(slot_in_scope, RecipeSlot.slot_type == "output", RecipeSlot.item_appearance_id.is_not(None))
            .group_by(RecipeSlot.item_appearance_id)
        )).all())
        used_direct = dict((await session.exec(
            select(RecipeSlot.item_appearance_id, func.count(func.distinct(RecipeSlot.recipe_id)))
            .where(slot_in_scope, RecipeSlot.slot_type.in_(("input", "aux_input")), RecipeSlot.item_appearance_id.is_not(None))
            .group_by(RecipeSlot.item_appearance_id)
        )).all())
        tag_recipe_counts = dict((await session.exec(
            select(RecipeSlot.tag_string, func.count(func.distinct(RecipeSlot.recipe_id)))
            .where(slot_in_scope, RecipeSlot.tag_string.is_not(None), RecipeSlot.slot_type.in_(("input", "aux_input")))
            .group_by(RecipeSlot.tag_string)
        )).all())

        # items
        rows = (await session.exec(
            select(ItemAppearance, ItemIdentity.canonical_name)
            .join(ItemIdentity, ItemIdentity.id == ItemAppearance.identity_id)
            .where(ItemAppearance.mod_version_id.in_(scope.mod_version_ids))
        )).all()
        tag_members: dict[str, int] = {}
        items_per_ns: dict[str, int] = {}
        for app, name in rows:
            tags = json.loads(app.tags_json) if app.tags_json else []
            for t in tags:
                tag_members[t] = tag_members.get(t, 0) + 1
            items_per_ns[app.namespace] = items_per_ns.get(app.namespace, 0) + 1
            item = {
                "item_id": app.item_id,
                "name": name,
                "namespace": app.namespace,
                "mod_name": mod_name.get(app.namespace, pretty_name(app.namespace)),
                "tags": tags,
                "kind": item_kind(app.item_id),
                "made_by": made_by.get(app.id, 0),
                "used_in": used_direct.get(app.id, 0) + sum(tag_recipe_counts.get(t, 0) for t in tags),
            }
            ix.items.append(item)
            ix.by_item_id[app.item_id] = item
            ix.by_app_id[app.id] = item
            ix.app_ids[app.item_id] = app.id
        ix.items.sort(key=lambda i: i["name"].lower())
        ix._keys = [(i["name"].lower(), i["item_id"].lower()) for i in ix.items]

        # mods
        for m in mod_rows:
            ix.mods.append({
                "namespace": m.namespace,
                "name": m.name,
                "description": m.description,
                "id": str(m.id),
                "item_count": items_per_ns.get(m.namespace, 0),
                "recipe_count": recipes_per_ns.get(m.namespace, 0),
            })
        ix.mods_by_ns = {m["namespace"]: m for m in ix.mods}

        # machines
        for mc in (await session.exec(
            select(MachineIdentity).where(MachineIdentity.game_id == scope.game_id)
        )).scalars().all():
            types = json.loads(mc.recipe_types_json or "[]")
            ns = _namespace_of(types[0]) if types else "minecraft"
            row = {
                "id": str(mc.id),
                "name": mc.name,
                "namespace": ns,
                "mod_name": mod_name.get(ns, pretty_name(ns)),
                "recipe_types": types,
                "recipe_count": machine_counts.get(mc.id, 0),
            }
            ix.machines.append(row)
            ix.machines_by_id[mc.id] = row

        # tags
        for t in set(tag_members) | set(tag_recipe_counts):
            ix.tags.append({
                "id": t,
                "member_count": tag_members.get(t, 0),
                "recipe_count": tag_recipe_counts.get(t, 0),
            })
        ix.tags_by_id = {t["id"]: t for t in ix.tags}
        # machines with no recipes in this scope belong to other packs/versions
        ix.machines = [m for m in ix.machines if m["recipe_count"]]
        ix.machines_by_id = {uuid.UUID(m["id"]): m for m in ix.machines}
        return ix

    # ── Queries ──────────────────────────────────────────────────────────────

    def filter_items(
        self, *, q: str | None = None, mod: str | None = None, tag: str | None = None,
        craftable: bool | None = None, sort: str = "name", kind: str | None = None,
    ) -> list[dict[str, Any]]:
        needle = q.strip().lower() if q else ""
        out = []
        for item, (lname, lid) in zip(self.items, self._keys):
            if kind and item["kind"] != kind:
                continue
            if mod and item["namespace"] != mod:
                continue
            if tag and tag not in item["tags"]:
                continue
            if craftable is True and not item["made_by"]:
                continue
            if craftable is False and item["made_by"]:
                continue
            if needle and needle not in lname and needle not in lid:
                continue
            out.append(item)
        if sort == "made_by":
            out.sort(key=lambda i: (-i["made_by"], i["name"].lower()))
        elif sort == "used_in":
            out.sort(key=lambda i: (-i["used_in"], i["name"].lower()))
        return out

    def search_items(self, needle: str, limit: int) -> list[dict[str, Any]]:
        scored: list[tuple[int, int, dict]] = []
        for item, (lname, lid) in zip(self.items, self._keys):
            if lname == needle:
                s = 0
            elif lname.startswith(needle):
                s = 1
            elif f" {needle}" in lname:
                s = 2
            elif needle in lname:
                s = 3
            elif needle in lid:
                s = 4
            else:
                continue
            scored.append((s, -(item["made_by"] + item["used_in"]), item))
        scored.sort(key=lambda x: (x[0], x[1]))
        return [x[2] for x in scored[:limit]]

    def search_mods(self, needle: str, limit: int) -> list[dict[str, Any]]:
        hits = [m for m in self.mods if (m["item_count"] or m["recipe_count"])
                and (needle in m["name"].lower() or needle in m["namespace"])]
        hits.sort(key=lambda m: (not m["name"].lower().startswith(needle), -m["item_count"]))
        return hits[:limit]

    def search_machines(self, needle: str, limit: int) -> list[dict[str, Any]]:
        hits = [m for m in self.machines if needle in m["name"].lower()]
        hits.sort(key=lambda m: -m["recipe_count"])
        return hits[:limit]

    def search_tags(self, needle: str, limit: int) -> list[dict[str, Any]]:
        hits = [t for t in self.tags if needle in t["id"].lower()]
        hits.sort(key=lambda t: (-t["recipe_count"], t["id"]))
        return hits[:limit]


# ── Cache ────────────────────────────────────────────────────────────────────

_indexes: dict[uuid.UUID, tuple[float, DiscoveryIndex]] = {}   # scope key -> (built_at, index)
_lock = asyncio.Lock()


async def get_index(session: AsyncSession, scope: Scope) -> DiscoveryIndex:
    hit = _indexes.get(scope.key)
    if hit and time.monotonic() - hit[0] < TTL_SECONDS:
        return hit[1]
    async with _lock:
        hit = _indexes.get(scope.key)
        if hit and time.monotonic() - hit[0] < TTL_SECONDS:
            return hit[1]
        ix = await DiscoveryIndex.build(session, scope)
        _indexes[scope.key] = (time.monotonic(), ix)
        while len(_indexes) > MAX_INDEXES:
            del _indexes[min(_indexes, key=lambda k: _indexes[k][0])]
        memory.trim()
        return ix


def invalidate() -> None:
    _indexes.clear()

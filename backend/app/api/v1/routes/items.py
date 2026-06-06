"""
Item lookup endpoints — single-hop queries analogous to JEI R/U keys.

  GET /items/search                  — fuzzy search by name or item ID
  GET /items/{item_id}/producers     — recipes that produce this item
  GET /items/{item_id}/consumers     — recipes that consume this item

item_id is a concrete ID like "mekanism:dust_gold" or a tag prefixed with
"#" like "#c:ingots/gold" (URL-encode # as %23 in path segments).
"""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import get_session
from app.models.identity import ItemAppearance, ItemIdentity, MachineIdentity
from app.models.recipe import Recipe, RecipeModVersionLink, RecipeSlot
from app.models.status import RecipeStatus
from app.models.versioning import ModPackVersion, ModPackModVersion

router = APIRouter(prefix="/items", tags=["items"])


# ── Shared helpers ────────────────────────────────────────────────────────────

async def _mod_version_ids_for_modpack(
    session: AsyncSession, modpack_id: uuid.UUID
) -> set[uuid.UUID]:
    result = await session.exec(
        select(ModPackModVersion).where(ModPackModVersion.modpack_id == modpack_id)
    )
    return {link.mod_version_id for link in result.scalars().all()}


async def _resolve_item_id(
    item_id: str,
    mod_version_ids: set[uuid.UUID] | None,
    session: AsyncSession,
) -> tuple[list[uuid.UUID], str | None]:
    """
    Returns (appearance_ids, resolved_from_tag).
    If item_id starts with '#' resolves via tags_json, otherwise by exact item_id.
    mod_version_ids=None means no scope filter.
    """
    if item_id.startswith("#"):
        tag = item_id[1:]
        q = select(ItemAppearance)
        if mod_version_ids is not None:
            q = q.where(ItemAppearance.mod_version_id.in_(mod_version_ids))
        result = await session.exec(q)
        matched = [
            a for a in result.scalars().all()
            if tag in (json.loads(a.tags_json) if a.tags_json else [])
        ]
        return [a.id for a in matched], tag
    else:
        q = select(ItemAppearance).where(ItemAppearance.item_id == item_id)
        if mod_version_ids is not None:
            q = q.where(ItemAppearance.mod_version_id.in_(mod_version_ids))
        result = await session.exec(q)
        return [a.id for a in result.scalars().all()], None


async def _build_recipe_responses(
    session: AsyncSession,
    recipe_ids: set[uuid.UUID],
    mod_version_ids: set[uuid.UUID] | None,
) -> list[dict[str, Any]]:
    if not recipe_ids:
        return []

    # Load appearances for item_id resolution
    app_q = select(ItemAppearance)
    if mod_version_ids is not None:
        app_q = app_q.where(ItemAppearance.mod_version_id.in_(mod_version_ids))
    app_result = await session.exec(app_q)
    app_id_to_item: dict[uuid.UUID, str] = {a.id: a.item_id for a in app_result.scalars().all()}

    machines_result = await session.exec(select(MachineIdentity))
    machine_map: dict[uuid.UUID, str] = {m.id: m.name for m in machines_result.scalars().all()}

    recipes_result = await session.exec(select(Recipe).where(Recipe.id.in_(recipe_ids)))
    db_recipes = {r.id: r for r in recipes_result.scalars().all()}

    slots_result = await session.exec(
        select(RecipeSlot).where(RecipeSlot.recipe_id.in_(recipe_ids))
    )
    slots_by_recipe: dict[uuid.UUID, list[RecipeSlot]] = defaultdict(list)
    for s in slots_result.scalars().all():
        slots_by_recipe[s.recipe_id].append(s)

    result_list: list[dict[str, Any]] = []
    for rid in recipe_ids:
        r = db_recipes.get(rid)
        if not r:
            continue
        rt = r.recipe_type
        inputs: list[dict] = []
        outputs: list[dict] = []
        aux: list[dict] = []
        for sl in sorted(slots_by_recipe[rid], key=lambda s: s.slot_order):
            item_id = app_id_to_item.get(sl.item_appearance_id) if sl.item_appearance_id else None
            slot_dict = {
                "tag": sl.tag_string,
                "item_id": item_id,
                "quantity": sl.quantity,
                "is_fluid": sl.is_fluid,
            }
            if sl.slot_type == "output":
                outputs.append(slot_dict)
            elif sl.slot_type == "input":
                inputs.append(slot_dict)
            else:
                aux.append(slot_dict)
        result_list.append({
            "id": str(rid),
            "machine": {
                "name": machine_map.get(r.machine_identity_id, rt),
                "recipe_type": rt,
                "mod_namespace": rt.split(":")[0] if ":" in rt else rt,
            },
            "inputs": inputs,
            "outputs": outputs,
            "aux_inputs": aux,
            "time_ticks": r.time_ticks,
            "energy_rf": r.energy_rf,
            "source_priority": r.source_priority,
        })
    return result_list


# ── Search ────────────────────────────────────────────────────────────────────

@router.get("/search")
async def search_items(
    session: AsyncSession = Depends(get_session),
    q: str = Query(..., min_length=2),
    modpack_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    mod_version_ids: set[uuid.UUID] | None = None
    if modpack_id:
        mp = await session.get(ModPackVersion, modpack_id)
        if not mp:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "ModPack version not found")
        mod_version_ids = await _mod_version_ids_for_modpack(session, modpack_id)

    # Search by item_id fragment
    app_q = select(ItemAppearance).where(ItemAppearance.item_id.icontains(q))
    if mod_version_ids is not None:
        app_q = app_q.where(ItemAppearance.mod_version_id.in_(mod_version_ids))
    app_result = await session.exec(app_q.limit(limit))
    appearances = list(app_result.scalars().all())

    # Also search by canonical_name via ItemIdentity join
    id_result = await session.exec(
        select(ItemIdentity).where(ItemIdentity.canonical_name.icontains(q)).limit(limit)
    )
    name_identities = {i.id: i for i in id_result.scalars().all()}

    if name_identities:
        extra_app_q = select(ItemAppearance).where(
            ItemAppearance.identity_id.in_(name_identities.keys())
        )
        if mod_version_ids is not None:
            extra_app_q = extra_app_q.where(ItemAppearance.mod_version_id.in_(mod_version_ids))
        extra_result = await session.exec(extra_app_q.limit(limit))
        seen_ids = {a.id for a in appearances}
        for a in extra_result.scalars().all():
            if a.id not in seen_ids:
                appearances.append(a)
                seen_ids.add(a.id)

    # Deduplicate by item_id (keep first occurrence per item_id)
    seen_item_ids: set[str] = set()
    unique: list[ItemAppearance] = []
    for a in appearances:
        if a.item_id not in seen_item_ids:
            unique.append(a)
            seen_item_ids.add(a.item_id)
    unique = unique[:limit]

    identity_ids = {a.identity_id for a in unique}
    identity_result = await session.exec(
        select(ItemIdentity).where(ItemIdentity.id.in_(identity_ids))
    )
    identity_map = {i.id: i for i in identity_result.scalars().all()}

    results = []
    for a in unique:
        identity = identity_map.get(a.identity_id)
        results.append({
            "item_id": a.item_id,
            "canonical_name": identity.canonical_name if identity else None,
            "identity_id": str(a.identity_id),
            "mod_namespace": a.namespace,
            "tags": json.loads(a.tags_json) if a.tags_json else [],
        })
    return {"results": results}


# ── Producers / Consumers ─────────────────────────────────────────────────────

async def _producers_or_consumers(
    item_id: str,
    slot_type: str,
    session: AsyncSession,
    modpack_id: uuid.UUID | None,
    recipe_status: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    mod_version_ids: set[uuid.UUID] | None = None
    if modpack_id:
        mp = await session.get(ModPackVersion, modpack_id)
        if not mp:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "ModPack version not found")
        mod_version_ids = await _mod_version_ids_for_modpack(session, modpack_id)

    appearance_ids, resolved_from_tag = await _resolve_item_id(item_id, mod_version_ids, session)

    if not appearance_ids and not item_id.startswith("#"):
        return {"item_id": item_id, "resolved_from_tag": None, "total": 0, "recipes": []}

    # Find matching recipe slots
    if item_id.startswith("#"):
        tag = item_id[1:]
        if appearance_ids:
            slot_q = select(RecipeSlot).where(
                RecipeSlot.slot_type == slot_type,
                or_(
                    RecipeSlot.item_appearance_id.in_(appearance_ids),
                    RecipeSlot.tag_string == tag,
                ),
            )
        else:
            slot_q = select(RecipeSlot).where(
                RecipeSlot.slot_type == slot_type,
                RecipeSlot.tag_string == tag,
            )
    else:
        slot_q = select(RecipeSlot).where(
            RecipeSlot.slot_type == slot_type,
            RecipeSlot.item_appearance_id.in_(appearance_ids),
        )

    slots_result = await session.exec(slot_q)
    candidate_recipe_ids: set[uuid.UUID] = {s.recipe_id for s in slots_result.scalars().all()}

    # Scope to modpack
    if mod_version_ids is not None and candidate_recipe_ids:
        links_result = await session.exec(
            select(RecipeModVersionLink).where(
                RecipeModVersionLink.mod_version_id.in_(mod_version_ids)
            )
        )
        scoped = {lnk.recipe_id for lnk in links_result.scalars().all()}
        candidate_recipe_ids &= scoped

    # Filter by status
    if candidate_recipe_ids:
        status_result = await session.exec(
            select(Recipe.id).where(
                Recipe.id.in_(candidate_recipe_ids),
                Recipe.status == recipe_status,
            )
        )
        candidate_recipe_ids = set(status_result.scalars().all())

    total = len(candidate_recipe_ids)
    page_ids = set(list(candidate_recipe_ids)[offset : offset + limit])
    recipes = await _build_recipe_responses(session, page_ids, mod_version_ids)

    return {
        "item_id": item_id,
        "resolved_from_tag": resolved_from_tag,
        "total": total,
        "recipes": recipes,
    }


@router.get("/{item_id}/producers")
async def get_producers(
    item_id: str,
    session: AsyncSession = Depends(get_session),
    modpack_id: uuid.UUID | None = Query(default=None),
    recipe_status: str = Query(default=RecipeStatus.APPROVED, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return await _producers_or_consumers(
        item_id, "output", session, modpack_id, recipe_status, limit, offset
    )


@router.get("/{item_id}/consumers")
async def get_consumers(
    item_id: str,
    session: AsyncSession = Depends(get_session),
    modpack_id: uuid.UUID | None = Query(default=None),
    recipe_status: str = Query(default=RecipeStatus.APPROVED, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return await _producers_or_consumers(
        item_id, "input", session, modpack_id, recipe_status, limit, offset
    )

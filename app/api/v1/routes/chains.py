"""
Chain traversal endpoint.

Find all recipe sequences that transform a starting item/tag into a target
item/tag, ranked by output quantity per single unit of input.

Works for any item, not just ores→ingots. Examples:
  raw_ore → ingot, dust → plate, ore → gem, log → planks, etc.

  GET /chains?modpack_slug=atm10&modpack_version=0.18.0
             &start_tag=c:ores/gold&target_tag=c:ingots/gold
  GET /chains?modpack_id=<uuid>&start_item=minecraft:gold_ore&target_tag=c:ingots/gold
  GET /chains?modpack_slug=atm10&modpack_version=0.18.0
             &start_tag=c:dusts/iron&target_item=minecraft:iron_ingot

Query parameters:
  Modpack (one required):
    modpack_id                       — UUID of the modpack record
    modpack_slug + modpack_version   — slug/version pair

  Start (one required):
    start_tag    — tag that resolves to starting items, e.g. "c:ores/gold"
    start_item   — concrete item_id to start from, e.g. "minecraft:gold_ore"

  Target (one required):
    target_tag   — chain succeeds when any output belongs to this tag
    target_item  — chain succeeds when this specific item_id is produced

  Optional:
    max_depth    — maximum chain length (default 7, max 12)
    min_yield    — only return chains with output_quantity >= this (default 0)
"""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import get_session
from app.models.identity import ItemAppearance, MachineIdentity
from app.models.recipe import Recipe, RecipeModVersionLink, RecipeSlot
from app.models.versioning import ModPack, ModPackModVersion

router = APIRouter(prefix="/chains", tags=["chains"])


# ── In-memory graph structures ────────────────────────────────────────────────

@dataclass
class _Slot:
    item_id: str | None
    tag_string: str | None
    quantity: float
    is_fluid: bool
    fluid_label: str | None = None


@dataclass
class _Recipe:
    id: uuid.UUID
    recipe_type: str
    machine_name: str
    import_key: str
    inputs: list[_Slot] = field(default_factory=list)
    outputs: list[_Slot] = field(default_factory=list)
    aux_inputs: list[_Slot] = field(default_factory=list)


# ── DB data loading ───────────────────────────────────────────────────────────

async def _load_recipe_graph(
    session: AsyncSession,
    modpack_id: uuid.UUID,
) -> tuple[list[_Recipe], dict[str, set[str]], dict[str, set[str]]]:
    """
    Load all recipes active in the given modpack from the DB.

    Returns:
      recipes       — in-memory recipe objects with slots populated
      tag_to_items  — tag string → set of item_ids
      item_to_tags  — item_id → set of tags it belongs to
    """
    mod_version_result = await session.exec(
        select(ModPackModVersion).where(ModPackModVersion.modpack_id == modpack_id)
    )
    mod_version_ids = {link.mod_version_id for link in mod_version_result.scalars().all()}

    if not mod_version_ids:
        return [], {}, {}

    appearance_result = await session.exec(
        select(ItemAppearance).where(ItemAppearance.mod_version_id.in_(mod_version_ids))
    )
    tag_to_items: dict[str, set[str]] = defaultdict(set)
    item_to_tags: dict[str, set[str]] = defaultdict(set)
    appearance_id_to_item: dict[uuid.UUID, str] = {}

    for app in appearance_result.scalars().all():
        appearance_id_to_item[app.id] = app.item_id
        for tag in (json.loads(app.tags_json) if app.tags_json else []):
            tag_to_items[tag].add(app.item_id)
            item_to_tags[app.item_id].add(tag)

    link_result = await session.exec(
        select(RecipeModVersionLink).where(
            RecipeModVersionLink.mod_version_id.in_(mod_version_ids)
        )
    )
    recipe_ids = {link.recipe_id for link in link_result.scalars().all()}

    if not recipe_ids:
        return [], dict(tag_to_items), dict(item_to_tags)

    machine_result = await session.exec(select(MachineIdentity))
    machine_id_to_name: dict[uuid.UUID, str] = {
        m.id: m.name for m in machine_result.scalars().all()
    }

    recipe_result = await session.exec(
        select(Recipe).where(Recipe.id.in_(recipe_ids))
    )
    db_recipes = list(recipe_result.scalars().all())

    slot_result = await session.exec(
        select(RecipeSlot).where(RecipeSlot.recipe_id.in_(recipe_ids))
    )
    slots_by_recipe: dict[uuid.UUID, list[RecipeSlot]] = defaultdict(list)
    for slot in slot_result.scalars().all():
        slots_by_recipe[slot.recipe_id].append(slot)

    recipes: list[_Recipe] = []
    for db_r in db_recipes:
        r = _Recipe(
            id=db_r.id,
            recipe_type=db_r.recipe_type,
            machine_name=machine_id_to_name.get(db_r.machine_identity_id, db_r.recipe_type),
            import_key=db_r.import_key,
        )
        for db_slot in sorted(slots_by_recipe[db_r.id], key=lambda s: s.slot_order):
            item_id = appearance_id_to_item.get(db_slot.item_appearance_id) \
                if db_slot.item_appearance_id else None
            slot = _Slot(
                item_id=item_id,
                tag_string=db_slot.tag_string,
                quantity=db_slot.quantity,
                is_fluid=db_slot.is_fluid,
                fluid_label=db_slot.fluid_label,
            )
            target = r.inputs if db_slot.slot_type == "input" \
                else (r.outputs if db_slot.slot_type == "output" else r.aux_inputs)
            target.append(slot)
        recipes.append(r)

    return recipes, dict(tag_to_items), dict(item_to_tags)


# ── Graph index ───────────────────────────────────────────────────────────────

class _RecipeGraph:
    def __init__(
        self,
        recipes: list[_Recipe],
        tag_to_items: dict[str, set[str]],
    ):
        self.tag_to_items = tag_to_items
        self._by_input: dict[str, list[_Recipe]] = defaultdict(list)
        self._by_output: dict[str, list[_Recipe]] = defaultdict(list)

        for r in recipes:
            for slot in r.outputs:
                if slot.item_id:
                    self._by_output[slot.item_id].append(r)

            primary = next((s for s in r.inputs if not s.is_fluid), None)
            if primary:
                if primary.item_id:
                    self._by_input[primary.item_id].append(r)
                elif primary.tag_string:
                    for item_id in tag_to_items.get(primary.tag_string, set()):
                        self._by_input[item_id].append(r)

    def recipes_consuming(self, item_id: str) -> list[_Recipe]:
        return self._by_input.get(item_id, [])


# ── DFS chain finder ──────────────────────────────────────────────────────────

def _find_chains(
    start_items: list[str],
    target_items: set[str],       # concrete item_ids that count as "success"
    graph: _RecipeGraph,
    max_depth: int,
) -> list[list[tuple[_Recipe, float, float]]]:
    """
    DFS from start_items until any current item is in target_items.
    Returns a list of chains; each chain is [(recipe, input_qty, output_qty)].
    """
    all_chains: list[list[tuple[_Recipe, float, float]]] = []

    def dfs(
        current_items: list[str],
        current_count: float,
        steps: list[tuple[_Recipe, float, float]],
        depth: int,
        seen: frozenset[uuid.UUID],
    ) -> None:
        if depth > max_depth:
            return

        for ci in current_items:
            if ci in target_items:
                if steps:
                    all_chains.append(list(steps))
                return

        tried: set[uuid.UUID] = set()
        for ci in current_items:
            for recipe in graph.recipes_consuming(ci):
                if recipe.id in seen or recipe.id in tried:
                    continue
                tried.add(recipe.id)

                primary = next((s for s in recipe.inputs if not s.is_fluid), None)
                runs = current_count / (primary.quantity if primary else 1.0)

                next_items_counts = [
                    (out.item_id, out.quantity * runs)
                    for out in recipe.outputs
                    if out.item_id
                ]
                if not next_items_counts:
                    continue

                primary_item, primary_qty = next_items_counts[0]
                dfs(
                    current_items=[item for item, _ in next_items_counts],
                    current_count=primary_qty,
                    steps=steps + [(recipe, current_count, primary_qty)],
                    depth=depth + 1,
                    seen=seen | {recipe.id},
                )

    for item in start_items:
        dfs([item], 1.0, [], 0, frozenset())

    return all_chains


def _deduplicate(
    chains: list[list[tuple[_Recipe, float, float]]],
) -> list[list[tuple[_Recipe, float, float]]]:
    seen: dict[tuple, list[tuple[_Recipe, float, float]]] = {}
    for chain in chains:
        key = (
            tuple((r.recipe_type, r.import_key) for r, _, _ in chain),
            round(chain[-1][2], 4),
        )
        seen.setdefault(key, chain)
    return list(seen.values())


def _serialize_chain(chain: list[tuple[_Recipe, float, float]]) -> dict:
    chemicals: list[str] = []
    steps = []
    for recipe, in_qty, out_qty in chain:
        for aux in recipe.aux_inputs:
            if aux.fluid_label:
                chemicals.append(aux.fluid_label)
        steps.append({
            "machine": recipe.machine_name,
            "recipe_type": recipe.recipe_type,
            "recipe_id": str(recipe.id),
            "input_quantity": in_qty,
            "output_quantity": out_qty,
            "inputs": [
                {"item_id": s.item_id, "tag_string": s.tag_string, "quantity": s.quantity}
                for s in recipe.inputs if not s.is_fluid
            ],
            "outputs": [
                {"item_id": s.item_id, "quantity": s.quantity}
                for s in recipe.outputs if not s.is_fluid and s.item_id
            ],
            "aux_inputs": [
                {"label": s.fluid_label or s.item_id, "quantity": s.quantity}
                for s in recipe.aux_inputs
            ],
        })
    return {
        "yield_per_input": chain[-1][2],
        "step_count": len(chain),
        "machine_sequence": [r.machine_name for r, _, _ in chain],
        "chemicals_required": sorted(set(chemicals)),
        "steps": steps,
    }


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("")
async def get_chains(
    session: AsyncSession = Depends(get_session),
    # Modpack identification
    modpack_id: uuid.UUID | None = Query(default=None),
    modpack_slug: str | None = Query(default=None),
    modpack_version: str | None = Query(default=None),
    # Start node
    start_tag: str | None = Query(default=None, description="Tag resolving to start items, e.g. 'c:ores/gold'"),
    start_item: str | None = Query(default=None, description="Concrete item_id to start from, e.g. 'minecraft:gold_ore'"),
    # Target node
    target_tag: str | None = Query(default=None, description="Tag; chain succeeds when any output belongs to it"),
    target_item: str | None = Query(default=None, description="Specific item_id; chain succeeds when it is produced"),
    # Options
    max_depth: int = Query(default=7, ge=1, le=12),
    min_yield: float = Query(default=0.0, ge=0.0),
) -> dict[str, Any]:

    # ── Resolve modpack ───────────────────────────────────────────────────────
    if modpack_id:
        mp = await session.get(ModPack, modpack_id)
        if not mp:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "ModPack not found")
    elif modpack_slug and modpack_version:
        result = await session.exec(
            select(ModPack).where(
                ModPack.slug == modpack_slug,
                ModPack.version == modpack_version,
            )
        )
        mp = result.scalars().one_or_none()
        if not mp:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "ModPack not found")
    else:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Provide modpack_id or both modpack_slug + modpack_version",
        )

    # ── Validate start/target params ──────────────────────────────────────────
    if not start_tag and not start_item:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Provide start_tag or start_item",
        )
    if not target_tag and not target_item:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Provide target_tag or target_item",
        )

    # ── Load recipe graph ─────────────────────────────────────────────────────
    recipes, tag_to_items, item_to_tags = await _load_recipe_graph(session, mp.id)

    # ── Resolve start items ───────────────────────────────────────────────────
    if start_item:
        # Single concrete item — doesn't need to be in the tag index
        resolved_start_items = [start_item]
        resolved_start_label = start_item
    else:
        resolved_start_items = list(tag_to_items.get(start_tag, set()))
        resolved_start_label = start_tag
        if not resolved_start_items:
            return _empty_result(mp, resolved_start_label, target_tag or target_item,
                                 f"No items found in start tag '{start_tag}'")

    # ── Resolve target items ──────────────────────────────────────────────────
    if target_item:
        resolved_target_items = {target_item}
        resolved_target_label = target_item
    else:
        resolved_target_items = tag_to_items.get(target_tag, set())
        resolved_target_label = target_tag
        if not resolved_target_items:
            return _empty_result(mp, resolved_start_label, resolved_target_label,
                                 f"No items found in target tag '{target_tag}'")

    if not recipes:
        return _empty_result(mp, resolved_start_label, resolved_target_label,
                             "No recipes loaded for this modpack")

    # ── Run DFS traversal ─────────────────────────────────────────────────────
    graph = _RecipeGraph(recipes, tag_to_items)
    raw_chains = _find_chains(resolved_start_items, resolved_target_items, graph, max_depth)
    raw_chains = _deduplicate(raw_chains)

    chains = [_serialize_chain(c) for c in raw_chains]
    if min_yield > 0:
        chains = [c for c in chains if c["yield_per_input"] >= min_yield]
    chains.sort(key=lambda c: c["yield_per_input"], reverse=True)

    return {
        "modpack": {"id": str(mp.id), "slug": mp.slug, "version": mp.version},
        "start": resolved_start_label,
        "target": resolved_target_label,
        "chain_count": len(chains),
        "chains": chains,
    }


def _empty_result(mp: ModPack, start: str | None, target: str | None, note: str) -> dict:
    return {
        "modpack": {"id": str(mp.id), "slug": mp.slug, "version": mp.version},
        "start": start,
        "target": target,
        "chain_count": 0,
        "chains": [],
        "note": note,
    }

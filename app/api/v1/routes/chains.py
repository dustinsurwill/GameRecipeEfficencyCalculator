"""
Chain traversal endpoint.

  GET /chains?modpack_id=<uuid>&ore=gold&target=ingot
  GET /chains?modpack_slug=atm10&modpack_version=0.18.0&ore_tag=c:ores/gold&target_tag=c:ingots/gold

Returns all processing chains from ore to target, ranked by yield per ore.

Algorithm: loads the recipe graph for the modpack into memory and runs the
same DFS used by atm10_ore_analyzer.py, but sourcing data from the DB
instead of the export directory.

Query parameters (pick one of each group):
  Modpack identification (one required):
    modpack_id         — UUID of the modpack record
    modpack_slug +
    modpack_version    — human-readable alternative

  Ore specification (one required):
    ore                — shorthand, resolves to tag "c:ores/<ore>"
    ore_tag            — full tag string, e.g. "c:ores/gold"

  Target specification (one required):
    target             — shorthand, resolves to "c:ingots/<target>" with gem fallback
    target_tag         — full tag string

  Optional:
    max_depth          — default 7
    min_yield          — default 0.0 (include all chains)
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import uuid

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


@dataclass
class ChainStep:
    machine: str
    recipe_type: str
    recipe_id: str
    input_quantity: float
    output_quantity: float
    inputs: list[dict]
    outputs: list[dict]
    aux_inputs: list[dict]


@dataclass
class Chain:
    yield_per_ore: float
    step_count: int
    machine_sequence: list[str]
    chemicals_required: list[str]
    steps: list[ChainStep]


# ── DB data loading ───────────────────────────────────────────────────────────

async def _load_recipe_graph(
    session: AsyncSession,
    modpack_id: uuid.UUID,
) -> tuple[list[_Recipe], dict[str, set[str]], dict[str, set[str]]]:
    """
    Load all recipes active in the given modpack.

    Returns:
      recipes        — list of in-memory Recipe objects with slots populated
      tag_to_items   — tag string → set of item_ids
      item_to_tags   — item_id → set of tags
    """
    # Find all mod_version_ids in the modpack
    result = await session.exec(
        select(ModPackModVersion).where(ModPackModVersion.modpack_id == modpack_id)
    )
    modpack_mod_versions = list(result.scalars().all())
    mod_version_ids = {link.mod_version_id for link in modpack_mod_versions}

    if not mod_version_ids:
        return [], {}, {}

    # Load all item appearances for those mod versions to build the tag index
    appearance_result = await session.exec(
        select(ItemAppearance).where(ItemAppearance.mod_version_id.in_(mod_version_ids))
    )
    appearances = list(appearance_result.scalars().all())

    tag_to_items: dict[str, set[str]] = defaultdict(set)
    item_to_tags: dict[str, set[str]] = defaultdict(set)
    appearance_id_to_item: dict[uuid.UUID, str] = {}

    for app in appearances:
        appearance_id_to_item[app.id] = app.item_id
        tags = json.loads(app.tags_json) if app.tags_json else []
        for tag in tags:
            tag_to_items[tag].add(app.item_id)
            item_to_tags[app.item_id].add(tag)

    # Load all recipe_mod_version_links for those mod versions
    link_result = await session.exec(
        select(RecipeModVersionLink).where(
            RecipeModVersionLink.mod_version_id.in_(mod_version_ids)
        )
    )
    recipe_ids = {link.recipe_id for link in link_result.scalars().all()}

    if not recipe_ids:
        return [], dict(tag_to_items), dict(item_to_tags)

    # Load machine identities for name lookup
    machine_result = await session.exec(select(MachineIdentity))
    machine_id_to_name: dict[uuid.UUID, str] = {
        m.id: m.name for m in machine_result.scalars().all()
    }

    # Load recipes and their slots
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
            item_id = None
            if db_slot.item_appearance_id:
                item_id = appearance_id_to_item.get(db_slot.item_appearance_id)

            slot = _Slot(
                item_id=item_id,
                tag_string=db_slot.tag_string,
                quantity=db_slot.quantity,
                is_fluid=db_slot.is_fluid,
                fluid_label=db_slot.fluid_label,
            )
            if db_slot.slot_type == "input":
                r.inputs.append(slot)
            elif db_slot.slot_type == "output":
                r.outputs.append(slot)
            else:
                r.aux_inputs.append(slot)

        recipes.append(r)

    return recipes, dict(tag_to_items), dict(item_to_tags)


# ── Graph index ───────────────────────────────────────────────────────────────

class _RecipeGraph:
    def __init__(
        self,
        recipes: list[_Recipe],
        tag_to_items: dict[str, set[str]],
        item_to_tags: dict[str, set[str]],
    ):
        self.tag_to_items = tag_to_items
        self.item_to_tags = item_to_tags
        self._by_output: dict[str, list[_Recipe]] = defaultdict(list)
        self._by_input: dict[str, list[_Recipe]] = defaultdict(list)

        for r in recipes:
            # Index by output item_id
            for slot in r.outputs:
                if slot.item_id and not slot.is_fluid:
                    self._by_output[slot.item_id].append(r)
                elif slot.item_id and slot.is_fluid:
                    # Fluids/chemicals that are outputs (e.g. dissolution → chemical)
                    self._by_output[slot.item_id].append(r)

            # Index by primary input (first non-fluid input)
            primary = next((s for s in r.inputs if not s.is_fluid), None)
            if primary:
                if primary.item_id:
                    self._by_input[primary.item_id].append(r)
                elif primary.tag_string:
                    for item_id in tag_to_items.get(primary.tag_string, set()):
                        self._by_input[item_id].append(r)

    def recipes_consuming(self, item_id: str) -> list[_Recipe]:
        return self._by_input.get(item_id, [])

    def recipes_producing(self, item_id: str) -> list[_Recipe]:
        return self._by_output.get(item_id, [])


# ── DFS chain finder ──────────────────────────────────────────────────────────

def _find_chains(
    start_items: list[str],
    target_tag: str,
    graph: _RecipeGraph,
    max_depth: int = 7,
) -> list[list[tuple[_Recipe, float, float]]]:
    """
    DFS from start_items to target_tag.
    Returns a list of chains; each chain is a list of (recipe, input_qty, output_qty).
    """
    target_items = graph.tag_to_items.get(target_tag, set())
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
                input_qty = primary.quantity if primary else 1.0
                runs = current_count / input_qty

                next_items_counts: list[tuple[str, float]] = []
                for out in recipe.outputs:
                    if out.item_id:
                        next_items_counts.append((out.item_id, out.quantity * runs))

                if not next_items_counts:
                    continue

                primary_out_item, primary_out_qty = next_items_counts[0]

                dfs(
                    current_items=[item for item, _ in next_items_counts],
                    current_count=primary_out_qty,
                    steps=steps + [(recipe, current_count, primary_out_qty)],
                    depth=depth + 1,
                    seen=seen | {recipe.id},
                )

    for ore_item in start_items:
        dfs([ore_item], 1.0, [], 0, frozenset())

    return all_chains


def _build_chain_response(
    raw_chain: list[tuple[_Recipe, float, float]],
) -> Chain:
    steps = []
    chemicals: list[str] = []
    for recipe, in_qty, out_qty in raw_chain:
        for aux in recipe.aux_inputs:
            if aux.fluid_label:
                chemicals.append(aux.fluid_label)
        steps.append(ChainStep(
            machine=recipe.machine_name,
            recipe_type=recipe.recipe_type,
            recipe_id=str(recipe.id),
            input_quantity=in_qty,
            output_quantity=out_qty,
            inputs=[
                {"item_id": s.item_id, "tag_string": s.tag_string, "quantity": s.quantity}
                for s in recipe.inputs if not s.is_fluid
            ],
            outputs=[
                {"item_id": s.item_id, "quantity": s.quantity}
                for s in recipe.outputs if not s.is_fluid and s.item_id
            ],
            aux_inputs=[
                {"label": s.fluid_label or s.item_id, "quantity": s.quantity}
                for s in recipe.aux_inputs
            ],
        ))

    return Chain(
        yield_per_ore=raw_chain[-1][2] if raw_chain else 0.0,
        step_count=len(raw_chain),
        machine_sequence=[r.machine_name for r, _, _ in raw_chain],
        chemicals_required=sorted(set(chemicals)),
        steps=steps,
    )


def _deduplicate_chains(
    chains: list[list[tuple[_Recipe, float, float]]],
) -> list[list[tuple[_Recipe, float, float]]]:
    """Collapse chains with identical machine + recipe_type sequences and same yield."""
    seen: dict[tuple, list[tuple[_Recipe, float, float]]] = {}
    for chain in chains:
        key = (
            tuple((r.recipe_type, r.import_key) for r, _, _ in chain),
            round(chain[-1][2], 4) if chain else 0,
        )
        if key not in seen:
            seen[key] = chain
    return list(seen.values())


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("")
async def get_chains(
    session: AsyncSession = Depends(get_session),
    modpack_id: uuid.UUID | None = Query(default=None),
    modpack_slug: str | None = Query(default=None),
    modpack_version: str | None = Query(default=None),
    ore: str | None = Query(default=None, description="Ore shorthand e.g. 'gold' → c:ores/gold"),
    ore_tag: str | None = Query(default=None, description="Full ore tag e.g. 'c:ores/gold'"),
    target: str | None = Query(default=None, description="Target shorthand e.g. 'ingot' or 'gold'"),
    target_tag: str | None = Query(default=None, description="Full target tag e.g. 'c:ingots/gold'"),
    max_depth: int = Query(default=7, ge=1, le=12),
    min_yield: float = Query(default=0.0, ge=0.0),
) -> dict[str, Any]:

    # Resolve modpack
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

    # Resolve ore tag
    if ore_tag:
        resolved_ore_tag = ore_tag
    elif ore:
        resolved_ore_tag = f"c:ores/{ore}"
    else:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Provide ore or ore_tag",
        )

    # Resolve target tag
    if target_tag:
        resolved_target_tag = target_tag
    elif target:
        resolved_target_tag = f"c:ingots/{target}"
    elif ore:
        resolved_target_tag = f"c:ingots/{ore}"
    else:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Provide target or target_tag",
        )

    # Load recipe graph from DB
    recipes, tag_to_items, item_to_tags = await _load_recipe_graph(session, mp.id)

    if not recipes:
        return {
            "modpack": {"id": str(mp.id), "slug": mp.slug, "version": mp.version},
            "ore_tag": resolved_ore_tag,
            "target_tag": resolved_target_tag,
            "chain_count": 0,
            "chains": [],
        }

    graph = _RecipeGraph(recipes, tag_to_items, item_to_tags)

    # Resolve ore tag to concrete items
    start_items = list(tag_to_items.get(resolved_ore_tag, set()))
    if not start_items:
        # Try gem fallback for target if ore not found
        return {
            "modpack": {"id": str(mp.id), "slug": mp.slug, "version": mp.version},
            "ore_tag": resolved_ore_tag,
            "target_tag": resolved_target_tag,
            "chain_count": 0,
            "chains": [],
            "note": f"No items found in ore tag '{resolved_ore_tag}'",
        }

    # Handle target tag fallback (ingot → gem)
    if not tag_to_items.get(resolved_target_tag) and target and "/" not in target:
        gem_tag = f"c:gems/{target}"
        if tag_to_items.get(gem_tag):
            resolved_target_tag = gem_tag

    # Run DFS traversal
    raw_chains = _find_chains(start_items, resolved_target_tag, graph, max_depth)
    raw_chains = _deduplicate_chains(raw_chains)

    # Filter by min_yield and sort
    chains = [_build_chain_response(c) for c in raw_chains]
    if min_yield > 0:
        chains = [c for c in chains if c.yield_per_ore >= min_yield]
    chains.sort(key=lambda c: c.yield_per_ore, reverse=True)

    return {
        "modpack": {"id": str(mp.id), "slug": mp.slug, "version": mp.version},
        "ore_tag": resolved_ore_tag,
        "target_tag": resolved_target_tag,
        "chain_count": len(chains),
        "chains": [
            {
                "yield_per_ore": c.yield_per_ore,
                "step_count": c.step_count,
                "machine_sequence": c.machine_sequence,
                "chemicals_required": c.chemicals_required,
                "steps": [
                    {
                        "machine": s.machine,
                        "recipe_type": s.recipe_type,
                        "recipe_id": s.recipe_id,
                        "input_quantity": s.input_quantity,
                        "output_quantity": s.output_quantity,
                        "inputs": s.inputs,
                        "outputs": s.outputs,
                        "aux_inputs": s.aux_inputs,
                    }
                    for s in c.steps
                ],
            }
            for c in chains
        ],
    }

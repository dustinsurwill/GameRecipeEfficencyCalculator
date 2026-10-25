"""
Chain traversal endpoint — spec: docs/chain_query_spec.md

  GET /chains?modpack_id=<uuid>&target=c:ingots/gold&start=c:ores/gold

Direction is determined by whether `start` is provided:
  start present  → forward DFS:  start → consuming recipes → target
                   ranked by yield_per_start_item (descending)
  start absent   → backward DFS: target ← producing recipes ← raw materials
                   ranked by raw_materials_per_target (ascending)

Both start and target accept either a concrete item_id ("mekanism:dust_gold")
or a tag prefixed with "#" ("#c:ingots/gold").
"""
from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app import memory
from app.db import get_session
from app.models.identity import ItemAppearance, ItemIdentity, MachineIdentity
from app.models.recipe import Recipe, RecipeModVersionLink, RecipeSlot
from app.models.status import RecipeStatus
from app.models.status import PublicationStatus
from app.models.versioning import (
    Game, GameVersion, Mod, ModPackIdentity, ModPackVersion, ModPackModVersion, ModVersion,
)

router = APIRouter(prefix="/chains", tags=["chains"])


# ── In-memory graph structures ────────────────────────────────────────────────

@dataclass(slots=True)
class _Slot:
    item_id: str | None
    tag_string: str | None
    quantity: float
    is_fluid: bool
    fluid_label: str | None = None


@dataclass(slots=True)
class _MachineInfo:
    id: str
    name: str


@dataclass(slots=True)
class _Recipe:
    id: uuid.UUID
    recipe_type: str
    machine: _MachineInfo
    import_key: str
    time_ticks: int | None
    energy_rf: int | None
    inputs: list[_Slot] = field(default_factory=list)
    outputs: list[_Slot] = field(default_factory=list)
    aux_inputs: list[_Slot] = field(default_factory=list)
    # What the route graph follows as inputs: item inputs plus fluid/chemical inputs (stored either as
    # inputs or aux). Catalysts (a non-consumed cast) and free fluids (water) are not followed.
    gin: list[_Slot] = field(default_factory=list)


# Free in every pack; following them would make nearly every machine route end in "water".
_FREE_FLUIDS = frozenset({"fluid:minecraft:water"})


def _bare_ns(item_id: str) -> str:
    """Namespace of an item id; fluids/chemicals are 'fluid:ns:name' / 'chemical:ns:name'."""
    for prefix in ("fluid:", "chemical:"):
        if item_id.startswith(prefix):
            item_id = item_id[len(prefix):]
            break
    return item_id.split(":", 1)[0]


def _link_inputs(r: _Recipe) -> None:
    def followed(s: _Slot) -> bool:
        return bool(s.item_id or s.tag_string) and s.item_id not in _FREE_FLUIDS

    r.gin = [s for s in r.inputs if followed(s)] + [s for s in r.aux_inputs if s.is_fluid and followed(s)]


# ── Exclusions ────────────────────────────────────────────────────────────────

class _Excluder:
    """
    What a search must not use. A recipe is blocked when any of these match:
      types     'mekanism' (whole mod's recipe types) or 'mekanism:purifying' (one type)
      machines  machine ids
      items     item ids appearing in ANY slot of the recipe
      mods      namespaces: recipe types of that mod, or any item from it in any slot
    `keep` (the start/target items) is exempt from item and mod exclusion, otherwise skipping a mod
    would make its own items unreachable. Results are memoised per recipe: one instance per search.
    """

    def __init__(
        self, types: list[str] = (), machines: list[str] = (), items: list[str] = (),
        mods: list[str] = (), keep: set[str] = frozenset(),
    ) -> None:
        self.types, self.machines = list(types), set(machines)
        self.items, self.mods, self.keep = set(items), set(mods), keep
        self._memo: dict[uuid.UUID, bool] = {}

    def item_blocked(self, item_id: str) -> bool:
        if item_id in self.keep:
            return False
        return item_id in self.items or _bare_ns(item_id) in self.mods

    def _slots_blocked(self, r: _Recipe) -> bool:
        for group in (r.inputs, r.outputs, r.aux_inputs):
            for s in group:
                if s.item_id and self.item_blocked(s.item_id):
                    return True
        return False

    def blocks(self, r: _Recipe) -> bool:
        hit = self._memo.get(r.id)
        if hit is not None:
            return hit
        rt = r.recipe_type
        ns = rt.split(":", 1)[0]
        blocked = (
            ns in self.mods
            or r.machine.id in self.machines
            or any(rt == rule if ":" in rule else ns == rule for rule in self.types)
            or self._slots_blocked(r)
        )
        self._memo[r.id] = blocked
        return blocked

    def describe(self) -> dict[str, list[str]]:
        return {
            "types": sorted(self.types), "machines": sorted(self.machines),
            "items": sorted(self.items), "mods": sorted(self.mods),
        }


# ── DB loading ────────────────────────────────────────────────────────────────

async def _mod_version_ids_for_modpack(
    session: AsyncSession, modpack_id: uuid.UUID
) -> set[uuid.UUID]:
    result = await session.exec(
        select(ModPackModVersion).where(ModPackModVersion.modpack_id == modpack_id)
    )
    return {link.mod_version_id for link in result.scalars().all()}


async def _load_recipe_graph(
    session: AsyncSession,
    mod_version_ids: set[uuid.UUID],
) -> tuple[list[_Recipe], dict[str, set[str]], dict[str, str]]:
    """
    Load approved recipes for the given mod_version_ids.

    Returns:
      recipes            — in-memory recipe objects
      tag_to_items       — tag string → set of item_ids
      item_to_canonical  — item_id → canonical_name
    """
    if not mod_version_ids:
        return [], {}, {}

    appearance_result = await session.exec(
        select(ItemAppearance).where(ItemAppearance.mod_version_id.in_(mod_version_ids))
    )
    appearances = list(appearance_result.scalars().all())

    tag_to_items: dict[str, set[str]] = defaultdict(set)
    appearance_id_to_item: dict[uuid.UUID, str] = {}
    for app in appearances:
        appearance_id_to_item[app.id] = app.item_id
        for tag in (json.loads(app.tags_json) if app.tags_json else []):
            tag_to_items[tag].add(app.item_id)

    # Resolve canonical names
    # Subquery (not a literal id list): packs have >32k items, over asyncpg's parameter limit
    identity_subq = select(ItemAppearance.identity_id).where(
        ItemAppearance.mod_version_id.in_(mod_version_ids)
    )
    item_to_canonical: dict[str, str] = {}
    if appearances:
        identity_result = await session.exec(
            select(ItemIdentity).where(ItemIdentity.id.in_(identity_subq))
        )
        identity_map = {i.id: i.canonical_name for i in identity_result.scalars().all()}
        for app in appearances:
            if app.identity_id in identity_map:
                item_to_canonical[app.item_id] = identity_map[app.identity_id]

    scoped_recipe_ids = select(RecipeModVersionLink.recipe_id).where(
        RecipeModVersionLink.mod_version_id.in_(mod_version_ids)
    )

    machine_result = await session.exec(select(MachineIdentity))
    machine_map: dict[uuid.UUID, _MachineInfo] = {
        m.id: _MachineInfo(id=str(m.id), name=m.name)
        for m in machine_result.scalars().all()
    }

    # Only approved recipes participate in traversal
    recipe_result = await session.exec(
        select(Recipe).where(
            Recipe.id.in_(scoped_recipe_ids),
            Recipe.status == RecipeStatus.APPROVED,
        )
    )
    db_recipes = list(recipe_result.scalars().all())
    if not db_recipes:
        return [], dict(tag_to_items), item_to_canonical

    slot_result = await session.exec(
        select(RecipeSlot).where(
            RecipeSlot.recipe_id.in_(
                select(Recipe.id).where(
                    Recipe.id.in_(scoped_recipe_ids), Recipe.status == RecipeStatus.APPROVED
                )
            )
        )
    )
    slots_by_recipe: dict[uuid.UUID, list[RecipeSlot]] = defaultdict(list)
    for slot in slot_result.scalars().all():
        slots_by_recipe[slot.recipe_id].append(slot)

    recipes: list[_Recipe] = []
    for db_r in db_recipes:
        rt = db_r.recipe_type
        machine = machine_map.get(
            db_r.machine_identity_id,
            _MachineInfo(id=str(db_r.machine_identity_id), name=rt),
        )
        r = _Recipe(
            id=db_r.id,
            recipe_type=rt,
            machine=machine,
            import_key=db_r.import_key,
            time_ticks=db_r.time_ticks,
            energy_rf=db_r.energy_rf,
        )
        for db_slot in sorted(slots_by_recipe[db_r.id], key=lambda s: s.slot_order):
            item_id = (
                appearance_id_to_item.get(db_slot.item_appearance_id)
                if db_slot.item_appearance_id
                else None
            )
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
        _link_inputs(r)
        recipes.append(r)

    return recipes, dict(tag_to_items), item_to_canonical


# ── Graph index ───────────────────────────────────────────────────────────────

class _RecipeGraph:
    def __init__(self, recipes: list[_Recipe], tag_to_items: dict[str, set[str]]):
        self.tag_to_items = tag_to_items
        self._by_input: dict[str, list[_Recipe]] = defaultdict(list)
        self._by_output: dict[str, list[_Recipe]] = defaultdict(list)

        for r in recipes:
            # Index by outputs (backward traversal)
            for slot in r.outputs:
                if slot.item_id:
                    self._by_output[slot.item_id].append(r)

            # Forward traversal: index by the primary (first non-fluid) input, and by every fluid or
            # chemical input, so melt -> cast style routes can be followed from a fluid.
            primary = next((s for s in r.inputs if not s.is_fluid), None)
            for slot in ([primary] if primary else []) + [g for g in r.gin if g.is_fluid]:
                if slot.item_id:
                    self._by_input[slot.item_id].append(r)
                elif slot.tag_string:
                    for item_id in tag_to_items.get(slot.tag_string, set()):
                        self._by_input[item_id].append(r)

    def forward_sources(self, r: _Recipe) -> list[str]:
        """Items from which the forward search can enter this recipe (see __init__)."""
        out: list[str] = []
        primary = next((x for x in r.inputs if not x.is_fluid), None)
        for slot in ([primary] if primary else []) + [g for g in r.gin if g.is_fluid]:
            if slot.item_id:
                out.append(slot.item_id)
            elif slot.tag_string:
                out.extend(self.tag_to_items.get(slot.tag_string, ()))
        return out

    def recipes_consuming(self, item_id: str) -> list[_Recipe]:
        return self._by_input.get(item_id, [])

    def recipes_producing(self, item_id: str) -> list[_Recipe]:
        return self._by_output.get(item_id, [])


# ── Graph cache ───────────────────────────────────────────────────────────────
# The graph is identical for every request against the same scope, so build it once and share it.
# Loading it per request cost ~250 MB of transient ORM objects each, multiplied by concurrency.

@dataclass(slots=True)
class _LoadedGraph:
    graph: _RecipeGraph
    tag_to_items: dict[str, set[str]]
    item_to_canonical: dict[str, str]
    n_recipes: int
    built_at: float


_GRAPH_TTL = 600.0
_GRAPH_MAX = 2  # distinct scopes kept; each is roughly 150–250 MB for an ATM10-sized pack
_graph_cache: dict[frozenset[uuid.UUID], _LoadedGraph] = {}
_graph_lock = asyncio.Lock()
_search_slots = asyncio.Semaphore(2)  # concurrent DFS threads; the rest queue instead of piling up


async def _get_graph(session: AsyncSession, mod_version_ids: set[uuid.UUID]) -> _LoadedGraph:
    key = frozenset(mod_version_ids)
    now = time.monotonic()
    hit = _graph_cache.get(key)
    if hit and now - hit.built_at < _GRAPH_TTL:
        return hit
    async with _graph_lock:
        hit = _graph_cache.get(key)
        if hit and time.monotonic() - hit.built_at < _GRAPH_TTL:
            return hit
        recipes, tag_to_items, item_to_canonical = await _load_recipe_graph(session, set(key))
        loaded = _LoadedGraph(
            graph=_RecipeGraph(recipes, tag_to_items),
            tag_to_items=tag_to_items,
            item_to_canonical=item_to_canonical,
            n_recipes=len(recipes),
            built_at=time.monotonic(),
        )
        _graph_cache[key] = loaded
        while len(_graph_cache) > _GRAPH_MAX:
            oldest = min(_graph_cache, key=lambda k: _graph_cache[k].built_at)
            del _graph_cache[oldest]
        memory.trim()
        return loaded


def invalidate_graph_cache() -> None:
    _graph_cache.clear()


# ── Tag / item resolution ─────────────────────────────────────────────────────

def _resolve(value: str, tag_to_items: dict[str, set[str]]) -> list[str]:
    if value.startswith("#"):
        return sorted(tag_to_items.get(value[1:], set()))
    return [value]


class _Budget:
    """
    Hard caps on a DFS: packs with crafting recipes have effectively unbounded paths, and the
    search runs in a thread that cannot be cancelled. Without all three limits a deep search keeps
    collecting chains (tens of MB/s) long after the client has gone away.
    """

    def __init__(
        self, limit: int = 3_000_000, max_chains: int = 50_000, seconds: float = 15.0,
        deadline: float | None = None,
    ) -> None:
        self.left = limit
        self.chains = 0
        self.max_chains = max_chains
        self.deadline = deadline if deadline is not None else time.monotonic() + seconds
        self.hit = False

    def spend(self) -> bool:
        if self.left <= 0:
            self.hit = True
            return False
        self.left -= 1
        if not self.left & 1023 and time.monotonic() > self.deadline:
            self.left = 0
            self.hit = True
            return False
        return True

    def add_chain(self) -> bool:
        """Record a collected chain; False means the cap is reached and the DFS should unwind."""
        self.chains += 1
        if self.chains >= self.max_chains:
            self.left = 0
            self.hit = True
            return False
        return True


def _consumed(recipe: _Recipe, item_id: str, tag_to_items: dict[str, set[str]]) -> float:
    """How much of `item_id` one run uses, summed over every input slot it can fill. A recipe that
    takes the item in two slots (or a slot and a tag it belongs to) must be charged for both, or the
    yield of the route is overstated."""
    total = 0.0
    for s in recipe.gin:
        if s.item_id == item_id or (s.tag_string and item_id in tag_to_items.get(s.tag_string, ())):
            total += s.quantity or 1.0
    return total


class _SeqCounter:
    """Lets each distinct machine sequence be collected a few times (the best-yield variant differs by
    recipe choice) but no more: thousands of crafting-layout or equivalent-recipe variants of one route
    otherwise fill the chain cap before deeper, genuinely different routes are reached."""

    def __init__(self, per_sequence: int = 4) -> None:
        self._n: dict[tuple, int] = {}
        self._cap = per_sequence

    def add(self, key: tuple) -> bool:
        n = self._n.get(key, 0)
        if not n and len(self._n) >= 300_000:     # bound memory: past this, new keys are not tracked
            return True
        if n >= self._cap:
            return False
        self._n[key] = n + 1
        return True


def _steps_to(targets: set[str], graph: _RecipeGraph, excl: _Excluder) -> dict[str, int]:
    """Fewest recipe steps from each item to any target, following the same edges the forward search
    uses. A forward DFS that does not know this spends nearly all of its budget in branches that can
    never reach the target; with it, a branch is dropped as soon as depth + steps_to > max_depth."""
    producers = graph.recipes_producing
    dist = {t: 0 for t in targets}
    frontier = list(targets)
    while frontier:
        nxt = []
        for item in frontier:
            d = dist[item] + 1
            for r in producers(item):
                if excl.blocks(r):
                    continue
                for src in graph.forward_sources(r):
                    if src not in dist and not (src in targets):
                        dist[src] = d
                        nxt.append(src)
        frontier = nxt
    return dist


# ── Forward DFS ───────────────────────────────────────────────────────────────
# Each chain: list of (recipe, runs) in execution order.
# `runs` = how many times that recipe runs, starting from 1.0 unit of start_item.

def _forward_dfs(
    start_items: list[str],
    target_items: set[str],
    graph: _RecipeGraph,
    excl: _Excluder,
    max_depth: int,
    allow_cycles: bool,
    budget: _Budget,
    steps_to: dict[str, int] | None = None,
) -> tuple[list[tuple[list[tuple[_Recipe, float]], bool, int | None]], int]:
    all_chains: list[tuple[list, bool, int | None]] = []
    cycles_excluded = 0
    seen = _SeqCounter()
    states = _SeqCounter(3)

    def dfs(
        item_id: str,
        quantity: float,
        steps: list[tuple[_Recipe, float]],
        path: frozenset[str],
        depth: int,
    ) -> None:
        nonlocal cycles_excluded

        if depth > max_depth or not budget.spend():
            return
        if steps_to is not None and depth + steps_to.get(item_id, max_depth + 1) > max_depth:
            return

        if not states.add((item_id, _sequence(steps))):
            return      # same item reached through the same machines already; only recipe variants differ
        if item_id in target_items:
            if seen.add(_sequence(steps)):
                all_chains.append((list(steps), False, None))
                budget.add_chain()
            return

        if item_id in path:
            if allow_cycles:
                all_chains.append((list(steps), True, depth))
                budget.add_chain()
            else:
                cycles_excluded += 1
            return

        new_path = path | {item_id}
        tried: set[uuid.UUID] = set()

        for recipe in graph.recipes_consuming(item_id):
            if recipe.id in tried or excl.blocks(recipe):
                continue
            tried.add(recipe.id)

            consumed = _consumed(recipe, item_id, graph.tag_to_items)
            if not consumed:
                continue
            runs = quantity / consumed

            for out_slot in recipe.outputs:
                if not out_slot.item_id:
                    continue
                dfs(
                    out_slot.item_id,
                    out_slot.quantity * runs,
                    steps + [(recipe, runs)],
                    new_path,
                    depth + 1,
                )

    for item in start_items:
        dfs(item, 1.0, [], frozenset(), 0)

    return all_chains, cycles_excluded


# ── Backward DFS ──────────────────────────────────────────────────────────────
# Traces target ← producing recipes ← raw materials.
# Each chain is stored reversed (from target-producer toward raw), then reversed
# at the leaf so the returned list is in forward (execution) order.
# Each entry: (steps_forward, has_cycle, cycle_at_step, raw_item_id, raw_qty)

def _backward_dfs(
    target_items: list[str],
    graph: _RecipeGraph,
    excl: _Excluder,
    max_depth: int,
    allow_cycles: bool,
    budget: _Budget,
) -> tuple[list[tuple[list[tuple[_Recipe, float]], bool, int | None, str, float]], int]:
    all_chains: list[tuple[list, bool, int | None, str, float]] = []
    cycles_excluded = 0

    def dfs(
        item_id: str,
        quantity: float,
        steps_rev: list[tuple[_Recipe, float]],
        path: frozenset[str],
        depth: int,
    ) -> None:
        nonlocal cycles_excluded

        if depth > max_depth or not budget.spend():
            return

        if item_id in path:
            if allow_cycles:
                all_chains.append((list(reversed(steps_rev)), True, depth, item_id, quantity))
                budget.add_chain()
            else:
                cycles_excluded += 1
            return

        producing = [r for r in graph.recipes_producing(item_id) if not excl.blocks(r)]

        if not producing:
            # Leaf — item_id is a raw material
            all_chains.append((list(reversed(steps_rev)), False, None, item_id, quantity))
            budget.add_chain()
            return

        new_path = path | {item_id}
        expanded = False

        for recipe in producing:
            out_slot = next((s for s in recipe.outputs if s.item_id == item_id), None)
            if not out_slot:
                continue

            runs = quantity / (out_slot.quantity or 1.0)

            for in_slot in recipe.gin:
                if in_slot.item_id:
                    input_items = [in_slot.item_id]
                elif in_slot.tag_string:
                    input_items = sorted(graph.tag_to_items.get(in_slot.tag_string, ()))
                else:
                    continue
                input_items = [i for i in input_items if not excl.item_blocked(i)]

                for input_item in input_items:
                    expanded = True
                    dfs(
                        input_item,
                        in_slot.quantity * runs,
                        steps_rev + [(recipe, runs)],
                        new_path,
                        depth + 1,
                    )

        if not expanded and not steps_rev:
            return
        if not expanded:
            # every producer takes only fluids/catalysts (e.g. casting): nothing to follow, so the item
            # is a starting point here, just as it was before such recipes were imported
            all_chains.append((list(reversed(steps_rev)), False, None, item_id, quantity))
            budget.add_chain()

    for item in target_items:
        dfs(item, 1.0, [], frozenset(), 0)

    return all_chains, cycles_excluded


# ── Iterative deepening + ranking ─────────────────────────────────────────────
# A plain depth-12 DFS spends its whole budget in the first branch it enters and never sees the short
# routes. Searching depth 1, 2, 3 … instead means the cheapest routes are always found first, and a
# later, truncated pass can only add to a complete shallower one.

ENOUGH_CHAINS = 400  # stop deepening once a complete pass has found this many routes


def _deepen(run, max_depth: int, enough: int = ENOUGH_CHAINS, seconds: float = 15.0):
    """`run(depth, budget) -> (chains, cycles_excluded)`. Returns (chains, cycles_excluded, truncated)."""
    deadline = time.monotonic() + seconds
    done: list = []
    cycles = 0
    for depth in range(1, max_depth + 1):
        budget = _Budget(deadline=deadline)
        chains, cycles = run(depth, budget)
        if budget.hit:
            return done + chains, cycles, True
        done = chains
        if len(done) >= enough:
            break
    return done, cycles, False


def _has_side_inputs(steps: list[tuple[_Recipe, float]]) -> bool:
    """True if any step consumes more than one kind of thing (items and fluids; Mekanism chemicals are
    in-network utilities and don't count). The search follows one item along the route, so those extra
    materials are free in the yield figure; such routes are flagged and ranked below self-contained
    processing chains."""
    for r, _ in steps:
        kinds = {s.item_id or "#" + (s.tag_string or "") for s in r.gin
                 if not (s.item_id or "").startswith("chemical:")}
        if len(kinds) > 1:
            return True
    return False


def _route_cost(steps: list[tuple[_Recipe, float]], ratio: float, higher_is_better: bool) -> float:
    """
    Lower is better. Fewer steps dominate, then fewer distinct machines (each is something to build
    and feed), then the item ratio on a log scale. The ratio is floored at 0.25 so a long chain
    through some high-multiplier recipe (seeds, lenses) cannot buy its way to the top with a
    fraction like 0.01 raw per item.
    """
    machines = len({r.machine.id for r, _ in steps})
    ratio_term = 0.4 * math.log2(max(ratio, 0.25))
    return len(steps) + 0.5 * machines + (-ratio_term if higher_is_better else ratio_term)


# ── Deduplication ─────────────────────────────────────────────────────────────

def _sequence(chain: list[tuple[_Recipe, float]], leaf: str = "") -> tuple:
    """Identity of a route for de-duplication: the machines used, in order, and where it starts.
    Two crafting recipes that differ only in layout are the same route to a player."""
    return tuple(r.machine.id for r, _ in chain) + (leaf,)


def _rank_forward(
    chains: list[tuple[list, bool, int | None]], target_items: set[str], by_yield: bool = True,
) -> list[tuple[list, bool, int | None]]:
    """Best route per distinct recipe sequence (loops last): highest yield first, or cheapest first."""
    best: dict[tuple, tuple[tuple, tuple]] = {}
    for entry in chains:
        chain, has_cycle, _ = entry
        if not chain:
            continue
        if has_cycle:
            cost = (True, False, 0.0, 0.0)
        else:
            r_last, runs_last = chain[-1]
            y = sum(s.quantity * runs_last for s in r_last.outputs if s.item_id in target_items)
            simple = _route_cost(chain, y, True)
            cost = (False, _has_side_inputs(chain), -round(y, 4), simple) if by_yield else (False, False, simple, -y)
        key = _sequence(chain)
        if key not in best or cost < best[key][0]:
            best[key] = (cost, entry)
    return [e for _, e in sorted(best.values(), key=lambda v: v[0])]


def _rank_backward(
    chains: list[tuple[list, bool, int | None, str, float]], by_yield: bool = False,
) -> list[tuple[list, bool, int | None, str, float]]:
    """Best route per distinct recipe sequence (loops last): least raw material first, or cheapest first.
    Zero-step 'routes' are dropped."""
    best: dict[tuple, tuple[tuple, tuple]] = {}
    for entry in chains:
        chain, has_cycle, _, raw_item, raw_qty = entry
        if not chain:
            continue
        simple = _route_cost(chain, raw_qty, False)
        cost = (has_cycle, round(raw_qty, 4), simple) if by_yield else (has_cycle, simple, raw_qty)
        key = _sequence(chain, raw_item)
        if key not in best or cost < best[key][0]:
            best[key] = (cost, entry)
    return [e for _, e in sorted(best.values(), key=lambda v: v[0])]


# ── Serialization ─────────────────────────────────────────────────────────────

def _serialize_forward_chain(
    steps: list[tuple[_Recipe, float]],
    target_items: set[str],
    item_to_canonical: dict[str, str],
    quantity_scale: float,
    has_cycle: bool,
    cycle_at_step: int | None,
    rank: int,
) -> dict[str, Any]:
    # raw_inputs: non-fluid inputs of step 1
    recipe0, runs0 = steps[0]
    raw_inputs = [
        {
            "tag": s.tag_string,
            "item_id": s.item_id,
            "quantity": s.quantity * runs0 * quantity_scale,
            "is_fluid": s.is_fluid,
        }
        for s in recipe0.gin
    ]

    # final_outputs: target-matching outputs of last step
    recipe_last, runs_last = steps[-1]
    final_outputs = []
    yield_per_start = 0.0
    for s in recipe_last.outputs:
        if s.item_id and s.item_id in target_items:
            qty = s.quantity * runs_last * quantity_scale
            yield_per_start += qty
            final_outputs.append({
                "item_id": s.item_id,
                "canonical_name": item_to_canonical.get(s.item_id),
                "quantity": qty,
            })

    return _build_chain_dict(
        steps=steps,
        item_to_canonical=item_to_canonical,
        quantity_scale=quantity_scale,
        rank=rank,
        has_cycle=has_cycle,
        cycle_at_step=cycle_at_step,
        yield_per_start_item=yield_per_start if not has_cycle else None,
        raw_materials_per_target=None,
        raw_inputs=raw_inputs,
        final_outputs=final_outputs,
    )


def _serialize_backward_chain(
    steps: list[tuple[_Recipe, float]],
    target_items: set[str],
    item_to_canonical: dict[str, str],
    quantity_scale: float,
    has_cycle: bool,
    cycle_at_step: int | None,
    raw_item_id: str,
    raw_qty: float,
    rank: int,
) -> dict[str, Any]:
    raw_inputs = [
        {
            "tag": None,
            "item_id": raw_item_id,
            "quantity": raw_qty * quantity_scale,
            "is_fluid": False,
        }
    ]

    # final_outputs: target outputs of last step
    final_outputs = []
    if steps:
        recipe_last, runs_last = steps[-1]
        for s in recipe_last.outputs:
            if s.item_id and s.item_id in target_items:
                final_outputs.append({
                    "item_id": s.item_id,
                    "canonical_name": item_to_canonical.get(s.item_id),
                    "quantity": s.quantity * runs_last * quantity_scale,
                })

    return _build_chain_dict(
        steps=steps,
        item_to_canonical=item_to_canonical,
        quantity_scale=quantity_scale,
        rank=rank,
        has_cycle=has_cycle,
        cycle_at_step=cycle_at_step,
        yield_per_start_item=None,
        raw_materials_per_target=raw_qty * quantity_scale if not has_cycle else None,
        raw_inputs=raw_inputs,
        final_outputs=final_outputs,
    )


def _build_chain_dict(
    steps: list[tuple[_Recipe, float]],
    item_to_canonical: dict[str, str],
    quantity_scale: float,
    rank: int,
    has_cycle: bool,
    cycle_at_step: int | None,
    yield_per_start_item: float | None,
    raw_materials_per_target: float | None,
    raw_inputs: list[dict],
    final_outputs: list[dict],
) -> dict[str, Any]:
    serialized_steps = []
    machines_seen: dict[str, dict] = {}
    chemicals: list[str] = []

    for i, (recipe, runs) in enumerate(steps):
        mod_ns = recipe.recipe_type.split(":")[0] if ":" in recipe.recipe_type else recipe.recipe_type
        machine_dict = {
            "id": recipe.machine.id,
            "name": recipe.machine.name,
            "recipe_type": recipe.recipe_type,
            "mod_namespace": mod_ns,
        }
        machines_seen.setdefault(recipe.machine.id, machine_dict)

        for aux in recipe.aux_inputs:
            label = aux.fluid_label or aux.item_id
            if label:
                chemicals.append(label)

        followed = {id(g) for g in recipe.gin}
        step_inputs = [
            {
                "tag": s.tag_string,
                "item_id": s.item_id,
                "canonical_name": item_to_canonical.get(s.item_id or ""),
                "quantity": s.quantity * runs * quantity_scale,
                "is_fluid": s.is_fluid,
            }
            for s in recipe.gin
        ]
        step_outputs = [
            {
                "tag": None,
                "item_id": s.item_id,
                "canonical_name": item_to_canonical.get(s.item_id or ""),
                "quantity": s.quantity * runs * quantity_scale,
                "is_fluid": s.is_fluid,
            }
            for s in recipe.outputs
            if s.item_id
        ]
        # catalysts, free fluids (water) and anything else the route doesn't follow
        step_aux = [
            {
                "item_id": s.item_id,
                "canonical_name": item_to_canonical.get(s.item_id or "") if s.item_id else None,
                "quantity": s.quantity * runs * quantity_scale,
                "is_fluid": s.is_fluid,
            }
            for s in recipe.aux_inputs
            if id(s) not in followed
        ]

        serialized_steps.append({
            "step": i + 1,
            "machine": machine_dict,
            "recipe_id": str(recipe.id),
            "inputs": step_inputs,
            "outputs": step_outputs,
            "aux_inputs": step_aux,
            "time_ticks": recipe.time_ticks,
            "energy_rf": recipe.energy_rf,
        })

    return {
        "rank": rank,
        "yield_per_start_item": yield_per_start_item,
        "raw_materials_per_target": raw_materials_per_target,
        "has_cycle": has_cycle,
        "cycle_at_step": cycle_at_step,
        "needs_other_inputs": _has_side_inputs(steps),
        "raw_inputs": raw_inputs,
        "final_outputs": final_outputs,
        "steps": serialized_steps,
        "machines_required": list(machines_seen.values()),
        "chemicals_required": sorted(set(chemicals)),
    }


# ── Scope resolution ──────────────────────────────────────────────────────────

async def _resolve_scope(
    session: AsyncSession,
    modpack: str | None,
    modpack_version: str | None,
    mods: list[str],
    game: str | None,
    game_version_str: str | None,
) -> tuple[set[uuid.UUID], dict[str, Any]]:
    """
    Tiered priority: modpack > mod[] > game.
    Returns (mod_version_ids, scope_info).
    """

    async def _resolve_gv() -> GameVersion | None:
        if not game:
            return None
        try:
            g = await session.get(Game, uuid.UUID(game))
        except ValueError:
            result = await session.exec(select(Game).where(Game.slug == game))
            g = result.scalars().one_or_none()
        if not g:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Game '{game}' not found")

        if game_version_str:
            try:
                gv = await session.get(GameVersion, uuid.UUID(game_version_str))
            except ValueError:
                result = await session.exec(
                    select(GameVersion).where(
                        GameVersion.game_id == g.id,
                        GameVersion.version == game_version_str,
                    )
                )
                gv = result.scalars().one_or_none()
        else:
            result = await session.exec(
                select(GameVersion)
                .where(GameVersion.game_id == g.id)
                .order_by(GameVersion.is_latest.desc())
                .limit(1)
            )
            gv = result.scalars().one_or_none()
        if not gv:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Game version not found")
        return gv

    # ── Priority 1: modpack ──────────────────────────────────────────────────
    if modpack:
        try:
            identity = await session.get(ModPackIdentity, uuid.UUID(modpack))
        except ValueError:
            result = await session.exec(
                select(ModPackIdentity).where(ModPackIdentity.slug == modpack)
            )
            identities = result.scalars().all()
            if len(identities) > 1:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Modpack slug '{modpack}' is ambiguous across games; add game= to disambiguate",
                )
            identity = identities[0] if identities else None

        if not identity:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"ModPack '{modpack}' not found")

        if modpack_version:
            try:
                mpv = await session.get(ModPackVersion, uuid.UUID(modpack_version))
                if mpv and mpv.identity_id != identity.id:
                    mpv = None
            except ValueError:
                result = await session.exec(
                    select(ModPackVersion).where(
                        ModPackVersion.identity_id == identity.id,
                        ModPackVersion.version == modpack_version,
                    )
                )
                mpv = result.scalars().one_or_none()
        else:
            result = await session.exec(
                select(ModPackVersion)
                .where(
                    ModPackVersion.identity_id == identity.id,
                    ModPackVersion.status == PublicationStatus.PUBLISHED,
                )
                .order_by(ModPackVersion.version.desc())
                .limit(1)
            )
            mpv = result.scalars().one_or_none()
            if not mpv:
                result = await session.exec(
                    select(ModPackVersion)
                    .where(ModPackVersion.identity_id == identity.id)
                    .order_by(ModPackVersion.version.desc())
                    .limit(1)
                )
                mpv = result.scalars().one_or_none()

        if not mpv:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No version found for modpack '{identity.name}'")

        links = await session.exec(
            select(ModPackModVersion).where(ModPackModVersion.modpack_id == mpv.id)
        )
        mv_ids = {lnk.mod_version_id for lnk in links.scalars().all()}
        return mv_ids, {
            "type": "modpack",
            "modpack_identity_id": str(identity.id),
            "modpack_version_id": str(mpv.id),
            "modpack_name": identity.name,
            "modpack_version": mpv.version,
        }

    # ── Priority 2: mod[] ────────────────────────────────────────────────────
    if mods:
        gv = await _resolve_gv()
        mv_ids: set[uuid.UUID] = set()
        resolved: list[dict] = []

        for param in mods:
            mod_ref, _, ver_str = param.partition("@")
            ver_str = ver_str or None

            try:
                mod_obj = await session.get(Mod, uuid.UUID(mod_ref))
            except ValueError:
                q = select(Mod).where(Mod.slug == mod_ref)
                if gv:
                    q = q.where(Mod.game_id == gv.game_id)
                result = await session.exec(q)
                mod_obj = result.scalars().one_or_none()

            if not mod_obj:
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"Mod '{mod_ref}' not found")

            if ver_str:
                q = select(ModVersion).where(
                    ModVersion.mod_id == mod_obj.id, ModVersion.version == ver_str
                )
                if gv:
                    q = q.where(ModVersion.game_version_id == gv.id)
                result = await session.exec(q)
                mv = result.scalars().one_or_none()
            elif gv:
                result = await session.exec(
                    select(ModVersion)
                    .where(ModVersion.mod_id == mod_obj.id, ModVersion.game_version_id == gv.id)
                    .order_by(ModVersion.imported_at.desc())
                    .limit(1)
                )
                mv = result.scalars().one_or_none()
            else:
                result = await session.exec(
                    select(ModVersion)
                    .where(
                        ModVersion.mod_id == mod_obj.id,
                        ModVersion.status == PublicationStatus.PUBLISHED,
                    )
                    .order_by(ModVersion.imported_at.desc())
                    .limit(1)
                )
                mv = result.scalars().one_or_none()

            if not mv:
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"No version found for mod '{mod_obj.name}'")

            mv_ids.add(mv.id)
            resolved.append({"mod": mod_obj.name, "version": mv.version, "mod_version_id": str(mv.id)})

        scope: dict[str, Any] = {"type": "mods", "mods": resolved}
        if gv:
            scope["game_version_id"] = str(gv.id)
        return mv_ids, scope

    # ── Priority 3: game ─────────────────────────────────────────────────────
    if game:
        gv = await _resolve_gv()
        if not gv:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "game= requires a resolvable game version")
        result = await session.exec(
            select(ModVersion).where(
                ModVersion.game_version_id == gv.id,
                ModVersion.status == PublicationStatus.PUBLISHED,
            )
        )
        mv_ids = {mv.id for mv in result.scalars().all()}
        return mv_ids, {
            "type": "game",
            "game_id": str(gv.game_id),
            "game_version_id": str(gv.id),
        }

    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "Provide at least one of: modpack=, mod[]=, game=",
    )


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("")
async def get_chains(
    session: AsyncSession = Depends(get_session),
    target: str = Query(..., description="Target item_id or #tag"),
    start: str | None = Query(default=None, description="Start item_id or #tag; omit for backward traversal"),
    modpack: str | None = Query(default=None, description="ModPack UUID or slug"),
    modpack_version: str | None = Query(default=None, description="ModPack version UUID or version string; defaults to latest published"),
    mod: list[str] = Query(default=[], description="Mod slug or UUID, optionally @version (e.g. mekanism@10.7)"),
    game: str | None = Query(default=None, description="Game UUID or slug"),
    game_version: str | None = Query(default=None, description="Game version UUID or version string"),
    exclude: list[str] = Query(default=[], description="Namespace ('mekanism') or recipe_type ('mekanism:purifying') to exclude"),
    exclude_machine: list[str] = Query(default=[], description="Machine ids whose recipes must not be used"),
    exclude_item: list[str] = Query(default=[], description="Item ids no recipe in the route may touch"),
    exclude_mod: list[str] = Query(default=[], description="Mod namespaces: skip their recipe types and any recipe using their items"),
    rank: Literal["auto", "yield", "simple"] = Query(
        default="auto",
        description="yield: most output per input (least raw per item going backward); "
                    "simple: fewest steps and machines; auto: yield when `start` is given, else simple",
    ),
    allow_cycles: bool = Query(default=False),
    max_depth: int = Query(default=8, ge=1, le=12),
    limit: int = Query(default=50, ge=1, le=200),
    quantity: float = Query(default=1.0, gt=0.0),
) -> dict[str, Any]:

    mod_version_ids, scope_info = await _resolve_scope(
        session, modpack, modpack_version, mod, game, game_version
    )

    loaded = await _get_graph(session, mod_version_ids)
    tag_to_items, item_to_canonical, graph = loaded.tag_to_items, loaded.item_to_canonical, loaded.graph

    target_items_list = _resolve(target, tag_to_items)
    if not target_items_list:
        return _empty_result(scope_info, target, start, "No items found for target")

    target_items_set = set(target_items_list)

    if not loaded.n_recipes:
        return _empty_result(scope_info, target, start, "No approved recipes in scope")

    direction: str
    by_yield = rank == "yield" or (rank == "auto" and start is not None)
    # Yield rankings need the deep, high-multiplier routes too, so don't stop at the first 400 found.
    enough = 10**9 if by_yield else ENOUGH_CHAINS

    if start is not None:
        direction = "forward"
        start_items = _resolve(start, tag_to_items)
        if not start_items:
            return _empty_result(scope_info, target, start, "No items found for start")

        excl = _Excluder(exclude, exclude_machine, exclude_item, exclude_mod, keep=target_items_set | set(start_items))
        steps_to = await asyncio.to_thread(_steps_to, target_items_set, graph, excl)
        async with _search_slots:
            raw_chains, cycles_excluded, truncated = await asyncio.to_thread(
                _deepen,
                lambda d, b: _forward_dfs(start_items, target_items_set, graph, excl, d, allow_cycles, b, steps_to),
                max_depth, enough,
            )
        raw_chains = _rank_forward(raw_chains, target_items_set, by_yield)
        memory.trim()
        total_found = len(raw_chains)
        paged = raw_chains[:limit]

        serialized = [
            _serialize_forward_chain(
                steps=chain,
                target_items=target_items_set,
                item_to_canonical=item_to_canonical,
                quantity_scale=quantity,
                has_cycle=has_cycle,
                cycle_at_step=cycle_step,
                rank=i + 1,
            )
            for i, (chain, has_cycle, cycle_step) in enumerate(paged)
        ]
        cycles_found = sum(1 for _, hc, _ in raw_chains if hc)

    else:
        direction = "backward"

        excl = _Excluder(exclude, exclude_machine, exclude_item, exclude_mod, keep=target_items_set)
        async with _search_slots:
            raw_chains_b, cycles_excluded, truncated = await asyncio.to_thread(
                _deepen,
                lambda d, b: _backward_dfs(target_items_list, graph, excl, d, allow_cycles, b),
                max_depth, enough,
            )
        raw_chains_b = _rank_backward(raw_chains_b, by_yield)
        memory.trim()
        total_found = len(raw_chains_b)

        paged_b = raw_chains_b[:limit]

        serialized = [
            _serialize_backward_chain(
                steps=chain,
                target_items=target_items_set,
                item_to_canonical=item_to_canonical,
                quantity_scale=quantity,
                has_cycle=has_cycle,
                cycle_at_step=cycle_step,
                raw_item_id=raw_item,
                raw_qty=raw_qty,
                rank=i + 1,
            )
            for i, (chain, has_cycle, cycle_step, raw_item, raw_qty) in enumerate(paged_b)
        ]
        cycles_found = sum(1 for _, hc, _, _, _ in raw_chains_b if hc)

    return {
        "target": target,
        "start": start,
        "direction": direction,
        "scope": scope_info,
        "quantity": quantity,
        "total_found": total_found,
        "returned": len(serialized),
        "cycles_found": cycles_found,
        "cycles_excluded": cycles_excluded,
        "excluded_applied": exclude,
        "excluded": excl.describe(),
        "rank": "yield" if by_yield else "simple",
        "truncated": truncated,
        "chains": serialized,
    }


def _empty_result(scope_info: dict, target: str, start: str | None, note: str) -> dict[str, Any]:
    return {
        "target": target,
        "start": start,
        "direction": "forward" if start else "backward",
        "scope": scope_info,
        "quantity": 1.0,
        "total_found": 0,
        "returned": 0,
        "cycles_found": 0,
        "cycles_excluded": 0,
        "excluded_applied": [],
        "chains": [],
        "note": note,
    }

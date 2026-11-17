"""
Factory calculator: "I want N of this per minute — what do I build?"

Unlike the route finder (which follows one item along a path), this solves the whole tree: it picks one
recipe for every item (cheapest by default, any can be overridden), then walks the tree from the target
down, accumulating how many crafts per second each recipe needs from everything that consumes its output.
Machines, power and raw resources follow from that.

Pure functions over the in-memory recipe graph (`chains._RecipeGraph`); no database access.

Model, deliberately simple:
  * one recipe per item; byproducts of a chosen recipe are credited against later demand for them and the
    rest is reported as surplus (they are not routed anywhere)
  * circular dependencies (an item that needs itself) are broken by treating that input as supplied
    from outside, with a warning
  * a machine tier is picked per machine type; modules fill every slot of every machine that can use them
  * not modelled: beacons, mining/other research bonuses, quality, pollution, belts, fuel for burner machines
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field

from app.api.v1.routes.chains import _Recipe, _RecipeGraph, _Slot, _tiers_for

MIN_FACTOR = 0.2          # speed and energy consumption can drop to 20% at most


@dataclass
class Module:
    name: str
    effects: dict[str, float]     # speed / productivity / consumption, as additive fractions


@dataclass
class Settings:
    overrides: dict[str, uuid.UUID] = field(default_factory=dict)       # item id -> recipe id to use
    tier_pref: dict[str, str] = field(default_factory=dict)             # machine name -> tier name
    module: str | None = None                                           # module name filling all slots
    modules: dict[str, dict[str, Module]] = field(default_factory=dict)  # machine id -> module name -> Module
    blocked: set[uuid.UUID] = field(default_factory=set)                # recipes never to pick
    beacons: int = 0                                                    # beacons reaching every machine that takes modules
    beacon_module: str | None = None                                    # module name filling both beacon slots
    beacon_share: float = 1.0                                           # machines each beacon serves (for the beacon count)
    beacon: dict | None = None                                          # the game's beacon: module_slots, effectivity, profile, ...
    belt: float | None = None                                           # items per second one belt carries
    balance: bool = True                                                # use up leftover fluids (cracking) where it helps


def recipe_label(r: _Recipe) -> str:
    """'factorio/space-age/space-age/recipe/casting-iron@metallurgy' -> 'Casting iron'."""
    last = r.import_key.rsplit("/", 1)[-1].split("@")[0]
    return last.replace("-", " ").replace("_", " ").capitalize()


def _inputs(r: _Recipe) -> list[_Slot]:
    """Everything the recipe consumes. Catalysts don't exist in throughput games (they are netted out)."""
    return [*r.inputs, *r.aux_inputs]


def _candidates(graph: _RecipeGraph, item: str, blocked: set[uuid.UUID]) -> list[_Recipe]:
    seen: dict[uuid.UUID, _Recipe] = {}
    for r in graph.recipes_producing(item):
        if r.id not in blocked and r.id not in seen and r.time_ticks:
            seen[r.id] = r
    return list(seen.values())


FOREIGN_MOD_PENALTY = 500.0


def best_recipes(graph: _RecipeGraph, blocked: set[uuid.UUID], settings: Settings | None = None) -> dict[str, _Recipe]:
    """The default recipe for every item: the one with the least total effort per unit, where effort is one
    per recipe step plus what its inputs cost (a mined ore costs 1, a plate smelted from it 2, ...).
    Recipes from mods other than the primary one (the alphabetically first, i.e. the base game) carry a big
    penalty, so they are only chosen where the base game has no way to make the item — otherwise the default
    for iron plate would be a lava foundry and for heavy oil another planet's oil ocean. Source recipes
    (mining, pumping) cost one per unit mined however much a single run yields.
    Productivity counts: a recipe that returns 1.5x per craft (a foundry or electromagnetic plant, or any machine
    with productivity modules selected) costs a third less per unit, so it wins over the same recipe in a plain
    machine. Computed by relaxation so loops (coal liquefaction, asteroid reprocessing) cannot trap it."""
    settings = settings or Settings()
    recipes: dict[uuid.UUID, _Recipe] = {}
    for rs in graph._by_output.values():
        for r in rs:
            if r.time_ticks and r.id not in blocked:
                recipes[r.id] = r
    primary = min((r.mod for r in recipes.values() if r.mod), default="")
    # deterministic: ties go to the recipe whose key sorts first (plain before "@electromagnetics" variants)
    recipes = dict(sorted(recipes.items(), key=lambda kv: (kv[1].mod != primary, kv[1].import_key)))
    # things nothing makes (and are not mined) are raw: one unit of effort each
    cost: dict[str, float] = {
        s.item_id: 1.0 for r in recipes.values() for s in _inputs(r) if s.item_id and not graph.recipes_producing(s.item_id)
    }
    best: dict[str, _Recipe] = {}
    for _ in range(60):
        changed = False
        for r in recipes.values():
            total = 1.0 + (FOREIGN_MOD_PENALTY if r.mod and r.mod != primary else 0.0)
            tier = pick_tier(r, settings.tier_pref)
            gain = 1.0 + (module_bonus(r, tier, settings)[1] if tier else 0.0)
            for s in _inputs(r):
                if not s.item_id:
                    break                                   # tag inputs are not priced
                c = cost.get(s.item_id)
                if c is None:
                    break
                total += s.quantity * c
            else:
                source = not r.inputs and not r.aux_inputs
                for o in r.outputs:
                    if o.item_id and o.quantity > 0:
                        c = total if source else total / (o.quantity * gain)
                        if c < cost.get(o.item_id, math.inf) - 1e-9:
                            cost[o.item_id], best[o.item_id] = c, r
                            changed = True
        if not changed:
            break
    return best


def pick_tier(r: _Recipe, pref: dict[str, str]) -> dict | None:
    tiers = _tiers_for(r)
    if not tiers:
        return None
    wanted = pref.get(r.machine.name)
    for t in tiers:
        if t["name"] == wanted:
            return t
    electric = [t for t in tiers if not t.get("fuel")] or tiers
    return electric[-1]                                     # fastest electric one


def _find_module(settings: Settings, name: str | None) -> Module | None:
    if not name:
        return None
    for ms in settings.modules.values():
        if name in ms:
            return ms[name]
    return None


def beacon_bonus(tier: dict, settings: Settings) -> tuple[float, float]:
    """(speed, consumption) bonus a machine gets from the beacons reaching it. Only machines with module slots are
    affected, a beacon takes no productivity, and with n beacons each one's effect is scaled by profile[n - 1]
    (the beacon's diminishing returns), so 4 beacons give 4 * 0.5 = 2x one beacon's effect, not 4x."""
    b = settings.beacon
    mod = _find_module(settings, settings.beacon_module)
    if not (b and mod and settings.beacons > 0 and tier.get("module_slots")):
        return 0.0, 0.0
    if any(k not in (b.get("allowed_effects") or []) for k in mod.effects):
        return 0.0, 0.0                                      # e.g. productivity modules do not fit a beacon
    profile = b.get("profile") or [1.0]
    n = settings.beacons
    each = b.get("module_slots", 2) * b.get("effectivity", 1.0) * profile[min(n, len(profile)) - 1]
    allowed = set(tier.get("allowed_effects") or [])
    return (n * each * mod.effects.get("speed", 0.0) if "speed" in allowed else 0.0,
            n * each * mod.effects.get("consumption", 0.0) if "consumption" in allowed else 0.0)


MAX_PRODUCTIVITY = 3.0    # the game caps the productivity bonus at +300%


def module_bonus(r: _Recipe, tier: dict, settings: Settings) -> tuple[float, float, float, int, str | None]:
    """(speed bonus, productivity bonus, consumption bonus, module count, module name) for one machine.
    The machine's own productivity (`base_productivity`: foundry, electromagnetic plant and biochamber give
    +50%) always counts, but only for recipes that allow productivity. A module is only used where every
    effect it has is allowed by the machine (and productivity by the recipe). Beacons add speed and consumption."""
    base = float(tier.get("base_productivity") or 0) if r.allow_productivity else 0.0
    b_speed, b_cons = beacon_bonus(tier, settings)
    mod = settings.modules.get(r.machine.id, {}).get(settings.module or "")
    slots = int(tier.get("module_slots") or 0)
    allowed = set(tier.get("allowed_effects") or [])
    if (not mod or not slots or any(k not in allowed for k in mod.effects)
            or (mod.effects.get("productivity", 0) > 0 and not r.allow_productivity)):
        return b_speed, min(base, MAX_PRODUCTIVITY), b_cons, 0, None
    e = mod.effects
    return (slots * e.get("speed", 0.0) + b_speed, min(base + slots * e.get("productivity", 0.0), MAX_PRODUCTIVITY),
            slots * e.get("consumption", 0.0) + b_cons, slots, mod.name)


_FLUID = ("fluid:", "chemical:")

Converter = tuple[_Recipe, str]       # a recipe that runs on a leftover fluid, and which input it is fed from


def _is_fluid(item: str | None) -> bool:
    return bool(item) and str(item).startswith(_FLUID)


def _fluid_surplus(result: dict) -> float:
    return sum(x["rate"] for x in result["surplus"] if _is_fluid(x["item_id"]))


def _new_node(r: _Recipe, tier: dict | None, prod_b: float, speed_b: float, cons_b: float,
              n_modules: int, module_name: str | None) -> dict:
    base = min(float(tier.get("base_productivity") or 0), prod_b) if tier and r.allow_productivity else 0.0
    return {
        "recipe_id": str(r.id), "recipe": recipe_label(r), "machine": r.machine.name, "seconds": r.time_ticks / 60,
        "crafts_per_s": 0.0, "depth": 0, "makes": [], "inputs": {}, "tier": None, "machines": None,
        "machines_exact": None, "power_kw": 0.0, "fuel": False, "modules": n_modules, "module_name": module_name,
        "productivity": prod_b, "base_productivity": base, "_r": r, "_tier": tier, "_speed": speed_b, "_cons": cons_b,
    }


def _solve(
    graph: _RecipeGraph, names: dict[str, str], target: str, rate: float, settings: Settings,
    default: dict[str, _Recipe], converters: list[Converter],
) -> dict:
    warnings: list[str] = []

    def choose(item: str) -> _Recipe | None:
        want = settings.overrides.get(item)
        if want:
            r = next((r for r in _candidates(graph, item, settings.blocked) if r.id == want), None)
            if r:
                return r
        return default.get(item)

    # 1. depth-first: the recipe per item, the order (children before parents), and loops to break
    chosen: dict[str, _Recipe | None] = {}
    order: list[str] = []
    state: dict[str, int] = {}
    broken: set[tuple[str, str]] = set()          # (item made, input item) edges ignored to break a loop

    def visit(item: str) -> None:
        if state.get(item) == 1:
            return
        state[item] = 0
        r = chosen[item] = choose(item)
        for s in (_inputs(r) if r else []):
            if not s.item_id:
                continue
            if state.get(s.item_id) == 0:
                broken.add((item, s.item_id))
                warnings.append(f"{names.get(s.item_id, s.item_id)} is needed to make {names.get(item, item)} "
                                f"and the other way round; it is counted as supplied from outside here.")
            else:
                visit(s.item_id)
        state[item] = 1
        order.append(item)

    for c, feed in converters:                    # what a converter needs besides its leftover feed is part of the plan
        for s in _inputs(c):
            if s.item_id and s.item_id != feed:
                visit(s.item_id)
    visit(target)

    # 2. demand flows from the target down (parents before children = reversed post-order). One node per
    # recipe: a recipe chosen for several items (oil processing makes three) runs once, for all of them.
    # `carry` is what the previous pass saw other recipes make as a byproduct, `conv` how often the previous pass ran
    # each converter. Parents are handled before children, so a byproduct of a recipe further down (heavy oil
    # cracking's light oil, coal liquefaction's petroleum gas) can only be credited to demand that was already
    # handled by repeating the pass.
    def propagate(carry: dict[str, float], conv: dict[uuid.UUID, float]):
        demand: dict[str, float] = {target: rate}
        supply: dict[str, float] = {}               # byproducts made so far, credited against demand
        produced: dict[str, float] = {}             # everything made, for the surplus
        eaten: dict[str, float] = {}                # leftover fluid used up by converters
        depth: dict[str, int] = {target: 0}
        nodes: dict[uuid.UUID, dict] = {}
        raw: list[dict] = []
        runs: list[tuple[_Recipe, str, dict | None, float, float, float, float, int, str | None]] = []

        for c, feed in converters:                  # converters run on leftovers: their output is credited up front
            n = conv.get(c.id, 0.0)
            if n <= 1e-12:
                continue
            tier = pick_tier(c, settings.tier_pref)
            speed_b, prod_b, cons_b, n_mod, mod_name = module_bonus(c, tier, settings) if tier else (0, 0, 0, 0, None)
            runs.append((c, feed, tier, n, prod_b, speed_b, cons_b, n_mod, mod_name))
            for o in c.outputs:
                if o.item_id:
                    amount = n * o.quantity * (1 + prod_b)
                    produced[o.item_id] = produced.get(o.item_id, 0.0) + amount
                    supply[o.item_id] = supply.get(o.item_id, 0.0) + amount
            for s in _inputs(c):
                if not s.item_id:
                    continue
                if s.item_id == feed:
                    eaten[feed] = eaten.get(feed, 0.0) + n * s.quantity
                else:
                    demand[s.item_id] = demand.get(s.item_id, 0.0) + n * s.quantity

        for item in reversed(order):
            r = chosen[item]
            need = max(0.0, demand.get(item, 0.0) - max(supply.get(item, 0.0), carry.get(item, 0.0)))
            label = names.get(item, item.split(":")[-1].replace("-", " "))
            if r is None:
                if need > 1e-9:
                    raw.append({"item_id": item, "name": label, "rate": need, "machine": None, "machines": None})
                continue
            if need <= 1e-9:
                continue                                         # fully covered by a byproduct of another recipe
            out_qty = sum(o.quantity for o in r.outputs if o.item_id == item) or 1.0
            tier = pick_tier(r, settings.tier_pref)
            speed_b, prod_b, cons_b, n_modules, module_name = module_bonus(r, tier, settings) if tier else (0, 0, 0, 0, None)
            crafts = need / (out_qty * (1 + prod_b))
            node = nodes.get(r.id)
            if node is None:
                node = nodes[r.id] = _new_node(r, tier, prod_b, speed_b, cons_b, n_modules, module_name)
            node["crafts_per_s"] += crafts
            node["depth"] = max(node["depth"], depth.get(item, 0))
            node["makes"].append({"item_id": item, "name": label, "rate": need, "alternatives": [
                {"id": str(alt.id), "machine": alt.machine.name, "recipe": recipe_label(alt),
                 "inputs": ", ".join(names.get(x.item_id or "", x.item_id or "?") for x in _inputs(alt)) or "nothing"}
                for alt in _candidates(graph, item, settings.blocked) if alt.id != r.id]})
            for o in r.outputs:                                  # byproducts offset later demand, the rest is surplus
                if not o.item_id:
                    continue
                produced[o.item_id] = produced.get(o.item_id, 0.0) + crafts * o.quantity * (1 + prod_b)
                if o.item_id != item:
                    supply[o.item_id] = supply.get(o.item_id, 0.0) + crafts * o.quantity * (1 + prod_b)
            for s in _inputs(r):
                if not s.item_id or (item, s.item_id) in broken:
                    continue
                flow = crafts * s.quantity
                demand[s.item_id] = demand.get(s.item_id, 0.0) + flow
                depth[s.item_id] = max(depth.get(s.item_id, 0), depth.get(item, 0) + 1)
                node["inputs"][s.item_id] = node["inputs"].get(s.item_id, 0.0) + flow

        for c, feed, tier, n, prod_b, speed_b, cons_b, n_mod, mod_name in runs:     # the converters' own rows
            node = nodes.get(c.id)
            if node is None:
                node = nodes[c.id] = _new_node(c, tier, prod_b, speed_b, cons_b, n_mod, mod_name)
            node["crafts_per_s"] += n
            node["converter"] = True
            node["feed"] = feed
            node["depth"] = max(node["depth"], 1)
            for o in c.outputs:
                if o.item_id:
                    node["makes"].append({"item_id": o.item_id, "name": names.get(o.item_id, o.item_id), "alternatives": [],
                                          "rate": n * o.quantity * (1 + prod_b), "leftover_use": True})
            for s in _inputs(c):
                if s.item_id:
                    node["inputs"][s.item_id] = node["inputs"].get(s.item_id, 0.0) + n * s.quantity
        return demand, produced, depth, nodes, raw, supply, eaten

    # the passes converge on a fixed point: each credit lowers some demand, which lowers the byproducts below it
    carry: dict[str, float] = {}
    conv: dict[uuid.UUID, float] = {}
    for _ in range(150):
        demand, produced, depth, nodes, raw, supply, eaten = propagate(carry, conv)
        nxt = {i: 0.5 * carry.get(i, 0.0) + 0.5 * v for i, v in supply.items()}
        left = {i: produced[i] - demand.get(i, 0.0) - eaten.get(i, 0.0) for i in produced}
        nconv = {}
        for c, feed in converters:      # run each converter a bit more (or less) so its feed is exactly used up
            qty = sum(s.quantity for s in _inputs(c) if s.item_id == feed) or 1.0
            nconv[c.id] = max(0.0, conv.get(c.id, 0.0) + 0.5 * left.get(feed, 0.0) / qty)
        small = 1e-9 * max(1.0, rate)
        done = (all(abs(nxt.get(i, 0.0) - carry.get(i, 0.0)) <= small for i in {*nxt, *carry})
                and all(abs(nconv[i] - conv.get(i, 0.0)) <= small for i in nconv))
        carry, conv = nxt, nconv
        if done:
            break

    # 3. machines and power from the final crafts per second of every recipe
    rows = list(nodes.values())
    total_power = total_fuel = 0.0
    total_machines = 0
    beacon_machines = 0
    for node in rows:
        r, tier = node.pop("_r"), node.pop("_tier")
        speed_b, cons_b = node.pop("_speed"), node.pop("_cons")
        node["item_id"], node["name"], node["rate"] = node["makes"][0]["item_id"], node["makes"][0]["name"], node["makes"][0]["rate"]
        node["inputs"] = [{"item_id": i, "name": names.get(i, i.split(":")[-1]), "rate": v} for i, v in node["inputs"].items()]
        if not tier:
            continue
        exact = node["crafts_per_s"] * node["seconds"] / (tier["speed"] * max(MIN_FACTOR, 1 + speed_b))
        count = math.ceil(exact - 1e-9)
        power = count * tier.get("power_kw", 0.0) * max(MIN_FACTOR, 1 + cons_b)
        node.update(tier=tier["name"], machines_exact=exact, machines=count, power_kw=power, fuel=bool(tier.get("fuel")))
        if any(beacon_bonus(tier, settings)):
            node["beacon_speed"] = beacon_bonus(tier, settings)[0]
            beacon_machines += count
        total_machines += count
        if tier.get("fuel"):
            total_fuel += power
        else:
            total_power += power

    # a source recipe (mining drill, pump) has no inputs: what it makes is a raw resource of the factory
    resources = [
        {"item_id": m["item_id"], "name": m["name"], "rate": m["rate"], "machine": n["machine"], "machines": n["machines"]}
        for n in rows if not n["inputs"] for m in n["makes"]
    ] + raw
    beacons = math.ceil(beacon_machines * settings.beacons / max(settings.beacon_share, 1e-9) - 1e-9) if beacon_machines else 0
    beacon_kw = beacons * (settings.beacon or {}).get("power_kw", 0.0)
    total_power += beacon_kw
    if settings.belt:                                        # belts carry items, not fluids
        for lst in ([m for n in rows for m in n["makes"]], resources, [i for n in rows for i in n["inputs"]]):
            for x in lst:
                if not _is_fluid(x["item_id"]):
                    x["belts"] = x["rate"] / settings.belt
    surplus = [
        {"item_id": i, "name": names.get(i, i), "rate": produced[i] - demand.get(i, 0.0) - eaten.get(i, 0.0)}
        for i in produced if produced[i] - demand.get(i, 0.0) - eaten.get(i, 0.0) > 1e-5 * max(rate, 1e-9)
    ]
    return {
        "rows": rows, "resources": resources, "surplus": surplus, "warnings": list(dict.fromkeys(warnings)),
        "power_kw": total_power, "fuel_kw": total_fuel, "machines": total_machines,
        "beacons": beacons, "beacon_power_kw": beacon_kw,
        "found": target in chosen and chosen[target] is not None, "notes": [],
    }


def _pick_converters(graph: _RecipeGraph, base: dict, settings: Settings) -> list[Converter]:
    """Recipes that could use up the fluids the plan leaves over: a recipe fed by a leftover fluid whose products are
    fluids the plan needs (or that another such recipe needs): oil cracking. One per leftover fluid."""
    leftover = [x["item_id"] for x in base["surplus"] if _is_fluid(x["item_id"])]
    if not leftover:
        return []
    wanted = {i["item_id"] for n in base["rows"] for i in n["inputs"] if _is_fluid(i["item_id"])}
    recipes: dict[uuid.UUID, _Recipe] = {}
    for rs in graph._by_output.values():
        for r in rs:
            if r.time_ticks and r.id not in settings.blocked and r.outputs and all(_is_fluid(o.item_id) for o in r.outputs):
                recipes[r.id] = r
    eats: dict[str, list[_Recipe]] = {}
    for r in recipes.values():
        for s in _inputs(r):
            if _is_fluid(s.item_id):
                eats.setdefault(s.item_id, []).append(r)
    for _ in range(4):                                       # a fluid is useful if something turns it into a useful one
        for item, rs in list(eats.items()):
            if item not in wanted and any(o.item_id in wanted for r in rs for o in r.outputs if o.item_id != item):
                wanted.add(item)
    picked: list[Converter] = []
    fed: set[str] = set()
    frontier = list(leftover)
    for _ in range(4):
        later: list[str] = []
        for feed in frontier:
            if feed in fed:
                continue
            fed.add(feed)
            best: tuple[float, _Recipe] | None = None
            for r in eats.get(feed, []):
                made = sum(o.quantity for o in r.outputs if o.item_id in wanted and o.item_id != feed)
                eaten = sum(s.quantity for s in _inputs(r) if s.item_id == feed)
                if made > 0 and eaten > 0 and (best is None or (made / eaten, r.import_key) > (best[0], best[1].import_key)):
                    best = (made / eaten, r)
            if best:
                picked.append((best[1], feed))
                later += [o.item_id for o in best[1].outputs if o.item_id and o.item_id != feed]
        frontier = later
    return picked


def solve(
    graph: _RecipeGraph, names: dict[str, str], target: str, rate: float, settings: Settings | None = None,
) -> dict:
    """`rate` is items per second. Returns rows (target first), totals, raw resources, surplus and warnings.

    Leftover fluids cannot simply be thrown away in the game (a full refinery stops), so when a fluid is left over
    the solver adds the recipes that use it up (heavy oil -> light oil -> petroleum gas by cracking), running each just
    often enough to eat the leftover, and keeps them only if less is left over."""
    settings = settings or Settings()
    default = best_recipes(graph, settings.blocked, settings)
    result = _solve(graph, names, target, rate, settings, default, [])
    converters = _pick_converters(graph, result, settings) if settings.balance else []
    if converters:
        trial = _solve(graph, names, target, rate, settings, default, converters)
        if _fluid_surplus(trial) < _fluid_surplus(result) - 1e-5 * max(rate, 1e-9):
            result = trial
    for row in result["rows"]:
        if row.get("converter"):
            result["notes"].append(f"{row['recipe']} ({row['machine']}) uses up leftover {names.get(row['feed'], row['feed'])}, "
                                   f"which the other recipes in this plan make more of than anything needs.")
    result["notes"] = list(dict.fromkeys(result["notes"]))
    return result


# ── Ratios ────────────────────────────────────────────────────────────────────

def _per_machine(r: _Recipe, settings: Settings) -> tuple[dict | None, float, float]:
    """(tier, crafts per second one machine runs, productivity) for a recipe in its default tier."""
    tier = pick_tier(r, settings.tier_pref)
    if not tier or not r.time_ticks:
        return None, 0.0, 0.0
    speed_b, prod_b, _, _, _ = module_bonus(r, tier, settings)
    return tier, tier["speed"] * max(MIN_FACTOR, 1 + speed_b) / (r.time_ticks / 60), prod_b


def small_ratio(x: float, limit: int = 12) -> tuple[int, int] | None:
    """x producers per consumer as a whole pair 'k consumers : p producers' (1 : 2, 3 : 2, ...): the smallest exact
    one (within 0.5%), else the smallest within 4%, else None."""
    if x <= 0:
        return None
    for tol in (0.005, 0.04):
        for k in range(1, limit + 1):
            p = round(x * k)
            if p >= 1 and abs(x * k - p) <= tol * p:
                return k, p
    return None


def ratios(graph: _RecipeGraph, names: dict[str, str], item: str, settings: Settings | None = None,
           limit: int = 8) -> list[dict]:
    """For every recipe that makes `item` (the default first): what one machine of it makes and consumes per second,
    and for each input how many machines of the input's default recipe keep it fed ("1 gear assembler : 2 rail
    assemblers"). Machines are the fastest electric tier unless `settings.tier_pref` says otherwise."""
    settings = settings or Settings()
    default = best_recipes(graph, settings.blocked, settings)
    candidates = _candidates(graph, item, settings.blocked)
    candidates.sort(key=lambda r: (default.get(item) is not r, r.import_key))
    out = []
    for r in candidates[:limit]:
        tier, crafts, prod = _per_machine(r, settings)
        if not tier:
            continue
        made = sum(o.quantity for o in r.outputs if o.item_id == item) * (1 + prod) * crafts
        inputs = []
        for s in _inputs(r):
            if not s.item_id:
                continue
            need = s.quantity * crafts
            feeder = default.get(s.item_id)
            row = {"item_id": s.item_id, "name": names.get(s.item_id, s.item_id.split(":")[-1].replace("-", " ")),
                   "per_s": need, "feeder": None, "machines": None, "pair": None}
            if feeder:
                f_tier, f_crafts, f_prod = _per_machine(feeder, settings)
                f_out = sum(o.quantity for o in feeder.outputs if o.item_id == s.item_id) * (1 + f_prod) * f_crafts
                if f_tier and f_out > 0:
                    row["feeder"] = {"machine": f_tier["name"], "recipe": recipe_label(feeder), "recipe_id": str(feeder.id)}
                    row["machines"] = need / f_out
                    row["pair"] = small_ratio(row["machines"])
            inputs.append(row)
        out.append({
            "recipe_id": str(r.id), "recipe": recipe_label(r), "machine": r.machine.name, "tier": tier["name"],
            "seconds": r.time_ticks / 60, "default": default.get(item) is r, "makes_per_s": made,
            "productivity": prod, "inputs": inputs,
        })
    return out

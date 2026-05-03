"""
ATM10 Ore Processing Chain Analyzer
Loads the KubeJS debug export, builds a recipe graph, and finds all
processing chains from raw ore → target item (default: ingot).

Usage:
    python analyzer.py --ore gold --target ingot
    python analyzer.py --ore gold --target ingot --all-ores
    python analyzer.py --list-ores
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Optional

EXPORT_ROOT = Path("allthemons_export")

# ──────────────────────────────────────────────────────────────────────────────
# Recipe normalisation
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Slot:
    """One ingredient or output slot."""
    tag: Optional[str]          # e.g. "c:ores/gold"  (None if item)
    item: Optional[str]         # e.g. "minecraft:gold_ingot"  (None if tag)
    count: float                # quantity (may be fractional for chance outputs)
    fluid: bool = False         # True for fluid/chemical inputs (tracked but not counted as items)
    chemical: Optional[str] = None  # mekanism chemical id

    def __repr__(self):
        ref = f"#{self.tag}" if self.tag else self.item
        return f"{self.count}x {ref}"


@dataclass
class Recipe:
    """Normalised recipe, mod-agnostic."""
    id: str                     # file path used as stable id
    type: str                   # original recipe type string
    machine: str                # human label e.g. "Enrichment Chamber"
    namespace: str              # mod that owns the recipe file
    inputs: list[Slot]          # item/tag inputs only
    outputs: list[Slot]
    aux_inputs: list[Slot] = field(default_factory=list)   # chemicals, fluids
    time_ticks: Optional[int] = None
    conditions_met: bool = True  # almostunified conditional result

    def primary_output(self) -> Optional[Slot]:
        return self.outputs[0] if self.outputs else None


# Map recipe type → human-readable machine name
MACHINE_NAMES: dict[str, str] = {
    "minecraft:smelting":               "Furnace",
    "minecraft:blasting":               "Blast Furnace",
    "minecraft:stonecutting":           "Stonecutter",
    "minecraft:crafting_shaped":        "Crafting Table",
    "minecraft:crafting_shapeless":     "Crafting Table",
    "mekanism:enriching":               "Enrichment Chamber",
    "mekanism:crushing":                "Crusher",
    "mekanism:purifying":               "Purification Chamber",
    "mekanism:injecting":               "Chemical Injection Chamber",
    "mekanism:washing":                 "Chemical Washer",
    "mekanism:dissolution":             "Chemical Dissolution Chamber",
    "mekanism:crystallizing":           "Crystallizer",
    "mekanism:compressing":             "Osmium Compressor",
    "mekanism:combining":               "Combiner",
    "mekanism:centrifuging":            "Isotopic Centrifuge",
    "mekanism:separating":              "Electrolytic Separator",
    "mekanism:activating":              "Solar Neutron Activator",
    "actuallyadditions:crushing":       "AA Crusher",
    "create:crushing":                  "Create Crushing Wheels",
    "create:milling":                   "Create Millstone",
    "create:compacting":                "Create Compactor",
    "create:mixing":                    "Create Mixer",
    "create:splashing":                 "Create Spout/Waterwheel",
    "immersiveengineering:crusher":     "IE Crusher",
    "immersiveengineering:arc_furnace": "IE Arc Furnace",
    "immersiveengineering:blast_furnace": "IE Blast Furnace",
    "immersiveengineering:hammer_crushing": "IE Engineer's Hammer",
    "immersiveengineering:metal_press": "IE Metal Press",
    "occultism:crushing":               "Occultism Spirit Crusher",
    "oritech:grinder":                  "Oritech Grinder",
    "oritech:pulverizer":               "Oritech Pulverizer",
    "oritech:centrifuge":               "Oritech Centrifuge",
    "oritech:foundry":                  "Oritech Foundry",
    "oritech:refinery":                 "Oritech Refinery",
    "oritech:assembler":                "Oritech Assembler",
    "industrialforegoing:crusher":      "IF Crusher",
    "productivemetalworks:item_melting": "Productive Metalworks Melter",
    "productivemetalworks:item_casting": "Productive Metalworks Caster",
    "integrateddynamics:squeezer":            "ID Squeezer",
    "integrateddynamics:mechanical_squeezer": "ID Mechanical Squeezer",
    "integrateddynamics:drying_basin":        "ID Drying Basin",
    "integrateddynamics:mechanical_drying_basin": "ID Mechanical Drying Basin",
    "enderio:sag_milling":              "EnderIO SAG Mill",
    "railcraft:crusher":                "Railcraft Crusher",
    "immersiveengineering:arcfurnace":  "IE Arc Furnace",
    "pneumaticcraft:pressure_chamber":  "PNC Pressure Chamber",
    "mekanism:compressing":             "Osmium Compressor",
    "mekanism:combining":               "Combiner",
    "mekanism:metallurgic_infusing":    "Metallurgic Infuser",
}

def machine_name(recipe_type: str) -> str:
    return MACHINE_NAMES.get(recipe_type, recipe_type.split(":")[-1].replace("_", " ").title())


def _slot_from_ingredient(ing: Any, count: float = 1.0) -> Optional[Slot]:
    """Parse a generic ingredient object into a Slot."""
    if ing is None:
        return None
    if isinstance(ing, list):
        # Pick first (almostunified usually resolves these anyway)
        ing = ing[0] if ing else None
        if ing is None:
            return None
    tag = ing.get("tag")
    item = ing.get("item") or ing.get("id")
    cnt = ing.get("count", count)
    return Slot(tag=tag, item=item, count=float(cnt))


def _parse_conditions_met(data: dict) -> bool:
    """Check if almostunified marked this recipe as active."""
    conds = data.get("neoforge:conditions", [])
    for c in conds:
        if c.get("type") == "almostunified:conditional":
            return bool(c.get("conditions_met", True))
    return True


def parse_recipe(path: Path) -> Optional[Recipe]:
    """Parse one recipe JSON file into a normalised Recipe."""
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return None

    rtype = data.get("type", "")
    if not rtype:
        return None

    conditions_met = _parse_conditions_met(data)
    namespace = path.parts[path.parts.index("recipes") + 1] if "recipes" in path.parts else "unknown"
    machine = machine_name(rtype)
    recipe_id = str(path.relative_to(EXPORT_ROOT))

    inputs: list[Slot] = []
    outputs: list[Slot] = []
    aux_inputs: list[Slot] = []
    time_ticks: Optional[int] = None

    # ── Mekanism single-item-in single-item-out ────────────────────────────
    if rtype in (
        "mekanism:enriching", "mekanism:crushing", "mekanism:compressing",
        "mekanism:activating",
    ):
        s = _slot_from_ingredient(data.get("input"))
        if s:
            inputs.append(s)
        out = data.get("output", {})
        outputs.append(Slot(tag=None, item=out.get("id"), count=float(out.get("count", 1))))

    elif rtype in ("mekanism:purifying", "mekanism:injecting"):
        item_in = _slot_from_ingredient(data.get("item_input"))
        if item_in:
            inputs.append(item_in)
        chem = data.get("chemical_input", {})
        if chem:
            aux_inputs.append(Slot(tag=None, item=None, count=float(chem.get("amount", 1)),
                                   fluid=True, chemical=chem.get("chemical")))
        out = data.get("output", {})
        outputs.append(Slot(tag=None, item=out.get("id"), count=float(out.get("count", 1))))

    elif rtype == "mekanism:dissolution":
        item_in = _slot_from_ingredient(data.get("item_input"))
        if item_in:
            inputs.append(item_in)
        chem = data.get("chemical_input", {})
        if chem:
            aux_inputs.append(Slot(tag=None, item=None, count=float(chem.get("amount", 1)),
                                   fluid=True, chemical=chem.get("chemical")))
        out = data.get("output", {})
        # dissolution outputs a chemical, not an item — track as output for chain resolution
        outputs.append(Slot(tag=None, item=out.get("id"), count=float(out.get("amount", 1)),
                            fluid=True))

    elif rtype == "mekanism:washing":
        chem_in = data.get("chemical_input", {})
        if chem_in:
            inputs.append(Slot(tag=None, item=chem_in.get("chemical"),
                               count=float(chem_in.get("amount", 1)), fluid=True))
        fluid_in = data.get("fluid_input", {})
        if fluid_in:
            aux_inputs.append(Slot(tag=fluid_in.get("tag"), item=fluid_in.get("fluid"),
                                   count=float(fluid_in.get("amount", 1)), fluid=True))
        out = data.get("output", {})
        outputs.append(Slot(tag=None, item=out.get("id"), count=float(out.get("amount", 1)),
                            fluid=True))

    elif rtype == "mekanism:crystallizing":
        chem_in = data.get("input", {})
        inputs.append(Slot(tag=None, item=chem_in.get("chemical"),
                           count=float(chem_in.get("amount", 1)), fluid=True))
        out = data.get("output", {})
        outputs.append(Slot(tag=None, item=out.get("id"), count=float(out.get("count", 1))))

    # ── Vanilla smelting / blasting ───────────────────────────────────────
    elif rtype in ("minecraft:smelting", "minecraft:blasting"):
        s = _slot_from_ingredient(data.get("ingredient"))
        if s:
            inputs.append(s)
        res = data.get("result", {})
        if isinstance(res, str):
            outputs.append(Slot(tag=None, item=res, count=1.0))
        else:
            outputs.append(Slot(tag=None, item=res.get("id"), count=float(res.get("count", 1))))
        time_ticks = data.get("cookingtime")

    # ── Actually Additions crushing ───────────────────────────────────────
    elif rtype == "actuallyadditions:crushing":
        s = _slot_from_ingredient(data.get("ingredient"))
        if s:
            inputs.append(s)
        for r in data.get("result", []):
            res = r.get("result", {})
            chance = float(r.get("chance", 1.0))
            outputs.append(Slot(tag=None, item=res.get("id"),
                                count=float(res.get("count", 1)) * chance))

    # ── Occultism crushing ────────────────────────────────────────────────
    elif rtype == "occultism:crushing":
        s = _slot_from_ingredient(data.get("ingredient"))
        if s:
            inputs.append(s)
        res = data.get("result", {})
        outputs.append(Slot(tag=None, item=res.get("id"), count=float(res.get("count", 1))))

    # ── Create crushing / milling ─────────────────────────────────────────
    elif rtype in ("create:crushing", "create:milling", "create:compacting", "create:mixing"):
        for ing in data.get("ingredients", []):
            s = _slot_from_ingredient(ing)
            if s:
                inputs.append(s)
        # Merge multiple result slots with the same item (e.g. 1x clump + 0.75x clump → 1.75x clump)
        item_totals: dict[str, float] = {}
        for r in data.get("results", []):
            item_id = r.get("id")
            if not item_id:
                continue
            chance = float(r.get("chance", 1.0))
            item_totals[item_id] = item_totals.get(item_id, 0.0) + float(r.get("count", 1)) * chance
        for item_id, total in item_totals.items():
            outputs.append(Slot(tag=None, item=item_id, count=total))
        time_ticks = data.get("processing_time") or data.get("processingTime")

    # ── Integrated Dynamics squeezer / mechanical_squeezer ──────────────────
    elif rtype in ("integrateddynamics:squeezer", "integrateddynamics:mechanical_squeezer",
                   "integrateddynamics:drying_basin", "integrateddynamics:mechanical_drying_basin"):
        inp = data.get("input_item", {})
        if inp.get("item"):
            inputs.append(Slot(tag=None, item=inp["item"], count=1.0))
        elif inp.get("tag"):
            inputs.append(Slot(tag=inp["tag"], item=None, count=1.0))
        # output_items wraps each slot as {"item": {id, count}} or {"tag": {tag, count}}
        # with optional top-level "count" and "chance"
        out_list = data.get("output_items", [])
        if data.get("output_item"):
            out_list = [{"item": data["output_item"]}]
        item_totals: dict[str, float] = {}
        tag_totals: dict[str, float] = {}
        for slot in out_list:
            chance = float(slot.get("chance", 1.0))
            top_count = float(slot.get("count", 1))
            item_wrap = slot.get("item")
            tag_wrap = slot.get("tag")
            if item_wrap and isinstance(item_wrap, dict):
                item_id = item_wrap.get("id") or item_wrap.get("item")
                cnt = float(item_wrap.get("count", top_count)) * chance
                if item_id:
                    item_totals[item_id] = item_totals.get(item_id, 0.0) + cnt
            elif tag_wrap and isinstance(tag_wrap, dict):
                tag_id = tag_wrap.get("tag")
                cnt = float(tag_wrap.get("count", top_count)) * chance
                if tag_id:
                    tag_totals[tag_id] = tag_totals.get(tag_id, 0.0) + cnt
        for item_id, total in item_totals.items():
            outputs.append(Slot(tag=None, item=item_id, count=total))
        for tag_id, total in tag_totals.items():
            outputs.append(Slot(tag=tag_id, item=None, count=total))
        time_ticks = data.get("duration")

    # ── EnderIO SAG Mill ──────────────────────────────────────────────────────
    elif rtype == "enderio:sag_milling":
        s = _slot_from_ingredient(data.get("input"))
        if s:
            inputs.append(s)
        item_totals2: dict[str, float] = {}
        for out in data.get("outputs", []):
            chance = float(out.get("chance", 1.0))
            item_wrap = out.get("item", {})
            item_id = item_wrap.get("id") or item_wrap.get("item")
            cnt = float(item_wrap.get("count", 1)) * chance
            if item_id:
                item_totals2[item_id] = item_totals2.get(item_id, 0.0) + cnt
        for item_id, total in item_totals2.items():
            outputs.append(Slot(tag=None, item=item_id, count=total))

    # ── Railcraft Crusher ─────────────────────────────────────────────────────
    elif rtype == "railcraft:crusher":
        s = _slot_from_ingredient(data.get("ingredient"))
        if s:
            inputs.append(s)
        for out in data.get("outputs", []):
            prob = float(out.get("probability", out.get("chance", 1.0)))
            res = out.get("result", {})
            item_id = res.get("item") or res.get("id")
            cnt = float(res.get("count", 1)) * prob
            if item_id:
                outputs.append(Slot(tag=None, item=item_id, count=cnt))

    # ── PneumaticCraft Pressure Chamber ──────────────────────────────────────
    elif rtype == "pneumaticcraft:pressure_chamber":
        for inp in data.get("inputs", []):
            s = _slot_from_ingredient(inp)
            if s:
                inputs.append(s)
        for res in data.get("results", []):
            item_id = res.get("id") or res.get("item")
            outputs.append(Slot(tag=None, item=item_id, count=float(res.get("count", 1))))

    # ── Mekanism Compressor / Combiner / Metallurgic Infuser ─────────────────
    elif rtype in ("mekanism:compressing", "mekanism:combining", "mekanism:metallurgic_infusing"):
        main = _slot_from_ingredient(
            data.get("main_input") or data.get("item_input") or data.get("input"))
        if main:
            inputs.append(main)
        extra = data.get("extra_input") or data.get("chemical_input")
        if extra:
            aux_inputs.append(Slot(
                tag=extra.get("tag"), item=None,
                count=float(extra.get("amount", extra.get("count", 1))),
                fluid=True, chemical=extra.get("chemical")))
        out = data.get("output", {})
        outputs.append(Slot(tag=None, item=out.get("id"), count=float(out.get("count", 1))))

    # ── IE Arc Furnace ────────────────────────────────────────────────────────
    elif rtype == "immersiveengineering:arcfurnace":
        inp = _slot_from_ingredient(data.get("input"))
        if inp:
            inputs.append(inp)
        for add in data.get("additives", []):
            s = _slot_from_ingredient(add)
            if s:
                aux_inputs.append(s)
        out = data.get("result", {})
        if isinstance(out, dict):
            item_id = out.get("id") or out.get("item")
            if item_id:
                outputs.append(Slot(tag=None, item=item_id, count=float(out.get("count", 1))))
        time_ticks = data.get("time")

        # ── Immersive Engineering crusher ─────────────────────────────────────
    elif rtype == "immersiveengineering:crusher":
        s = _slot_from_ingredient(data.get("input"))
        if s:
            inputs.append(s)
        # IE primary result: {"item": ..., "count": N} or {"basePredicate": {"tag":...}, "count": N}
        res = data.get("result", {})
        if res:
            pred = res.get("basePredicate", res)  # unwrap basePredicate if present
            item = pred.get("item") or pred.get("id")
            tag = pred.get("tag")
            count = float(res.get("count", 1))
            outputs.append(Slot(tag=tag, item=item, count=count))
        # IE secondaries: list of {output: {tag/item}, chance: float}
        for sec in data.get("secondaries", []):
            chance = float(sec.get("chance", 1.0))
            out = sec.get("output", {})
            item = out.get("item") or out.get("id")
            tag = out.get("tag")
            outputs.append(Slot(tag=tag, item=item, count=chance))

    # ── Oritech machines ──────────────────────────────────────────────────
    elif rtype in ("oritech:grinder", "oritech:pulverizer", "oritech:centrifuge",
                   "oritech:foundry", "oritech:refinery", "oritech:assembler"):
        for ing in data.get("ingredients", []):
            s = _slot_from_ingredient(ing)
            if s:
                inputs.append(s)
        fluid_in = data.get("fluidInput", {})
        if fluid_in and fluid_in.get("fluid") and fluid_in["fluid"] != "minecraft:empty":
            aux_inputs.append(Slot(tag=None, item=fluid_in["fluid"],
                                   count=float(fluid_in.get("amount", 1)), fluid=True))
        item_totals3: dict[str, float] = {}
        for r in data.get("results", []):
            chance = float(r.get("chance", 1.0))
            item_id = r.get("id")
            if item_id:
                item_totals3[item_id] = item_totals3.get(item_id, 0.0) + float(r.get("count", 1)) * chance
        for item_id, total in item_totals3.items():
            outputs.append(Slot(tag=None, item=item_id, count=total))
        time_ticks = data.get("time")

    # ── Industrial Foregoing crusher ──────────────────────────────────────
    elif rtype == "industrialforegoing:crusher":
        s = _slot_from_ingredient(data.get("input"))
        if s:
            inputs.append(s)
        res = data.get("output", {})
        outputs.append(Slot(tag=None, item=res.get("id"), count=float(res.get("count", 1))))

    else:
        # Unknown type — skip silently
        return None

    if not inputs or not outputs:
        return None

    return Recipe(
        id=recipe_id,
        type=rtype,
        machine=machine,
        namespace=namespace,
        inputs=inputs,
        outputs=outputs,
        aux_inputs=aux_inputs,
        time_ticks=time_ticks,
        conditions_met=conditions_met,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Tag resolution
# ──────────────────────────────────────────────────────────────────────────────

class TagIndex:
    """
    Loads all item tag files and builds a bidirectional mapping:
      tag → set of concrete items
      item → set of tags it belongs to
    """

    def __init__(self, tag_root: Path):
        self._tag_to_items: dict[str, set[str]] = defaultdict(set)
        self._item_to_tags: dict[str, set[str]] = defaultdict(set)
        self._raw: dict[str, list] = {}  # tag → raw entries (may include sub-tags)
        self._load(tag_root)
        self._resolve()

    def _load(self, tag_root: Path):
        # Structure: tags/minecraft/item/<tag_namespace>/<tag_path>.json
        # e.g. tags/minecraft/item/c/ores/gold.json → tag id "c:ores/gold"
        #      tags/minecraft/item/minecraft/gold_ores.json → tag id "minecraft:gold_ores"
        item_dir = tag_root / "item"
        if not item_dir.exists():
            return
        for namespace_dir in item_dir.iterdir():
            if not namespace_dir.is_dir():
                continue
            tag_namespace = namespace_dir.name
            for f in namespace_dir.rglob("*.json"):
                rel = f.relative_to(namespace_dir)
                parts = list(rel.parts)
                parts[-1] = parts[-1].replace(".json", "")
                tag_id = tag_namespace + ":" + "/".join(parts)
                try:
                    entries = json.loads(f.read_text())
                    self._raw[tag_id] = entries if isinstance(entries, list) else []
                except Exception:
                    pass

    def _resolve(self, depth: int = 0):
        """Iteratively expand sub-tags until stable."""
        if depth > 10:
            return
        changed = False
        for tag_id, entries in self._raw.items():
            for entry in entries:
                if isinstance(entry, str):
                    clean = entry.rstrip("?")  # ? means optional
                    if clean.startswith("#"):
                        sub_tag = clean[1:]
                        sub_items = self._tag_to_items.get(sub_tag, set())
                        before = len(self._tag_to_items[tag_id])
                        self._tag_to_items[tag_id].update(sub_items)
                        if len(self._tag_to_items[tag_id]) != before:
                            changed = True
                    else:
                        self._tag_to_items[tag_id].add(clean)
                        self._item_to_tags[clean].add(tag_id)
        if changed:
            self._resolve(depth + 1)

    def items_for_tag(self, tag: str) -> set[str]:
        return self._tag_to_items.get(tag, set())

    def tags_for_item(self, item: str) -> set[str]:
        return self._item_to_tags.get(item, set())

    def item_matches_slot(self, item: str, slot: Slot) -> bool:
        if slot.item and slot.item == item:
            return True
        if slot.tag:
            return item in self.items_for_tag(slot.tag)
        return False

    def slot_matches_item_or_chemical(self, slot: Slot, candidates: set[str]) -> bool:
        """Return True if slot could be satisfied by any candidate item."""
        for c in candidates:
            if self.item_matches_slot(c, slot):
                return True
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Recipe graph
# ──────────────────────────────────────────────────────────────────────────────

class RecipeGraph:
    """
    Indexes all parsed recipes for fast lookup in both directions:
      - By output item id  (what produces X)
      - By input item id   (what can I make from X — forward traversal)
    """

    def __init__(self, recipes: list[Recipe], tags: TagIndex):
        self.tags = tags
        self.all: list[Recipe] = recipes
        self._by_output: dict[str, list[Recipe]] = defaultdict(list)
        self._by_input: dict[str, list[Recipe]] = defaultdict(list)

        for r in recipes:
            for slot in r.outputs:
                if slot.item:
                    self._by_output[slot.item].append(r)

            primary = next((s for s in r.inputs if not s.fluid), None)
            if primary:
                if primary.item:
                    self._by_input[primary.item].append(r)
                elif primary.tag:
                    for item in tags.items_for_tag(primary.tag):
                        self._by_input[item].append(r)

    def recipes_consuming(self, item_id: str) -> list[Recipe]:
        """All recipes whose primary input is this item (or a tag containing it)."""
        return self._by_input.get(item_id, [])

    def recipes_producing(self, item_id: str) -> list[Recipe]:
        """All recipes that output this specific item."""
        return self._by_output.get(item_id, [])


# ──────────────────────────────────────────────────────────────────────────────
# Chain traversal
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ChainStep:
    recipe: Recipe
    input_count: float      # how many of the input item we consume per ore
    output_count: float     # how many of the output item we get per ore


@dataclass
class Chain:
    steps: list[ChainStep]
    ore_item: str           # concrete starting item (e.g. "minecraft:gold_ore")
    target_item: str

    @property
    def ingots_per_ore(self) -> float:
        """Effective ingots produced per 1 ore block."""
        if not self.steps:
            return 0.0
        return self.steps[-1].output_count

    @property
    def machine_sequence(self) -> list[str]:
        return [s.recipe.machine for s in self.steps]

    @property
    def machines_required(self) -> set[str]:
        return set(self.machine_sequence)

    @property
    def step_count(self) -> int:
        return len(self.steps)

    def label(self) -> str:
        return " → ".join(self.machine_sequence)

    def chemicals_required(self) -> list[str]:
        chems = []
        for step in self.steps:
            for aux in step.recipe.aux_inputs:
                if aux.chemical:
                    chems.append(aux.chemical)
            for slot in step.recipe.inputs:
                if slot.fluid and slot.item:
                    chems.append(slot.item)
        return chems


def _slot_output_count(recipe: Recipe, target_slot_item: Optional[str],
                        target_slot_tag: Optional[str], tags: TagIndex) -> float:
    """Sum output counts for slots matching the target item/tag."""
    total = 0.0
    for slot in recipe.outputs:
        if slot.fluid:
            continue
        if target_slot_item and slot.item == target_slot_item:
            total += slot.count
        elif target_slot_tag and slot.item and slot.item in tags.items_for_tag(target_slot_tag):
            total += slot.count
        elif not target_slot_item and not target_slot_tag and slot.item:
            total += slot.count
    return total


def find_chains(
    start_items: list[str],        # concrete ore items (e.g. all items in #c:ores/gold)
    target_tag: str,               # e.g. "c:ingots/gold"
    graph: RecipeGraph,
    max_depth: int = 8,
    visited_types: Optional[frozenset[str]] = None,
) -> list[Chain]:
    """
    DFS chain finder. Starting from start_items, find all recipe sequences
    that ultimately produce something in target_tag.

    Returns one Chain per complete path found.
    """
    tags = graph.tags
    target_items = tags.items_for_tag(target_tag)
    all_chains: list[Chain] = []

    def dfs(
        current_items: list[str],   # what we have right now
        current_count: float,       # how many per original ore
        steps_so_far: list[ChainStep],
        depth: int,
        seen_recipe_ids: frozenset[str],
    ):
        if depth > max_depth:
            return

        # Check if any current item already is a target
        for ci in current_items:
            if ci in target_items:
                if steps_so_far:
                    all_chains.append(Chain(
                        steps=list(steps_so_far),
                        ore_item=start_items[0],
                        target_item=ci,
                    ))
                return

        # Try every recipe that CONSUMES one of our current items (forward traversal)
        tried: set[str] = set()
        for ci in current_items:
            for recipe in graph.recipes_consuming(ci):
                if recipe.id in seen_recipe_ids:
                    continue
                if recipe.id in tried:
                    continue
                tried.add(recipe.id)
                primary_input = next((s for s in recipe.inputs if not s.fluid), None)

                # Figure out scaling: how many times do we run this recipe
                # given current_count of input?
                input_count_needed = primary_input.count if primary_input else 1.0
                runs = current_count / input_count_needed

                # What does this recipe produce?
                next_items_and_counts: list[tuple[str, float]] = []
                for out_slot in recipe.outputs:
                    if out_slot.fluid:
                        # Fluids/chemicals are tracked differently — include
                        # as next item so washing/crystallizing chains work
                        if out_slot.item:
                            next_items_and_counts.append((out_slot.item, out_slot.count * runs))
                    elif out_slot.item:
                        next_items_and_counts.append((out_slot.item, out_slot.count * runs))

                if not next_items_and_counts:
                    continue

                # Pick the dominant output for the step label
                primary_out = next_items_and_counts[0]
                step = ChainStep(
                    recipe=recipe,
                    input_count=current_count,
                    output_count=primary_out[1],
                )

                dfs(
                    current_items=[item for item, _ in next_items_and_counts],
                    current_count=primary_out[1],
                    steps_so_far=steps_so_far + [step],
                    depth=depth + 1,
                    seen_recipe_ids=seen_recipe_ids | {recipe.id},
                )

    for ore_item in start_items:
        dfs(
            current_items=[ore_item],
            current_count=1.0,
            steps_so_far=[],
            depth=0,
            seen_recipe_ids=frozenset(),
        )

    return all_chains


# ──────────────────────────────────────────────────────────────────────────────
# Loading
# ──────────────────────────────────────────────────────────────────────────────

def load_all_recipes(export_root: Path) -> list[Recipe]:
    recipes = []
    for folder in ("recipes", "added_recipes"):
        folder_path = export_root / folder
        if not folder_path.exists():
            continue
        for path in folder_path.rglob("*.json"):
            r = parse_recipe(path)
            if r and r.conditions_met:
                recipes.append(r)
    return recipes


def load_tags(export_root: Path) -> TagIndex:
    return TagIndex(export_root / "tags" / "minecraft")


# ──────────────────────────────────────────────────────────────────────────────
# Display
# ──────────────────────────────────────────────────────────────────────────────

def fmt_item(item_id: Optional[str]) -> str:
    if not item_id:
        return "?"
    return item_id.split(":")[-1].replace("_", " ").title()


def fmt_count(n: float) -> str:
    if n == int(n):
        return str(int(n))
    return f"{n:.3f}".rstrip("0").rstrip(".")


def print_chain(chain: Chain, rank: int):
    print(f"\n  #{rank}  {chain.ingots_per_ore:.3f}x per ore  |  {chain.label()}")
    print(f"        Starting ore: {fmt_item(chain.ore_item)}")
    for i, step in enumerate(chain.steps, 1):
        r = step.recipe
        inp_str = ", ".join(
            f"{fmt_count(s.count)}x {fmt_item(s.item) if s.item else '#'+str(s.tag)}"
            for s in r.inputs if not s.fluid
        )
        out_str = ", ".join(
            f"{fmt_count(s.count)}x {fmt_item(s.item)}"
            for s in r.outputs if not s.fluid and s.item
        )
        aux_str = ""
        all_aux = r.aux_inputs + [s for s in r.inputs if s.fluid]
        if all_aux:
            aux_str = "  [+" + ", ".join(
                f"{fmt_count(a.count)}x {fmt_item(a.chemical or a.item)}"
                for a in all_aux
            ) + "]"
        print(f"        Step {i}: {inp_str} → {out_str}  ({r.machine}){aux_str}")
    chems = chain.chemicals_required()
    if chems:
        unique_chems = sorted(set(fmt_item(c) for c in chems))
        print(f"        Chemicals/fluids: {', '.join(unique_chems)}")


def print_summary_table(chains: list[Chain], ore_name: str, target_name: str):
    if not chains:
        print(f"  No chains found from {ore_name} → {target_name}")
        return

    sorted_chains = sorted(chains, key=lambda c: c.ingots_per_ore, reverse=True)

    print(f"\n{'═'*70}")
    print(f"  {ore_name.upper()} ORE → {target_name.upper()}")
    print(f"  Found {len(chains)} chain(s), sorted best→worst")
    print(f"{'═'*70}")

    for rank, chain in enumerate(sorted_chains, 1):
        print_chain(chain, rank)

    best = sorted_chains[0]
    worst = sorted_chains[-1]
    print(f"\n  ▶  Best:  {best.ingots_per_ore:.3f}x — {best.label()}")
    print(f"  ▶  Worst: {worst.ingots_per_ore:.3f}x — {worst.label()}")
    print()


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

COMMON_ORES = [
    "gold", "iron", "copper", "tin", "lead", "silver", "nickel", "osmium",
    "aluminum", "zinc", "platinum", "uranium", "iridium", "diamond", "emerald",
    "coal", "redstone", "lapis",
]


def main():
    parser = argparse.ArgumentParser(description="ATM10 Ore Processing Chain Analyzer")
    parser.add_argument("--ore", help="Ore name (e.g. gold, iron, copper)")
    parser.add_argument("--target-tag", help="Target output tag (default: c:ingots/<ore>)")
    parser.add_argument("--all-ores", action="store_true", help="Analyze all common ores")
    parser.add_argument("--list-ores", action="store_true", help="List detectable ores")
    parser.add_argument("--max-depth", type=int, default=7, help="Max chain depth (default 7)")
    parser.add_argument("--min-yield", type=float, default=0.0,
                        help="Only show chains with >= this ingots/ore")
    args = parser.parse_args()

    print("Loading recipes...")
    recipes = load_all_recipes(EXPORT_ROOT)
    print(f"  Loaded {len(recipes)} recipes")

    print("Loading tags...")
    tags = load_tags(EXPORT_ROOT)
    print(f"  Loaded {len(tags._tag_to_items)} tags")

    graph = RecipeGraph(recipes, tags)

    if args.list_ores:
        print("\nDetectable ores (have #c:ores/<name> tag with items):")
        for ore in sorted(COMMON_ORES):
            items = tags.items_for_tag(f"c:ores/{ore}")
            if items:
                print(f"  {ore:15s} — {len(items)} items in tag")
        return

    target_ores = COMMON_ORES if args.all_ores else ([args.ore] if args.ore else ["gold"])

    for ore_name in target_ores:
        ore_tag = f"c:ores/{ore_name}"
        ore_items = tags.items_for_tag(ore_tag)
        if not ore_items:
            print(f"\n  [{ore_name}] No items found in tag #{ore_tag}, skipping.")
            continue

        # Also add raw ore items
        raw_tag = f"c:raw_materials/{ore_name}"
        raw_items = tags.items_for_tag(raw_tag)

        target_tag = args.target_tag or f"c:ingots/{ore_name}"
        target_items = tags.items_for_tag(target_tag)
        if not target_items:
            # Fall back to gem tag (diamond, emerald, etc.)
            target_tag = f"c:gems/{ore_name}"
            target_items = tags.items_for_tag(target_tag)
        if not target_items:
            print(f"\n  [{ore_name}] No items in target tag #{target_tag}, skipping.")
            continue

        # Find chains starting from ore blocks
        print(f"\nAnalyzing {ore_name} (#{ore_tag} → #{target_tag})...")
        chains = find_chains(
            start_items=list(ore_items),
            target_tag=target_tag,
            graph=graph,
            max_depth=args.max_depth,
        )

        if args.min_yield > 0:
            chains = [c for c in chains if c.ingots_per_ore >= args.min_yield]

        # Deduplicate: same machine sequence + same per-step recipe types + same yield
        # Different starting ore variants (gold_ore vs deepslate_gold_ore) that feed the same
        # tag-based recipe are logically identical chains — collapse them, keeping the
        # most recognisable ore name (shortest / most generic).
        seen: dict[tuple, Chain] = {}
        for c in chains:
            # Key: sequence of (recipe_type, input_tag_or_item, primary_output_item) per step
            step_sig = tuple(
                (s.recipe.type,
                 s.recipe.inputs[0].tag or s.recipe.inputs[0].item if s.recipe.inputs else "",
                 s.recipe.outputs[0].item if s.recipe.outputs else "")
                for s in c.steps
            )
            key = (step_sig, round(c.ingots_per_ore, 4))
            if key not in seen:
                seen[key] = c
            else:
                # Prefer the chain whose starting ore is the canonical overworld one
                existing = seen[key]
                if "minecraft:gold_ore" in c.ore_item or "minecraft:iron_ore" in c.ore_item                         or len(c.ore_item) < len(existing.ore_item):
                    seen[key] = c
        unique_chains = list(seen.values())

        print_summary_table(unique_chains, ore_name, target_tag.split("/")[-1])


if __name__ == "__main__":
    main()
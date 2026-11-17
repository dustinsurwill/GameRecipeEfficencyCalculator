"""
Factorio importer — reads the recipe prototypes straight from an installed game.

Factorio ships its data as plain Lua (`<game>/data/<mod>/prototypes/recipe*.lua`, each file is a
`data:extend({...})` call). A tiny Lua driver (needs the `lua` binary, e.g. `lua5.4`) loads those files
with a stub `data` table and prints the recipes as JSON; everything after that is plain Python.

    uv run python -m app.importer.factorio [--factorio-dir ~/.local/share/Steam/steamapps/common/Factorio]

Two packs are created for the installed version: "Vanilla" (base game) and "Space Age" (base + Space Age
+ Quality + Elevated Rails). Ids are namespaced by the mod that defines the thing (`base:iron-plate`,
`space-age:holmium-plate`), fluids get the usual `fluid:` prefix.

Known gaps: only recipes are read (no mining, boilers, offshore pumps, spoilage, burning), recycling
recipes are generated at load time by the game and are not included, and `data-updates` files that tweak
recipes are not applied.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import select, update

import app.models  # noqa: F401
from app.db import async_session_factory
from app.importer.pipeline import PackSpec, import_recipes
from app.models.identity import ItemIdentity, MachineIdentity
from app.models.versioning import Game

# pipeline.py puts backend/ on sys.path, which is what makes this importable
from atm10_ore_analyzer import MACHINE_NAMES, Recipe, Slot, TagIndex

DEFAULT_FACTORIO_DIR = Path("~/.local/share/Steam/steamapps/common/Factorio").expanduser()

# Load order matters: when two mods define the same name the earlier one owns it.
MODS: list[tuple[str, str, list[str]]] = [
    # (mod dir, display name, recipe files relative to the mod dir)
    ("base", "Base Game", ["prototypes/recipe.lua"]),
    ("elevated-rails", "Elevated Rails", ["prototypes/recipe/elevated-rails.lua"]),
    ("quality", "Quality", ["prototypes/recipe.lua"]),
    ("space-age", "Space Age", ["prototypes/recipe.lua"]),
]

# Recipe category -> machine that crafts it. A recipe with several categories uses the first.
CATEGORY_MACHINES = {
    "crafting": "Assembling Machine",
    "basic-crafting": "Assembling Machine",
    "advanced-crafting": "Assembling Machine",
    "crafting-with-fluid": "Assembling Machine",
    "hand-crafting": "Assembling Machine",
    "smelting": "Furnace",
    "chemistry": "Chemical Plant",
    "oil-processing": "Oil Refinery",
    "centrifuging": "Centrifuge",
    "rocket-building": "Rocket Silo",
    "captive-spawner-process": "Captive Biter Spawner",
    "crushing": "Crusher",
    "cryogenics": "Cryogenic Plant",
    "electromagnetics": "Electromagnetic Plant",
    "metallurgy": "Foundry",
    "organic": "Biochamber",
    "recycling": "Recycler",
}

MACHINE_NAMES.update({f"factorio:{cat}": machine for cat, machine in CATEGORY_MACHINES.items()})

_LUA_DRIVER = r"""
local root = arg[1]
local out = {}
local function esc(s) return (s:gsub('[%c"\\]', function(c) return string.format("\\u%04x", c:byte()) end)) end
local function enc(v)
  local t = type(v)
  if t == "table" then
    local n = 0
    for _ in pairs(v) do n = n + 1 end
    if n == 0 then return "[]" end
    local isarr = true
    for i = 1, n do if v[i] == nil then isarr = false break end end
    local parts = {}
    if isarr then
      for i = 1, n do parts[#parts + 1] = enc(v[i]) end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    for k, x in pairs(v) do parts[#parts + 1] = '"' .. esc(tostring(k)) .. '":' .. enc(x) end
    return "{" .. table.concat(parts, ",") .. "}"
  elseif t == "string" then return '"' .. esc(v) .. '"'
  elseif t == "number" or t == "boolean" then return tostring(v)
  else return "null" end
end
local cur
data = {extend = function(_, list)
  for _, p in ipairs(list) do
    if p.type == "recipe" then p.__mod = cur; out[#out + 1] = p end
  end
end}
for i = 2, #arg do
  local mod, file = arg[i]:match("([^:]+):(.+)")
  cur = mod
  dofile(root .. "/" .. mod .. "/" .. file)
end
io.write(enc(out))
"""


# ── Reading the game ──────────────────────────────────────────────────────────

def _find_lua() -> str:
    for name in ("lua5.4", "lua", "luajit", "lua5.3"):
        if shutil.which(name):
            return name
    raise SystemExit("A Lua interpreter is required (install lua5.4) to read Factorio's data files.")


def load_raw_recipes(data_dir: Path) -> list[dict[str, Any]]:
    files = [f"{mod}:{rel}" for mod, _, rels in MODS for rel in rels]
    proc = subprocess.run(
        [_find_lua(), "-", str(data_dir), *files], input=_LUA_DRIVER,
        capture_output=True, text=True, check=False, timeout=120,
    )
    if proc.returncode != 0:
        raise SystemExit(f"Lua failed reading {data_dir}:\n{proc.stderr}")
    return json.loads(proc.stdout)


def mod_version(data_dir: Path, mod: str) -> str:
    return json.loads((data_dir / mod / "info.json").read_text())["version"]


def load_names(data_dir: Path) -> dict[str, str]:
    """Display names from the English locale: 'iron-plate' -> 'Iron plate'. Item names win over fluid
    names over entity names (an item is usually the placeable entity's item)."""
    names: dict[str, dict[str, str]] = {"entity-name": {}, "fluid-name": {}, "item-name": {}}
    for mod, _, _ in MODS:
        for cfg in (data_dir / mod / "locale" / "en").glob("*.cfg"):
            section = None
            for line in cfg.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1]
                elif section in names and "=" in line and not line.startswith(("#", ";")):
                    key, _, value = line.partition("=")
                    names[section].setdefault(key.strip(), value.strip())
    merged = {**names["entity-name"], **names["fluid-name"], **names["item-name"]}
    return merged


def _defining_text(data_dir: Path) -> dict[str, str]:
    """Per mod, the concatenated text of its prototype files (recipes and techs excluded) so we can tell
    which mod defines a given item or fluid name."""
    out: dict[str, str] = {}
    for mod, _, _ in MODS:
        chunks = []
        for f in (data_dir / mod / "prototypes").rglob("*.lua"):
            if f.parent.name == "recipe" or f.name.startswith(("recipe", "technology", "tips")):
                continue
            chunks.append(f.read_text(encoding="utf-8", errors="replace"))
        out[mod] = "\n".join(chunks)
    return out


def owner_of(name: str, texts: dict[str, str], cache: dict[str, str]) -> str:
    if name not in cache:
        pattern = re.compile(r'\bname\s*=\s*"' + re.escape(name) + '"')
        cache[name] = next((m for m, _, _ in MODS if pattern.search(texts[m])), MODS[0][0])
    return cache[name]


# ── Converting to the pipeline's Recipe type ──────────────────────────────────

def _amount(entry: dict[str, Any]) -> float:
    """Expected amount of one ingredient/product entry (chances and ranges folded in)."""
    if "amount" in entry:
        qty = float(entry["amount"])
    else:
        qty = (float(entry.get("amount_min", 0)) + float(entry.get("amount_max", 0))) / 2
    prob = float(entry.get("probability", 1))
    prob *= float(entry.get("independent_probability", 1))
    shared = entry.get("shared_probability")
    if isinstance(shared, dict):
        prob *= float(shared.get("max", 1)) - float(shared.get("min", 0))
    return qty * prob


def _is_certain(entry: dict[str, Any]) -> bool:
    return (entry.get("probability", 1) == 1 and entry.get("independent_probability", 1) == 1
            and "shared_probability" not in entry)


def _entries(raw: Any) -> list[dict[str, Any]]:
    """Normalise a recipe's ingredients/results list (also accepts the old `{"iron-plate", 2}` form)."""
    out = []
    for e in raw or []:
        if isinstance(e, list):
            e = {"type": "item", "name": e[0], "amount": e[1]}
        out.append(e)
    return out


def _net(ingredients: list[dict], results: list[dict]) -> tuple[dict, dict]:
    """Per (type, name) totals, with catalysts (amounts returned unchanged, e.g. Kovarex's U-235)
    cancelled out. A catalyst is only cancelled when the product side is certain (no chance)."""
    ins: dict[tuple[str, str], float] = {}
    outs: dict[tuple[str, str], float] = {}
    cat_in: dict[tuple[str, str], float] = {}
    cat_out: dict[tuple[str, str], float] = {}
    for e in ingredients:
        k = (e.get("type", "item"), e["name"])
        ins[k] = ins.get(k, 0) + _amount(e)
        cat_in[k] = cat_in.get(k, 0) + float(e.get("ignored_by_stats", 0))
    for e in results:
        k = (e.get("type", "item"), e["name"])
        outs[k] = outs.get(k, 0) + _amount(e)
        if _is_certain(e):
            cat_out[k] = cat_out.get(k, 0) + float(e.get("ignored_by_stats", 0))
    for k in set(cat_in) & set(cat_out):
        cancel = min(cat_in[k], cat_out[k], ins.get(k, 0), outs.get(k, 0))
        ins[k] = ins.get(k, 0) - cancel
        outs[k] = outs.get(k, 0) - cancel
    return ({k: v for k, v in ins.items() if v > 1e-9}, {k: v for k, v in outs.items() if v > 1e-9})


def convert_recipes(
    raw: list[dict[str, Any]], owner: "callable[[str], str]"
) -> list[Recipe]:
    """Raw Lua recipe tables -> pipeline Recipes. `owner(name)` returns the mod that defines a name."""
    recipes: list[Recipe] = []
    for r in raw:
        if r.get("parameter") or r.get("hidden"):
            continue
        ingredients, results = _entries(r.get("ingredients")), _entries(r.get("results"))
        ins, outs = _net(ingredients, results)
        if not ins or not outs:
            continue            # nothing to chain through (spawner eggs etc.)

        def slot(key: tuple[str, str], qty: float) -> Slot:
            kind, name = key
            return Slot(tag=None, item=f"{owner(name)}:{name}", count=round(qty, 6), fluid=(kind == "fluid"))

        inputs = [slot(k, q) for k, q in ins.items() if k[0] != "fluid"]
        aux = [slot(k, q) for k, q in ins.items() if k[0] == "fluid"]
        outputs = [slot(k, q) for k, q in outs.items()]
        categories = r.get("categories") or ([r["category"]] if r.get("category") else ["crafting"])
        category = categories[0]
        mod = r["__mod"]
        recipes.append(Recipe(
            id=f"factorio/{mod}/recipe/{r['name']}",
            type=f"factorio:{category}",
            machine=CATEGORY_MACHINES.get(category, category.replace("-", " ").title()),
            namespace=mod,
            inputs=inputs,
            outputs=outputs,
            aux_inputs=aux,
            time_ticks=round(float(r.get("energy_required", 0.5)) * 60),
        ))
    return recipes


# ── Database ──────────────────────────────────────────────────────────────────

async def _apply_display_names(names: dict[str, str]) -> int:
    """Replace the derived canonical names ('Iron Plate') with Factorio's own ('Iron plate' -> 'Iron Plate',
    'Uranium-235'). Item identities store their id in `notes` as 'item_id:<id>'."""
    changed = 0
    async with async_session_factory() as session:
        async with session.begin():
            game = (await session.exec(select(Game).where(Game.slug == "factorio"))).scalars().one()
            rows = (await session.exec(select(ItemIdentity).where(ItemIdentity.game_id == game.id))).scalars().all()
            for ident in rows:
                item_id = (ident.notes or "").removeprefix("item_id:").removeprefix("fluid:")
                name = names.get(item_id.partition(":")[2])
                if name:
                    pretty = name[:1].upper() + name[1:]
                    if pretty != ident.canonical_name:
                        ident.canonical_name = pretty
                        session.add(ident)
                        changed += 1
            await session.exec(
                update(MachineIdentity).where(MachineIdentity.game_id == game.id).values(is_time_based=True)
            )
    return changed


async def run(factorio_dir: Path) -> None:
    data_dir = factorio_dir / "data"
    if not (data_dir / "base" / "info.json").exists():
        raise SystemExit(f"{data_dir}/base/info.json not found — pass --factorio-dir")
    version = mod_version(data_dir, "base")
    print(f"Factorio {version} at {factorio_dir}")

    raw = load_raw_recipes(data_dir)
    texts, cache = _defining_text(data_dir), {}
    recipes = convert_recipes(raw, lambda name: owner_of(name, texts, cache))
    print(f"  {len(raw)} recipe prototypes -> {len(recipes)} usable recipes")

    mods_meta = {
        mod: {"id": mod, "name": display, "version": mod_version(data_dir, mod)} for mod, display, _ in MODS
    }
    game_version = ".".join(version.split(".")[:2])
    await import_recipes(
        recipes, TagIndex(Path("/nonexistent")), TagIndex(Path("/nonexistent"), kind="fluid"),
        game_name="Factorio", game_slug="factorio", game_version_str=game_version,
        packs=[
            PackSpec("Vanilla", "vanilla", version, {"base"}),
            PackSpec("Space Age", "space-age", version, {m for m, _, _ in MODS}),
        ],
        mods_meta=mods_meta, recipe_metric="throughput", source_priority=0,
    )
    print(f"  Display names updated: {await _apply_display_names(load_names(data_dir))}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Import Factorio recipes from an installed game")
    ap.add_argument("--factorio-dir", type=Path, default=DEFAULT_FACTORIO_DIR,
                    help="Factorio install directory (contains data/ and bin/)")
    asyncio.run(run(ap.parse_args().factorio_dir))

"""
Import pipeline — reads a KubeJS debug export and populates the database.

Entry point (CLI):
    python -m app.importer \
        --export-dir allthemons_export \
        --game minecraft \
        --game-version 1.21.1 \
        --modpack atm10 \
        --modpack-version 0.18.0

This script is idempotent: re-running with the same export will upsert
records rather than duplicate them. The import_key (file path) is the
deduplication key for recipes.

Design decisions:
- One ItemIdentity per unique item_id within the game (curators merge later)
- ItemAppearance created per (item_id, mod_version_id) pair
- MachineIdentity created per unique recipe_type string
- All imported recipes are set to status=approved (no community review needed)
- conditions_met=False recipes are skipped (almostunified inactive variants)
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

import app.models  # noqa: F401 — registers all tables with SQLAlchemy metadata

from app.db import async_session_factory
from app.models.identity import ItemAppearance, ItemIdentity, MachineIdentity
from app.models.recipe import Recipe, RecipeModVersionLink, RecipeSlot, SourcePriority
from app.models.status import PublicationStatus, RecipeStatus
from app.models.versioning import (
    Game, GameVersion, Mod, ModPackIdentity, ModPackVersion, ModPackModVersion, ModVersion,
)

# Re-use the recipe parser and tag index from the CLI analyzer
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from atm10_ore_analyzer import (
    MACHINE_NAMES,
    TagIndex,
    load_all_recipes,
    load_tags,
    machine_name,
    parse_recipe,
)
from atm10_ore_analyzer import Recipe as CliRecipe
from atm10_ore_analyzer import Slot


# ── Versioning helpers ────────────────────────────────────────────────────────

async def _get_or_create_game(session: AsyncSession, name: str, slug: str) -> Game:
    result = await session.exec(select(Game).where(Game.slug == slug))
    game = result.scalars().one_or_none()
    if not game:
        game = Game(name=name, slug=slug, recipe_metric="discrete")
        session.add(game)
        await session.flush()
    return game


async def _get_or_create_game_version(
    session: AsyncSession, game: Game, version: str
) -> GameVersion:
    result = await session.exec(
        select(GameVersion).where(
            GameVersion.game_id == game.id, GameVersion.version == version
        )
    )
    gv = result.scalars().one_or_none()
    if not gv:
        gv = GameVersion(game_id=game.id, version=version, is_latest=True)
        session.add(gv)
        await session.flush()
    return gv


async def _get_or_create_modpack(
    session: AsyncSession,
    game_version: GameVersion,
    name: str,
    slug: str,
    version: str,
) -> ModPackVersion:
    # Get or create the stable identity record
    identity_result = await session.exec(
        select(ModPackIdentity).where(
            ModPackIdentity.game_id == game_version.game_id,
            ModPackIdentity.slug == slug,
        )
    )
    identity = identity_result.scalars().one_or_none()
    if not identity:
        identity = ModPackIdentity(
            game_id=game_version.game_id,
            name=name,
            slug=slug,
        )
        session.add(identity)
        await session.flush()

    # Get or create this specific version
    version_result = await session.exec(
        select(ModPackVersion).where(
            ModPackVersion.identity_id == identity.id,
            ModPackVersion.version == version,
        )
    )
    mpv = version_result.scalars().one_or_none()
    if not mpv:
        mpv = ModPackVersion(
            identity_id=identity.id,
            game_version_id=game_version.id,
            version=version,
            status=PublicationStatus.PUBLISHED,
        )
        session.add(mpv)
        await session.flush()
    return mpv


async def _get_or_create_mod(
    session: AsyncSession, game: Game, mod_id: str, name: str, namespace: str,
    description: str | None = None,
) -> Mod:
    result = await session.exec(
        select(Mod).where(Mod.game_id == game.id, Mod.namespace == namespace)
    )
    mod = result.scalars().one_or_none()
    if not mod:
        mod = Mod(
            game_id=game.id,
            name=name,
            slug=mod_id,
            namespace=namespace,
            description=description,
        )
        session.add(mod)
        await session.flush()
    return mod


async def _get_or_create_mod_version(
    session: AsyncSession, mod: Mod, game_version: GameVersion, version: str
) -> ModVersion:
    result = await session.exec(
        select(ModVersion).where(
            ModVersion.mod_id == mod.id,
            ModVersion.game_version_id == game_version.id,
            ModVersion.version == version,
        )
    )
    mv = result.scalars().one_or_none()
    if not mv:
        mv = ModVersion(
            mod_id=mod.id,
            game_version_id=game_version.id,
            version=version,
            status=PublicationStatus.PUBLISHED,
        )
        session.add(mv)
        await session.flush()
    return mv


async def _ensure_modpack_mod_version_link(
    session: AsyncSession, modpack: ModPackVersion, mod_version: ModVersion
) -> None:
    result = await session.exec(
        select(ModPackModVersion).where(
            ModPackModVersion.modpack_id == modpack.id,
            ModPackModVersion.mod_version_id == mod_version.id,
        )
    )
    if not result.scalars().one_or_none():
        link = ModPackModVersion(modpack_id=modpack.id, mod_version_id=mod_version.id)
        session.add(link)
        await session.flush()


# ── Identity helpers ──────────────────────────────────────────────────────────

def _derive_canonical_name(item_id: str, item_tags: set[str]) -> str:
    """Derive a human-readable canonical name from an item_id + its tags."""
    for tag in sorted(item_tags):
        if tag.startswith("c:ingots/"):
            ore = tag.split("/")[-1].replace("_", " ").title()
            return f"{ore} Ingot"
        if tag.startswith("c:gems/"):
            ore = tag.split("/")[-1].replace("_", " ").title()
            return f"{ore} Gem"
        if tag.startswith("c:dusts/"):
            ore = tag.split("/")[-1].replace("_", " ").title()
            return f"{ore} Dust"
        if tag.startswith("c:plates/"):
            ore = tag.split("/")[-1].replace("_", " ").title()
            return f"{ore} Plate"
        if tag.startswith("c:raw_materials/"):
            ore = tag.split("/")[-1].replace("_", " ").title()
            return f"Raw {ore}"
    # Fallback: derive from item_id ("mekanism:dust_gold" → "Dust Gold (Mekanism)")
    ns, _, path = item_id.partition(":")
    return path.replace("/", " ").replace("_", " ").title()


async def _get_or_create_item_identity(
    session: AsyncSession, game: Game, item_id: str, item_tags: set[str]
) -> ItemIdentity:
    ns = item_id.split(":")[0]
    result = await session.exec(
        select(ItemIdentity).where(
            ItemIdentity.game_id == game.id,
            ItemIdentity.canonical_namespace == ns,
            # Use the item_id itself as a stable discriminator via notes field
            ItemIdentity.notes == f"item_id:{item_id}",
        )
    )
    identity = result.scalars().one_or_none()
    if not identity:
        canonical_name = _derive_canonical_name(item_id, item_tags)
        identity = ItemIdentity(
            game_id=game.id,
            canonical_name=canonical_name,
            canonical_namespace=ns,
            notes=f"item_id:{item_id}",
        )
        session.add(identity)
        await session.flush()
    return identity


async def _get_or_create_item_appearance(
    session: AsyncSession,
    game: Game,
    item_id: str,
    mod_version: ModVersion,
    tags: set[str],
) -> ItemAppearance:
    result = await session.exec(
        select(ItemAppearance).where(
            ItemAppearance.item_id == item_id,
            ItemAppearance.mod_version_id == mod_version.id,
        )
    )
    appearance = result.scalars().one_or_none()
    if not appearance:
        identity = await _get_or_create_item_identity(session, game, item_id, tags)
        appearance = ItemAppearance(
            identity_id=identity.id,
            mod_version_id=mod_version.id,
            item_id=item_id,
            namespace=item_id.split(":")[0],
            tags_json=json.dumps(sorted(tags)),
        )
        session.add(appearance)
        await session.flush()
    return appearance


# ── Machine identity helpers ──────────────────────────────────────────────────

async def _get_or_create_machine_identity(
    session: AsyncSession, game: Game, recipe_type: str
) -> MachineIdentity:
    result = await session.exec(
        select(MachineIdentity).where(
            MachineIdentity.game_id == game.id,
            MachineIdentity.name == machine_name(recipe_type),
        )
    )
    machine = result.scalars().one_or_none()
    if not machine:
        machine = MachineIdentity(
            game_id=game.id,
            name=machine_name(recipe_type),
            recipe_types_json=json.dumps([recipe_type]),
            is_time_based=False,
        )
        session.add(machine)
        await session.flush()
    else:
        # Add this recipe_type to the machine's list if not already there
        existing_types = json.loads(machine.recipe_types_json)
        if recipe_type not in existing_types:
            existing_types.append(recipe_type)
            machine.recipe_types_json = json.dumps(existing_types)
            session.add(machine)
            await session.flush()
    return machine


# ── Recipe import ─────────────────────────────────────────────────────────────

async def _import_recipe(
    session: AsyncSession,
    cli_recipe: CliRecipe,
    game: Game,
    mod_version: ModVersion,
    modpack: ModPackVersion,
    tag_index: TagIndex,
    is_modpack_recipe: bool,
    item_mod_versions: dict[str, ModVersion],
) -> None:
    import_key = cli_recipe.id

    # Skip if already imported
    result = await session.exec(select(Recipe).where(Recipe.import_key == import_key))
    if result.scalars().one_or_none():
        return

    machine = await _get_or_create_machine_identity(session, game, cli_recipe.type)

    recipe = Recipe(
        machine_identity_id=machine.id,
        recipe_type=cli_recipe.type,
        import_key=import_key,
        source_priority=SourcePriority.MODPACK if is_modpack_recipe else SourcePriority.MOD,
        modpack_version_id=modpack.id if is_modpack_recipe else None,
        time_ticks=int(cli_recipe.time_ticks) if cli_recipe.time_ticks else None,
        conditions_met=cli_recipe.conditions_met,
        status=RecipeStatus.APPROVED,
    )
    session.add(recipe)
    await session.flush()

    # Link recipe to mod version
    link = RecipeModVersionLink(recipe_id=recipe.id, mod_version_id=mod_version.id)
    session.add(link)

    # Create recipe slots
    all_slots = (
        [(s, "input") for s in cli_recipe.inputs]
        + [(s, "output") for s in cli_recipe.outputs]
        + [(s, "aux_input") for s in cli_recipe.aux_inputs]
    )
    for order, (slot, slot_type) in enumerate(all_slots):
        appearance_id = None
        if slot_type == "output" and slot.tag and not slot.item and not slot.fluid:
            # "outputs any c:dusts/iron": store one concrete member so the route graph can use it
            member = tag_index.canonical_item(slot.tag)
            if not member:
                continue
            slot = Slot(tag=None, item=member, count=slot.count)
        if not (slot.item or slot.tag or slot.chemical):
            continue

        if slot.item and not slot.fluid:
            item_tags = tag_index.tags_for_item(slot.item)
            # Items belong to the mod that owns their namespace, not the recipe's mod
            owner_mv = item_mod_versions.get(slot.item.split(":")[0], mod_version)
            app = await _get_or_create_item_appearance(
                session, game, slot.item, owner_mv, item_tags
            )
            appearance_id = app.id

        rs = RecipeSlot(
            recipe_id=recipe.id,
            slot_type=slot_type,
            slot_order=order,
            tag_string=slot.tag if slot.tag else None,
            item_appearance_id=appearance_id,
            quantity=slot.count,
            is_fluid=slot.fluid,
            fluid_label=slot.chemical or (slot.item if slot.fluid else None),
        )
        session.add(rs)

    await session.flush()


# ── Main import function ──────────────────────────────────────────────────────

async def run_import(
    export_dir: Path,
    game_name: str,
    game_slug: str,
    game_version_str: str,
    modpack_name: str,
    modpack_slug: str,
    modpack_version: str,
    batch_size: int = 500,
) -> None:
    print(f"Loading recipes from {export_dir} ...")
    cli_recipes = load_all_recipes(export_dir)
    print(f"  Parsed {len(cli_recipes)} recipes")

    print("Loading tags ...")
    tag_index = load_tags(export_dir)
    print(f"  Loaded {len(tag_index._tag_to_items)} tags")

    # Load mod metadata from mods.json if present
    mods_meta: dict[str, dict] = {}
    mods_file = export_dir / "mods.json"
    if mods_file.exists():
        raw = json.loads(mods_file.read_text())
        for entry in raw:
            mods_meta[entry["id"]] = entry

    # Determine which recipes came from "added_recipes" (modpack overrides)
    modpack_recipe_keys: set[str] = set()
    added_dir = export_dir / "added_recipes"
    if added_dir.exists():
        for f in added_dir.rglob("*.json"):
            modpack_recipe_keys.add(str(f.relative_to(export_dir)))

    print("Connecting to database ...")
    async with async_session_factory() as session:
        async with session.begin():
            game = await _get_or_create_game(session, game_name, game_slug)
            game_version = await _get_or_create_game_version(session, game, game_version_str)
            modpack = await _get_or_create_modpack(
                session, game_version, modpack_name, modpack_slug, modpack_version
            )

            # Group recipes by namespace to create mod records
            namespace_to_recipes: dict[str, list[CliRecipe]] = {}
            for r in cli_recipes:
                namespace_to_recipes.setdefault(r.namespace, []).append(r)
            # Also create mods for namespaces that only appear as item owners (e.g. "minecraft")
            for r in cli_recipes:
                for sl in (*r.inputs, *r.outputs):
                    if sl.item and not sl.fluid:
                        namespace_to_recipes.setdefault(sl.item.split(":")[0], [])

            print(f"  Found {len(namespace_to_recipes)} namespaces")

            mod_versions: dict[str, ModVersion] = {}
            for ns, _ in namespace_to_recipes.items():
                meta = mods_meta.get(ns, {})
                mod_name = meta.get("name") or ("Minecraft" if ns == "minecraft" else ns.replace("_", " ").title())
                mod_version_str = meta.get("version", "unknown")

                mod = await _get_or_create_mod(
                    session, game, ns, mod_name, ns, meta.get("description")
                )
                mv = await _get_or_create_mod_version(
                    session, mod, game_version, mod_version_str
                )
                await _ensure_modpack_mod_version_link(session, modpack, mv)
                mod_versions[ns] = mv

            print(f"  Created/verified {len(mod_versions)} mod versions")

        # Import recipes in batches (each batch in its own transaction)
        total = len(cli_recipes)
        imported = 0
        skipped = 0

        for batch_start in range(0, total, batch_size):
            batch = cli_recipes[batch_start : batch_start + batch_size]
            async with session.begin():
                for cli_recipe in batch:
                    if not cli_recipe.conditions_met:
                        skipped += 1
                        continue

                    mv = mod_versions.get(cli_recipe.namespace)
                    if not mv:
                        skipped += 1
                        continue

                    is_modpack_recipe = cli_recipe.id in modpack_recipe_keys
                    await _import_recipe(
                        session, cli_recipe, game, mv, modpack, tag_index, is_modpack_recipe,
                        mod_versions,
                    )
                    imported += 1

            progress = min(batch_start + batch_size, total)
            print(f"  {progress}/{total} recipes processed ...", end="\r")

        print(f"\n  Imported {imported} recipes, skipped {skipped}")

    print("Import complete.")


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Import a KubeJS export into RecipeBook")
    parser.add_argument("--export-dir", default="allthemons_export",
                        help="Path to the export root directory")
    parser.add_argument("--game", default="Minecraft", help="Game name")
    parser.add_argument("--game-slug", default="minecraft", help="Game slug (URL-safe)")
    parser.add_argument("--game-version", default="1.21.1", help="Game version string")
    parser.add_argument("--modpack", default="All The Mods 10", help="ModPack name")
    parser.add_argument("--modpack-slug", default="atm10", help="ModPack slug")
    parser.add_argument("--modpack-version", default="0.18.0", help="ModPack version string")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    asyncio.run(
        run_import(
            export_dir=Path(args.export_dir),
            game_name=args.game,
            game_slug=args.game_slug,
            game_version_str=args.game_version,
            modpack_name=args.modpack,
            modpack_slug=args.modpack_slug,
            modpack_version=args.modpack_version,
            batch_size=args.batch_size,
        )
    )

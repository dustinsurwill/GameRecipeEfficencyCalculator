import argparse
import asyncio
from pathlib import Path

from app.importer.pipeline import run_import

parser = argparse.ArgumentParser(description="Import a KubeJS export into RecipeBook")
parser.add_argument("--export-dir", default="allthemons_export")
parser.add_argument("--game", default="Minecraft")
parser.add_argument("--game-slug", default="minecraft")
parser.add_argument("--game-version", default="1.21.1")
parser.add_argument("--modpack", default="All The Mods 10")
parser.add_argument("--modpack-slug", default="atm10")
parser.add_argument("--modpack-version", default="0.18.0")
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
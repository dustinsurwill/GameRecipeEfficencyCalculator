"""Small admin commands.

    uv run python -m app.cli promote you@example.com --superuser      # edit the catalog, publish recipes
    uv run python -m app.cli promote you@example.com --curator        # publish recipes, add items/machines
    uv run python -m app.cli promote you@example.com --demote         # remove both
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

import app.models  # noqa: F401
from app.db import async_session_factory
from app.models.user import User


async def promote(email: str, superuser: bool, curator: bool, demote: bool) -> None:
    async with async_session_factory() as session:
        async with session.begin():
            user = (await session.exec(select(User).where(User.email == email.strip()))).scalars().one_or_none()
            if user is None:
                raise SystemExit(f"No account with email {email}. Register on the site first.")
            if demote:
                user.is_superuser = user.is_curator = False
            else:
                user.is_superuser = user.is_superuser or superuser
                user.is_curator = user.is_curator or curator or superuser
            session.add(user)
            print(f"{user.display_name} <{user.email}>: superuser={user.is_superuser} curator={user.is_curator}")
    print("The website sees the change immediately. API access tokens carry the role until they are renewed: log in again for a new one.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("promote", help="grant or remove superuser / curator rights")
    p.add_argument("email")
    p.add_argument("--superuser", action="store_true")
    p.add_argument("--curator", action="store_true")
    p.add_argument("--demote", action="store_true", help="remove both roles")
    args = ap.parse_args()
    if not (args.superuser or args.curator or args.demote):
        ap.error("pass --superuser, --curator or --demote")
    asyncio.run(promote(args.email, args.superuser, args.curator, args.demote))

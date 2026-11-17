"""
Hand entry on the website: /contribute — recipes, items, machines (and, for superusers, the catalog:
games, mods, modpacks and their versions). The rules live in app/contrib.py and app/api/v1/routes/catalog.py;
these pages only collect the form and show the error or the result.

Fixed routes: registered before the `/{game}/...` catch-alls in main.py.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app import contrib
from app.api.v1.routes import catalog
from app.db import get_session
from app.models.identity import MachineIdentity
from app.models.status import PublicationStatus, RecipeStatus
from app.models.versioning import (
    Game, GameCreate, GameVersion, GameVersionCreate, Mod, ModCreate, ModPackIdentity, ModPackIdentityCreate,
    ModPackModVersion, ModPackVersion, ModPackVersionCreate, ModVersion, ModVersionCreate,
)
from app.web.deps import WebUser, optional_user, require_user, same_origin
from app.web.templating import render

router = APIRouter(prefix="/contribute", dependencies=[Depends(optional_user)])
Session = Annotated[AsyncSession, Depends(get_session)]

ITEM_ROWS_IN, ITEM_ROWS_OUT = 8, 4


# ── Lookups ───────────────────────────────────────────────────────────────────

async def _packs(session: AsyncSession, user: WebUser) -> list[dict]:
    """Every modpack version the user may contribute to, labelled 'Game · Pack version'."""
    q = (select(ModPackVersion, ModPackIdentity, Game)
         .join(ModPackIdentity, ModPackIdentity.id == ModPackVersion.identity_id)
         .join(Game, Game.id == ModPackIdentity.game_id)
         .order_by(Game.name, ModPackIdentity.name, ModPackVersion.created_at.desc()))
    if not user.is_superuser:
        q = q.where(ModPackVersion.status == PublicationStatus.PUBLISHED)
    return [
        {"id": str(v.id), "label": f"{g.name} · {p.name} {v.version}", "game_id": g.id, "game": g, "pack": p, "version": v}
        for v, p, g in (await session.exec(q)).all()
    ]


async def _pack(session: AsyncSession, user: WebUser, pack_id: str) -> dict | None:
    return next((p for p in await _packs(session, user) if p["id"] == pack_id), None)


async def _pack_mod_versions(session: AsyncSession, pack: dict) -> list[dict]:
    rows = (await session.exec(
        select(ModVersion, Mod)
        .join(Mod, Mod.id == ModVersion.mod_id)
        .join(ModPackModVersion, ModPackModVersion.mod_version_id == ModVersion.id)
        .where(ModPackModVersion.modpack_id == pack["version"].id)
        .order_by(Mod.name)
    )).all()
    return [{"id": str(mv.id), "label": f"{m.name} {mv.version}", "mod": m, "mv": mv} for mv, m in rows]


def _pack_url(pack: dict, path: str = "") -> str:
    return f"/{pack['game'].slug}/{pack['pack'].slug}/{pack['version'].version}{path}"


def _fail(request: Request, template: str, message: str, status_code: int = 422, **ctx):
    return render(request, template, status_code=status_code, error=message, **ctx)


def _http_message(e: HTTPException | contrib.ContribError) -> str:
    return str(e.detail if isinstance(e, HTTPException) else e)


# ── Hub ───────────────────────────────────────────────────────────────────────

@router.get("")
async def hub(request: Request, user: WebUser | None = Depends(optional_user)):
    return render(request, "contribute/hub.html")


# ── Recipe ────────────────────────────────────────────────────────────────────

async def _recipe_context(session: AsyncSession, user: WebUser, pack_id: str) -> dict:
    packs = await _packs(session, user)
    pack = next((p for p in packs if p["id"] == pack_id), None)
    ctx: dict = {"packs": packs, "pack": pack, "rows_in": ITEM_ROWS_IN, "rows_out": ITEM_ROWS_OUT}
    if pack:
        ctx["mods"] = await _pack_mod_versions(session, pack)
        ctx["machines"] = (await session.exec(
            select(MachineIdentity).where(MachineIdentity.game_id == pack["game_id"]).order_by(MachineIdentity.name)
        )).scalars().all()
    return ctx


@router.get("/recipe")
async def recipe_form(request: Request, session: Session, pack: str = "", user: WebUser = Depends(require_user)):
    return render(request, "contribute/recipe.html", form={}, **await _recipe_context(session, user, pack))


@router.post("/recipe", dependencies=[Depends(same_origin)])
async def recipe_submit(
    request: Request, session: Session, user: WebUser = Depends(require_user),
    pack: str = Form(""), machine: str = Form(""), mod_version: str = Form(""), recipe_type: str = Form(""),
    time_seconds: str = Form(""), energy_rf: str = Form(""),
    in_ref: list[str] = Form([]), in_qty: list[str] = Form([]), out_ref: list[str] = Form([]), out_qty: list[str] = Form([]),
):
    ctx = await _recipe_context(session, user, pack)
    form = {"machine": machine, "mod_version": mod_version, "recipe_type": recipe_type, "time_seconds": time_seconds,
            "energy_rf": energy_rf, "in": list(zip(in_ref, in_qty)), "out": list(zip(out_ref, out_qty))}
    if not ctx["pack"]:
        return _fail(request, "contribute/recipe.html", "Pick the pack the recipe is for.", form=form, **ctx)

    def number(text: str, what: str, default: float | None = None) -> float | None:
        if not text.strip():
            return default
        try:
            return float(text)
        except ValueError:
            raise contrib.ContribError(f"{what} must be a number (got '{text}')")

    try:
        slots = [
            contrib.SlotCreate(slot_type=kind, item=ref.strip(), quantity=number(qty, f"Quantity of {ref.strip()}", 1.0))
            for kind, refs, qtys in (("input", in_ref, in_qty), ("output", out_ref, out_qty))
            for ref, qty in zip(refs, qtys) if ref.strip()
        ]
        try:
            machine_id, mv_id = uuid.UUID(machine), uuid.UUID(mod_version)
        except ValueError:
            raise contrib.ContribError("Pick a machine and the mod the recipe belongs to.")
        energy = number(energy_rf, "Energy")
        recipe = await contrib.create_recipe(session, user.token, contrib.RecipeCreate(
            machine_identity_id=machine_id, recipe_type=recipe_type or None, mod_version_ids=[mv_id],
            lookup_mod_version_ids=[uuid.UUID(m["id"]) for m in ctx["mods"]],
            modpack_version_id=ctx["pack"]["version"].id, time_seconds=number(time_seconds, "Time"),
            energy_rf=int(energy) if energy else None, slots=slots,
        ))
    except contrib.ContribError as e:
        return _fail(request, "contribute/recipe.html", str(e), e.status_code, form=form, **ctx)
    return render(request, "contribute/recipe.html", form={}, saved=recipe, approved=recipe.status == RecipeStatus.APPROVED, **ctx)


# ── Item ──────────────────────────────────────────────────────────────────────

@router.get("/item")
async def item_form(request: Request, session: Session, pack: str = "", user: WebUser = Depends(require_user)):
    packs = await _packs(session, user)
    chosen = next((p for p in packs if p["id"] == pack), None)
    mods = await _pack_mod_versions(session, chosen) if chosen else []
    return render(request, "contribute/item.html", packs=packs, pack=chosen, mods=mods, form={})


@router.post("/item", dependencies=[Depends(same_origin)])
async def item_submit(
    request: Request, session: Session, user: WebUser = Depends(require_user),
    pack: str = Form(""), mod_version: str = Form(""), item_id: str = Form(""), name: str = Form(""),
    kind: str = Form("item"), tags: str = Form(""),
):
    packs = await _packs(session, user)
    chosen = next((p for p in packs if p["id"] == pack), None)
    mods = await _pack_mod_versions(session, chosen) if chosen else []
    ctx = {"packs": packs, "pack": chosen, "mods": mods}
    form = {"mod_version": mod_version, "item_id": item_id, "name": name, "kind": kind, "tags": tags}
    try:
        try:
            mv_id = uuid.UUID(mod_version)
        except ValueError:
            raise contrib.ContribError("Pick the mod the item belongs to.")
        item = await contrib.create_item(session, user.token, contrib.ItemCreate(
            mod_version_id=mv_id, item_id=item_id, name=name, kind=kind,
            tags=[t for t in tags.replace("\n", ",").split(",")],
        ))
    except contrib.ContribError as e:
        return _fail(request, "contribute/item.html", str(e), e.status_code, form=form, **ctx)
    return render(request, "contribute/item.html", form={"mod_version": mod_version, "kind": kind}, saved=item,
                  saved_url=_pack_url(chosen, f"/items/{item.item_id}") if chosen else None, **ctx)


# ── Machine ───────────────────────────────────────────────────────────────────

async def _games(session: AsyncSession) -> list[Game]:
    return list((await session.exec(select(Game).order_by(Game.name))).scalars().all())


def _parse_tiers(text: str) -> list[contrib.SpeedTier]:
    """One machine per line: 'Assembling machine 2 | 0.75 | crafting, advanced-crafting' (categories optional)."""
    tiers = []
    for line in text.splitlines():
        if not line.strip():
            continue
        name, _, rest = line.partition("|")
        speed, _, cats = rest.partition("|")
        try:
            tiers.append(contrib.SpeedTier(name=name.strip(), speed=float(speed), categories=[c.strip() for c in cats.split(",") if c.strip()]))
        except ValueError:
            raise contrib.ContribError(f"Speed line '{line.strip()}' should look like: Name | 0.75 | category, category")
    return tiers


@router.get("/machine")
async def machine_form(request: Request, session: Session, user: WebUser = Depends(require_user)):
    return render(request, "contribute/machine.html", games=await _games(session), form={})


@router.post("/machine", dependencies=[Depends(same_origin)])
async def machine_submit(
    request: Request, session: Session, user: WebUser = Depends(require_user),
    game: str = Form(""), name: str = Form(""), recipe_types: str = Form(""), time_based: str = Form(""), tiers: str = Form(""),
):
    games = await _games(session)
    form = {"game": game, "name": name, "recipe_types": recipe_types, "time_based": time_based, "tiers": tiers}
    try:
        try:
            game_id = uuid.UUID(game)
        except ValueError:
            raise contrib.ContribError("Pick the game the machine is for.")
        machine = await contrib.create_machine(session, user.token, contrib.MachineCreate(
            game_id=game_id, name=name, recipe_types=[t for t in recipe_types.replace("\n", ",").split(",")],
            is_time_based=bool(time_based), speed_tiers=_parse_tiers(tiers),
        ))
    except contrib.ContribError as e:
        return _fail(request, "contribute/machine.html", str(e), e.status_code, games=games, form=form)
    return render(request, "contribute/machine.html", games=games, form={"game": game}, saved=machine)


# ── Catalog (superusers) ──────────────────────────────────────────────────────

async def _catalog_context(session: AsyncSession) -> dict:
    return {
        "games": await _games(session),
        "game_versions": [{"id": str(v.id), "label": f"{g.name} {v.version}"} for v, g in (await session.exec(
            select(GameVersion, Game).join(Game, Game.id == GameVersion.game_id).order_by(Game.name, GameVersion.version.desc())
        )).all()],
        "mods": list((await session.exec(select(Mod).order_by(Mod.name))).scalars().all()),
        "mod_versions": [{"id": str(mv.id), "label": f"{m.name} {mv.version}"} for mv, m in (await session.exec(
            select(ModVersion, Mod).join(Mod, Mod.id == ModVersion.mod_id).order_by(Mod.name, ModVersion.version.desc())
        )).all()],
        "modpacks": list((await session.exec(select(ModPackIdentity).order_by(ModPackIdentity.name))).scalars().all()),
        "modpack_versions": [{"id": str(v.id), "label": f"{p.name} {v.version}"} for v, p in (await session.exec(
            select(ModPackVersion, ModPackIdentity).join(ModPackIdentity, ModPackIdentity.id == ModPackVersion.identity_id)
            .order_by(ModPackIdentity.name, ModPackVersion.created_at.desc())
        )).all()],
    }


async def _catalog_page(request: Request, session: AsyncSession, **extra):
    return render(request, "contribute/catalog.html", **await _catalog_context(session), **extra)


def _require_superuser(user: WebUser) -> None:
    if not user.is_superuser:
        raise HTTPException(403, "Only superusers can edit the catalog. Run `uv run python -m app.cli promote <email> --superuser` to become one.")


@router.get("/catalog")
async def catalog_form(request: Request, session: Session, user: WebUser = Depends(require_user)):
    _require_superuser(user)
    return await _catalog_page(request, session)


async def _run_catalog(request: Request, session: AsyncSession, user: WebUser, kind: str, make):
    """Run one catalog API handler as the signed-in superuser and report the outcome on the page."""
    _require_superuser(user)
    try:
        created = await make()
    except HTTPException as e:
        await session.rollback()
        return await _catalog_page(request, session, error=_http_message(e), section=kind)
    except ValueError as e:
        return await _catalog_page(request, session, error=f"Check the values: {e}", section=kind)
    return await _catalog_page(request, session, saved=f"Created {kind}: {getattr(created, 'name', None) or getattr(created, 'version', '')}", section=kind)


@router.post("/catalog/game", dependencies=[Depends(same_origin)])
async def catalog_game(request: Request, session: Session, user: WebUser = Depends(require_user),
                       name: str = Form(...), slug: str = Form(...), recipe_metric: str = Form("discrete")):
    return await _run_catalog(request, session, user, "game", lambda: catalog.create_game(
        GameCreate(name=name.strip(), slug=slug.strip().lower(), recipe_metric=recipe_metric), session, user.token))


@router.post("/catalog/game-version", dependencies=[Depends(same_origin)])
async def catalog_game_version(request: Request, session: Session, user: WebUser = Depends(require_user),
                               game: str = Form(...), version: str = Form(...), is_latest: str = Form("")):
    return await _run_catalog(request, session, user, "game version", lambda: catalog.create_game_version(
        uuid.UUID(game), GameVersionCreate(version=version.strip(), is_latest=bool(is_latest)), session, user.token))


@router.post("/catalog/mod", dependencies=[Depends(same_origin)])
async def catalog_mod(request: Request, session: Session, user: WebUser = Depends(require_user),
                      game: str = Form(...), name: str = Form(...), namespace: str = Form(...), description: str = Form("")):
    ns = namespace.strip().lower()
    return await _run_catalog(request, session, user, "mod", lambda: catalog.create_mod(
        ModCreate(game_id=uuid.UUID(game), name=name.strip(), slug=ns, namespace=ns, description=description.strip() or None),
        session, user.token))


@router.post("/catalog/mod-version", dependencies=[Depends(same_origin)])
async def catalog_mod_version(request: Request, session: Session, user: WebUser = Depends(require_user),
                              mod: str = Form(...), game_version: str = Form(...), version: str = Form(...)):
    return await _run_catalog(request, session, user, "mod version", lambda: catalog.create_mod_version(
        uuid.UUID(mod), ModVersionCreate(game_version_id=uuid.UUID(game_version), version=version.strip()), session, user.token))


@router.post("/catalog/modpack", dependencies=[Depends(same_origin)])
async def catalog_modpack(request: Request, session: Session, user: WebUser = Depends(require_user),
                          game: str = Form(...), name: str = Form(...), slug: str = Form(...), description: str = Form("")):
    return await _run_catalog(request, session, user, "modpack", lambda: catalog.create_modpack_identity(
        ModPackIdentityCreate(game_id=uuid.UUID(game), name=name.strip(), slug=slug.strip().lower(), description=description.strip() or None),
        session, user.token))


@router.post("/catalog/modpack-version", dependencies=[Depends(same_origin)])
async def catalog_modpack_version(request: Request, session: Session, user: WebUser = Depends(require_user),
                                  modpack: str = Form(...), game_version: str = Form(...), version: str = Form(...)):
    return await _run_catalog(request, session, user, "modpack version", lambda: catalog.create_modpack_version(
        uuid.UUID(modpack), ModPackVersionCreate(game_version_id=uuid.UUID(game_version), version=version.strip()), session, user.token))


@router.post("/catalog/modpack-mod", dependencies=[Depends(same_origin)])
async def catalog_modpack_mod(request: Request, session: Session, user: WebUser = Depends(require_user),
                              modpack_version: str = Form(...), mod_version: str = Form(...)):
    async def make():
        await catalog.add_mod_version_to_modpack_version(uuid.UUID(modpack_version), uuid.UUID(mod_version), session, user.token)
        return type("Done", (), {"name": "mod version added to the pack", "version": ""})()
    return await _run_catalog(request, session, user, "pack content", make)

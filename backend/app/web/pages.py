"""Public, server-rendered discovery pages.

URL layout (version may be `latest`):
  /                                      games and modpacks
  /{game}                                modpacks for a game
  /{game}/{pack}                         versions of a modpack
  /{game}/{pack}/{version}               discovery home
  /{game}/{pack}/{version}/items[/{id}]  /mods[/{ns}]  /machines[/{id}]  /tag  /routes
  /{game}/{pack}/{version}/_search | _recipes | _pick | _picked    (HTMX fragments)
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.v1.routes import chains
from app.db import get_session
from app.discovery import service
from app.discovery.index import DiscoveryIndex
from app.discovery.scope import (
    Scope, ScopeNotFound, get_game, list_games, list_packs, list_versions,
)
from app.web.deps import optional_user, web_index, web_scope
from app.web.templating import render

router = APIRouter(dependencies=[Depends(optional_user)])

ITEMS_PER_PAGE = 48
RECIPES_PER_PAGE = 12
CRAFTING_TYPES = [
    "minecraft:crafting_shaped", "minecraft:crafting_shapeless", "crafting_shaped",
    "crafting_shapeless", "kubejs:shaped", "kubejs:shapeless",
]
# Route searches skip these recipe types unless the user changes the Advanced options. The Mystical
# Agriculture reprocessor turns any seed into any essence, so it otherwise dominates every route.
DEFAULT_SKIP_RECIPE_TYPES = ("mysticalagriculture:reprocessor",)
Idx = Depends(web_index)
Sc = Depends(web_scope)


def _nf(e: Exception) -> HTTPException:
    return HTTPException(404, str(e))


# ── Games / packs / versions ──────────────────────────────────────────────────

@router.get("/")
async def home(request: Request, session: AsyncSession = Depends(get_session)):
    games = []
    for g in await list_games(session):
        packs = []
        for p in await list_packs(session, g.id):
            versions = await list_versions(session, p.id) or await list_versions(session, p.id, include_unpublished=True)
            packs.append({"pack": p, "latest": versions[0] if versions else None, "n_versions": len(versions)})
        games.append({"game": g, "packs": packs})
    return render(request, "home.html", games=games)


@router.get("/{game}")
async def game_page(request: Request, game: str, session: AsyncSession = Depends(get_session)):
    try:
        g = await get_game(session, game)
    except ScopeNotFound as e:
        raise _nf(e)
    packs = []
    for p in await list_packs(session, g.id):
        versions = await list_versions(session, p.id) or await list_versions(session, p.id, include_unpublished=True)
        packs.append({"pack": p, "latest": versions[0] if versions else None, "n_versions": len(versions)})
    return render(request, "game.html", game=g, packs=packs)


@router.get("/{game}/{pack}")
async def pack_page(request: Request, game: str, pack: str, session: AsyncSession = Depends(get_session)):
    from app.discovery.scope import resolve_scope
    try:
        scope = await resolve_scope(session, game, pack)
    except ScopeNotFound as e:
        raise _nf(e)
    versions = await list_versions(session, scope.pack_id) or await list_versions(
        session, scope.pack_id, include_unpublished=True
    )
    return render(request, "pack.html", scope=scope, versions=versions)


# ── Discovery home ────────────────────────────────────────────────────────────

@router.get("/{game}/{pack}/{version}")
async def scope_home(request: Request, scope: Scope = Sc, idx: DiscoveryIndex = Idx):
    return render(request, "scope_home.html", scope=scope, **service.overview(idx))


@router.get("/{game}/{pack}/{version}/_search")
async def search_fragment(request: Request, q: str = "", scope: Scope = Sc, idx: DiscoveryIndex = Idx):
    if len(q.strip()) < 2:
        return render(request, "partials/search_results.html", scope=scope, results=None)
    return render(request, "partials/search_results.html", scope=scope, results=service.search(idx, q), q=q.strip())


# ── Items ─────────────────────────────────────────────────────────────────────

@router.get("/{game}/{pack}/{version}/items")
async def items_page(
    request: Request, scope: Scope = Sc, idx: DiscoveryIndex = Idx,
    q: str = "", mod: str = "", tag: str = "", sort: str = "name", craftable: str = "", page: int = Query(1, ge=1),
):
    if sort not in ("name", "made_by", "used_in"):
        sort = "name"
    rows = idx.filter_items(q=q or None, mod=mod or None, tag=tag or None,
                            craftable=True if craftable else None, sort=sort)
    pages = max(1, -(-len(rows) // ITEMS_PER_PAGE))
    page = min(page, pages)
    shown = rows[(page - 1) * ITEMS_PER_PAGE : page * ITEMS_PER_PAGE]
    return render(
        request, "items.html", scope=scope, items=shown, total=len(rows), page=page, pages=pages,
        f={"q": q, "mod": mod, "tag": tag, "sort": sort, "craftable": craftable},
    )


async def _recipe_block(
    session: AsyncSession, scope: Scope, idx: DiscoveryIndex, *, offset: int = 0, **kw: Any
) -> dict[str, Any]:
    res = await service.list_recipes(session, scope, idx, limit=RECIPES_PER_PAGE, offset=offset, **kw)
    shown = offset + len(res["items"])
    return {
        "recipes": res["items"], "total": res["total"], "shown": shown,
        "next_offset": shown if shown < res["total"] else None,
        "left": min(RECIPES_PER_PAGE, res["total"] - shown),
    }


@router.get("/{game}/{pack}/{version}/items/{item_id}")
async def item_page(
    request: Request, item_id: str, scope: Scope = Sc, idx: DiscoveryIndex = Idx,
    session: AsyncSession = Depends(get_session), tab: str = "", machine: str = "",
):
    try:
        item = await service.item_detail(session, scope, idx, item_id)
    except service.NotFound as e:
        raise _nf(e)
    if tab not in ("output", "input"):
        tab = "output" if item["made_by"] > 0 or item["used_in"] == 0 else "input"
    facets = item["machines"]["made_in" if tab == "output" else "used_in"]
    mid = _uuid_or_none(machine)
    block = await _recipe_block(session, scope, idx, item=item_id, role=tab, machine=mid)
    return render(
        request, "item.html", scope=scope, item=item, tab=tab, machine=machine if mid else "",
        facets=facets, highlight=item_id, rec_params={"item": item_id, "role": tab, "machine": machine if mid else ""},
        **block,
    )


@router.get("/{game}/{pack}/{version}/_recipes")
async def recipes_fragment(
    request: Request, scope: Scope = Sc, idx: DiscoveryIndex = Idx,
    session: AsyncSession = Depends(get_session),
    item: str = "", role: str = "output", machine: str = "", mod: str = "", offset: int = Query(0, ge=0),
):
    """'Show more' fragment: next page of recipe cards plus the next button."""
    if role not in ("output", "input"):
        role = "output"
    try:
        block = await _recipe_block(
            session, scope, idx, offset=offset, item=item or None, role=role,
            machine=_uuid_or_none(machine), mod=mod or None,
        )
    except service.NotFound as e:
        raise _nf(e)
    return render(
        request, "partials/recipe_more.html", scope=scope, highlight=item or None,
        rec_params={"item": item, "role": role, "machine": machine, "mod": mod}, **block,
    )


def _uuid_or_none(v: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(v) if v else None
    except ValueError:
        return None


# ── Mods ──────────────────────────────────────────────────────────────────────

@router.get("/{game}/{pack}/{version}/mods")
async def mods_page(request: Request, q: str = "", scope: Scope = Sc, idx: DiscoveryIndex = Idx):
    return render(request, "mods.html", scope=scope, mods=service.list_mods(idx, q or None), q=q)


@router.get("/{game}/{pack}/{version}/mods/{namespace}")
async def mod_page(
    request: Request, namespace: str, scope: Scope = Sc, idx: DiscoveryIndex = Idx,
    session: AsyncSession = Depends(get_session), tab: str = "items", q: str = "", page: int = Query(1, ge=1),
):
    try:
        mod = service.get_mod(idx, namespace)
    except service.NotFound as e:
        raise _nf(e)
    ctx: dict[str, Any] = {}
    if tab == "machines":
        pass
    elif tab == "recipes":
        ctx.update(await _recipe_block(session, scope, idx, mod=namespace))
        ctx["rec_params"] = {"mod": namespace}
    else:
        tab = "items"
        rows = idx.filter_items(q=q or None, mod=namespace, sort="used_in")
        pages = max(1, -(-len(rows) // ITEMS_PER_PAGE))
        page = min(page, pages)
        ctx.update(items=rows[(page - 1) * ITEMS_PER_PAGE : page * ITEMS_PER_PAGE], total=len(rows), page=page, pages=pages)
    return render(request, "mod.html", scope=scope, mod=mod, tab=tab, q=q, **ctx)


# ── Machines ──────────────────────────────────────────────────────────────────

@router.get("/{game}/{pack}/{version}/machines")
async def machines_page(request: Request, q: str = "", scope: Scope = Sc, idx: DiscoveryIndex = Idx):
    return render(request, "machines.html", scope=scope, machines=service.list_machines(idx, q or None), q=q)


@router.get("/{game}/{pack}/{version}/machines/{machine_id}")
async def machine_page(
    request: Request, machine_id: uuid.UUID, scope: Scope = Sc, idx: DiscoveryIndex = Idx,
    session: AsyncSession = Depends(get_session),
):
    try:
        machine = service.get_machine(idx, machine_id)
    except service.NotFound as e:
        raise _nf(e)
    block = await _recipe_block(session, scope, idx, machine=machine_id)
    return render(request, "machine.html", scope=scope, machine=machine, rec_params={"machine": str(machine_id)}, **block)


# ── Tags ──────────────────────────────────────────────────────────────────────

@router.get("/{game}/{pack}/{version}/tag")
async def tag_page(
    request: Request, id: str, scope: Scope = Sc, idx: DiscoveryIndex = Idx,
    session: AsyncSession = Depends(get_session), tab: str = "members",
):
    try:
        tag = service.get_tag(idx, id)
    except service.NotFound as e:
        raise _nf(e)
    ctx: dict[str, Any] = {}
    if tab == "recipes":
        ctx.update(await _recipe_block(session, scope, idx, item="#" + id, role="input"))
        ctx["rec_params"] = {"item": "#" + id, "role": "input"}
    else:
        tab = "members"
    return render(request, "tag.html", scope=scope, tag=tag, tab=tab, **ctx)


# ── Route finder ──────────────────────────────────────────────────────────────

def _default_skip_machines(idx: DiscoveryIndex) -> list[str]:
    return [m["id"] for m in idx.machines if any(t in DEFAULT_SKIP_RECIPE_TYPES for t in m["recipe_types"])]


def _skip_chip(idx: DiscoveryIndex, kind: str, value: str) -> dict[str, str] | None:
    """Display data for one 'skip this' entry, or None if it doesn't exist in this scope."""
    if kind == "machine":
        try:
            m = idx.machines_by_id.get(uuid.UUID(value))
        except ValueError:
            return None
        return {"kind": "machine", "param": "xm", "value": value, "label": m["name"], "sub": "machine", "ns": m["namespace"]} if m else None
    if kind == "mod":
        m = idx.mods_by_ns.get(value)
        return {"kind": "mod", "param": "xmod", "value": value, "label": m["name"], "sub": "mod", "ns": value} if m else None
    if kind == "item":
        i = idx.by_item_id.get(value)
        return {"kind": "item", "param": "xi", "value": value, "label": i["name"], "sub": "item", "ns": i["namespace"]} if i else None
    return None


@router.get("/{game}/{pack}/{version}/routes")
async def routes_page(
    request: Request, scope: Scope = Sc, idx: DiscoveryIndex = Idx,
    session: AsyncSession = Depends(get_session),
    target: str = "", start: str = "", depth: int = Query(6, ge=1, le=10), crafting: str = "", ran: str = "", rank: str = "auto",
    xm: list[str] = Query([]), xmod: list[str] = Query([]), xi: list[str] = Query([]),
):
    # `ran` marks a submitted form: an unchecked box then means "include", and empty skip lists mean "skip nothing".
    skip_crafting = bool(crafting) if ran else True
    defaults = _default_skip_machines(idx)
    if not ran:
        xm = defaults
    skips = [c for kind, vals in (("machine", xm), ("mod", xmod), ("item", xi)) for v in dict.fromkeys(vals)
             if (c := _skip_chip(idx, kind, v))]
    sel_target = idx.by_item_id.get(target)
    sel_start = idx.by_item_id.get(start)
    result, error = None, None
    if ran and not sel_target:
        error = "Pick the item you want to make."
    elif sel_target:
        pick = lambda kind: [c["value"] for c in skips if c["kind"] == kind]  # noqa: E731
        try:
            result = await chains.get_chains(
                session=session, target=target, start=start or None,
                modpack=str(scope.pack_id), modpack_version=str(scope.version_id), mod=[], game=None,
                game_version=None, exclude=CRAFTING_TYPES if skip_crafting else [],
                exclude_machine=pick("machine"), exclude_item=pick("item"), exclude_mod=pick("mod"),
                rank=rank if rank in ('yield', 'simple') else 'auto',
                allow_cycles=False, max_depth=depth, limit=25, quantity=1.0,
            )
        except HTTPException as e:
            error = str(e.detail)
    changed = bool(ran) and (set(xm) != set(defaults) or bool(xmod) or bool(xi) or not skip_crafting or depth != 6 or rank != 'auto')
    return render(
        request, "routes.html", scope=scope, target=sel_target, start=sel_start, depth=depth,
        skip_crafting=skip_crafting, result=result, error=error, idx=idx, skips=skips, advanced_open=changed, rank=rank,
    )


@router.get("/{game}/{pack}/{version}/_skip")
async def skip_results(request: Request, q: str = "", scope: Scope = Sc, idx: DiscoveryIndex = Idx):
    needle = q.strip().lower()
    if len(needle) < 2:
        return render(request, "partials/skip_results.html", scope=scope, machines=[], mods=[], items=[])
    return render(
        request, "partials/skip_results.html", scope=scope,
        machines=idx.search_machines(needle, 5), mods=idx.search_mods(needle, 4), items=idx.search_items(needle, 5),
    )


@router.get("/{game}/{pack}/{version}/_skip_add")
async def skip_add(request: Request, kind: str, value: str, scope: Scope = Sc, idx: DiscoveryIndex = Idx):
    chip = _skip_chip(idx, kind, value)
    if not chip:
        raise HTTPException(404, "Nothing to skip")
    return render(request, "partials/skip_chip.html", scope=scope, c=chip)


@router.get("/{game}/{pack}/{version}/_pick")
async def pick_fragment(
    request: Request, which: str = "target", q: str = "", scope: Scope = Sc, idx: DiscoveryIndex = Idx,
):
    items = idx.search_items(q.strip().lower(), 8) if len(q.strip()) >= 2 else []
    return render(request, "partials/pick_results.html", scope=scope, which=which, items=items)


@router.get("/{game}/{pack}/{version}/_picked")
async def picked_fragment(request: Request, which: str, id: str = "", scope: Scope = Sc, idx: DiscoveryIndex = Idx):
    return render(request, "partials/picker.html", scope=scope, which=which, item=idx.by_item_id.get(id))

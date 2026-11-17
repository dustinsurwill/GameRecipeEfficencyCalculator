"""
Hand entry: create items, machines and recipes. Shared by the JSON API and the website's /contribute forms.

Who may do what
  * recipe   — any signed-in user. Curators and superusers publish it at once; everyone else's recipe waits
               in the review queue (`pending_review`, see /admin/recipes).
  * item     — superusers, curators and admins of the mod the item belongs to (items show up in every
               pack that contains that mod version, so they are not open to everyone).
  * machine  — superusers and curators (a machine is game-wide).
Games, mods, modpacks and their versions are created through the catalog API (superusers).
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.tokens import TokenData
from app.models.admin import AdminRole, is_mod_admin
from app.models.identity import ItemAppearance, ItemIdentity, MachineIdentity
from app.models.recipe import Recipe, RecipeModVersionLink, RecipeSlot, SourcePriority
from app.models.status import RecipeStatus
from app.models.versioning import Game, Mod, ModVersion


class ContribError(Exception):
    """A request that cannot be honoured; `status_code` is the HTTP status the API answers with."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


# ── Input schemas ─────────────────────────────────────────────────────────────

class ItemCreate(SQLModel):
    mod_version_id: uuid.UUID
    item_id: str                    # "base:iron-plate"; the namespace must be the mod's
    name: str                       # "Iron plate"
    kind: str = "item"              # item | fluid | chemical
    tags: list[str] = []


class SpeedTier(SQLModel):
    name: str
    speed: float
    categories: list[str] = []


class MachineCreate(SQLModel):
    game_id: uuid.UUID
    name: str
    recipe_types: list[str] = []    # recipe type strings this machine runs, e.g. "base:smelting"
    is_time_based: bool = False
    speed_tiers: list[SpeedTier] = []


class SlotCreate(SQLModel):
    slot_type: str                  # input | output | aux_input
    item: str                       # item id ("base:iron-plate"), item name ("Iron plate") or "#tag"
    quantity: float = 1.0


class RecipeCreate(SQLModel):
    machine_identity_id: uuid.UUID
    recipe_type: str | None = None              # defaults to "<mod namespace>:<machine name>"
    mod_version_ids: list[uuid.UUID]            # the mod version(s) the recipe belongs to (>= 1)
    lookup_mod_version_ids: list[uuid.UUID] = []  # where item names/ids are looked up (defaults to the above)
    modpack_version_id: uuid.UUID | None = None
    time_seconds: float | None = None
    energy_rf: int | None = None
    slots: list[SlotCreate]


# ── Permissions ───────────────────────────────────────────────────────────────

def is_trusted(token: TokenData) -> bool:
    return token.is_superuser or token.is_curator


async def can_edit_mod(session: AsyncSession, token: TokenData, mod_id: uuid.UUID) -> bool:
    return is_trusted(token) or await is_mod_admin(session, token.user_id, mod_id, AdminRole.MAINTAINER)


# ── Items ─────────────────────────────────────────────────────────────────────

_ID = re.compile(r"^[a-z0-9_.\-]+:[a-z0-9_.\-/]+$")
_PREFIX = {"item": "", "fluid": "fluid:", "chemical": "chemical:"}


def _bare(item_id: str) -> str:
    for p in ("fluid:", "chemical:"):
        if item_id.startswith(p):
            return item_id[len(p):]
    return item_id


async def create_item(session: AsyncSession, token: TokenData, body: ItemCreate) -> ItemAppearance:
    mv = await session.get(ModVersion, body.mod_version_id)
    if not mv:
        raise ContribError("Mod version not found", 404)
    mod = await session.get(Mod, mv.mod_id)
    if not await can_edit_mod(session, token, mod.id):
        raise ContribError(f"Only a curator or an admin of {mod.name} can add items to it", 403)
    if body.kind not in _PREFIX:
        raise ContribError("Kind must be item, fluid or chemical")
    name = body.name.strip()
    bare = _bare(body.item_id.strip().lower())
    if not name:
        raise ContribError("The item needs a name")
    if not _ID.match(bare):
        raise ContribError(f"'{body.item_id}' is not a valid id; use namespace:name, e.g. {mod.namespace}:iron-plate")
    if bare.split(":")[0] != mod.namespace:
        raise ContribError(f"Items of {mod.name} start with '{mod.namespace}:' (got '{bare.split(':')[0]}:')")
    item_id = _PREFIX[body.kind] + bare

    if (await session.exec(select(ItemAppearance).where(
        ItemAppearance.item_id == item_id, ItemAppearance.mod_version_id == mv.id
    ))).scalars().first():
        raise ContribError(f"{item_id} already exists in this mod version", 409)

    identity = (await session.exec(select(ItemIdentity).where(
        ItemIdentity.game_id == mod.game_id, ItemIdentity.notes == f"item_id:{item_id}"
    ))).scalars().first()
    if not identity:
        identity = ItemIdentity(game_id=mod.game_id, canonical_name=name, canonical_namespace=mod.namespace,
                                notes=f"item_id:{item_id}")
        session.add(identity)
        await session.flush()
    appearance = ItemAppearance(
        identity_id=identity.id, mod_version_id=mv.id, item_id=item_id, namespace=mod.namespace,
        tags_json=json.dumps(sorted({t.strip() for t in body.tags if t.strip()})),
    )
    session.add(appearance)
    await session.commit()
    await session.refresh(appearance)
    return appearance


# ── Machines ──────────────────────────────────────────────────────────────────

async def create_machine(session: AsyncSession, token: TokenData, body: MachineCreate) -> MachineIdentity:
    if not is_trusted(token):
        raise ContribError("Only a curator or superuser can add machines", 403)
    if not await session.get(Game, body.game_id):
        raise ContribError("Game not found", 404)
    name = body.name.strip()
    if not name:
        raise ContribError("The machine needs a name")
    if (await session.exec(select(MachineIdentity).where(
        MachineIdentity.game_id == body.game_id, func.lower(MachineIdentity.name) == name.lower()
    ))).scalars().first():
        raise ContribError(f"A machine called '{name}' already exists", 409)
    for t in body.speed_tiers:
        if t.speed <= 0:
            raise ContribError(f"Speed of '{t.name}' must be above 0")
    machine = MachineIdentity(
        game_id=body.game_id, name=name,
        recipe_types_json=json.dumps([r.strip() for r in body.recipe_types if r.strip()]),
        is_time_based=body.is_time_based,
        speed_tiers_json=json.dumps([t.model_dump() for t in body.speed_tiers]) if body.speed_tiers else None,
    )
    session.add(machine)
    await session.commit()
    await session.refresh(machine)
    return machine


# ── Recipes ───────────────────────────────────────────────────────────────────

async def _resolve_item(session: AsyncSession, mod_version_ids: list[uuid.UUID], text: str) -> ItemAppearance:
    """An item id ('base:iron-plate', 'fluid:base:water') or a name ('Iron plate') -> its appearance."""
    text = text.strip()
    base = select(ItemAppearance).where(ItemAppearance.mod_version_id.in_(mod_version_ids))
    exact = (await session.exec(base.where(ItemAppearance.item_id.in_([text, f"fluid:{text}", f"chemical:{text}"])))).scalars().all()
    if len(exact) == 1 or (exact and len({a.item_id for a in exact}) == 1):
        return exact[0]
    if exact:
        raise ContribError(f"'{text}' matches {', '.join(sorted(a.item_id for a in exact))}; write the full id")
    named = (await session.exec(
        base.join(ItemIdentity, ItemIdentity.id == ItemAppearance.identity_id)
        .where(func.lower(ItemIdentity.canonical_name) == text.lower())
    )).scalars().all()
    if len({a.item_id for a in named}) == 1:
        return named[0]
    if named:
        raise ContribError(f"'{text}' is ambiguous ({', '.join(sorted({a.item_id for a in named}))}); write the full id")
    raise ContribError(f"No item called '{text}' in these mods. Add it first (Contribute → New item) or check the spelling")


async def create_recipe(session: AsyncSession, token: TokenData, body: RecipeCreate) -> Recipe:
    machine = await session.get(MachineIdentity, body.machine_identity_id)
    if not machine:
        raise ContribError("Machine not found", 404)
    if not body.mod_version_ids:
        raise ContribError("Pick the mod the recipe belongs to")
    mvs = [await session.get(ModVersion, i) for i in body.mod_version_ids]
    if any(m is None for m in mvs):
        raise ContribError("Mod version not found", 404)
    mods = [await session.get(Mod, m.mod_id) for m in mvs]
    if any(m.game_id != machine.game_id for m in mods):
        raise ContribError("The machine and the mod belong to different games")

    lookup = body.lookup_mod_version_ids or body.mod_version_ids
    slots: list[tuple[str, SlotCreate, ItemAppearance | None, str | None]] = []
    for s in body.slots:
        if s.slot_type not in ("input", "output", "aux_input"):
            raise ContribError(f"Unknown slot type '{s.slot_type}'")
        if s.quantity <= 0:
            raise ContribError(f"Quantity of '{s.item}' must be above 0")
        if s.item.strip().startswith("#"):
            if s.slot_type == "output":
                raise ContribError("An output must be a specific item, not a tag")
            slots.append((s.slot_type, s, None, s.item.strip()[1:]))
        else:
            slots.append((s.slot_type, s, await _resolve_item(session, lookup, s.item), None))
    outputs = [x for x in slots if x[0] == "output"]
    inputs = [x for x in slots if x[0] != "output"]
    if not outputs:
        raise ContribError("A recipe needs at least one output")
    if not inputs and not machine.is_time_based:
        raise ContribError("A recipe needs at least one input (only mining/pumping-style machines need none)")

    recipe_type = (body.recipe_type or "").strip() or (
        f"{mods[0].namespace}:{re.sub(r'[^a-z0-9]+', '_', machine.name.lower()).strip('_')}"
    )
    trusted = is_trusted(token)
    recipe = Recipe(
        machine_identity_id=machine.id, recipe_type=recipe_type, import_key=f"community/{uuid.uuid4()}",
        source_priority=SourcePriority.COMMUNITY, contributor_id=token.user_id,
        modpack_version_id=body.modpack_version_id,
        time_ticks=round(body.time_seconds * 60) if body.time_seconds else None, energy_rf=body.energy_rf,
        energy_source="community" if body.energy_rf else None,
        status=RecipeStatus.APPROVED if trusted else RecipeStatus.PENDING_REVIEW,
    )
    session.add(recipe)
    await session.flush()
    for order, (slot_type, s, appearance, tag) in enumerate(slots):
        fluid = bool(appearance and appearance.item_id.startswith(("fluid:", "chemical:")))
        if fluid and slot_type == "input":
            slot_type = "aux_input"     # fluids a recipe consumes are stored as aux inputs, like the importers do
        session.add(RecipeSlot(
            recipe_id=recipe.id, slot_type=slot_type, slot_order=order, tag_string=tag,
            item_appearance_id=appearance.id if appearance else None, quantity=s.quantity,
            is_fluid=fluid, fluid_label=_bare(appearance.item_id) if fluid else None,
        ))
    for mv in mvs:
        session.add(RecipeModVersionLink(recipe_id=recipe.id, mod_version_id=mv.id))
    await session.commit()
    await session.refresh(recipe)
    return recipe

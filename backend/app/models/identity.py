"""
Identity layer — separates the *concept* of an item or machine from its
concrete appearances across mods and versions.

Problem this solves:
  mekanism:dust_gold, immersiveengineering:dust_gold, alltheores:gold_dust,
  and enderio:powdered_gold are all the same "thing": Gold Dust.
  They share the tag #c:dusts/gold and should resolve to the same identity
  in chain queries.

  Without this layer, a chain traversal that produces mekanism:dust_gold
  can't connect to a recipe that consumes #c:dusts/gold unless we resolve
  the tag at query time — which works but is slow.

  With this layer, the chain traverser works on identities, and appearance
  mapping is done at import time.

Design:
  ItemIdentity   = "Gold Dust" — stable across games and versions
  ItemAppearance = "mekanism:dust_gold in Mekanism 10.7 for MC 1.21.1"
                 = one row per (item_id, mod_version) pair

  Recipe slots reference ItemAppearances (concrete) or tag strings.
  Chain queries resolve tags to identities, then appearances.
"""
import uuid

from sqlmodel import Field, Relationship, SQLModel

from app.db import TimestampMixin


# ── Item Identity ─────────────────────────────────────────────────────────────

class ItemIdentity(TimestampMixin, table=True):
    """
    The canonical concept of an item, independent of which mod adds it.

    Examples:
      - "Gold Ingot"       (minecraft:gold_ingot — vanilla, stable)
      - "Gold Dust"        (multiple mods, same concept)
      - "Gold Clump"       (Mekanism intermediate)
      - "Enrichment Chamber" (machine)

    The pgvector embedding (future) lives here for fuzzy identity matching
    when importing new mods/versions.
    """
    __tablename__ = "item_identities"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    canonical_name: str = Field(index=True)      # "Gold Dust"
    canonical_namespace: str | None = None       # "minecraft" for vanilla items
    # Which game this identity belongs to
    game_id: uuid.UUID = Field(foreign_key="games.id", index=True)
    # Human notes for curators
    notes: str | None = None
    icon_url: str | None = None

    appearances: list["ItemAppearance"] = Relationship(back_populates="identity")


class ItemAppearance(TimestampMixin, table=True):
    """
    A concrete item ID in a specific mod version.

    One ItemIdentity "Gold Dust" has many appearances:
      - mekanism:dust_gold    in mekanism@10.7.18
      - alltheores:gold_dust  in alltheores@3.2.0
      - immersiveengineering:dust_gold in ie@12.4.2

    tags[] mirrors the item's tag memberships from the export, stored as a
    Postgres array for fast set-intersection queries.
    """
    __tablename__ = "item_appearances"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    identity_id: uuid.UUID = Field(foreign_key="item_identities.id", index=True)
    mod_version_id: uuid.UUID = Field(foreign_key="mod_versions.id", index=True)

    # The concrete item ID: "mekanism:dust_gold"
    item_id: str = Field(index=True)
    # Namespace extracted for quick lookups: "mekanism"
    namespace: str = Field(index=True)

    # Tag memberships as a JSON-serialisable list stored in TEXT for SQLite
    # compatibility; on Postgres this becomes a proper array column via
    # sa_column override in migration
    tags_json: str = Field(default="[]")  # JSON array of tag strings

    identity: ItemIdentity = Relationship(back_populates="appearances")


# ── Machine Identity ──────────────────────────────────────────────────────────

class MachineIdentity(TimestampMixin, table=True):
    """
    The canonical concept of a processing machine.

    "Enrichment Chamber" is the same machine concept across all Mekanism versions
    even though its energy costs and recipe set may change between versions.

    The recipe_type string (e.g. "mekanism:enriching") is the primary key for
    lookup during import — we match recipe type strings to machine identities.
    """
    __tablename__ = "machine_identities"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    game_id: uuid.UUID = Field(foreign_key="games.id", index=True)
    mod_id: uuid.UUID | None = Field(default=None, foreign_key="mods.id")
    name: str                         # "Enrichment Chamber"
    # All recipe type strings that map to this machine
    # e.g. ["mekanism:enriching"] — stored as JSON for SQLite compat
    recipe_types_json: str = Field(default="[]")
    # Whether this machine processes items per-unit or per-time
    is_time_based: bool = Field(default=False)
    icon_url: str | None = None

    upgrade_items: list["MachineUpgradeItem"] = Relationship(
        back_populates="machine"
    )


# ── Machine Upgrades ──────────────────────────────────────────────────────────

class MachineUpgradeItem(TimestampMixin, table=True):
    """
    An item that can be inserted into a machine to modify its behaviour.
    E.g. EnderIO SAG Mill grinding balls, or Mekanism speed upgrades.

    Each upgrade item has one or more modifiers (yield %, speed %, energy %).
    """
    __tablename__ = "machine_upgrade_items"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    machine_id: uuid.UUID = Field(foreign_key="machine_identities.id", index=True)
    # The item that goes in the upgrade slot
    item_appearance_id: uuid.UUID | None = Field(
        default=None, foreign_key="item_appearances.id"
    )
    # Human name if item_appearance not yet linked
    display_name: str
    max_slots: int | None = None  # how many can be inserted simultaneously
    # Source: "item_tooltip" | "wiki" | "community"
    data_source: str = Field(default="community")

    machine: MachineIdentity = Relationship(back_populates="upgrade_items")
    modifiers: list["MachineUpgradeModifier"] = Relationship(
        back_populates="upgrade_item"
    )


class MachineUpgradeModifier(SQLModel, table=True):
    """
    One modifier contributed by an upgrade item.
    E.g. EnderIO End Steel Ball: YIELD_MAIN=1.4, YIELD_BONUS=2.4, ENERGY=0.7
    """
    __tablename__ = "machine_upgrade_modifiers"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    upgrade_item_id: uuid.UUID = Field(
        foreign_key="machine_upgrade_items.id", index=True
    )
    # YIELD_MAIN | YIELD_BONUS | SPEED | ENERGY_USE
    modifier_type: str
    multiplier: float
    # True = modifiers stack additively (rare); False = multiplicative
    stacks_additively: bool = Field(default=False)

    upgrade_item: MachineUpgradeItem = Relationship(back_populates="modifiers")


# ── API Schemas ───────────────────────────────────────────────────────────────

class ItemIdentityRead(SQLModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    canonical_name: str
    canonical_namespace: str | None
    game_id: uuid.UUID
    notes: str | None
    icon_url: str | None


class ItemAppearanceRead(SQLModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    identity_id: uuid.UUID
    mod_version_id: uuid.UUID
    item_id: str
    namespace: str
    tags_json: str


class MachineIdentityRead(SQLModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    game_id: uuid.UUID
    mod_id: uuid.UUID | None
    name: str
    recipe_types_json: str
    is_time_based: bool
    icon_url: str | None


class MachineUpgradeModifierRead(SQLModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    modifier_type: str
    multiplier: float
    stacks_additively: bool


class MachineUpgradeItemRead(SQLModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    machine_id: uuid.UUID
    display_name: str
    max_slots: int | None
    data_source: str
    modifiers: list[MachineUpgradeModifierRead] = []


class ItemAppearanceWithIdentityRead(SQLModel):
    appearance: ItemAppearanceRead
    identity: ItemIdentityRead

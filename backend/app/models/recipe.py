"""
Recipe layer — the core data model.

Key design decisions:

1. Recipe has a source_priority that determines which definition wins
   when the same logical recipe exists in multiple sources:
     0 = game default (vanilla smelting)
     1 = mod default (Mekanism enriching)
     2 = modpack override (ATM10 KubeJS changes)
     3 = community-entered (hand-entered from in-game observation)

2. RecipeSlot separates inputs, outputs, and aux inputs (chemicals, fluids).
   Tag-based slots store the tag string; item-based slots reference an
   ItemAppearance. Either tag_string or item_appearance_id must be set.

3. RecipeModVersionLink is the set of mod versions required for this recipe
   to be active. A recipe is visible in a modpack if ALL of its required
   mod versions are members of that modpack.

4. RecipeOverride explicitly records when a modpack replaces a recipe,
   preserving the diff for display ("ATM10 changed this recipe: before/after").

5. Energy and time are on Recipe, not RecipeSlot. These are machine-level
   costs, not per-slot costs.
"""
import uuid
from enum import IntEnum

from sqlmodel import Field, Relationship, SQLModel

from app.db import TimestampMixin
from app.models.status import RecipeStatus


class SourcePriority(IntEnum):
    GAME = 0
    MOD = 1
    MODPACK = 2
    COMMUNITY = 3


class SlotType(str):
    INPUT = "input"
    OUTPUT = "output"
    AUX_INPUT = "aux_input"   # chemicals, fluids consumed but not tracked as items


# ── Recipe ────────────────────────────────────────────────────────────────────

class Recipe(TimestampMixin, table=True):
    """
    One recipe: a set of inputs → outputs in a specific machine.

    Identified by its source file path during import, so re-importing
    the same export is idempotent.
    """
    __tablename__ = "recipes"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    machine_identity_id: uuid.UUID = Field(
        foreign_key="machine_identities.id", index=True
    )
    # The original recipe type string from the export: "mekanism:enriching"
    recipe_type: str = Field(index=True)
    # Stable import key — file path or generated hash. Used for deduplication.
    import_key: str = Field(unique=True, index=True)

    # Priority for override resolution
    source_priority: int = Field(default=SourcePriority.MOD)
    # Which modpack version this recipe was imported from (null = from a mod directly)
    modpack_version_id: uuid.UUID | None = Field(default=None, foreign_key="modpack_versions.id")
    # Contributor for community-entered recipes
    contributor_id: uuid.UUID | None = Field(default=None, foreign_key="users.id")

    # Machine costs — base values before any upgrade modifiers
    time_ticks: int | None = None          # null = no time cost (vanilla crafting)
    energy_rf: int | None = None           # null = no energy cost
    energy_source: str | None = None       # "export" | "wiki" | "community"

    # Whether almostunified or another condition system marked this active
    conditions_met: bool = Field(default=True)

    # Publication / review status
    # draft          — being prepared, visible only to mod/pack admins
    # pending_review — submitted by community, awaiting admin approval
    # approved       — public
    # rejected       — declined, with reason in RecipeReview
    status: str = Field(default=RecipeStatus.DRAFT, index=True)

    # For community submissions: the original contributor
    # (contributor_id already on model above, reused here)

    slots: list["RecipeSlot"] = Relationship(
        back_populates="recipe",
        sa_relationship_kwargs={"lazy": "noload"},
    )
    mod_version_links: list["RecipeModVersionLink"] = Relationship(
        back_populates="recipe",
        sa_relationship_kwargs={"lazy": "noload"},
    )


# ── RecipeSlot ────────────────────────────────────────────────────────────────

class RecipeSlot(SQLModel, table=True):
    """
    One input or output slot in a recipe.

    Either tag_string or item_appearance_id must be set (not both).
    Tag-based slots are resolved at query time via ItemAppearance.tags_json.

    For chance-based outputs (e.g. Create Crushing Wheels):
      quantity = base_quantity * probability
    so a 0.75 chance of 1x = quantity of 0.75.
    Slots with the same item and slot_type are merged at import time.
    """
    __tablename__ = "recipe_slots"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    recipe_id: uuid.UUID = Field(foreign_key="recipes.id", index=True)

    slot_type: str  # "input" | "output" | "aux_input"
    slot_order: int = Field(default=0)  # display ordering

    # Exactly one of these must be set:
    tag_string: str | None = Field(default=None, index=True)  # "c:dusts/gold"
    item_appearance_id: uuid.UUID | None = Field(
        default=None, foreign_key="item_appearances.id"
    )

    # Quantity (already probability-weighted for chance outputs)
    quantity: float = Field(default=1.0)

    # True for fluid/chemical aux inputs — tracked but not counted in
    # item-yield calculations
    is_fluid: bool = Field(default=False)
    # Human-readable label for fluids without an ItemAppearance
    fluid_label: str | None = None

    recipe: Recipe = Relationship(back_populates="slots")


# ── RecipeModVersionLink ──────────────────────────────────────────────────────

class RecipeModVersionLink(SQLModel, table=True):
    """
    Which mod versions must be active for this recipe to be available.

    A recipe is visible in a modpack if ALL of its required mod versions
    are members of that modpack (ModPackModVersion table).

    Most recipes require just one mod version. Cross-mod recipes (e.g. a
    recipe that uses both Mekanism and Applied Energistics items) list both.
    """
    __tablename__ = "recipe_mod_version_links"

    recipe_id: uuid.UUID = Field(
        foreign_key="recipes.id", primary_key=True
    )
    mod_version_id: uuid.UUID = Field(
        foreign_key="mod_versions.id", primary_key=True
    )

    recipe: Recipe = Relationship(back_populates="mod_version_links")


# ── RecipeOverride ────────────────────────────────────────────────────────────

class RecipeOverride(TimestampMixin, table=True):
    """
    Records that a modpack replaces a base recipe with a different one.

    This preserves the before/after for display in the UI:
      "ATM10 changed this Mekanism recipe: [base] → [override]"

    base_recipe_id   = the original recipe from the mod
    override_recipe_id = the replacement recipe from the modpack
    modpack_id       = which modpack made this change
    """
    __tablename__ = "recipe_overrides"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    base_recipe_id: uuid.UUID = Field(foreign_key="recipes.id", index=True)
    override_recipe_id: uuid.UUID = Field(foreign_key="recipes.id", index=True)
    modpack_version_id: uuid.UUID = Field(foreign_key="modpack_versions.id", index=True)
    # Why this override exists — from KubeJS comments, curator notes, etc.
    notes: str | None = None


# ── API Schemas ───────────────────────────────────────────────────────────────

class RecipeSlotRead(SQLModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    slot_type: str
    slot_order: int
    tag_string: str | None
    item_appearance_id: uuid.UUID | None
    quantity: float
    is_fluid: bool
    fluid_label: str | None


class RecipeRead(SQLModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    machine_identity_id: uuid.UUID
    recipe_type: str
    import_key: str
    source_priority: int
    contributor_id: uuid.UUID | None
    modpack_version_id: uuid.UUID | None
    time_ticks: int | None
    energy_rf: int | None
    conditions_met: bool
    status: str
    slots: list[RecipeSlotRead] = []


class RecipeOverrideRead(SQLModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    base_recipe_id: uuid.UUID
    override_recipe_id: uuid.UUID
    modpack_id: uuid.UUID
    notes: str | None

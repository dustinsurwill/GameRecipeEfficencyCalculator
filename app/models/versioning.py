"""
Versioning layer — everything that needs to be scoped to a specific
game version, mod version, or modpack version lives here.

Key design decisions:
- Game and Mod are stable identity records (the "what")
- GameVersion, ModVersion, ModPack are the versioned instances (the "which version")
- A Mod is always tied to a Game (Mekanism for Minecraft ≠ some other game)
- ModVersion is tied to a GameVersion (Mekanism 10.7 runs on MC 1.21.1)
- ModPack pins exact ModVersions, and can carry recipe overrides on top
"""
import uuid
from datetime import datetime

from sqlmodel import Field, Relationship, SQLModel

from app.db import TimestampMixin
from app.models.status import PublicationStatus


# ── Game ──────────────────────────────────────────────────────────────────────

class Game(TimestampMixin, table=True):
    """
    The base game: Minecraft, Factorio, Satisfactory, etc.
    Not versioned itself — versions live in GameVersion.
    """
    __tablename__ = "games"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(unique=True)             # "Minecraft"
    slug: str = Field(unique=True, index=True) # "minecraft"
    # Which metric type governs recipes in this game
    # "discrete"  — Minecraft: no time, just inputs/outputs
    # "throughput" — Factorio/Satisfactory: per-second or per-minute rates
    recipe_metric: str = Field(default="discrete")
    icon_url: str | None = None

    versions: list["GameVersion"] = Relationship(back_populates="game")
    mods: list["Mod"] = Relationship(back_populates="game")


class GameVersion(TimestampMixin, table=True):
    """
    A specific released version of a game: MC 1.20.1, MC 1.21.1, etc.
    Items and recipes that haven't changed between versions share the same
    identity but appear in multiple versions.
    """
    __tablename__ = "game_versions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    game_id: uuid.UUID = Field(foreign_key="games.id", index=True)
    version: str                  # "1.21.1"
    released_at: datetime | None = None
    is_latest: bool = Field(default=False)

    game: Game = Relationship(back_populates="versions")
    mod_versions: list["ModVersion"] = Relationship(back_populates="game_version")
    modpacks: list["ModPack"] = Relationship(back_populates="game_version")


# ── Mod ───────────────────────────────────────────────────────────────────────

class Mod(TimestampMixin, table=True):
    """
    A mod identity: Mekanism, Create, Immersive Engineering, etc.
    The stable concept across all versions.
    """
    __tablename__ = "mods"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    game_id: uuid.UUID = Field(foreign_key="games.id", index=True)
    name: str                     # "Mekanism"
    slug: str = Field(index=True) # "mekanism"
    namespace: str = Field(index=True)  # "mekanism" — the item ID prefix
    source_url: str | None = None # CurseForge / Modrinth / GitHub URL
    description: str | None = None

    game: Game = Relationship(back_populates="mods")
    versions: list["ModVersion"] = Relationship(back_populates="mod")


class ModVersion(TimestampMixin, table=True):
    """
    A specific mod version: Mekanism 10.7.18.84 for MC 1.21.1.
    Recipes belong to mod versions, not mods.
    """
    __tablename__ = "mod_versions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    mod_id: uuid.UUID = Field(foreign_key="mods.id", index=True)
    game_version_id: uuid.UUID = Field(foreign_key="game_versions.id", index=True)
    version: str                  # "10.7.18.84"
    imported_at: datetime | None = None
    # File hash for detecting duplicate imports
    import_hash: str | None = Field(default=None, index=True)
    # Publication state — only published versions are visible to public
    status: str = Field(default=PublicationStatus.DRAFT, index=True)

    mod: Mod = Relationship(back_populates="versions")
    game_version: GameVersion = Relationship(back_populates="mod_versions")
    modpack_links: list["ModPackModVersion"] = Relationship(
        back_populates="mod_version"
    )


# ── ModPack ───────────────────────────────────────────────────────────────────

class ModPack(TimestampMixin, table=True):
    """
    A curated collection of mod versions: ATM10 v0.18.0-beta, SkyFactory 5, etc.

    ModPacks can carry recipe overrides on top of their constituent mods —
    this is how KubeJS scripts, CraftTweaker changes, and datapack overrides
    are represented. A recipe from modpack_id takes priority over the same
    recipe from any mod_version_id.
    """
    __tablename__ = "modpacks"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    game_version_id: uuid.UUID = Field(foreign_key="game_versions.id", index=True)
    name: str                     # "All the Mods 10"
    slug: str = Field(index=True) # "atm10"
    version: str                  # "0.18.0-beta"
    source_url: str | None = None
    description: str | None = None
    # Publication state — only published packs are visible to public
    status: str = Field(default=PublicationStatus.DRAFT, index=True)

    game_version: GameVersion = Relationship(back_populates="modpacks")
    mod_version_links: list["ModPackModVersion"] = Relationship(
        back_populates="modpack"
    )


class ModPackModVersion(SQLModel, table=True):
    """
    Junction: which mod versions are in a given modpack.
    This is the active set — a recipe is visible if all of its
    required mod_versions are members of the active modpack.
    """
    __tablename__ = "modpack_mod_versions"

    modpack_id: uuid.UUID = Field(
        foreign_key="modpacks.id", primary_key=True
    )
    mod_version_id: uuid.UUID = Field(
        foreign_key="mod_versions.id", primary_key=True
    )

    modpack: ModPack = Relationship(back_populates="mod_version_links")
    mod_version: ModVersion = Relationship(back_populates="modpack_links")


# ── API Schemas ───────────────────────────────────────────────────────────────

class GameRead(SQLModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    name: str
    slug: str
    recipe_metric: str
    icon_url: str | None

class GameVersionRead(SQLModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    game_id: uuid.UUID
    version: str
    is_latest: bool
    released_at: datetime | None

class ModRead(SQLModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    game_id: uuid.UUID
    name: str
    slug: str
    namespace: str
    source_url: str | None

class ModVersionRead(SQLModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    mod_id: uuid.UUID
    game_version_id: uuid.UUID
    version: str
    imported_at: datetime | None
    status: str

class ModPackRead(SQLModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    game_version_id: uuid.UUID
    name: str
    slug: str
    version: str
    source_url: str | None
    status: str

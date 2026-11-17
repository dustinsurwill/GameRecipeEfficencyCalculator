"""
Versioning layer — everything scoped to a specific game version, mod version,
or modpack version lives here.

Key design decisions:
- Game / Mod / ModPackIdentity are stable identity records (the "what")
- GameVersion / ModVersion / ModPackVersion are the versioned instances (the "which version")
- A Mod is always tied to a Game (Mekanism for Minecraft ≠ some other game)
- ModVersion is tied to a GameVersion (Mekanism 10.7 runs on MC 1.21.1)
- ModPackIdentity is tied to a Game (ATM10 is a Minecraft modpack)
- ModPackVersion pins exact ModVersions and ties to a specific GameVersion
"""
import uuid
from datetime import datetime

from sqlmodel import Field, Relationship, SQLModel

from app.db import TimestampMixin
from app.models.status import PublicationStatus


# ── Game ──────────────────────────────────────────────────────────────────────

class Game(TimestampMixin, table=True):
    """The base game: Minecraft, Factorio, Satisfactory, etc."""
    __tablename__ = "games"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(unique=True)
    slug: str = Field(unique=True, index=True)
    recipe_metric: str = Field(default="discrete")  # "discrete" | "throughput"
    icon_url: str | None = None
    # game-wide numbers the planner needs, as JSON: {"belts": [{name, items_per_s}], "beacon": {...}}
    constants_json: str | None = None

    versions: list["GameVersion"] = Relationship(back_populates="game")
    mods: list["Mod"] = Relationship(back_populates="game")


class GameVersion(TimestampMixin, table=True):
    """A specific released version of a game: MC 1.20.1, MC 1.21.1, etc."""
    __tablename__ = "game_versions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    game_id: uuid.UUID = Field(foreign_key="games.id", index=True)
    version: str                   # "1.21.1"
    released_at: datetime | None = None
    is_latest: bool = Field(default=False)

    game: Game = Relationship(back_populates="versions")
    mod_versions: list["ModVersion"] = Relationship(back_populates="game_version")
    modpacks: list["ModPackVersion"] = Relationship(back_populates="game_version")


# ── Mod ───────────────────────────────────────────────────────────────────────

class Mod(TimestampMixin, table=True):
    """A mod identity: Mekanism, Create, Immersive Engineering, etc."""
    __tablename__ = "mods"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    game_id: uuid.UUID = Field(foreign_key="games.id", index=True)
    name: str
    slug: str = Field(index=True)
    namespace: str = Field(index=True)
    source_url: str | None = None
    description: str | None = None
    icon_url: str | None = None

    game: Game = Relationship(back_populates="mods")
    versions: list["ModVersion"] = Relationship(back_populates="mod")


class ModVersion(TimestampMixin, table=True):
    """A specific mod version: Mekanism 10.7.18.84 for MC 1.21.1."""
    __tablename__ = "mod_versions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    mod_id: uuid.UUID = Field(foreign_key="mods.id", index=True)
    game_version_id: uuid.UUID = Field(foreign_key="game_versions.id", index=True)
    version: str
    imported_at: datetime | None = None
    import_hash: str | None = Field(default=None, index=True)
    status: str = Field(default=PublicationStatus.DRAFT, index=True)

    mod: Mod = Relationship(back_populates="versions")
    game_version: GameVersion = Relationship(back_populates="mod_versions")
    modpack_links: list["ModPackModVersion"] = Relationship(back_populates="mod_version")


# ── ModPack ───────────────────────────────────────────────────────────────────

class ModPackIdentity(TimestampMixin, table=True):
    """
    The stable concept of a modpack across all its releases.
    "All the Mods 10" is a concept that exists independently of version 0.18 or 0.19.
    Mirrors the Mod → ModVersion split.
    """
    __tablename__ = "modpack_identities"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    game_id: uuid.UUID = Field(foreign_key="games.id", index=True)
    name: str                      # "All the Mods 10"
    slug: str = Field(index=True)  # "atm10"
    source_url: str | None = None
    description: str | None = None
    icon_url: str | None = None

    game: Game = Relationship()
    versions: list["ModPackVersion"] = Relationship(back_populates="identity")


class ModPackVersion(TimestampMixin, table=True):
    """
    A specific released version of a modpack: ATM10 0.18.0-beta, ATM10 0.19.0, etc.
    Pins exact ModVersions via ModPackModVersion. Can carry recipe overrides on top
    of its constituent mods (KubeJS scripts, CraftTweaker changes, etc.).
    """
    __tablename__ = "modpack_versions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    identity_id: uuid.UUID = Field(foreign_key="modpack_identities.id", index=True)
    game_version_id: uuid.UUID = Field(foreign_key="game_versions.id", index=True)
    version: str                   # "0.18.0-beta"
    status: str = Field(default=PublicationStatus.DRAFT, index=True)
    icon_url: str | None = None    # version-specific icon; falls back to identity.icon_url

    identity: ModPackIdentity = Relationship(back_populates="versions")
    game_version: GameVersion = Relationship(back_populates="modpacks")
    mod_version_links: list["ModPackModVersion"] = Relationship(back_populates="modpack_version")


class ModPackModVersion(SQLModel, table=True):
    """
    Junction: which mod versions are in a given modpack version.
    A recipe is visible if all of its required mod_versions are members of the
    active modpack version.
    """
    __tablename__ = "modpack_mod_versions"

    modpack_id: uuid.UUID = Field(foreign_key="modpack_versions.id", primary_key=True)
    mod_version_id: uuid.UUID = Field(foreign_key="mod_versions.id", primary_key=True)

    modpack_version: ModPackVersion = Relationship(back_populates="mod_version_links")
    mod_version: ModVersion = Relationship(back_populates="modpack_links")


# ── API Schemas ───────────────────────────────────────────────────────────────

class GameCreate(SQLModel):
    name: str
    slug: str
    recipe_metric: str = "discrete"
    icon_url: str | None = None

class GameVersionCreate(SQLModel):
    version: str
    released_at: datetime | None = None
    is_latest: bool = False

class ModCreate(SQLModel):
    game_id: uuid.UUID
    name: str
    slug: str
    namespace: str
    source_url: str | None = None
    description: str | None = None
    icon_url: str | None = None

class ModVersionCreate(SQLModel):
    game_version_id: uuid.UUID
    version: str

class ModPackIdentityCreate(SQLModel):
    game_id: uuid.UUID
    name: str
    slug: str
    source_url: str | None = None
    description: str | None = None
    icon_url: str | None = None

class ModPackVersionCreate(SQLModel):
    game_version_id: uuid.UUID
    version: str
    icon_url: str | None = None

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
    description: str | None
    icon_url: str | None

class ModVersionRead(SQLModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    mod_id: uuid.UUID
    game_version_id: uuid.UUID
    version: str
    imported_at: datetime | None
    status: str

class ModPackIdentityRead(SQLModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    game_id: uuid.UUID
    name: str
    slug: str
    source_url: str | None
    description: str | None
    icon_url: str | None

class ModPackVersionRead(SQLModel):
    """Flattened view: version fields + identity name/slug for convenience."""
    id: uuid.UUID
    identity_id: uuid.UUID
    game_version_id: uuid.UUID
    name: str
    slug: str
    version: str
    status: str
    icon_url: str | None
    source_url: str | None
    description: str | None

# Backward-compat aliases used by existing code that imports ModPackRead / ModPackCreate
ModPackRead = ModPackVersionRead
ModPackCreate = ModPackVersionCreate

"""
User identity models.

Design principles:
- User  = the person. No OAuth platform data here.
- OAuthAccount = a credential that belongs to a person.
  One person can have many OAuthAccounts across many providers.
- email on User is their *chosen* contact email (set in profile),
  NOT derived from any OAuth account automatically.
- Accounts are only linked when the user is already authenticated
  and explicitly connects a new OAuth account from their profile.
  No silent email-match linking.
"""
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlmodel import Field, Relationship, SQLModel

from app.db import TimestampMixin

if TYPE_CHECKING:
    pass


# ── User ──────────────────────────────────────────────────────────────────────

class UserBase(SQLModel):
    display_name: str = Field(min_length=2, max_length=64)
    # Nullable — OAuth-only users may never provide an email
    email: str | None = Field(default=None, index=True)
    email_verified: bool = Field(default=False)
    bio: str | None = Field(default=None, max_length=500)
    avatar_url: str | None = Field(default=None)


class User(UserBase, TimestampMixin, table=True):
    __tablename__ = "users"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
    )

    # Password auth — nullable because OAuth-only users have no password
    hashed_password: str | None = Field(default=None)

    # Account state
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)

    # Platform roles
    is_curator: bool = Field(default=False)   # can approve recipe submissions

    # Relations
    oauth_accounts: list["OAuthAccount"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"lazy": "noload"},
    )
    admin_groups: list["AdminGroup"] = Relationship(  # type: ignore[name-defined]
        back_populates="user",
        sa_relationship_kwargs={
            "foreign_keys": "[AdminGroup.user_id]",
            "lazy": "noload",
        },
    )


# ── OAuthAccount ──────────────────────────────────────────────────────────────

class OAuthAccount(TimestampMixin, table=True):
    """
    A single OAuth credential linked to a User.
    Multiple rows can point to the same user_id.

    provider examples: "github", "google", "authentik"
    provider_user_id: the immutable ID from that provider (not email —
        emails can change and aren't guaranteed unique across providers)
    """
    __tablename__ = "oauth_accounts"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
    )
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)

    provider: str = Field(index=True)           # "github" | "google" | "authentik"
    provider_user_id: str = Field(index=True)   # immutable ID from that provider

    # The email address on THIS specific OAuth account — informational only,
    # not used for identity matching or auto-linking
    provider_email: str | None = Field(default=None)

    # Tokens — needed if we ever call provider APIs on the user's behalf
    access_token: str
    refresh_token: str | None = Field(default=None)
    token_expires_at: datetime | None = Field(default=None)

    last_used_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    # Relation back to user
    user: User = Relationship(back_populates="oauth_accounts")


# ── API Schemas (Pydantic, not table models) ──────────────────────────────────

class UserRead(SQLModel):
    """What the API returns about a user. Never includes hashed_password."""
    model_config = {"from_attributes": True}  # allows validation from ORM objects

    id: uuid.UUID
    display_name: str
    email: str | None
    email_verified: bool
    bio: str | None
    avatar_url: str | None
    is_active: bool
    is_curator: bool
    created_at: datetime


class UserCreate(SQLModel):
    """Registration via email + password."""
    display_name: str = Field(min_length=2, max_length=64)
    email: str
    password: str = Field(min_length=8)


class UserUpdate(SQLModel):
    """Profile fields a user can update themselves."""
    display_name: str | None = Field(default=None, min_length=2, max_length=64)
    email: str | None = None
    bio: str | None = Field(default=None, max_length=500)
    avatar_url: str | None = None


class OAuthAccountRead(SQLModel):
    """Safe summary of a connected OAuth account for the profile page."""
    model_config = {"from_attributes": True}

    id: uuid.UUID
    provider: str
    provider_email: str | None
    last_used_at: datetime
    created_at: datetime


class UserReadWithAccounts(UserRead):
    """Full profile response — includes connected OAuth accounts."""
    oauth_accounts: list[OAuthAccountRead] = []

"""
Publication status for ModVersions, ModPacks, and Recipes.

State machines:

ModVersion / ModPack:
  draft ──────────────► published ──► deprecated
    │                        │
    └──(admin rejects)──► archived

Recipe:
  draft ──────────────► pending_review ──► approved
    │                          │               │
    │                          └──► rejected   │
    │                                          │
    └──(admin shortcut for known-good)─────────┘

Transitions and who can trigger them:

  ModVersion/ModPack:
    draft → published:   mod/modpack ADMIN only
    published → deprecated: mod/modpack ADMIN only
    any → archived:      superuser only

  Recipe:
    draft → pending_review:  any authenticated user (submitting)
    pending_review → approved: mod_admin, modpack_admin (for their scope),
                               curator, or superuser
    pending_review → rejected: same as approved
    approved → draft:        mod_admin (to pull back and fix)
    rejected → draft:        original contributor (to revise and resubmit)
"""
import uuid
from enum import StrEnum

from sqlmodel import Field, SQLModel

from app.db import TimestampMixin


class PublicationStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class RecipeStatus(StrEnum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


# ── RecipeReview ──────────────────────────────────────────────────────────────

class RecipeReview(TimestampMixin, table=True):
    """
    Immutable audit record of every approval or rejection decision.

    We create a new row for each decision rather than updating the recipe's
    status field in-place, so the full review history is preserved.
    The recipe's current status is always the most recent review's decision.

    Multiple reviews can exist for one recipe (e.g. rejected → revised →
    resubmitted → approved).
    """
    __tablename__ = "recipe_reviews"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    recipe_id: uuid.UUID = Field(foreign_key="recipes.id", index=True)
    reviewer_id: uuid.UUID = Field(foreign_key="users.id", index=True)

    # The decision recorded in this review
    decision: str  # RecipeStatus.APPROVED | RecipeStatus.REJECTED

    # Required for rejections, optional for approvals
    reason: str | None = Field(default=None, max_length=2000)

    # Snapshot of the recipe status before this decision (for audit trail)
    previous_status: str | None = None


# ── API Schemas ───────────────────────────────────────────────────────────────

class RecipeReviewRead(SQLModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    recipe_id: uuid.UUID
    reviewer_id: uuid.UUID
    decision: str
    reason: str | None
    previous_status: str | None
    created_at: "datetime"  # from TimestampMixin


class RecipeReviewCreate(SQLModel):
    """Body for POST /recipes/{id}/review"""
    decision: str  # "approved" | "rejected"
    reason: str | None = Field(default=None, max_length=2000)


from datetime import datetime  # noqa: E402 — avoid circular at module top
RecipeReviewRead.model_rebuild()

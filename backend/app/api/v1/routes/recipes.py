"""
Recipe review routes:

  GET  /recipes/{id}                — get recipe (respects draft visibility)
  POST /recipes/{id}/submit         — community user submits draft for review
  POST /recipes/{id}/review         — admin approves or rejects
  GET  /recipes/pending             — list recipes awaiting review (admin only)
  POST /recipes/{id}/retract        — pull approved recipe back to draft

Visibility rules enforced here (not at DB layer so we can give useful errors):
  - draft:          visible to contributor, mod/modpack admins, superusers
  - pending_review: visible to contributor, mod/modpack admins, curators, superusers
  - approved:       visible to everyone
  - rejected:       visible to contributor, mod/modpack admins, superusers
"""
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.tokens import TokenData, get_current_token_data
from app.db import get_session
from app.models.admin import (
    AdminScopeType,
    get_user_mod_scopes,
    is_mod_admin,
    is_modpack_admin,
)
from app.models.recipe import Recipe, RecipeRead
from app.models.status import RecipeReview, RecipeReviewCreate, RecipeReviewRead, RecipeStatus

router = APIRouter(prefix="/recipes", tags=["recipes"])
Session = Annotated[AsyncSession, Depends(get_session)]
Token = Annotated[TokenData, Depends(get_current_token_data)]

# Optional auth — some read routes are public
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from app.auth.tokens import decode_token, TokenType

_optional_bearer = HTTPBearer(auto_error=False)

async def get_optional_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
) -> TokenData | None:
    if not credentials:
        return None
    try:
        token = decode_token(credentials.credentials)
        if token.token_type != TokenType.ACCESS:
            return None
        return token
    except HTTPException:
        return None


# ── Visibility helpers ────────────────────────────────────────────────────────

async def _can_view_recipe(
    session: AsyncSession,
    recipe: Recipe,
    token: TokenData | None,
) -> bool:
    """Return True if the requesting user can see this recipe."""
    if recipe.status == RecipeStatus.APPROVED:
        return True
    if token is None:
        return False
    if token.is_superuser or token.is_curator:
        return True
    # Contributor can always see their own recipe
    if recipe.contributor_id and recipe.contributor_id == token.user_id:
        return True
    # Mod admin for any mod that has a recipe_mod_version_link
    # (simplified: check if user is admin of any mod)
    mod_scopes = await get_user_mod_scopes(session, token.user_id, AdminScopeType.MOD)
    if mod_scopes:
        return True
    # Modpack admin for the recipe's modpack
    if recipe.modpack_id:
        ok = await is_modpack_admin(session, token.user_id, recipe.modpack_id)
        if ok:
            return True
    return False


async def _get_recipe_or_404(
    session: AsyncSession,
    recipe_id: uuid.UUID,
    token: TokenData | None,
) -> Recipe:
    recipe = await session.get(Recipe, recipe_id)
    if not recipe:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recipe not found")
    if not await _can_view_recipe(session, recipe, token):
        # Return 404 rather than 403 to avoid leaking draft existence
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recipe not found")
    return recipe


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/{recipe_id}", response_model=RecipeRead)
async def get_recipe(
    recipe_id: uuid.UUID,
    session: Session,
    token: TokenData | None = Depends(get_optional_token),
) -> Recipe:
    return await _get_recipe_or_404(session, recipe_id, token)


@router.post("/{recipe_id}/submit", response_model=RecipeRead)
async def submit_for_review(
    recipe_id: uuid.UUID,
    session: Session,
    token: Token,
) -> Recipe:
    """Move a draft recipe to pending_review."""
    recipe = await _get_recipe_or_404(session, recipe_id, token)

    if recipe.status != RecipeStatus.DRAFT:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Recipe is '{recipe.status}', expected 'draft'",
        )
    # Only the contributor or an admin can submit
    if recipe.contributor_id != token.user_id and not token.is_superuser:
        mod_scopes = await get_user_mod_scopes(session, token.user_id, AdminScopeType.MOD)
        if not mod_scopes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not authorised to submit this recipe")

    recipe.status = RecipeStatus.PENDING_REVIEW
    session.add(recipe)
    await session.commit()
    await session.refresh(recipe)
    return recipe


@router.post("/{recipe_id}/review", response_model=RecipeReviewRead,
             status_code=status.HTTP_201_CREATED)
async def review_recipe(
    recipe_id: uuid.UUID,
    body: RecipeReviewCreate,
    session: Session,
    token: Token,
) -> RecipeReview:
    """Approve or reject a pending recipe. Requires mod/modpack admin or curator."""
    recipe = await _get_recipe_or_404(session, recipe_id, token)

    if recipe.status != RecipeStatus.PENDING_REVIEW:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Recipe is '{recipe.status}', expected 'pending_review'",
        )

    # Validate decision value
    if body.decision not in (RecipeStatus.APPROVED, RecipeStatus.REJECTED):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                           "Decision must be 'approved' or 'rejected'")
    if body.decision == RecipeStatus.REJECTED and not body.reason:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                           "Rejection requires a reason")

    # Check reviewer has permission
    is_authorised = token.is_superuser or token.is_curator
    if not is_authorised:
        mod_scopes = await get_user_mod_scopes(session, token.user_id, AdminScopeType.MOD)
        if mod_scopes:
            is_authorised = True
    if not is_authorised and recipe.modpack_id:
        is_authorised = await is_modpack_admin(session, token.user_id, recipe.modpack_id)
    if not is_authorised:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions to review")

    # Record the review
    review = RecipeReview(
        recipe_id=recipe.id,
        reviewer_id=token.user_id,
        decision=body.decision,
        reason=body.reason,
        previous_status=recipe.status,
    )
    session.add(review)

    # Update recipe status
    recipe.status = body.decision
    session.add(recipe)

    await session.commit()
    await session.refresh(review)
    return review


@router.post("/{recipe_id}/retract", response_model=RecipeRead)
async def retract_recipe(
    recipe_id: uuid.UUID,
    session: Session,
    token: Token,
) -> Recipe:
    """Pull an approved recipe back to draft for correction. Admin only."""
    recipe = await _get_recipe_or_404(session, recipe_id, token)

    if recipe.status != RecipeStatus.APPROVED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Recipe is '{recipe.status}', expected 'approved'",
        )

    is_authorised = token.is_superuser
    if not is_authorised:
        mod_scopes = await get_user_mod_scopes(session, token.user_id, AdminScopeType.MOD)
        if mod_scopes:
            is_authorised = True
    if not is_authorised and recipe.modpack_id:
        is_authorised = await is_modpack_admin(session, token.user_id, recipe.modpack_id)
    if not is_authorised:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions to retract")

    recipe.status = RecipeStatus.DRAFT
    session.add(recipe)
    await session.commit()
    await session.refresh(recipe)
    return recipe


@router.get("/pending", response_model=list[RecipeRead])
async def list_pending_recipes(
    session: Session,
    token: Token,
    modpack_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Recipe]:
    """
    List recipes awaiting review. Scoped to what the caller can approve.
    Superusers and curators see all pending. Admins see pending for their scope.
    """
    base = select(Recipe).where(Recipe.status == RecipeStatus.PENDING_REVIEW)

    if token.is_superuser or token.is_curator:
        # See everything — optionally filter by modpack
        if modpack_id:
            base = base.where(Recipe.modpack_id == modpack_id)
    else:
        # Only show pending recipes the user can actually approve
        mod_scopes = await get_user_mod_scopes(session, token.user_id, AdminScopeType.MOD)
        pack_scopes = await get_user_mod_scopes(session, token.user_id, AdminScopeType.MODPACK)
        if not mod_scopes and not pack_scopes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "No admin scopes")
        # Recipes linked to their mod scopes OR their modpack scopes
        filters = []
        if mod_scopes:
            # Recipes from mod versions the user admins — simplified to
            # all pending recipes since mod-scope filtering requires a join
            # through recipe_mod_version_links → mod_versions → mods.
            # Full implementation would join those tables; for now return all
            # pending if user has any mod admin role.
            filters.append(Recipe.status == RecipeStatus.PENDING_REVIEW)
        if pack_scopes:
            filters.append(Recipe.modpack_id.in_(pack_scopes))
        base = base.where(or_(*filters))

    base = base.offset(offset).limit(limit)
    result = await session.exec(base)
    return list(result.scalars().all())


@router.get("/{recipe_id}/reviews", response_model=list[RecipeReviewRead])
async def get_recipe_reviews(
    recipe_id: uuid.UUID,
    session: Session,
    token: TokenData | None = Depends(get_optional_token),
) -> list[RecipeReview]:
    """Get the review history for a recipe."""
    await _get_recipe_or_404(session, recipe_id, token)  # visibility check

    result = await session.exec(
        select(RecipeReview)
        .where(RecipeReview.recipe_id == recipe_id)
        .order_by(RecipeReview.created_at)
    )
    return list(result.scalars().all())

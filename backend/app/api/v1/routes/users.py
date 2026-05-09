"""
User profile routes:

  GET  /users/me               — get own profile with linked OAuth accounts
  PATCH /users/me              — update display name, bio, etc.
  DELETE /users/me/oauth/{id}  — unlink an OAuth account
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.tokens import get_current_user_id
from app.db import get_session
from app.models.user import OAuthAccount, User, UserReadWithAccounts, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/me", response_model=UserReadWithAccounts)
async def get_me(
    session: Session,
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> User:
    result = await session.exec(
        select(User).where(User.id == user_id).options(selectinload(User.oauth_accounts))
    )
    user = result.scalars().one_or_none()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.patch("/me", response_model=UserReadWithAccounts)
async def update_me(
    body: UserUpdate,
    session: Session,
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> User:
    result = await session.exec(
        select(User).where(User.id == user_id).options(selectinload(User.oauth_accounts))
    )
    user = result.scalars().one_or_none()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    # Only update fields that were explicitly provided
    update_data = body.model_dump(exclude_unset=True)

    # If updating email, check it's not taken by another user
    if "email" in update_data and update_data["email"] != user.email:
        existing = await session.exec(
            select(User).where(
                User.email == update_data["email"],
                User.id != user_id,
            )
        )
        if existing.first():
            raise HTTPException(status.HTTP_409_CONFLICT, "Email already in use")
        # Reset verification on email change
        update_data["email_verified"] = False

    for field, value in update_data.items():
        setattr(user, field, value)

    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.delete("/me/oauth/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_oauth(
    account_id: uuid.UUID,
    session: Session,
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> None:
    oauth = await session.get(OAuthAccount, account_id)
    if not oauth or oauth.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")

    # Safety: don't let users lock themselves out
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    if not user.hashed_password:
        # Count remaining OAuth accounts
        result = await session.exec(
            select(OAuthAccount).where(OAuthAccount.user_id == user_id)
        )
        accounts = result.all()
        if len(accounts) <= 1:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Cannot unlink your only login method. Add a password or another OAuth account first.",
            )

    await session.delete(oauth)
    await session.commit()

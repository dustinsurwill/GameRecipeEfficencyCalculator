"""
RefreshToken table — persisted so tokens can be revoked.

On logout:  delete the row.
On refresh: verify jti exists + not expired, then rotate
            (delete old row, issue new token + new row).
On password change / suspicious activity: delete ALL rows for user.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlmodel import Field, SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings

settings = get_settings()


class RefreshToken(SQLModel, table=True):
    __tablename__ = "refresh_tokens"

    jti: str = Field(primary_key=True)      # JWT ID — matches jti claim in token
    user_id: uuid.UUID = Field(index=True, foreign_key="users.id")
    expires_at: datetime
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    # Optional: track where this session came from
    user_agent: str | None = Field(default=None, max_length=256)
    ip_address: str | None = Field(default=None, max_length=45)


# ── Repository functions ──────────────────────────────────────────────────────

async def store_refresh_token(
    session: AsyncSession,
    jti: str,
    user_id: uuid.UUID,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> RefreshToken:
    expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.jwt_refresh_token_expire_days
    )
    token = RefreshToken(
        jti=jti,
        user_id=user_id,
        expires_at=expires_at,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    session.add(token)
    await session.commit()
    return token


async def get_refresh_token(session: AsyncSession, jti: str) -> RefreshToken | None:
    result = await session.exec(select(RefreshToken).where(RefreshToken.jti == jti))
    return result.first()


async def revoke_refresh_token(session: AsyncSession, jti: str) -> None:
    await session.exec(delete(RefreshToken).where(RefreshToken.jti == jti))
    await session.commit()


async def revoke_all_user_tokens(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Call this on password change, account compromise, etc."""
    await session.exec(
        delete(RefreshToken).where(RefreshToken.user_id == user_id)
    )
    await session.commit()

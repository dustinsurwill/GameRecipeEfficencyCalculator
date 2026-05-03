from collections.abc import AsyncGenerator
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings

settings = get_settings()

# ── Engine ────────────────────────────────────────────────────────────────────
# pool_pre_ping keeps stale connections from causing errors after DB restart
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # critical for async — avoids lazy-load after commit
)


# ── Session dependency ────────────────────────────────────────────────────────
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


# ── Shared base fields ────────────────────────────────────────────────────────
# Not a table itself — just a mixin for created_at/updated_at
class TimestampMixin(SQLModel):
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        nullable=False,
        sa_column_kwargs={"onupdate": lambda: datetime.now(timezone.utc)},
    )

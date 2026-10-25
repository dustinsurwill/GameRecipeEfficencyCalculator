"""Shared fixtures: in-memory SQLite, a fresh schema per test, and a TestClient."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.refresh import RefreshToken  # noqa: F401
from app.db import get_session
from app.main import app
from app.models.user import OAuthAccount, User  # noqa: F401

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

# Module-level engine/factory so all requests share the same in-memory DB
_engine = create_async_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def setup_db():
    """Create tables before each test, drop after; also clear the in-process caches."""
    from app.api.v1.routes.chains import invalidate_graph_cache
    from app.discovery.index import invalidate

    invalidate()
    invalidate_graph_cache()
    async with _engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield
    async with _engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)


async def override_get_session():
    """Fresh session per request — avoids event-loop sharing issues."""
    async with factory() as session:
        yield session


@pytest.fixture
def client():
    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()

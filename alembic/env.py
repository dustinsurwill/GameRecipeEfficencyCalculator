"""
Alembic migration environment — async-compatible with SQLModel.

Run migrations:
  alembic upgrade head

Generate a new migration after model changes:
  alembic revision --autogenerate -m "description"
"""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlmodel import SQLModel

# Import all models via the central __init__ so Alembic sees every table.
# When you add a new model file, add it to app/models/__init__.py.
import app.models  # noqa: F401 — registers all tables in SQLModel.metadata

from app.config import get_settings

settings = get_settings()

# Alembic Config object
config = context.config

# Use our sync DB URL (Alembic doesn't support asyncpg directly)
config.set_main_option("sqlalchemy.url", settings.database_url_sync)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# SQLModel.metadata sees all table= models that have been imported above
target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (generates SQL script)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Used for online migrations against a live DB."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

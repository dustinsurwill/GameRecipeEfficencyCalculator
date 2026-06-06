"""Split modpack into modpack_identities + modpack_versions

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-06

Mirrors the Mod → ModVersion split applied to modpacks:
  - modpack_identities: stable concept ("All the Mods 10")
  - modpack_versions:   specific release ("ATM10 0.18.0-beta"), renamed from modpacks

Data migration:
  - One modpack_identities row per unique slug, deriving game_id via game_versions
  - Existing modpacks rows keep their id (become modpack_versions)
  - identity_id populated from the slug → identity mapping

Column renames:
  - recipes.modpack_id → recipes.modpack_version_id
  - recipe_overrides.modpack_id → recipe_overrides.modpack_version_id
"""
from __future__ import annotations

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy import text

revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Create modpack_identities ──────────────────────────────────────────
    op.create_table(
        'modpack_identities',
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('game_id', sa.Uuid(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('slug', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('source_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('icon_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['game_id'], ['games.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_modpack_identities_game_id', 'modpack_identities', ['game_id'])
    op.create_index('ix_modpack_identities_slug', 'modpack_identities', ['slug'])

    # ── 2. Add nullable identity_id to modpacks ───────────────────────────────
    op.add_column('modpacks', sa.Column('identity_id', sa.Uuid(), nullable=True))

    # ── 3. Data migration ─────────────────────────────────────────────────────
    conn = op.get_bind()

    rows = conn.execute(text("""
        SELECT mp.slug, mp.name, mp.source_url, mp.description, mp.icon_url, gv.game_id
        FROM modpacks mp
        JOIN game_versions gv ON mp.game_version_id = gv.id
        GROUP BY mp.slug, gv.game_id, mp.name, mp.source_url, mp.description, mp.icon_url
    """)).fetchall()

    # One identity per unique (slug, game_id) pair
    seen: dict[tuple, str] = {}
    now = sa.func.now()

    for row in rows:
        key = (row.slug, str(row.game_id))
        if key in seen:
            continue
        identity_id = str(uuid.uuid4())
        seen[key] = identity_id
        conn.execute(text("""
            INSERT INTO modpack_identities
              (id, created_at, updated_at, game_id, name, slug, source_url, description, icon_url)
            VALUES
              (:id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
               :game_id, :name, :slug, :source_url, :description, :icon_url)
        """), {
            "id": identity_id,
            "game_id": str(row.game_id),
            "name": row.name,
            "slug": row.slug,
            "source_url": row.source_url,
            "description": row.description,
            "icon_url": row.icon_url,
        })

    # Update modpacks.identity_id for each row
    for (slug, game_id_str), identity_id in seen.items():
        conn.execute(text("""
            UPDATE modpacks SET identity_id = :iid
            WHERE slug = :slug
              AND game_version_id IN (
                  SELECT id FROM game_versions WHERE game_id = :game_id
              )
        """), {"iid": identity_id, "slug": slug, "game_id": game_id_str})

    # ── 4. Drop columns that moved to modpack_identities ─────────────────────
    # Use batch_alter_table for SQLite compatibility
    with op.batch_alter_table('modpacks') as batch_op:
        batch_op.alter_column('identity_id', nullable=False)
        batch_op.drop_column('name')
        batch_op.drop_column('slug')
        batch_op.drop_column('source_url')
        batch_op.drop_column('description')
        # icon_url stays on modpacks as a version-specific override
        batch_op.create_foreign_key(
            'fk_modpack_versions_identity_id',
            'modpack_identities',
            ['identity_id'],
            ['id'],
        )
        batch_op.create_index('ix_modpack_versions_identity_id', ['identity_id'])

    # ── 5. Rename modpacks → modpack_versions ─────────────────────────────────
    op.rename_table('modpacks', 'modpack_versions')

    # Rename the index that was on modpacks (SQLite recreates these via batch;
    # on Postgres the rename takes the indexes with it but we rename them for clarity)
    # Drop old named indexes before renaming on Postgres
    dialect = conn.dialect.name
    if dialect != 'sqlite':
        op.drop_index('ix_modpacks_game_version_id', table_name='modpack_versions')
        op.drop_index('ix_modpacks_status', table_name='modpack_versions')
        op.create_index('ix_modpack_versions_game_version_id', 'modpack_versions', ['game_version_id'])
        op.create_index('ix_modpack_versions_status', 'modpack_versions', ['status'])

    # ── 6. Rename modpack_id → modpack_version_id in recipes ─────────────────
    with op.batch_alter_table('recipes') as batch_op:
        batch_op.alter_column('modpack_id', new_column_name='modpack_version_id')

    # ── 7. Rename modpack_id → modpack_version_id in recipe_overrides ────────
    with op.batch_alter_table('recipe_overrides') as batch_op:
        batch_op.alter_column('modpack_id', new_column_name='modpack_version_id')


def downgrade() -> None:
    # Reverse rename in recipe_overrides
    with op.batch_alter_table('recipe_overrides') as batch_op:
        batch_op.alter_column('modpack_version_id', new_column_name='modpack_id')

    # Reverse rename in recipes
    with op.batch_alter_table('recipes') as batch_op:
        batch_op.alter_column('modpack_version_id', new_column_name='modpack_id')

    # Rename modpack_versions → modpacks
    op.rename_table('modpack_versions', 'modpacks')

    # Restore columns on modpacks (with placeholder values — data loss is expected on downgrade)
    with op.batch_alter_table('modpacks') as batch_op:
        batch_op.add_column(sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('slug', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('source_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True))

    # Drop identity_id from modpacks
    with op.batch_alter_table('modpacks') as batch_op:
        batch_op.drop_column('identity_id')

    # Drop modpack_identities
    op.drop_index('ix_modpack_identities_slug', table_name='modpack_identities')
    op.drop_index('ix_modpack_identities_game_id', table_name='modpack_identities')
    op.drop_table('modpack_identities')

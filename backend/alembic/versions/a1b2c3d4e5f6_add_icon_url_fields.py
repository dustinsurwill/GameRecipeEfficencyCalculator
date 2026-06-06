"""add icon_url to mods, modpacks, item_identities, machine_identities

Revision ID: a1b2c3d4e5f6
Revises: 9023b2d18d99
Create Date: 2026-05-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '9023b2d18d99'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('mods', sa.Column('icon_url', sa.String(), nullable=True))
    op.add_column('modpacks', sa.Column('icon_url', sa.String(), nullable=True))
    op.add_column('item_identities', sa.Column('icon_url', sa.String(), nullable=True))
    op.add_column('machine_identities', sa.Column('icon_url', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('machine_identities', 'icon_url')
    op.drop_column('item_identities', 'icon_url')
    op.drop_column('modpacks', 'icon_url')
    op.drop_column('mods', 'icon_url')

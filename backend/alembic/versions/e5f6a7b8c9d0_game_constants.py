"""Games: constants_json (belt speeds, beacon, ...)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-11-17
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("games", sa.Column("constants_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("games", "constants_json")

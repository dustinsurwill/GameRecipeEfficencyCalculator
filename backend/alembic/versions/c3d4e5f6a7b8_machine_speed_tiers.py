"""Machine speed tiers for throughput calculations

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-11-16
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("machine_identities", sa.Column("speed_tiers_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("machine_identities", "speed_tiers_json")

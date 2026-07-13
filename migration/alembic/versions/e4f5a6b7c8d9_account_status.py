"""Account status for reconciling venue-deleted subaccounts

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-07-14
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, Sequence[str], None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "account",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
    )


def downgrade() -> None:
    op.drop_column("account", "status")

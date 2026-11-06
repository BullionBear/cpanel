"""Add tags column to api_credential

Revision ID: f5g6h7i8j9k0
Revises: e4f5a6b7c8d9
Create Date: 2026-07-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "f5g6h7i8j9k0"
down_revision: Union[str, Sequence[str], None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "api_credential",
        sa.Column("tags", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("api_credential", "tags")

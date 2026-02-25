"""trading_history_cursor_cursor_start_end

Add cursor_start (int), cursor_end (int) after product_id; remove column cursor.

Revision ID: s8t9u0v1w2x3
Revises: r7s8t9u0v1w2
Create Date: 2026-02-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "s8t9u0v1w2x3"
down_revision: Union[str, Sequence[str], None] = "r7s8t9u0v1w2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("trading_history_cursor", sa.Column("cursor_start", sa.Integer(), nullable=True))
    op.add_column("trading_history_cursor", sa.Column("cursor_end", sa.Integer(), nullable=True))
    op.drop_column("trading_history_cursor", "cursor")


def downgrade() -> None:
    op.add_column("trading_history_cursor", sa.Column("cursor", sa.String(256), nullable=True))
    op.drop_column("trading_history_cursor", "cursor_end")
    op.drop_column("trading_history_cursor", "cursor_start")

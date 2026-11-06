"""add_symbol_id_to_trading_history_cursor

Add symbol_id to trading_history_cursor and index it.

Revision ID: u1v2w3x4y5z6
Revises: t9u0v1w2x3y4
Create Date: 2026-02-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "u1v2w3x4y5z6"
down_revision: Union[str, Sequence[str], None] = "t9u0v1w2x3y4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("trading_history_cursor", sa.Column("symbol_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_trading_history_cursor_symbol_id_ref_symbol",
        "trading_history_cursor",
        "ref_symbol",
        ["symbol_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_trading_history_cursor_symbol_id"), "trading_history_cursor", ["symbol_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_trading_history_cursor_symbol_id"), table_name="trading_history_cursor")
    op.drop_constraint("fk_trading_history_cursor_symbol_id_ref_symbol", "trading_history_cursor", type_="foreignkey")
    op.drop_column("trading_history_cursor", "symbol_id")

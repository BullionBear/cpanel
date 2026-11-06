"""create_trading_history_cursor_table

Create table trading_history_cursor: id, acct_id, product_id, cursor, created_at, updated_at.

Revision ID: r7s8t9u0v1w2
Revises: q6r7s8t9u0v1
Create Date: 2026-02-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "r7s8t9u0v1w2"
down_revision: Union[str, Sequence[str], None] = "q6r7s8t9u0v1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trading_history_cursor",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("acct_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("cursor", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["acct_id"], ["account.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["ref_product.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_trading_history_cursor_acct_id"), "trading_history_cursor", ["acct_id"], unique=False)
    op.create_index(op.f("ix_trading_history_cursor_product_id"), "trading_history_cursor", ["product_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_trading_history_cursor_product_id"), table_name="trading_history_cursor")
    op.drop_index(op.f("ix_trading_history_cursor_acct_id"), table_name="trading_history_cursor")
    op.drop_table("trading_history_cursor")

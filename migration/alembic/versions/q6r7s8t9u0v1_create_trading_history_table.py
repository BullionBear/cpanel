"""create_trading_history_table

Create table trading_history: id, exch_id, exch_tid, acct_id, order_id, symbol,
product_id, side, exec_px, exec_qty, exec_fee, fee_currency, is_maker, exec_time.

Revision ID: q6r7s8t9u0v1
Revises: p5q6r7s8t9u0
Create Date: 2026-02-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "q6r7s8t9u0v1"
down_revision: Union[str, Sequence[str], None] = "p5q6r7s8t9u0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trading_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("exch_id", sa.Integer(), nullable=False),
        sa.Column("exch_tid", sa.Integer(), nullable=False),
        sa.Column("acct_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("exec_px", sa.Numeric(36, 18), nullable=False),
        sa.Column("exec_qty", sa.Numeric(36, 18), nullable=False),
        sa.Column("exec_fee", sa.Numeric(36, 18), nullable=False),
        sa.Column("fee_currency", sa.String(16), nullable=False),
        sa.Column("is_maker", sa.Boolean(), nullable=False),
        sa.Column("exec_time", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["exch_id"], ["ref_exchange.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["acct_id"], ["account.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["ref_product.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_trading_history_exch_id"), "trading_history", ["exch_id"], unique=False)
    op.create_index(op.f("ix_trading_history_exch_tid"), "trading_history", ["exch_tid"], unique=False)
    op.create_index(op.f("ix_trading_history_acct_id"), "trading_history", ["acct_id"], unique=False)
    op.create_index(op.f("ix_trading_history_order_id"), "trading_history", ["order_id"], unique=False)
    op.create_index(op.f("ix_trading_history_symbol"), "trading_history", ["symbol"], unique=False)
    op.create_index(op.f("ix_trading_history_product_id"), "trading_history", ["product_id"], unique=False)
    op.create_index(op.f("ix_trading_history_exec_time"), "trading_history", ["exec_time"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_trading_history_exec_time"), table_name="trading_history")
    op.drop_index(op.f("ix_trading_history_product_id"), table_name="trading_history")
    op.drop_index(op.f("ix_trading_history_symbol"), table_name="trading_history")
    op.drop_index(op.f("ix_trading_history_order_id"), table_name="trading_history")
    op.drop_index(op.f("ix_trading_history_acct_id"), table_name="trading_history")
    op.drop_index(op.f("ix_trading_history_exch_tid"), table_name="trading_history")
    op.drop_index(op.f("ix_trading_history_exch_id"), table_name="trading_history")
    op.drop_table("trading_history")

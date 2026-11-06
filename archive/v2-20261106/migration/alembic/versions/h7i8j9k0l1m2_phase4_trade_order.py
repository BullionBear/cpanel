"""Phase 4: trade_order table

Revision ID: h7i8j9k0l1m2
Revises: g6h7i8j9k0l1
Create Date: 2026-07-15
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "h7i8j9k0l1m2"
down_revision: Union[str, Sequence[str], None] = "g6h7i8j9k0l1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NUM = sa.Numeric(36, 18)


def upgrade() -> None:
    op.create_table(
        "trade_order",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.BigInteger(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("universal_symbol", sa.String(length=64), nullable=False),
        sa.Column("client_order_id", sa.String(length=64), nullable=True),
        sa.Column("native_order_id", sa.String(length=64), nullable=True),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("type", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("qty", NUM, nullable=False),
        sa.Column("price", NUM, nullable=True),
        sa.Column("filled_qty", NUM, nullable=False, server_default="0"),
        sa.Column("external", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("account_id", "client_order_id", name="uq_order_account_client"),
    )
    op.create_index("ix_trade_order_account_id", "trade_order", ["account_id"])
    op.create_index("ix_trade_order_exchange", "trade_order", ["exchange"])
    op.create_index("ix_trade_order_universal_symbol", "trade_order", ["universal_symbol"])
    op.create_index("ix_trade_order_status", "trade_order", ["status"])
    op.create_index("ix_trade_order_account_status", "trade_order", ["account_id", "status"])
    op.create_index("ix_trade_order_account_native", "trade_order", ["account_id", "native_order_id"])


def downgrade() -> None:
    op.drop_index("ix_trade_order_account_native", table_name="trade_order")
    op.drop_index("ix_trade_order_account_status", table_name="trade_order")
    op.drop_index("ix_trade_order_status", table_name="trade_order")
    op.drop_index("ix_trade_order_universal_symbol", table_name="trade_order")
    op.drop_index("ix_trade_order_exchange", table_name="trade_order")
    op.drop_index("ix_trade_order_account_id", table_name="trade_order")
    op.drop_table("trade_order")

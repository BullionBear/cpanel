"""Phase 5: trade_history, order_history, sync_cursor tables

Revision ID: i8j9k0l1m2n3
Revises: h7i8j9k0l1m2
Create Date: 2026-07-15
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "i8j9k0l1m2n3"
down_revision: Union[str, Sequence[str], None] = "h7i8j9k0l1m2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NUM = sa.Numeric(36, 18)


def upgrade() -> None:
    op.create_table(
        "trade_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.BigInteger(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("universal_symbol", sa.String(length=64), nullable=False),
        sa.Column("native_trade_id", sa.String(length=64), nullable=False),
        sa.Column("native_order_id", sa.String(length=64), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("price", NUM, nullable=False),
        sa.Column("qty", NUM, nullable=False),
        sa.Column("fee", NUM, nullable=False),
        sa.Column("fee_asset", sa.String(length=32), nullable=False),
        sa.Column("traded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "account_id", "universal_symbol", "native_trade_id",
            name="uq_trade_history_account_symbol_native",
        ),
    )
    op.create_index("ix_trade_history_account_id", "trade_history", ["account_id"])
    op.create_index("ix_trade_history_exchange", "trade_history", ["exchange"])
    op.create_index("ix_trade_history_universal_symbol", "trade_history", ["universal_symbol"])
    op.create_index("ix_trade_history_native_order_id", "trade_history", ["native_order_id"])
    op.create_index("ix_trade_history_traded_at", "trade_history", ["traded_at"])
    op.create_index("ix_trade_history_account_traded", "trade_history", ["account_id", "traded_at"])

    op.create_table(
        "order_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.BigInteger(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("universal_symbol", sa.String(length=64), nullable=False),
        sa.Column("native_order_id", sa.String(length=64), nullable=False),
        sa.Column("client_order_id", sa.String(length=64), nullable=True),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("type", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("qty", NUM, nullable=False),
        sa.Column("price", NUM, nullable=True),
        sa.Column("filled_qty", NUM, nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "account_id", "universal_symbol", "native_order_id",
            name="uq_order_history_account_symbol_native",
        ),
    )
    op.create_index("ix_order_history_account_id", "order_history", ["account_id"])
    op.create_index("ix_order_history_exchange", "order_history", ["exchange"])
    op.create_index("ix_order_history_universal_symbol", "order_history", ["universal_symbol"])
    op.create_index("ix_order_history_status", "order_history", ["status"])
    op.create_index("ix_order_history_placed_at", "order_history", ["placed_at"])
    op.create_index("ix_order_history_account_placed", "order_history", ["account_id", "placed_at"])

    op.create_table(
        "sync_cursor",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.BigInteger(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("data_kind", sa.String(length=16), nullable=False),
        sa.Column("stream_key", sa.String(length=80), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "account_id", "data_kind", "stream_key",
            name="uq_sync_cursor_account_kind_stream",
        ),
    )
    op.create_index("ix_sync_cursor_account_id", "sync_cursor", ["account_id"])
    op.create_index("ix_sync_cursor_exchange", "sync_cursor", ["exchange"])


def downgrade() -> None:
    op.drop_index("ix_sync_cursor_exchange", table_name="sync_cursor")
    op.drop_index("ix_sync_cursor_account_id", table_name="sync_cursor")
    op.drop_table("sync_cursor")

    op.drop_index("ix_order_history_account_placed", table_name="order_history")
    op.drop_index("ix_order_history_placed_at", table_name="order_history")
    op.drop_index("ix_order_history_status", table_name="order_history")
    op.drop_index("ix_order_history_universal_symbol", table_name="order_history")
    op.drop_index("ix_order_history_exchange", table_name="order_history")
    op.drop_index("ix_order_history_account_id", table_name="order_history")
    op.drop_table("order_history")

    op.drop_index("ix_trade_history_account_traded", table_name="trade_history")
    op.drop_index("ix_trade_history_traded_at", table_name="trade_history")
    op.drop_index("ix_trade_history_native_order_id", table_name="trade_history")
    op.drop_index("ix_trade_history_universal_symbol", table_name="trade_history")
    op.drop_index("ix_trade_history_exchange", table_name="trade_history")
    op.drop_index("ix_trade_history_account_id", table_name="trade_history")
    op.drop_table("trade_history")

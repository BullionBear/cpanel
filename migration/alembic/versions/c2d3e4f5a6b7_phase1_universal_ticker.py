"""Phase 1: universal_ticker table

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-07-13
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NUM = sa.Numeric(36, 18)


def upgrade() -> None:
    op.create_table(
        "universal_ticker",
        sa.Column("universal_symbol", sa.String(length=64), primary_key=True),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("market_type", sa.String(length=16), nullable=False),
        sa.Column("native_symbol", sa.String(length=64), nullable=False),
        sa.Column("base_asset", sa.String(length=32), nullable=False),
        sa.Column("quote_asset", sa.String(length=32), nullable=False),
        sa.Column("price_tick", NUM, nullable=False),
        sa.Column("qty_step", NUM, nullable=False),
        sa.Column("min_qty", NUM, nullable=False),
        sa.Column("max_qty", NUM, nullable=True),
        sa.Column("min_notional", NUM, nullable=True),
        sa.Column("contract_size", NUM, nullable=True),
        sa.Column("settle_asset", sa.String(length=32), nullable=True),
        sa.Column("expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("last_price", NUM, nullable=True),
        sa.Column("price_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("exchange", "market_type", "native_symbol", name="uq_ticker_native"),
    )
    op.create_index("ix_universal_ticker_exchange", "universal_ticker", ["exchange"])
    op.create_index("ix_universal_ticker_market_type", "universal_ticker", ["market_type"])
    op.create_index("ix_universal_ticker_status", "universal_ticker", ["status"])


def downgrade() -> None:
    op.drop_index("ix_universal_ticker_status", table_name="universal_ticker")
    op.drop_index("ix_universal_ticker_market_type", table_name="universal_ticker")
    op.drop_index("ix_universal_ticker_exchange", table_name="universal_ticker")
    op.drop_table("universal_ticker")

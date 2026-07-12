"""restructure_ref_symbol_metadata

Restructure ref_symbol into core + derivative + filter + universal tables:
- ref_universal_symbol: cross-exchange canonical instrument (true universal ticker)
- ref_symbol: core table; precision integers replaced by exact price_tick_size,
  adds status / listing_timestamp / raw_meta / universal_id
- ref_symbol_derivative: 1:0..1 derivative-specific attributes (margin, expiry)
- ref_symbol_filter: long-format numeric filters (QTY_STEP / MIN_NOTIONAL / ...)

Dev-only data: existing ref_symbol rows and trading_history_cursor rows are dropped.

Revision ID: z6a7b8c9d0e1
Revises: y5z6a7b8c9d0
Create Date: 2026-07-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "z6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "y5z6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Dev data is disposable: clear cursors that FK ref_symbol, then drop the table.
    op.execute("TRUNCATE trading_history_cursor")
    op.drop_constraint(
        "fk_trading_history_cursor_symbol_id_ref_symbol",
        "trading_history_cursor",
        type_="foreignkey",
    )
    op.drop_table("ref_symbol")

    op.create_table(
        "ref_universal_symbol",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("universal_ticker", sa.String(), nullable=False),
        sa.Column("base_id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=False),
        sa.Column("instrument_type", sa.String(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(["base_id"], ["ref_token.id"]),
        sa.ForeignKeyConstraint(["quote_id"], ["ref_token.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ref_universal_symbol_universal_ticker"),
        "ref_universal_symbol",
        ["universal_ticker"],
        unique=True,
    )
    op.create_index(op.f("ix_ref_universal_symbol_base_id"), "ref_universal_symbol", ["base_id"], unique=False)
    op.create_index(op.f("ix_ref_universal_symbol_quote_id"), "ref_universal_symbol", ["quote_id"], unique=False)

    op.create_table(
        "ref_symbol",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("exchange_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("base_id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=False),
        sa.Column("price_tick_size", sa.Numeric(36, 18), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("listing_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("universal_id", sa.Integer(), nullable=False),
        sa.Column("raw_meta", JSONB(), nullable=True),
        sa.ForeignKeyConstraint(["exchange_id"], ["ref_exchange.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["ref_product.id"]),
        sa.ForeignKeyConstraint(["base_id"], ["ref_token.id"]),
        sa.ForeignKeyConstraint(["quote_id"], ["ref_token.id"]),
        sa.ForeignKeyConstraint(["universal_id"], ["ref_universal_symbol.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("exchange_id", "product_id", "name", name="uq_ref_symbol_exchange_product_name"),
    )
    op.create_index(op.f("ix_ref_symbol_exchange_id"), "ref_symbol", ["exchange_id"], unique=False)
    op.create_index(op.f("ix_ref_symbol_product_id"), "ref_symbol", ["product_id"], unique=False)
    op.create_index(op.f("ix_ref_symbol_base_id"), "ref_symbol", ["base_id"], unique=False)
    op.create_index(op.f("ix_ref_symbol_quote_id"), "ref_symbol", ["quote_id"], unique=False)
    op.create_index(op.f("ix_ref_symbol_universal_id"), "ref_symbol", ["universal_id"], unique=False)

    op.create_table(
        "ref_symbol_derivative",
        sa.Column("symbol_id", sa.Integer(), nullable=False),
        sa.Column("margin_id", sa.Integer(), nullable=False),
        sa.Column("expiry_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settlement_type", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["symbol_id"], ["ref_symbol.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["margin_id"], ["ref_token.id"]),
        sa.PrimaryKeyConstraint("symbol_id"),
    )
    op.create_index(op.f("ix_ref_symbol_derivative_margin_id"), "ref_symbol_derivative", ["margin_id"], unique=False)

    op.create_table(
        "ref_symbol_filter",
        sa.Column("symbol_id", sa.Integer(), nullable=False),
        sa.Column("filter_type", sa.String(), nullable=False),
        sa.Column("unit_type", sa.String(), nullable=False),
        sa.Column("step_size", sa.Numeric(36, 18), nullable=True),
        sa.Column("min_value", sa.Numeric(36, 18), nullable=True),
        sa.Column("max_value", sa.Numeric(36, 18), nullable=True),
        sa.ForeignKeyConstraint(["symbol_id"], ["ref_symbol.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("symbol_id", "filter_type", "unit_type"),
    )

    op.create_foreign_key(
        "fk_trading_history_cursor_symbol_id_ref_symbol",
        "trading_history_cursor",
        "ref_symbol",
        ["symbol_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.execute("TRUNCATE trading_history_cursor")
    op.drop_constraint(
        "fk_trading_history_cursor_symbol_id_ref_symbol",
        "trading_history_cursor",
        type_="foreignkey",
    )
    op.drop_table("ref_symbol_filter")
    op.drop_index(op.f("ix_ref_symbol_derivative_margin_id"), table_name="ref_symbol_derivative")
    op.drop_table("ref_symbol_derivative")
    op.drop_index(op.f("ix_ref_symbol_universal_id"), table_name="ref_symbol")
    op.drop_index(op.f("ix_ref_symbol_quote_id"), table_name="ref_symbol")
    op.drop_index(op.f("ix_ref_symbol_base_id"), table_name="ref_symbol")
    op.drop_index(op.f("ix_ref_symbol_product_id"), table_name="ref_symbol")
    op.drop_index(op.f("ix_ref_symbol_exchange_id"), table_name="ref_symbol")
    op.drop_table("ref_symbol")
    op.drop_index(op.f("ix_ref_universal_symbol_quote_id"), table_name="ref_universal_symbol")
    op.drop_index(op.f("ix_ref_universal_symbol_base_id"), table_name="ref_universal_symbol")
    op.drop_index(op.f("ix_ref_universal_symbol_universal_ticker"), table_name="ref_universal_symbol")
    op.drop_table("ref_universal_symbol")

    # Restore the previous ref_symbol shape (empty)
    op.create_table(
        "ref_symbol",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("exchange_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("base_id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=False),
        sa.Column("price_precision", sa.Integer(), nullable=False),
        sa.Column("size_precision", sa.Integer(), nullable=False),
        sa.Column("universal_ticker", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["exchange_id"], ["ref_exchange.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["ref_product.id"]),
        sa.ForeignKeyConstraint(["base_id"], ["ref_token.id"]),
        sa.ForeignKeyConstraint(["quote_id"], ["ref_token.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ref_symbol_exchange_id"), "ref_symbol", ["exchange_id"], unique=False)
    op.create_index(op.f("ix_ref_symbol_product_id"), "ref_symbol", ["product_id"], unique=False)
    op.create_index(op.f("ix_ref_symbol_base_id"), "ref_symbol", ["base_id"], unique=False)
    op.create_index(op.f("ix_ref_symbol_quote_id"), "ref_symbol", ["quote_id"], unique=False)
    op.create_index(op.f("ix_ref_symbol_universal_ticker"), "ref_symbol", ["universal_ticker"], unique=True)

    op.create_foreign_key(
        "fk_trading_history_cursor_symbol_id_ref_symbol",
        "trading_history_cursor",
        "ref_symbol",
        ["symbol_id"],
        ["id"],
        ondelete="CASCADE",
    )

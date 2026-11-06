"""Funding fee history table (exchange-sourced)

Revision ID: m4n5o6p7q8r9
Revises: l3m4n5o6p7q8
Create Date: 2026-07-17
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "m4n5o6p7q8r9"
down_revision: Union[str, Sequence[str], None] = "l3m4n5o6p7q8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NUM = sa.Numeric(36, 18)


def upgrade() -> None:
    op.create_table(
        "funding_fee_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.BigInteger(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("universal_symbol", sa.String(length=64), nullable=False),
        sa.Column("native_funding_id", sa.String(length=128), nullable=False),
        sa.Column("asset", sa.String(length=32), nullable=False),
        sa.Column("amount", NUM, nullable=False),
        sa.Column("funding_rate", NUM, nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "account_id", "native_funding_id",
            name="uq_funding_fee_history_account_native",
        ),
    )
    op.create_index("ix_funding_fee_history_account_id", "funding_fee_history", ["account_id"])
    op.create_index("ix_funding_fee_history_exchange", "funding_fee_history", ["exchange"])
    op.create_index(
        "ix_funding_fee_history_universal_symbol",
        "funding_fee_history",
        ["universal_symbol"],
    )
    op.create_index("ix_funding_fee_history_asset", "funding_fee_history", ["asset"])
    op.create_index("ix_funding_fee_history_settled_at", "funding_fee_history", ["settled_at"])
    op.create_index(
        "ix_funding_fee_history_account_settled",
        "funding_fee_history",
        ["account_id", "settled_at"],
    )
    op.create_index(
        "ix_funding_fee_history_account_symbol",
        "funding_fee_history",
        ["account_id", "universal_symbol"],
    )


def downgrade() -> None:
    op.drop_index("ix_funding_fee_history_account_symbol", table_name="funding_fee_history")
    op.drop_index("ix_funding_fee_history_account_settled", table_name="funding_fee_history")
    op.drop_index("ix_funding_fee_history_settled_at", table_name="funding_fee_history")
    op.drop_index("ix_funding_fee_history_asset", table_name="funding_fee_history")
    op.drop_index("ix_funding_fee_history_universal_symbol", table_name="funding_fee_history")
    op.drop_index("ix_funding_fee_history_exchange", table_name="funding_fee_history")
    op.drop_index("ix_funding_fee_history_account_id", table_name="funding_fee_history")
    op.drop_table("funding_fee_history")

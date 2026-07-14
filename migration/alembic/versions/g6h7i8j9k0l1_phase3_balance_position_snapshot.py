"""Phase 3: balance_snapshot and position_snapshot tables

Revision ID: g6h7i8j9k0l1
Revises: f5g6h7i8j9k0
Create Date: 2026-07-14
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "g6h7i8j9k0l1"
down_revision: Union[str, Sequence[str], None] = "f5g6h7i8j9k0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NUM = sa.Numeric(36, 18)


def upgrade() -> None:
    op.create_table(
        "balance_snapshot",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("batch_id", sa.String(length=32), nullable=False),
        sa.Column("account_id", sa.BigInteger(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("wallet", sa.String(length=16), nullable=False),
        sa.Column("asset", sa.String(length=32), nullable=False),
        sa.Column("free", NUM, nullable=False),
        sa.Column("locked", NUM, nullable=False),
        sa.Column("total", NUM, nullable=False),
        sa.Column("value_quote", NUM, nullable=True),
        sa.Column("quote_asset", sa.String(length=16), nullable=True),
        sa.Column(
            "taken_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_balance_snapshot_batch_id", "balance_snapshot", ["batch_id"])
    op.create_index("ix_balance_snapshot_account_id", "balance_snapshot", ["account_id"])
    op.create_index("ix_balance_snapshot_exchange", "balance_snapshot", ["exchange"])
    op.create_index("ix_balance_snapshot_taken_at", "balance_snapshot", ["taken_at"])
    op.create_index(
        "ix_balance_snapshot_account_taken", "balance_snapshot", ["account_id", "taken_at"]
    )

    op.create_table(
        "position_snapshot",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("batch_id", sa.String(length=32), nullable=False),
        sa.Column("account_id", sa.BigInteger(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("universal_symbol", sa.String(length=64), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("size", NUM, nullable=False),
        sa.Column("entry_price", NUM, nullable=False),
        sa.Column("mark_price", NUM, nullable=True),
        sa.Column("unrealized_pnl", NUM, nullable=True),
        sa.Column("margin", NUM, nullable=True),
        sa.Column("value_quote", NUM, nullable=True),
        sa.Column("quote_asset", sa.String(length=16), nullable=True),
        sa.Column(
            "taken_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_position_snapshot_batch_id", "position_snapshot", ["batch_id"])
    op.create_index("ix_position_snapshot_account_id", "position_snapshot", ["account_id"])
    op.create_index("ix_position_snapshot_exchange", "position_snapshot", ["exchange"])
    op.create_index("ix_position_snapshot_universal_symbol", "position_snapshot", ["universal_symbol"])
    op.create_index("ix_position_snapshot_taken_at", "position_snapshot", ["taken_at"])
    op.create_index(
        "ix_position_snapshot_account_taken", "position_snapshot", ["account_id", "taken_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_position_snapshot_account_taken", table_name="position_snapshot")
    op.drop_index("ix_position_snapshot_taken_at", table_name="position_snapshot")
    op.drop_index("ix_position_snapshot_universal_symbol", table_name="position_snapshot")
    op.drop_index("ix_position_snapshot_exchange", table_name="position_snapshot")
    op.drop_index("ix_position_snapshot_account_id", table_name="position_snapshot")
    op.drop_index("ix_position_snapshot_batch_id", table_name="position_snapshot")
    op.drop_table("position_snapshot")

    op.drop_index("ix_balance_snapshot_account_taken", table_name="balance_snapshot")
    op.drop_index("ix_balance_snapshot_taken_at", table_name="balance_snapshot")
    op.drop_index("ix_balance_snapshot_exchange", table_name="balance_snapshot")
    op.drop_index("ix_balance_snapshot_account_id", table_name="balance_snapshot")
    op.drop_index("ix_balance_snapshot_batch_id", table_name="balance_snapshot")
    op.drop_table("balance_snapshot")

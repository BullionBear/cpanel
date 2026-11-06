"""Deposit and withdrawal history tables (exchange-sourced)

Revision ID: l3m4n5o6p7q8
Revises: k0l1m2n3o4p5
Create Date: 2026-07-17
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "l3m4n5o6p7q8"
down_revision: Union[str, Sequence[str], None] = "k0l1m2n3o4p5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NUM = sa.Numeric(36, 18)


def upgrade() -> None:
    op.create_table(
        "deposit_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.BigInteger(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("native_deposit_id", sa.String(length=128), nullable=False),
        sa.Column("asset", sa.String(length=32), nullable=False),
        sa.Column("amount", NUM, nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("network", sa.String(length=32), nullable=True),
        sa.Column("tx_id", sa.String(length=128), nullable=True),
        sa.Column("credited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "account_id", "native_deposit_id",
            name="uq_deposit_history_account_native",
        ),
    )
    op.create_index("ix_deposit_history_account_id", "deposit_history", ["account_id"])
    op.create_index("ix_deposit_history_exchange", "deposit_history", ["exchange"])
    op.create_index("ix_deposit_history_asset", "deposit_history", ["asset"])
    op.create_index("ix_deposit_history_status", "deposit_history", ["status"])
    op.create_index("ix_deposit_history_created_at", "deposit_history", ["created_at"])
    op.create_index(
        "ix_deposit_history_account_created", "deposit_history", ["account_id", "created_at"]
    )
    op.create_index(
        "ix_deposit_history_account_asset", "deposit_history", ["account_id", "asset"]
    )

    op.create_table(
        "withdrawal_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.BigInteger(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("native_withdrawal_id", sa.String(length=128), nullable=False),
        sa.Column("asset", sa.String(length=32), nullable=False),
        sa.Column("amount", NUM, nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("network", sa.String(length=32), nullable=True),
        sa.Column("tx_id", sa.String(length=128), nullable=True),
        sa.Column("fee", NUM, nullable=True),
        sa.Column("fee_asset", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "account_id", "native_withdrawal_id",
            name="uq_withdrawal_history_account_native",
        ),
    )
    op.create_index("ix_withdrawal_history_account_id", "withdrawal_history", ["account_id"])
    op.create_index("ix_withdrawal_history_exchange", "withdrawal_history", ["exchange"])
    op.create_index("ix_withdrawal_history_asset", "withdrawal_history", ["asset"])
    op.create_index("ix_withdrawal_history_status", "withdrawal_history", ["status"])
    op.create_index("ix_withdrawal_history_created_at", "withdrawal_history", ["created_at"])
    op.create_index(
        "ix_withdrawal_history_account_created",
        "withdrawal_history",
        ["account_id", "created_at"],
    )
    op.create_index(
        "ix_withdrawal_history_account_asset",
        "withdrawal_history",
        ["account_id", "asset"],
    )


def downgrade() -> None:
    op.drop_index("ix_withdrawal_history_account_asset", table_name="withdrawal_history")
    op.drop_index("ix_withdrawal_history_account_created", table_name="withdrawal_history")
    op.drop_index("ix_withdrawal_history_created_at", table_name="withdrawal_history")
    op.drop_index("ix_withdrawal_history_status", table_name="withdrawal_history")
    op.drop_index("ix_withdrawal_history_asset", table_name="withdrawal_history")
    op.drop_index("ix_withdrawal_history_exchange", table_name="withdrawal_history")
    op.drop_index("ix_withdrawal_history_account_id", table_name="withdrawal_history")
    op.drop_table("withdrawal_history")

    op.drop_index("ix_deposit_history_account_asset", table_name="deposit_history")
    op.drop_index("ix_deposit_history_account_created", table_name="deposit_history")
    op.drop_index("ix_deposit_history_created_at", table_name="deposit_history")
    op.drop_index("ix_deposit_history_status", table_name="deposit_history")
    op.drop_index("ix_deposit_history_asset", table_name="deposit_history")
    op.drop_index("ix_deposit_history_exchange", table_name="deposit_history")
    op.drop_index("ix_deposit_history_account_id", table_name="deposit_history")
    op.drop_table("deposit_history")

"""internal_transfer_history: off-chain value movement

Deposit and withdrawal history only ever record value crossing a chain. A
subaccount emptied by a wallet move plus a sub->master transfer leaves no
trace in either, which is how BINANCE/scylla lost 91.84 USDT with nothing in
the panel to explain it. This table is that missing record.

Keyed by (account, scope, native id): a venue numbers its wallet-level and
account-level transfer records in separate spaces that may collide.

Revision ID: q8r9s0t1u2v3
Revises: p7q8r9s0t1u2
Create Date: 2026-08-30
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "q8r9s0t1u2v3"
down_revision: Union[str, Sequence[str], None] = "p7q8r9s0t1u2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NUM = sa.Numeric(36, 18)


def upgrade() -> None:
    op.create_table(
        "internal_transfer_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.BigInteger(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("native_transfer_id", sa.String(length=128), nullable=False),
        sa.Column("scope", sa.String(length=8), nullable=False),
        sa.Column("direction", sa.String(length=4), nullable=True),
        sa.Column("asset", sa.String(length=32), nullable=False),
        sa.Column("amount", NUM, nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("from_wallet", sa.String(length=16), nullable=True),
        sa.Column("to_wallet", sa.String(length=16), nullable=True),
        sa.Column("counterparty", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "account_id", "scope", "native_transfer_id",
            name="uq_internal_transfer_history_account_scope_native",
        ),
    )
    op.create_index(
        "ix_internal_transfer_history_account_id", "internal_transfer_history", ["account_id"]
    )
    op.create_index(
        "ix_internal_transfer_history_exchange", "internal_transfer_history", ["exchange"]
    )
    op.create_index(
        "ix_internal_transfer_history_scope", "internal_transfer_history", ["scope"]
    )
    op.create_index(
        "ix_internal_transfer_history_asset", "internal_transfer_history", ["asset"]
    )
    op.create_index(
        "ix_internal_transfer_history_status", "internal_transfer_history", ["status"]
    )
    op.create_index(
        "ix_internal_transfer_history_created_at", "internal_transfer_history", ["created_at"]
    )
    op.create_index(
        "ix_internal_transfer_history_account_created",
        "internal_transfer_history",
        ["account_id", "created_at"],
    )
    op.create_index(
        "ix_internal_transfer_history_account_asset",
        "internal_transfer_history",
        ["account_id", "asset"],
    )


def downgrade() -> None:
    for index in (
        "ix_internal_transfer_history_account_asset",
        "ix_internal_transfer_history_account_created",
        "ix_internal_transfer_history_created_at",
        "ix_internal_transfer_history_status",
        "ix_internal_transfer_history_asset",
        "ix_internal_transfer_history_scope",
        "ix_internal_transfer_history_exchange",
        "ix_internal_transfer_history_account_id",
    ):
        op.drop_index(index, table_name="internal_transfer_history")
    op.drop_table("internal_transfer_history")

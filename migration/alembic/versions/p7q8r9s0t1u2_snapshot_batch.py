"""snapshot_batch: one row per capture pass, empty passes included

A capture only writes balance/position rows for what an account holds, so an
emptied account wrote nothing and the read paths kept resolving the latest
batch to the last pass that *did* find something — serving a stale holding as
current. This table records the pass itself.

Existing batches are backfilled from the rows they wrote, so history captured
before this revision keeps resolving. Backfilled batches are all non-empty by
construction (an empty one left no trace to recover); the gap they represent
stays a gap.

Revision ID: p7q8r9s0t1u2
Revises: o6p7q8r9s0t1
Create Date: 2026-08-30
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "p7q8r9s0t1u2"
down_revision: Union[str, Sequence[str], None] = "o6p7q8r9s0t1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "snapshot_batch",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("batch_id", sa.String(length=32), nullable=False, unique=True),
        sa.Column("account_id", sa.BigInteger(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("balance_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("position_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "taken_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_snapshot_batch_account_id", "snapshot_batch", ["account_id"])
    op.create_index("ix_snapshot_batch_exchange", "snapshot_batch", ["exchange"])
    op.create_index("ix_snapshot_batch_taken_at", "snapshot_batch", ["taken_at"])
    op.create_index(
        "ix_snapshot_batch_account_taken", "snapshot_batch", ["account_id", "taken_at"]
    )

    # Every row of a pass shares batch_id/account_id/exchange/taken_at, so the
    # grouped min(taken_at) is that pass's exact stamp.
    op.execute(
        """
        INSERT INTO snapshot_batch (
            batch_id, account_id, exchange, balance_rows, position_rows, taken_at
        )
        SELECT batch_id,
               account_id,
               exchange,
               SUM(is_balance),
               SUM(is_position),
               MIN(taken_at)
        FROM (
            SELECT batch_id, account_id, exchange, taken_at, 1 AS is_balance, 0 AS is_position
            FROM balance_snapshot
            UNION ALL
            SELECT batch_id, account_id, exchange, taken_at, 0 AS is_balance, 1 AS is_position
            FROM position_snapshot
        ) AS captured_rows
        GROUP BY batch_id, account_id, exchange
        """
    )


def downgrade() -> None:
    op.drop_index("ix_snapshot_batch_account_taken", table_name="snapshot_batch")
    op.drop_index("ix_snapshot_batch_taken_at", table_name="snapshot_batch")
    op.drop_index("ix_snapshot_batch_exchange", table_name="snapshot_batch")
    op.drop_index("ix_snapshot_batch_account_id", table_name="snapshot_batch")
    op.drop_table("snapshot_batch")

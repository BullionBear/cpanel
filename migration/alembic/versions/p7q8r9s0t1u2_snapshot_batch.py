"""snapshot_batch: record every capture pass, including empty ones

A capture of an account holding nothing writes no balance or position rows,
so deriving the latest batch from those rows pinned an emptied account to its
last funded capture forever. This table records the pass itself.

Existing history is backfilled from the batch stamps already on the snapshot
rows, so the batches captured before this revision keep their timestamps.

Revision ID: p7q8r9s0t1u2
Revises: o6p7q8r9s0t1
Create Date: 2026-08-23
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "p7q8r9s0t1u2"
down_revision: Union[str, Sequence[str], None] = "o6p7q8r9s0t1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Every row of a batch carries the same account, exchange, and taken_at, so
# MIN() just picks that shared value out of the group.
_BACKFILL = """
    INSERT INTO snapshot_batch (batch_id, account_id, exchange, taken_at)
    SELECT s.batch_id, MIN(s.account_id), MIN(s.exchange), MIN(s.taken_at)
      FROM {table} s
     WHERE s.batch_id NOT IN (SELECT batch_id FROM snapshot_batch)
     GROUP BY s.batch_id
"""


def upgrade() -> None:
    op.create_table(
        "snapshot_batch",
        sa.Column("batch_id", sa.String(length=32), primary_key=True),
        sa.Column(
            "account_id", sa.BigInteger(), sa.ForeignKey("account.id"), nullable=False
        ),
        sa.Column("exchange", sa.String(length=32), nullable=False),
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

    op.execute(_BACKFILL.format(table="balance_snapshot"))
    op.execute(_BACKFILL.format(table="position_snapshot"))


def downgrade() -> None:
    op.drop_index("ix_snapshot_batch_account_taken", table_name="snapshot_batch")
    op.drop_index("ix_snapshot_batch_taken_at", table_name="snapshot_batch")
    op.drop_index("ix_snapshot_batch_exchange", table_name="snapshot_batch")
    op.drop_index("ix_snapshot_batch_account_id", table_name="snapshot_batch")
    op.drop_table("snapshot_batch")

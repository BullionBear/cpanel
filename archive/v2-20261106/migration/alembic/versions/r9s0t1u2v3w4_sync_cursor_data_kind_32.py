"""sync_cursor.data_kind: widen to 32 chars

``internal_transfers`` is 18 characters and the column was varchar(16), so
every attempt to create one of those cursors raised StringDataRightTruncation
on PostgreSQL, poisoning the session and aborting the rest of that account's
history sync. SQLite does not enforce varchar length, which is why the test
suite never saw it.

Revision ID: r9s0t1u2v3w4
Revises: q8r9s0t1u2v3
Create Date: 2026-08-30
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "r9s0t1u2v3w4"
down_revision: Union[str, Sequence[str], None] = "q8r9s0t1u2v3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "sync_cursor",
        "data_kind",
        existing_type=sa.String(length=16),
        type_=sa.String(length=32),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Rows whose kind no longer fits would block the narrowing; drop those
    # cursors rather than fail. They are rebuilt on the next sync.
    op.execute("DELETE FROM sync_cursor WHERE length(data_kind) > 16")
    op.alter_column(
        "sync_cursor",
        "data_kind",
        existing_type=sa.String(length=32),
        type_=sa.String(length=16),
        existing_nullable=False,
    )

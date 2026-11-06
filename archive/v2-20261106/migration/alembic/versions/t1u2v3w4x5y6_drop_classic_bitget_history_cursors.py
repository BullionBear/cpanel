"""Drop classic Bitget history sync cursors.

UTA trade, order, and funding streams are SPOT / USDT-FUTURES /
COIN-FUTURES. The classic keys are no longer polled, so leaving them
makes the history page show streams that never move. Deposit and
withdrawal cursors stay on the ``account`` key; the connector ignores
the old ``id`` field and continues from ``start``.

Revision ID: t1u2v3w4x5y6
Revises: s0t1u2v3w4x5
Create Date: 2026-09-29
"""
from alembic import op

revision = "t1u2v3w4x5y6"
down_revision = "s0t1u2v3w4x5"
branch_labels = None
depends_on = None

_CLASSIC_STREAMS = ("spot", "mix:USDT-FUTURES", "mix:COIN-FUTURES")


def upgrade() -> None:
    keys = ", ".join(f"'{key}'" for key in _CLASSIC_STREAMS)
    op.execute(
        f"DELETE FROM sync_cursor WHERE exchange = 'BITGET' AND stream_key IN ({keys})"
    )


def downgrade() -> None:
    # Cursors are rebuilt by the next history sync. Nothing to restore.
    pass

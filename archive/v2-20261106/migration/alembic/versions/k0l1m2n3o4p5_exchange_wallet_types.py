"""Relabel wallet types to per-exchange vocabulary.

Wallet structure differs per venue, so the generic MARGIN/DERIVATIVES labels
are replaced: Binance snapshots use SPOT/FUNDING/FUTURES (DERIVATIVES was the
futures margin wallet), and Bybit's Unified Trading Account reports everything
under UNIFIED except the FUNDING wallet.

Revision ID: k0l1m2n3o4p5
Revises: j9k0l1m2n3o4
Create Date: 2026-07-17
"""
from alembic import op

revision = "k0l1m2n3o4p5"
down_revision = "j9k0l1m2n3o4"
branch_labels = None
depends_on = None

# (table, exchange column present?) — transfer rows carry from/to wallet legs.
_RELABEL = [
    ("BINANCE", "DERIVATIVES", "FUTURES"),
    ("BYBIT", "DERIVATIVES", "UNIFIED"),
    ("BYBIT", "SPOT", "UNIFIED"),
    ("BYBIT", "MARGIN", "UNIFIED"),
]


def upgrade() -> None:
    for exchange, old, new in _RELABEL:
        op.execute(
            f"UPDATE balance_snapshot SET wallet = '{new}' "
            f"WHERE exchange = '{exchange}' AND wallet = '{old}'"
        )
        op.execute(
            f"UPDATE transfer SET from_wallet = '{new}' "
            f"WHERE from_exchange = '{exchange}' AND from_wallet = '{old}'"
        )
        op.execute(
            f"UPDATE transfer SET to_wallet = '{new}' "
            f"WHERE to_exchange = '{exchange}' AND to_wallet = '{old}'"
        )


def downgrade() -> None:
    # The old labels are ambiguous (UNIFIED covered several); restore the
    # closest original.
    for exchange, new, old in [
        ("BINANCE", "DERIVATIVES", "FUTURES"),
        ("BYBIT", "DERIVATIVES", "UNIFIED"),
    ]:
        op.execute(
            f"UPDATE balance_snapshot SET wallet = '{new}' "
            f"WHERE exchange = '{exchange}' AND wallet = '{old}'"
        )
        op.execute(
            f"UPDATE transfer SET from_wallet = '{new}' "
            f"WHERE from_exchange = '{exchange}' AND from_wallet = '{old}'"
        )
        op.execute(
            f"UPDATE transfer SET to_wallet = '{new}' "
            f"WHERE to_exchange = '{exchange}' AND to_wallet = '{old}'"
        )

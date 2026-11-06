"""Split FUTURES wallet into UMARGIN and CMARGIN.

The panel used one FUTURES label for both USD-M and coin-M futures accounts.
Those are distinct venue wallets (Binance UMFUTURE/CMFUTURE, Bitget
usdt_futures/coin_futures, Gate settle=usdt vs btc), so they become their
own WalletType values. Existing FUTURES rows cannot be split after the fact
and are relabeled UMARGIN; the next snapshot / history sync writes the
correct wallet.

Revision ID: s0t1u2v3w4x5
Revises: r9s0t1u2v3w4
Create Date: 2026-08-30
"""
from alembic import op

revision = "s0t1u2v3w4x5"
down_revision = "r9s0t1u2v3w4"
branch_labels = None
depends_on = None

_TABLES = (
    ("balance_snapshot", "wallet"),
    ("transfer", "from_wallet"),
    ("transfer", "to_wallet"),
    ("internal_transfer_history", "from_wallet"),
    ("internal_transfer_history", "to_wallet"),
)


def upgrade() -> None:
    for table, column in _TABLES:
        op.execute(f"UPDATE {table} SET {column} = 'UMARGIN' WHERE {column} = 'FUTURES'")


def downgrade() -> None:
    for table, column in _TABLES:
        op.execute(f"UPDATE {table} SET {column} = 'FUTURES' WHERE {column} = 'UMARGIN'")

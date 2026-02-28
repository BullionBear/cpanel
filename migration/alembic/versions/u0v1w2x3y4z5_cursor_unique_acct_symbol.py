"""cursor_unique_acct_symbol

Add unique constraint on (acct_id, symbol_id) for trading_history_cursor.

Revision ID: u0v1w2x3y4z5
Revises: v2w3x4y5z6a7
Create Date: 2026-02-25

"""
from typing import Sequence, Union

from alembic import op


revision: str = "u0v1w2x3y4z5"
down_revision: Union[str, Sequence[str], None] = "v2w3x4y5z6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_trading_history_cursor_acct_symbol",
        "trading_history_cursor",
        ["acct_id", "symbol_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_trading_history_cursor_acct_symbol",
        "trading_history_cursor",
        type_="unique",
    )

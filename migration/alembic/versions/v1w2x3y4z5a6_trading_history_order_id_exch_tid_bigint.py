"""trading_history_order_id_exch_tid_bigint

Alter trading_history order_id and exch_tid from INTEGER to BIGINT.

Revision ID: v1w2x3y4z5a6
Revises: u0v1w2x3y4z5
Create Date: 2026-02-25

"""
from typing import Sequence, Union

from alembic import op


revision: str = "v1w2x3y4z5a6"
down_revision: Union[str, Sequence[str], None] = "u0v1w2x3y4z5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE trading_history ALTER COLUMN order_id TYPE BIGINT USING order_id::BIGINT"
    )
    op.execute(
        "ALTER TABLE trading_history ALTER COLUMN exch_tid TYPE BIGINT USING exch_tid::BIGINT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE trading_history ALTER COLUMN order_id TYPE INTEGER USING order_id::INTEGER"
    )
    op.execute(
        "ALTER TABLE trading_history ALTER COLUMN exch_tid TYPE INTEGER USING exch_tid::INTEGER"
    )

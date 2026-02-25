"""cursor_start_end_bigint

Alter cursor_start and cursor_end from INTEGER to BIGINT (for epoch milliseconds).

Revision ID: t9u0v1w2x3y4
Revises: s8t9u0v1w2x3
Create Date: 2026-02-25

"""
from typing import Sequence, Union

from alembic import op


revision: str = "t9u0v1w2x3y4"
down_revision: Union[str, Sequence[str], None] = "s8t9u0v1w2x3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE trading_history_cursor ALTER COLUMN cursor_start TYPE BIGINT USING cursor_start::BIGINT"
    )
    op.execute(
        "ALTER TABLE trading_history_cursor ALTER COLUMN cursor_end TYPE BIGINT USING cursor_end::BIGINT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE trading_history_cursor ALTER COLUMN cursor_start TYPE INTEGER USING cursor_start::INTEGER"
    )
    op.execute(
        "ALTER TABLE trading_history_cursor ALTER COLUMN cursor_end TYPE INTEGER USING cursor_end::INTEGER"
    )

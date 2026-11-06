"""drop_product_id_from_trading_history_cursor

Remove product_id from trading_history_cursor (symbol_id already determines product).

Revision ID: v2w3x4y5z6a7
Revises: u1v2w3x4y5z6
Create Date: 2026-02-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "v2w3x4y5z6a7"
down_revision: Union[str, Sequence[str], None] = "u1v2w3x4y5z6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_trading_history_cursor_product_id")
    op.execute("ALTER TABLE trading_history_cursor DROP CONSTRAINT IF EXISTS trading_history_cursor_product_id_fkey")
    op.execute("ALTER TABLE trading_history_cursor DROP COLUMN IF EXISTS product_id")


def downgrade() -> None:
    op.add_column("trading_history_cursor", sa.Column("product_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "trading_history_cursor_product_id_fkey",
        "trading_history_cursor",
        "ref_product",
        ["product_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_trading_history_cursor_product_id ON trading_history_cursor (product_id)")

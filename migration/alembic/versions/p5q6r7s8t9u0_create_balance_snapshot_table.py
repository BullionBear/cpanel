"""create_balance_snapshot_table

Create table balance_snapshot with columns: id, sid, wallet_id, token, amount, ref_price.

Revision ID: p5q6r7s8t9u0
Revises: o4p5q6r7s8t9
Create Date: 2026-02-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "p5q6r7s8t9u0"
down_revision: Union[str, Sequence[str], None] = "o4p5q6r7s8t9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "balance_snapshot",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sid", sa.Integer(), nullable=False),
        sa.Column("wallet_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(32), nullable=False),
        sa.Column("amount", sa.Numeric(36, 18), nullable=False),
        sa.Column("ref_price", sa.Numeric(36, 18), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["wallet_id"], ["wallet.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_balance_snapshot_sid"), "balance_snapshot", ["sid"], unique=False)
    op.create_index(op.f("ix_balance_snapshot_token"), "balance_snapshot", ["token"], unique=False)
    op.create_index(op.f("ix_balance_snapshot_wallet_id"), "balance_snapshot", ["wallet_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_balance_snapshot_wallet_id"), table_name="balance_snapshot")
    op.drop_index(op.f("ix_balance_snapshot_token"), table_name="balance_snapshot")
    op.drop_index(op.f("ix_balance_snapshot_sid"), table_name="balance_snapshot")
    op.drop_table("balance_snapshot")

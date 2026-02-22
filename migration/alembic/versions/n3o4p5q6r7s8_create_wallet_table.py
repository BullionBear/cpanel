"""create_wallet_table

Create table wallet with columns: id, category_id, user_id, acct_id, name, is_active.

Revision ID: n3o4p5q6r7s8
Revises: m2n3o4p5q6r7
Create Date: 2026-02-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "n3o4p5q6r7s8"
down_revision: Union[str, Sequence[str], None] = "m2n3o4p5q6r7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wallet",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("acct_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.ForeignKeyConstraint(["acct_id"], ["account.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["ref_wallet.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_wallet_acct_id"), "wallet", ["acct_id"], unique=False)
    op.create_index(op.f("ix_wallet_category_id"), "wallet", ["category_id"], unique=False)
    op.create_index(op.f("ix_wallet_user_id"), "wallet", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_wallet_user_id"), table_name="wallet")
    op.drop_index(op.f("ix_wallet_category_id"), table_name="wallet")
    op.drop_index(op.f("ix_wallet_acct_id"), table_name="wallet")
    op.drop_table("wallet")

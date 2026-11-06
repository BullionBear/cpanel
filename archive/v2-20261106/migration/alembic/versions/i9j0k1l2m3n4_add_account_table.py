"""add_account_table

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-02-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "i9j0k1l2m3n4"
down_revision: Union[str, Sequence[str], None] = "h8i9j0k1l2m3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "account",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uid", sa.String(), nullable=False),
        sa.Column("exch_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("parent_uid", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["exch_id"], ["ref_exchange.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_account_exch_id"), "account", ["exch_id"], unique=False)
    op.create_index(op.f("ix_account_parent_uid"), "account", ["parent_uid"], unique=False)
    op.create_index(op.f("ix_account_uid"), "account", ["uid"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_account_uid"), table_name="account")
    op.drop_index(op.f("ix_account_parent_uid"), table_name="account")
    op.drop_index(op.f("ix_account_exch_id"), table_name="account")
    op.drop_table("account")

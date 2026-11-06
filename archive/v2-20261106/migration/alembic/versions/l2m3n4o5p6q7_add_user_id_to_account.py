"""add_user_id_to_account

Add user_id column to account (4th column: id, uid, exch_id, user_id, name, parent_uid).

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-02-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "l2m3n4o5p6q7"
down_revision: Union[str, Sequence[str], None] = "k1l2m3n4o5p6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "account",
        sa.Column("user_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_account_user_id_users"),
        "account",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_account_user_id"), "account", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_account_user_id"), table_name="account")
    op.drop_constraint(op.f("fk_account_user_id_users"), "account", type_="foreignkey")
    op.drop_column("account", "user_id")

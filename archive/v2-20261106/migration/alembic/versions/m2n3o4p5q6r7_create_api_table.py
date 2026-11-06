"""create_api_table

Create table api with columns: id, acct_id, user_id, exch_id, name, api_type, api_key, api_secret, passphrase, created_at, updated_at.

Revision ID: m2n3o4p5q6r7
Revises: l2m3n4o5p6q7
Create Date: 2026-02-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "m2n3o4p5q6r7"
down_revision: Union[str, Sequence[str], None] = "l2m3n4o5p6q7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename indexes on archived table so ix_api_* names are free for the new api table.
    # (When api was renamed to api_archived_20260221, indexes kept names like ix_api_user_id.)
    conn = op.get_bind()
    for old_name, new_name in [
        ("ix_api_acct_id", "ix_api_archived_20260221_acct_id"),
        ("ix_api_exch_id", "ix_api_archived_20260221_exch_id"),
        ("ix_api_user_id", "ix_api_archived_20260221_user_id"),
    ]:
        conn.execute(sa.text(f'ALTER INDEX IF EXISTS "{old_name}" RENAME TO "{new_name}"'))

    op.create_table(
        "api",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("acct_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("exch_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("api_type", sa.String(), nullable=False),
        sa.Column("api_key", sa.String(), nullable=False),
        sa.Column("api_secret", sa.String(), nullable=False),
        sa.Column("passphrase", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["acct_id"], ["account.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["exch_id"], ["ref_exchange.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_api_acct_id"), "api", ["acct_id"], unique=False)
    op.create_index(op.f("ix_api_exch_id"), "api", ["exch_id"], unique=False)
    op.create_index(op.f("ix_api_user_id"), "api", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_api_user_id"), table_name="api")
    op.drop_index(op.f("ix_api_exch_id"), table_name="api")
    op.drop_index(op.f("ix_api_acct_id"), table_name="api")
    op.drop_table("api")

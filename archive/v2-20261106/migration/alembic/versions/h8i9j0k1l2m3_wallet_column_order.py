"""wallet_column_order

Recreate wallet table with column order: id, name, wallet_type_id, api_id, is_active.

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-02-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "h8i9j0k1l2m3"
down_revision: Union[str, Sequence[str], None] = "g7h8i9j0k1l2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Recreate wallet table with desired column order."""
    # Create new table with column order: id, name, wallet_type_id, api_id, is_active
    op.create_table(
        "wallet_new",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("wallet_type_id", sa.Integer(), nullable=False),
        sa.Column("api_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["api_id"], ["api.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["wallet_type_id"], ["ref_wallet.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_wallet_new_wallet_type_id"), "wallet_new", ["wallet_type_id"], unique=False)
    op.create_index(op.f("ix_wallet_new_api_id"), "wallet_new", ["api_id"], unique=False)

    # Copy data (same column names, order in SELECT doesn't change physical table order)
    op.execute(
        """
        INSERT INTO wallet_new (id, name, wallet_type_id, api_id, is_active)
        SELECT id, name, wallet_type_id, api_id, is_active FROM wallet
        """
    )

    # Preserve sequence for id if using serial
    op.execute("SELECT setval(pg_get_serial_sequence('wallet_new', 'id'), COALESCE((SELECT MAX(id) FROM wallet_new), 1))")

    op.drop_table("wallet")
    op.rename_table("wallet_new", "wallet")
    op.execute("ALTER SEQUENCE IF EXISTS wallet_new_id_seq RENAME TO wallet_id_seq")

    # Indexes kept names ix_wallet_new_*; drop and recreate with standard names
    op.drop_index(op.f("ix_wallet_new_wallet_type_id"), table_name="wallet")
    op.drop_index(op.f("ix_wallet_new_api_id"), table_name="wallet")
    op.create_index(op.f("ix_wallet_wallet_type_id"), "wallet", ["wallet_type_id"], unique=False)
    op.create_index(op.f("ix_wallet_api_id"), "wallet", ["api_id"], unique=False)


def downgrade() -> None:
    """Revert to previous column order (id, name, wallet_type_id, is_active, api_id)."""
    op.create_table(
        "wallet_old",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("wallet_type_id", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("api_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["api_id"], ["api.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["wallet_type_id"], ["ref_wallet.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_wallet_old_wallet_type_id"), "wallet_old", ["wallet_type_id"], unique=False)
    op.create_index(op.f("ix_wallet_old_api_id"), "wallet_old", ["api_id"], unique=False)
    op.execute(
        """
        INSERT INTO wallet_old (id, name, wallet_type_id, is_active, api_id)
        SELECT id, name, wallet_type_id, is_active, api_id FROM wallet
        """
    )
    op.execute("SELECT setval(pg_get_serial_sequence('wallet_old', 'id'), COALESCE((SELECT MAX(id) FROM wallet_old), 1))")
    op.drop_index(op.f("ix_wallet_api_id"), table_name="wallet")
    op.drop_index(op.f("ix_wallet_wallet_type_id"), table_name="wallet")
    op.drop_table("wallet")
    op.rename_table("wallet_old", "wallet")
    op.execute("ALTER SEQUENCE IF EXISTS wallet_old_id_seq RENAME TO wallet_id_seq")
    op.drop_index(op.f("ix_wallet_old_wallet_type_id"), table_name="wallet")
    op.drop_index(op.f("ix_wallet_old_api_id"), table_name="wallet")
    op.create_index(op.f("ix_wallet_wallet_type_id"), "wallet", ["wallet_type_id"], unique=False)
    op.create_index(op.f("ix_wallet_api_id"), "wallet", ["api_id"], unique=False)
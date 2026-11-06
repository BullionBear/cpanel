"""add_wallet_table

Revision ID: e5f6g7h8i9j0
Revises: d4e5f6g7h8i9
Create Date: 2026-02-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e5f6g7h8i9j0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6g7h8i9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "wallet",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("exchange_id", sa.Integer(), nullable=False),
        sa.Column("wallet_type_id", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["exchange_id"], ["ref_exchange.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["wallet_type_id"], ["ref_wallet.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_wallet_exchange_id"), "wallet", ["exchange_id"], unique=False)
    op.create_index(op.f("ix_wallet_wallet_type_id"), "wallet", ["wallet_type_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_wallet_wallet_type_id"), table_name="wallet")
    op.drop_index(op.f("ix_wallet_exchange_id"), table_name="wallet")
    op.drop_table("wallet")

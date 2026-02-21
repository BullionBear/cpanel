"""remove_exchange_id_from_wallet

Revision ID: g7h8i9j0k1l2
Revises: f6g7h8i9j0k1
Create Date: 2026-02-20

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "g7h8i9j0k1l2"
down_revision: Union[str, Sequence[str], None] = "f6g7h8i9j0k1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: drop exchange_id (exchange comes via api_id -> api)."""
    op.drop_index(op.f("ix_wallet_exchange_id"), table_name="wallet")
    op.drop_constraint("wallet_exchange_id_fkey", "wallet", type_="foreignkey")
    op.drop_column("wallet", "exchange_id")


def downgrade() -> None:
    """Downgrade schema: restore exchange_id column."""
    import sqlalchemy as sa

    op.add_column("wallet", sa.Column("exchange_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "wallet_exchange_id_fkey",
        "wallet",
        "ref_exchange",
        ["exchange_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_wallet_exchange_id"), "wallet", ["exchange_id"], unique=False)

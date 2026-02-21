"""add_api_id_to_wallet

Revision ID: f6g7h8i9j0k1
Revises: e5f6g7h8i9j0
Create Date: 2026-02-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f6g7h8i9j0k1"
down_revision: Union[str, Sequence[str], None] = "e5f6g7h8i9j0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("wallet", sa.Column("api_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_wallet_api_id",
        "wallet",
        "api",
        ["api_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_wallet_api_id"), "wallet", ["api_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_wallet_api_id"), table_name="wallet")
    op.drop_constraint("fk_wallet_api_id", "wallet", type_="foreignkey")
    op.drop_column("wallet", "api_id")

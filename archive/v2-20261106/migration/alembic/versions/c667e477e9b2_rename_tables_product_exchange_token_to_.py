"""rename tables product exchange token to ref_product ref_exchange ref_token

Revision ID: c667e477e9b2
Revises: cb4fc370a36d
Create Date: 2026-01-10 16:49:41.560176

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c667e477e9b2'
down_revision: Union[str, Sequence[str], None] = 'cb4fc370a36d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # In PostgreSQL, renaming a table automatically updates foreign key references
    # Foreign key constraints will automatically reference the new table names
    op.rename_table('product', 'ref_product')
    op.rename_table('exchange', 'ref_exchange')
    op.rename_table('token', 'ref_token')


def downgrade() -> None:
    """Downgrade schema."""
    # Rename tables back to original names
    # Foreign key constraints will automatically reference the original table names
    op.rename_table('ref_token', 'token')
    op.rename_table('ref_exchange', 'exchange')
    op.rename_table('ref_product', 'product')

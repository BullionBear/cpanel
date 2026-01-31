"""add api_type column to api table

Revision ID: a1b2c3d4e5f6
Revises: c667e477e9b2
Create Date: 2026-01-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'c667e477e9b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add api_type column with default 'HMAC' for existing records
    op.add_column('api', sa.Column('api_type', sa.String(), nullable=False, server_default='HMAC'))
    # Remove the server_default after populating existing records
    op.alter_column('api', 'api_type', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('api', 'api_type')

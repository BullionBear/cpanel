"""api_credential passphrase_enc for Bitget-style keys

Revision ID: o6p7q8r9s0t1
Revises: n5o6p7q8r9s0
Create Date: 2026-07-18
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "o6p7q8r9s0t1"
down_revision: Union[str, Sequence[str], None] = "n5o6p7q8r9s0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "api_credential",
        sa.Column("passphrase_enc", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("api_credential", "passphrase_enc")

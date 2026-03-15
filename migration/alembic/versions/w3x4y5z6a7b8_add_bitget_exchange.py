"""add_bitget_exchange

Insert Bitget into ref_exchange (id=3, name='Bitget', slug='BITGET').

Revision ID: w3x4y5z6a7b8
Revises: v1w2x3y4z5a6
Create Date: 2026-03-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "w3x4y5z6a7b8"
down_revision: Union[str, Sequence[str], None] = "v1w2x3y4z5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO ref_exchange (id, name, slug) VALUES (3, 'Bitget', 'BITGET') "
            "ON CONFLICT (id) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM ref_exchange WHERE id = 3 AND slug = 'BITGET'"))

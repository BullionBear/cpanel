"""rename_sequences_add_umargin

Rename stale PostgreSQL sequences to match renamed tables, and seed
UMARGIN product into ref_product.

Revision ID: x4y5z6a7b8c9
Revises: w3x4y5z6a7b8
Create Date: 2026-03-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "x4y5z6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "w3x4y5z6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RENAMES = [
    ("product_id_seq", "ref_product_id_seq"),
    ("exchange_id_seq", "ref_exchange_id_seq"),
    ("token_id_seq", "ref_token_id_seq"),
    ("symbol_id_seq", "ref_symbol_id_seq"),
]


def upgrade() -> None:
    for old, new in RENAMES:
        op.execute(sa.text(f"ALTER SEQUENCE IF EXISTS {old} RENAME TO {new}"))

    op.execute(
        sa.text(
            "INSERT INTO ref_product (name, slug) VALUES ('USDT-Margined Perpetual', 'UMARGIN') "
            "ON CONFLICT (slug) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM ref_product WHERE slug = 'UMARGIN'"))

    for old, new in RENAMES:
        op.execute(sa.text(f"ALTER SEQUENCE IF EXISTS {new} RENAME TO {old}"))

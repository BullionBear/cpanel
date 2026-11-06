"""rename_symbol_to_ref_symbol

Rename table symbol to ref_symbol (reference type).

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-02-21

"""
from typing import Sequence, Union

from alembic import op


revision: str = "k1l2m3n4o5p6"
down_revision: Union[str, Sequence[str], None] = "j0k1l2m3n4o5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table("symbol", "ref_symbol")


def downgrade() -> None:
    op.rename_table("ref_symbol", "symbol")

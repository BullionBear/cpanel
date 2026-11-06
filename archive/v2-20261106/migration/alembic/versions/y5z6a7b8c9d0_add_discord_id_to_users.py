"""Add discord_id to users

Revision ID: y5z6a7b8c9d0
Revises: x4y5z6a7b8c9
Create Date: 2026-07-12 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "y5z6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "x4y5z6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("discord_id", sa.String(), nullable=True))
    op.create_index(op.f("ix_users_discord_id"), "users", ["discord_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_users_discord_id"), table_name="users")
    op.drop_column("users", "discord_id")

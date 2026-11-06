"""Panel API tokens and audit request metadata

Revision ID: n5o6p7q8r9s0
Revises: m4n5o6p7q8r9
Create Date: 2026-07-18
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "n5o6p7q8r9s0"
down_revision: Union[str, Sequence[str], None] = "m4n5o6p7q8r9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "panel_access_token",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("actor_name", sa.String(length=128), nullable=True),
        sa.Column("access_token", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("access_token"),
    )
    op.create_index("ix_panel_access_token_actor_id", "panel_access_token", ["actor_id"])

    op.add_column("audit_log", sa.Column("ip_address", sa.String(length=64), nullable=True))
    op.add_column("audit_log", sa.Column("user_agent", sa.String(length=512), nullable=True))
    op.add_column("audit_log", sa.Column("auth_method", sa.String(length=32), nullable=True))
    op.add_column("audit_log", sa.Column("return_code", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_log", "return_code")
    op.drop_column("audit_log", "auth_method")
    op.drop_column("audit_log", "user_agent")
    op.drop_column("audit_log", "ip_address")
    op.drop_index("ix_panel_access_token_actor_id", table_name="panel_access_token")
    op.drop_table("panel_access_token")

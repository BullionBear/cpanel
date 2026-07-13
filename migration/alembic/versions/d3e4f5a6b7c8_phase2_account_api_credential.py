"""Phase 2: account and api_credential tables

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-07-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "account",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("account_type", sa.String(length=8), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), sa.ForeignKey("account.id"), nullable=True),
        sa.Column("native_uid", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("exchange", "label", name="uq_account_exchange_label"),
    )
    op.create_index("ix_account_exchange", "account", ["exchange"])

    op.create_table(
        "api_credential",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.BigInteger(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("scheme", sa.String(length=16), nullable=False),
        sa.Column("api_key", sa.String(length=256), nullable=False),
        sa.Column("secret_enc", sa.Text(), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=True),
        sa.Column("permissions", JSONB(), nullable=True),
        sa.Column("read_only", sa.Boolean(), nullable=True),
        sa.Column("can_withdraw", sa.Boolean(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_api_credential_account_id", "api_credential", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_api_credential_account_id", table_name="api_credential")
    op.drop_table("api_credential")
    op.drop_index("ix_account_exchange", table_name="account")
    op.drop_table("account")

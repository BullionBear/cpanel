"""Phase 6: transfer and transfer_step tables

Revision ID: j9k0l1m2n3o4
Revises: i8j9k0l1m2n3
Create Date: 2026-07-17
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "j9k0l1m2n3o4"
down_revision: Union[str, Sequence[str], None] = "i8j9k0l1m2n3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NUM = sa.Numeric(36, 18)


def upgrade() -> None:
    op.create_table(
        "transfer",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("stuck", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("asset", sa.String(length=32), nullable=False),
        sa.Column("amount", NUM, nullable=False),
        sa.Column("from_account_id", sa.BigInteger(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("to_account_id", sa.BigInteger(), sa.ForeignKey("account.id"), nullable=True),
        sa.Column("from_exchange", sa.String(length=32), nullable=False),
        sa.Column("to_exchange", sa.String(length=32), nullable=False),
        sa.Column("from_wallet", sa.String(length=16), nullable=True),
        sa.Column("to_wallet", sa.String(length=16), nullable=True),
        sa.Column("network", sa.String(length=32), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("address_tag", sa.String(length=64), nullable=True),
        sa.Column("native_transfer_id", sa.String(length=64), nullable=True),
        sa.Column("native_withdrawal_id", sa.String(length=64), nullable=True),
        sa.Column("tx_id", sa.String(length=128), nullable=True),
        sa.Column("deposit_native_id", sa.String(length=128), nullable=True),
        sa.Column("received_amount", NUM, nullable=True),
        sa.Column("fee", NUM, nullable=True),
        sa.Column("fee_asset", sa.String(length=32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_transfer_status", "transfer", ["status"])
    op.create_index("ix_transfer_from_account_id", "transfer", ["from_account_id"])
    op.create_index("ix_transfer_to_account_id", "transfer", ["to_account_id"])
    op.create_index("ix_transfer_from_exchange", "transfer", ["from_exchange"])

    op.create_table(
        "transfer_step",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("transfer_id", sa.BigInteger(), sa.ForeignKey("transfer.id"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("transfer_id", "seq", name="uq_transfer_step_transfer_seq"),
    )
    op.create_index("ix_transfer_step_transfer_id", "transfer_step", ["transfer_id"])


def downgrade() -> None:
    op.drop_index("ix_transfer_step_transfer_id", table_name="transfer_step")
    op.drop_table("transfer_step")

    op.drop_index("ix_transfer_from_exchange", table_name="transfer")
    op.drop_index("ix_transfer_to_account_id", table_name="transfer")
    op.drop_index("ix_transfer_from_account_id", table_name="transfer")
    op.drop_index("ix_transfer_status", table_name="transfer")
    op.drop_table("transfer")

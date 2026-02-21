"""rename_api_and_wallet_to_archived

Rename api -> api_archived_20260221, wallet -> wallet_archived_20240221.
Drop foreign keys that reference these tables before renaming.

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-02-21

"""
from typing import Sequence, Union

from alembic import op


revision: str = "j0k1l2m3n4o5"
down_revision: Union[str, Sequence[str], None] = "i9j0k1l2m3n4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Drop FK from wallet -> api (so we can rename api)
    #    Constraint name may be wallet_api_id_fkey (PostgreSQL default) or fk_wallet_api_id
    op.execute(
        "ALTER TABLE wallet DROP CONSTRAINT IF EXISTS wallet_api_id_fkey"
    )
    op.execute(
        "ALTER TABLE wallet DROP CONSTRAINT IF EXISTS fk_wallet_api_id"
    )

    # 2. Rename api to api_archived_20260221
    op.rename_table("api", "api_archived_20260221")

    # 3. Drop FK from wallet -> ref_wallet (so wallet can be renamed without dependency issues;
    #    ref_wallet still exists, we only rename wallet - no FK references wallet from other tables)
    #    We don't need to drop wallet_type_id for renaming wallet. Only drop FKs that *reference* the table we rename.
    #    So nothing references wallet. Just rename.
    op.rename_table("wallet", "wallet_archived_20240221")


def downgrade() -> None:
    op.rename_table("wallet_archived_20240221", "wallet")
    op.rename_table("api_archived_20260221", "api")

    # Re-create FK wallet.api_id -> api.id
    op.create_foreign_key(
        "wallet_api_id_fkey",
        "wallet",
        "api",
        ["api_id"],
        ["id"],
        ondelete="CASCADE",
    )

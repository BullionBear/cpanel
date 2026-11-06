"""account_uid_to_bigint

Alter account.uid and account.parent_uid from VARCHAR to BIGINT.

Revision ID: o4p5q6r7s8t9
Revises: n3o4p5q6r7s8
Create Date: 2026-02-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "o4p5q6r7s8t9"
down_revision: Union[str, Sequence[str], None] = "n3o4p5q6r7s8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL: alter column type; cast existing string UIDs to bigint
    op.execute(
        "ALTER TABLE account ALTER COLUMN uid TYPE BIGINT USING NULLIF(TRIM(uid), '')::BIGINT"
    )
    op.execute(
        "ALTER TABLE account ALTER COLUMN parent_uid TYPE BIGINT USING CASE WHEN parent_uid IS NULL OR TRIM(parent_uid) = '' THEN NULL ELSE parent_uid::BIGINT END"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE account ALTER COLUMN uid TYPE VARCHAR USING uid::TEXT")
    op.execute(
        "ALTER TABLE account ALTER COLUMN parent_uid TYPE VARCHAR USING CASE WHEN parent_uid IS NULL THEN NULL ELSE parent_uid::TEXT END"
    )

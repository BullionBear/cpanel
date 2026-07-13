"""archive_all_tables

The CPanel project is being rebuilt from scratch. Archive every live table by
renaming it to <name>_archived_20260713, following the convention used by
j0k1l2m3n4o5 (api_archived_20260221, wallet_archived_20240221).

All interrelated tables are renamed together, so foreign keys keep working
(PostgreSQL tracks them by OID). Sequences and indexes owned by the archived
tables are also renamed with the same suffix so the global namespace is clear
for the rebuilt schema (e.g. a future `users` table can recreate `users_id_seq`
and `ix_users_username` without conflicts).

Previously archived tables (api_archived_20260221, wallet_archived_20240221)
are left untouched.

Revision ID: a0b1c2d3e4f5
Revises: z6a7b8c9d0e1
Create Date: 2026-07-13

"""
from typing import Sequence, Union

from alembic import op


revision: str = "a0b1c2d3e4f5"
down_revision: Union[str, Sequence[str], None] = "z6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SUFFIX = "_archived_20260713"

TABLES = [
    "ref_symbol_filter",
    "ref_symbol_derivative",
    "trading_history_cursor",
    "trading_history",
    "balance_snapshot",
    "account",
    "api",
    "wallet",
    "access",
    "user_preference",
    "ref_symbol",
    "ref_universal_symbol",
    "ref_wallet",
    "ref_token",
    "ref_product",
    "ref_exchange",
    "users",
]


def upgrade() -> None:
    for table in TABLES:
        op.rename_table(table, f"{table}{SUFFIX}")

    # Rename sequences owned by the archived tables (sequence names are
    # schema-global, unlike constraints which are per-table).
    op.execute(
        f"""
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN
                SELECT s.relname AS seq
                FROM pg_class s
                JOIN pg_depend d ON d.objid = s.oid AND d.deptype = 'a'
                JOIN pg_class t ON t.oid = d.refobjid
                JOIN pg_namespace n ON n.oid = s.relnamespace
                WHERE s.relkind = 'S'
                  AND n.nspname = current_schema()
                  AND t.relname LIKE '%{SUFFIX}'
                  AND s.relname NOT LIKE '%{SUFFIX}'
            LOOP
                EXECUTE format('ALTER SEQUENCE %I RENAME TO %I', r.seq, r.seq || '{SUFFIX}');
            END LOOP;
        END $$;
        """
    )

    # Rename indexes (including PK/unique constraint indexes) on the archived
    # tables, since index names are also schema-global.
    op.execute(
        f"""
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN
                SELECT ic.relname AS idx
                FROM pg_index i
                JOIN pg_class ic ON ic.oid = i.indexrelid
                JOIN pg_class tc ON tc.oid = i.indrelid
                JOIN pg_namespace n ON n.oid = ic.relnamespace
                WHERE n.nspname = current_schema()
                  AND tc.relname LIKE '%{SUFFIX}'
                  AND ic.relname NOT LIKE '%{SUFFIX}'
            LOOP
                EXECUTE format('ALTER INDEX %I RENAME TO %I', r.idx, r.idx || '{SUFFIX}');
            END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    # Restore index names by stripping the suffix.
    op.execute(
        f"""
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN
                SELECT ic.relname AS idx
                FROM pg_index i
                JOIN pg_class ic ON ic.oid = i.indexrelid
                JOIN pg_class tc ON tc.oid = i.indrelid
                JOIN pg_namespace n ON n.oid = ic.relnamespace
                WHERE n.nspname = current_schema()
                  AND tc.relname LIKE '%{SUFFIX}'
                  AND ic.relname LIKE '%{SUFFIX}'
            LOOP
                EXECUTE format(
                    'ALTER INDEX %I RENAME TO %I',
                    r.idx,
                    left(r.idx, length(r.idx) - length('{SUFFIX}'))
                );
            END LOOP;
        END $$;
        """
    )

    # Restore sequence names.
    op.execute(
        f"""
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN
                SELECT s.relname AS seq
                FROM pg_class s
                JOIN pg_depend d ON d.objid = s.oid AND d.deptype = 'a'
                JOIN pg_class t ON t.oid = d.refobjid
                JOIN pg_namespace n ON n.oid = s.relnamespace
                WHERE s.relkind = 'S'
                  AND n.nspname = current_schema()
                  AND t.relname LIKE '%{SUFFIX}'
                  AND s.relname LIKE '%{SUFFIX}'
            LOOP
                EXECUTE format(
                    'ALTER SEQUENCE %I RENAME TO %I',
                    r.seq,
                    left(r.seq, length(r.seq) - length('{SUFFIX}'))
                );
            END LOOP;
        END $$;
        """
    )

    for table in reversed(TABLES):
        op.rename_table(f"{table}{SUFFIX}", table)

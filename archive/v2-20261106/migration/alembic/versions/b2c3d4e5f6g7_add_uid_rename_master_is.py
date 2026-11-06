"""add uid column and rename master_is to master_uid

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-01-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6g7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # PostgreSQL doesn't support adding columns at specific positions,
    # so we need to recreate the table with the correct column order.
    
    # 1. Drop existing foreign keys and indexes
    op.drop_constraint('api_master_is_fkey', 'api', type_='foreignkey')
    op.drop_index(op.f('ix_api_master_is'), table_name='api')
    
    # 2. Create new table with correct column order (uid after exchange, master_uid instead of master_is)
    op.create_table('api_new',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('exchange', sa.String(), nullable=False),
        sa.Column('uid', sa.String(), nullable=False),  # New column
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('api_type', sa.String(), nullable=False),
        sa.Column('api_key', sa.String(), nullable=False),
        sa.Column('api_secret', sa.String(), nullable=False),
        sa.Column('passphrase', sa.String(), nullable=True),
        sa.Column('master_uid', sa.String(), nullable=True),  # Renamed from master_is, now String without FK
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    # 3. Copy data from old table, converting master_is (int) to master_uid (string)
    # For existing records, uid will be empty (needs to be populated manually or on next update)
    op.execute("""
        INSERT INTO api_new (id, user_id, exchange, uid, name, api_type, api_key, api_secret, passphrase, master_uid, created_at, updated_at)
        SELECT id, user_id, exchange, '', name, api_type, api_key, api_secret, passphrase, 
               CASE WHEN master_is IS NOT NULL THEN master_is::text ELSE NULL END,
               created_at, updated_at
        FROM api
    """)
    
    # 4. Drop old table
    op.drop_table('api')
    
    # 5. Rename new table to original name
    op.rename_table('api_new', 'api')
    
    # 6. Re-create indexes (no foreign key for master_uid)
    op.create_index(op.f('ix_api_exchange'), 'api', ['exchange'], unique=False)
    op.create_index(op.f('ix_api_user_id'), 'api', ['user_id'], unique=False)
    op.create_index(op.f('ix_api_uid'), 'api', ['uid'], unique=False)
    
    # 7. Re-create user_id foreign key only
    op.create_foreign_key('api_user_id_fkey', 'api', 'users', ['user_id'], ['id'], ondelete='CASCADE')
    
    # 8. Reset the sequence for the id column
    op.execute("""
        SELECT setval(pg_get_serial_sequence('api', 'id'), COALESCE((SELECT MAX(id) FROM api), 0) + 1, false)
    """)


def downgrade() -> None:
    """Downgrade schema."""
    # 1. Create old table structure (master_is as Integer with FK)
    op.create_table('api_old',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('exchange', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('api_type', sa.String(), nullable=False),
        sa.Column('api_key', sa.String(), nullable=False),
        sa.Column('api_secret', sa.String(), nullable=False),
        sa.Column('passphrase', sa.String(), nullable=True),
        sa.Column('master_is', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    # 2. Copy data (excluding uid, converting master_uid back to master_is)
    op.execute("""
        INSERT INTO api_old (id, user_id, exchange, name, api_type, api_key, api_secret, passphrase, master_is, created_at, updated_at)
        SELECT id, user_id, exchange, name, api_type, api_key, api_secret, passphrase,
               CASE WHEN master_uid IS NOT NULL AND master_uid ~ '^[0-9]+$' THEN master_uid::integer ELSE NULL END,
               created_at, updated_at
        FROM api
    """)
    
    # 3. Drop current table
    op.drop_table('api')
    
    # 4. Rename to original
    op.rename_table('api_old', 'api')
    
    # 5. Re-create indexes
    op.create_index(op.f('ix_api_exchange'), 'api', ['exchange'], unique=False)
    op.create_index(op.f('ix_api_master_is'), 'api', ['master_is'], unique=False)
    op.create_index(op.f('ix_api_user_id'), 'api', ['user_id'], unique=False)
    
    # 6. Re-create foreign keys
    op.create_foreign_key('api_master_is_fkey', 'api', 'api', ['master_is'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('api_user_id_fkey', 'api', 'users', ['user_id'], ['id'], ondelete='CASCADE')
    
    # 7. Reset sequence
    op.execute("""
        SELECT setval(pg_get_serial_sequence('api', 'id'), COALESCE((SELECT MAX(id) FROM api), 0) + 1, false)
    """)

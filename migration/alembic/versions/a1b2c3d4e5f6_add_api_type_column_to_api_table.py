"""add api_type column to api table

Revision ID: a1b2c3d4e5f6
Revises: c667e477e9b2
Create Date: 2026-01-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'c667e477e9b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # PostgreSQL doesn't support adding columns at specific positions,
    # so we need to recreate the table with the correct column order.
    
    # 1. Create new table with correct column order (api_type before api_key)
    op.create_table('api_new',
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
    
    # 2. Copy data from old table, setting api_type='HMAC' for existing records
    op.execute("""
        INSERT INTO api_new (id, user_id, exchange, name, api_type, api_key, api_secret, passphrase, master_is, created_at, updated_at)
        SELECT id, user_id, exchange, name, 'HMAC', api_key, api_secret, passphrase, master_is, created_at, updated_at
        FROM api
    """)
    
    # 3. Drop old table (this will also drop its indexes and foreign keys)
    op.drop_table('api')
    
    # 4. Rename new table to original name
    op.rename_table('api_new', 'api')
    
    # 5. Re-create indexes
    op.create_index(op.f('ix_api_exchange'), 'api', ['exchange'], unique=False)
    op.create_index(op.f('ix_api_master_is'), 'api', ['master_is'], unique=False)
    op.create_index(op.f('ix_api_user_id'), 'api', ['user_id'], unique=False)
    
    # 6. Re-create foreign keys
    op.create_foreign_key('api_master_is_fkey', 'api', 'api', ['master_is'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('api_user_id_fkey', 'api', 'users', ['user_id'], ['id'], ondelete='CASCADE')
    
    # 7. Reset the sequence for the id column (find the actual sequence name)
    op.execute("""
        SELECT setval(pg_get_serial_sequence('api', 'id'), COALESCE((SELECT MAX(id) FROM api), 0) + 1, false)
    """)


def downgrade() -> None:
    """Downgrade schema."""
    # 1. Create old table structure without api_type
    op.create_table('api_old',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('exchange', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('api_key', sa.String(), nullable=False),
        sa.Column('api_secret', sa.String(), nullable=False),
        sa.Column('passphrase', sa.String(), nullable=True),
        sa.Column('master_is', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    # 2. Copy data (excluding api_type)
    op.execute("""
        INSERT INTO api_old (id, user_id, exchange, name, api_key, api_secret, passphrase, master_is, created_at, updated_at)
        SELECT id, user_id, exchange, name, api_key, api_secret, passphrase, master_is, created_at, updated_at
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

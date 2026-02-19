"""Add cache_entries table for persistent caching

Revision ID: 20260219_cache
Revises: 20260216_deleted_at
Create Date: 2026-02-19

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260219_cache'
down_revision = '20260216_deleted_at'
branch_labels = None
depends_on = None


def upgrade():
    """Add cache_entries table."""
    op.create_table(
        'cache_entries',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('cache_key', sa.String(255), unique=True, nullable=False),
        sa.Column('cache_type', sa.String(50), nullable=False),
        sa.Column('value', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_cache_entries_cache_key', 'cache_entries', ['cache_key'], unique=True)
    op.create_index('ix_cache_entries_cache_type', 'cache_entries', ['cache_type'])
    op.create_index('ix_cache_entries_expires_at', 'cache_entries', ['expires_at'])
    op.create_index('ix_cache_entries_type_expires', 'cache_entries', ['cache_type', 'expires_at'])


def downgrade():
    """Remove cache_entries table."""
    op.drop_index('ix_cache_entries_type_expires', table_name='cache_entries')
    op.drop_index('ix_cache_entries_expires_at', table_name='cache_entries')
    op.drop_index('ix_cache_entries_cache_type', table_name='cache_entries')
    op.drop_index('ix_cache_entries_cache_key', table_name='cache_entries')
    op.drop_table('cache_entries')

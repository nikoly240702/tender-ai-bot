"""Add deleted_at column to sniper_filters for soft-delete

Revision ID: 20260216_deleted_at
Revises: 20260215_notify
Create Date: 2026-02-16

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260216_deleted_at'
down_revision = '20260215_notify'
branch_labels = None
depends_on = None


def upgrade():
    """Add deleted_at column and index to sniper_filters."""
    op.add_column('sniper_filters',
        sa.Column('deleted_at', sa.DateTime(), nullable=True)
    )
    op.create_index(
        'ix_sniper_filters_user_deleted',
        'sniper_filters',
        ['user_id', 'deleted_at']
    )


def downgrade():
    """Remove deleted_at column and index."""
    op.drop_index('ix_sniper_filters_user_deleted', table_name='sniper_filters')
    op.drop_column('sniper_filters', 'deleted_at')

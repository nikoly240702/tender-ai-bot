"""Add notify_chat_ids to sniper_filters for per-filter notification routing

Revision ID: 20260215_notify
Revises: 20260215_groups
Create Date: 2026-02-15

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260215_notify'
down_revision = '20260215_groups'
branch_labels = None
depends_on = None


def upgrade():
    """Add notify_chat_ids column to sniper_filters."""
    op.add_column('sniper_filters',
        sa.Column('notify_chat_ids', sa.JSON(), nullable=True)
    )


def downgrade():
    """Remove notify_chat_ids column."""
    op.drop_column('sniper_filters', 'notify_chat_ids')

"""Add reactivation_events table for tracking

Revision ID: 20260219_react
Revises: 20260219_cache
Create Date: 2026-02-19

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260219_react'
down_revision = '20260219_cache'
branch_labels = None
depends_on = None


def upgrade():
    """Add reactivation_events table."""
    op.create_table(
        'reactivation_events',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('event_type', sa.String(50), nullable=False),
        sa.Column('message_variant', sa.String(50), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_reactivation_events_user_type', 'reactivation_events', ['user_id', 'event_type'])
    op.create_index('ix_reactivation_events_date', 'reactivation_events', ['created_at'])


def downgrade():
    """Remove reactivation_events table."""
    op.drop_index('ix_reactivation_events_date', table_name='reactivation_events')
    op.drop_index('ix_reactivation_events_user_type', table_name='reactivation_events')
    op.drop_table('reactivation_events')

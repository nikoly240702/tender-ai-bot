"""add broadcast_recipients table

Revision ID: 20260407_bcast
Revises: 20260407_email
"""
from alembic import op
import sqlalchemy as sa


revision = '20260407_bcast'
down_revision = '20260407_email'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'broadcast_recipients',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('broadcast_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('delivered_at', sa.DateTime(), nullable=True),
        sa.Column('clicked_at', sa.DateTime(), nullable=True),
        sa.Column('clicked_button', sa.String(length=50), nullable=True),
        sa.Column('converted_at', sa.DateTime(), nullable=True),
        sa.Column('converted_payment_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['broadcast_id'], ['broadcast_messages.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['sniper_users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['converted_payment_id'], ['payments.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('broadcast_id', 'user_id', name='uq_broadcast_recipient'),
    )
    op.create_index('ix_broadcast_recipients_user_id', 'broadcast_recipients', ['user_id'])
    op.create_index('ix_broadcast_recipients_broadcast_id', 'broadcast_recipients', ['broadcast_id'])
    op.create_index('ix_broadcast_recipients_status', 'broadcast_recipients', ['status'])


def downgrade() -> None:
    op.drop_index('ix_broadcast_recipients_status', 'broadcast_recipients')
    op.drop_index('ix_broadcast_recipients_broadcast_id', 'broadcast_recipients')
    op.drop_index('ix_broadcast_recipients_user_id', 'broadcast_recipients')
    op.drop_table('broadcast_recipients')

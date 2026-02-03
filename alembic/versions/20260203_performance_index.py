"""Add composite index for is_tender_notified optimization

Revision ID: 20260203_perf
Revises: 20260203_ai_intent
Create Date: 2026-02-03

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260203_perf'
down_revision = '20260203_ai_intent'
branch_labels = None
depends_on = None


def upgrade():
    """Add composite index for better query performance."""
    # Составной индекс для is_tender_notified(tender_number, user_id)
    # Ускоряет проверку: был ли уже отправлен тендер пользователю
    op.create_index(
        'ix_sniper_notifications_user_tender',
        'sniper_notifications',
        ['user_id', 'tender_number']
    )


def downgrade():
    """Remove composite index."""
    op.drop_index('ix_sniper_notifications_user_tender', 'sniper_notifications')

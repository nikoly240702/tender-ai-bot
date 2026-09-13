"""Add notified_chat_id to sniper_notifications — fixes a group-notification
dedup bug found while testing the Moscow Portal integration (13.09.2026).

is_tender_sent_to_chat() used to dedup group notifications by checking
whether ANY user routing to that chat had a sniper_notifications row for
the same tender_number within 24h — but the table never recorded WHICH
chat a notification was actually delivered to, only user_id. When a
filter's notify_chat_ids includes both the owner's personal chat AND a
group (a normal, common setup), the personal send (always processed
first) writes a row for that user_id, and the very next iteration's
group-dedup check then falsely matches that same row — the group send
gets silently skipped as "already sent", even though it never was.

This affects every company on the platform whose filters route to both
a personal chat and a group, not just the Moscow Portal source.

Revision ID: 20260913_notified_chat
Revises: 20260913_filters_nacrejim
Create Date: 2026-09-13

"""
from alembic import op
import sqlalchemy as sa


revision = '20260913_notified_chat'
down_revision = '20260913_filters_nacrejim'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('sniper_notifications', sa.Column('notified_chat_id', sa.BigInteger(), nullable=True))
    op.create_index('ix_sniper_notifications_notified_chat_id', 'sniper_notifications', ['notified_chat_id'])


def downgrade() -> None:
    op.drop_index('ix_sniper_notifications_notified_chat_id', table_name='sniper_notifications')
    op.drop_column('sniper_notifications', 'notified_chat_id')

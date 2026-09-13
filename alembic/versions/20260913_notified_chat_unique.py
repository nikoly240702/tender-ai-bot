"""Widen uq_notification_user_filter_tender to include notified_chat_id.

Follow-up to 20260913_notified_chat_id: that migration added the column
and fixed is_tender_sent_to_chat's dedup query, but the OLD unique
constraint (user_id, filter_id, tender_number) — added long before
per-chat tracking existed — still allows only ONE row per that triple,
regardless of notified_chat_id. A filter routing to both a personal
chat and a group needs TWO rows for the same (user, filter, tender):
one per actual delivery target. Without this, the group's
save_notification() call hits a real IntegrityError on every send
(confirmed live 13.09.2026 running the group catch-up script — all 20
Telegram sends succeeded, but 20/20 DB inserts were rejected as
"duplicate"), so notified_chat_id for the group never actually
persists and is_tender_sent_to_chat can never dedup it going forward —
every future cycle would re-attempt (and re-send) the same group
notification.

Revision ID: 20260913_notified_chat_unique
Revises: 20260913_notified_chat
Create Date: 2026-09-13

"""
from alembic import op


revision = '20260913_notified_chat_unique'
down_revision = '20260913_notified_chat'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint('uq_notification_user_filter_tender', 'sniper_notifications')
    op.create_unique_constraint(
        'uq_notification_user_filter_tender_chat',
        'sniper_notifications',
        ['user_id', 'filter_id', 'tender_number', 'notified_chat_id'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_notification_user_filter_tender_chat', 'sniper_notifications')
    op.create_unique_constraint(
        'uq_notification_user_filter_tender',
        'sniper_notifications',
        ['user_id', 'filter_id', 'tender_number'],
    )

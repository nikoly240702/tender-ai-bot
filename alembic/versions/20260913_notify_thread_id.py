"""Add notify_thread_id to sniper_filters — route notifications into a
specific Telegram forum topic (subgroup) instead of the group's General.

Kept separate from notify_chat_ids on purpose: the cabinet's existing
notify-targets endpoint (update_filter_notify_targets) unconditionally
coerces notify_chat_ids to a flat list of plain ints on every save —
embedding a thread id inside that array would get silently wiped the
next time anyone touches that settings UI.

Revision ID: 20260913_notify_thread
Revises: 20260913_supplier_region
Create Date: 2026-09-13

"""
from alembic import op
import sqlalchemy as sa


revision = '20260913_notify_thread'
down_revision = '20260913_supplier_region'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('sniper_filters', sa.Column('notify_thread_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('sniper_filters', 'notify_thread_id')

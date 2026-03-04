"""Add match_info JSON to sniper_notifications for AI Sheets export

Stores the match_info dict (including AI fields) at notification time,
so Google Sheets export can reuse already-computed AI data.

Revision ID: 20260303_match_info
Revises: 20260301_notif_dedup
Create Date: 2026-03-03

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '20260303_match_info'
down_revision = '20260301_notif_dedup'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'sniper_notifications',
        sa.Column('match_info', postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('sniper_notifications', 'match_info')

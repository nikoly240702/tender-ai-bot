"""Add Bitrix24 fields to sniper_notifications

Adds bitrix24_exported, bitrix24_exported_at, bitrix24_deal_id columns
to track which tenders have been exported to Bitrix24 and their deal IDs.

Revision ID: 20260304_bitrix24
Revises: 20260303_match_info
Create Date: 2026-03-04

"""
from alembic import op
import sqlalchemy as sa


revision = '20260304_bitrix24'
down_revision = '20260303_match_info'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'sniper_notifications',
        sa.Column('bitrix24_exported', sa.Boolean(), nullable=False, server_default='false')
    )
    op.add_column(
        'sniper_notifications',
        sa.Column('bitrix24_exported_at', sa.DateTime(), nullable=True)
    )
    op.add_column(
        'sniper_notifications',
        sa.Column('bitrix24_deal_id', sa.String(100), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('sniper_notifications', 'bitrix24_deal_id')
    op.drop_column('sniper_notifications', 'bitrix24_exported_at')
    op.drop_column('sniper_notifications', 'bitrix24_exported')

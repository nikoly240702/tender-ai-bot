"""add email and email_notifications_enabled to sniper_users

Revision ID: 20260407_email
Revises: 20260304_bitrix24
Create Date: 2026-04-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20260407_email'
down_revision: Union[str, None] = '20260304_bitrix24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'sniper_users',
        sa.Column('email', sa.String(length=255), nullable=True)
    )
    op.add_column(
        'sniper_users',
        sa.Column(
            'email_notifications_enabled',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false')
        )
    )


def downgrade() -> None:
    op.drop_column('sniper_users', 'email_notifications_enabled')
    op.drop_column('sniper_users', 'email')

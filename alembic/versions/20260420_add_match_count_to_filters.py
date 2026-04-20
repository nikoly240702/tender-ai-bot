"""add match_count and last_match_at to sniper_filters

Revision ID: 20260420_match_count
Revises: 20260407_bcast
Create Date: 2026-04-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20260420_match_count'
down_revision: Union[str, None] = '20260407_bcast'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'sniper_filters',
        sa.Column('match_count', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'sniper_filters',
        sa.Column('last_match_at', sa.DateTime(), nullable=True),
    )

    # Бэкфилл существующих счётчиков из уже сохранённых уведомлений.
    op.execute(
        """
        UPDATE sniper_filters f
        SET match_count = sub.cnt,
            last_match_at = sub.last_sent
        FROM (
            SELECT filter_id, COUNT(*) AS cnt, MAX(sent_at) AS last_sent
            FROM sniper_notifications
            WHERE filter_id IS NOT NULL
            GROUP BY filter_id
        ) sub
        WHERE f.id = sub.filter_id
        """
    )


def downgrade() -> None:
    op.drop_column('sniper_filters', 'last_match_at')
    op.drop_column('sniper_filters', 'match_count')

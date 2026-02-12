"""dedup notifications and add unique constraint

Revision ID: e5f6g7h8i9j0
Revises: d4e5f6g7h8i9
Create Date: 2026-02-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f6g7h8i9j0'
down_revision: Union[str, None] = 'd4e5f6g7h8i9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Удаляем дубликаты, оставляя самую свежую запись (MAX id)
    op.execute("""
        DELETE FROM sniper_notifications
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM sniper_notifications
            GROUP BY user_id, filter_id, tender_number
        )
    """)

    # Добавляем unique constraint для предотвращения будущих дубликатов
    op.create_unique_constraint(
        'uq_notification_user_filter_tender',
        'sniper_notifications',
        ['user_id', 'filter_id', 'tender_number']
    )


def downgrade() -> None:
    op.drop_constraint('uq_notification_user_filter_tender', 'sniper_notifications')

"""Fix notification unique constraint: (user_id, filter_id, tender_number) → (user_id, tender_number)

Причина: старый constraint включал filter_id, что позволяло:
1. Дубликаты когда filter_id=NULL (NULL != NULL в PostgreSQL)
2. Дубликаты одного тендера от разных фильтров одного пользователя

Revision ID: 20260301_notif_dedup
Revises: 20260225_docs
Create Date: 2026-03-01

"""
from alembic import op
import sqlalchemy as sa


revision = '20260301_notif_dedup'
down_revision = '20260225_docs'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Дедупликация: оставляем только одну запись на (user_id, tender_number)
    #    Сохраняем ту, что с наибольшим id (самую свежую)
    op.execute("""
        DELETE FROM sniper_notifications
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM sniper_notifications
            GROUP BY user_id, tender_number
        )
    """)

    # 2. Удаляем старый constraint (по трём полям включая filter_id)
    op.drop_constraint(
        'uq_notification_user_filter_tender',
        'sniper_notifications',
        type_='unique'
    )

    # 3. Добавляем новый constraint только по (user_id, tender_number)
    op.create_unique_constraint(
        'uq_notification_user_tender',
        'sniper_notifications',
        ['user_id', 'tender_number']
    )


def downgrade() -> None:
    op.drop_constraint('uq_notification_user_tender', 'sniper_notifications', type_='unique')
    op.create_unique_constraint(
        'uq_notification_user_filter_tender',
        'sniper_notifications',
        ['user_id', 'filter_id', 'tender_number']
    )

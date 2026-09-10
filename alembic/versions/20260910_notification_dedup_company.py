"""Widen notification dedup: (user_id, tender_number) → (user_id, company_id, tender_number)

Причина: при multi-workspace один и тот же юзер администрирует несколько
изолированных компаний с копиями одних и тех же фильтров. Старый constraint
по (user_id, tender_number) означал, что тендер, совпавший с фильтрами обеих
компаний, порождал ровно ОДНО уведомление — второй workspace не видел ничего.

Бэкфилл/дедуп не нужен: миграция 20260910_multi_workspace уже проставила
company_id (или оставила NULL — а NULL в PostgreSQL не конфликтует с NULL
в unique-constraint). Это чистая смена формы ключа.

Revision ID: 20260910_notif_company
Revises: 20260910_multi_workspace
Create Date: 2026-09-10

"""
from alembic import op
import sqlalchemy as sa


revision = '20260910_notif_company'
down_revision = '20260910_multi_workspace'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        'uq_notification_user_tender',
        'sniper_notifications',
        type_='unique'
    )
    op.create_unique_constraint(
        'uq_notification_user_company_tender',
        'sniper_notifications',
        ['user_id', 'company_id', 'tender_number']
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_notification_user_company_tender',
        'sniper_notifications',
        type_='unique'
    )
    # Возврат к узкому ключу: сначала снимаем дубли, которые он запрещает
    # (оставляем самую свежую запись на (user_id, tender_number)).
    op.execute("""
        DELETE FROM sniper_notifications
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM sniper_notifications
            GROUP BY user_id, tender_number
        )
    """)
    op.create_unique_constraint(
        'uq_notification_user_tender',
        'sniper_notifications',
        ['user_id', 'tender_number']
    )

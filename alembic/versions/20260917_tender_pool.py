"""Общий пул тендеров: скачиваем один раз, матчим локально всеми фильтрами.

Сейчас запрос к площадке привязан к паре (фильтр × ключевое слово): 39
фильтров по ~25 ключевиков дают около 2000 запросов за цикл, причём
фильтры с похожими словами качают одно и то же по многу раз. Замер
17.09.2026: цикл длится 45 минут, ~18 000 запросов в сутки, и площадка
начинает резать доступ.

Пул разрывает эту связь: список новых публикаций забирается постранично
(до 200 карточек за запрос) независимо от числа фильтров и пользователей,
а матчинг идёт локально по сохранённым строкам. Объём обращений перестаёт
зависеть от числа клиентов.

Таблица намеренно хранит ровно то, что отдаёт страница выдачи (проверено
на живой выдаче 17.09.2026): объект закупки, номер, заказчик, начальная
цена, даты размещения и окончания подачи. Региона в выдаче нет — его
добираем позже и только для совпавших тендеров, которых единицы.

Revision ID: 20260917_tender_pool
Revises: 20260914_margin_costs
Create Date: 2026-09-17

"""
from alembic import op
import sqlalchemy as sa


revision = '20260917_tender_pool'
down_revision = '20260914_margin_costs'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'tender_pool',
        sa.Column('tender_number', sa.String(40), primary_key=True),
        sa.Column('name', sa.Text(), nullable=True),
        sa.Column('customer', sa.Text(), nullable=True),
        sa.Column('price', sa.Float(), nullable=True),
        sa.Column('region', sa.String(255), nullable=True),
        sa.Column('law', sa.String(16), nullable=True),
        sa.Column('procedure_type', sa.String(255), nullable=True),
        sa.Column('status', sa.String(255), nullable=True),
        sa.Column('url', sa.String(500), nullable=True),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('submission_deadline', sa.DateTime(), nullable=True),
        sa.Column('source', sa.String(32), nullable=False, server_default='eis'),
        sa.Column('fetched_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        # NULL = ещё не прогонялся через фильтры. Матчер берёт такие строки,
        # поэтому это же поле служит очередью и переживает рестарт воркера.
        sa.Column('matched_at', sa.DateTime(), nullable=True),
        # Детали (регион, дедлайн с карточки) догружаются отдельно и только
        # для совпавших — по этому флагу видно, что уже обогащено.
        sa.Column('enriched_at', sa.DateTime(), nullable=True),
    )
    # Очередь матчинга: выбираем необработанные, свежие сверху.
    op.create_index('ix_tender_pool_matching', 'tender_pool',
                    ['matched_at', 'fetched_at'])
    # Чистка старых записей и отчёты по периоду.
    op.create_index('ix_tender_pool_published', 'tender_pool', ['published_at'])


def downgrade() -> None:
    op.drop_index('ix_tender_pool_published', table_name='tender_pool')
    op.drop_index('ix_tender_pool_matching', table_name='tender_pool')
    op.drop_table('tender_pool')

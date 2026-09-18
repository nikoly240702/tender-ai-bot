"""Кэш поиска товаров: система не ищет одно и то же дважды.

Позиции в тендерах повторяются постоянно — «бумага А4 80 г/м2»
встречается в сотнях закупок. Без кэша каждый прогон закупщика заново
тратил бы поисковые запросы и время на то, что уже искали.

Кэш общий, не по компаниям: в нём лежат результаты веб-поиска, то есть
публичные данные, а ключ — нормализованный текст позиции из тендерной
документации, которая тоже публична. Общий кэш означает, что чем больше
система работает, тем дешевле и быстрее каждый следующий подбор.

Revision ID: 20260918_prod_search
Revises: 20260917_tender_pool
Create Date: 2026-09-18

"""
from alembic import op
import sqlalchemy as sa


revision = '20260918_prod_search'
down_revision = '20260917_tender_pool'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'product_search_cache',
        # sha256 нормализованного текста: сам текст бывает длинным, а по
        # нему нужен быстрый точный поиск.
        sa.Column('query_key', sa.String(64), primary_key=True),
        sa.Column('query_text', sa.Text(), nullable=False),
        sa.Column('results', sa.JSON(), nullable=False),
        sa.Column('hits', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    # Для отчёта «что чаще всего ищем» и для чистки самых старых записей.
    op.create_index('ix_product_search_cache_hits', 'product_search_cache', ['hits'])
    op.create_index('ix_product_search_cache_updated', 'product_search_cache', ['updated_at'])


def downgrade() -> None:
    op.drop_index('ix_product_search_cache_updated', table_name='product_search_cache')
    op.drop_index('ix_product_search_cache_hits', table_name='product_search_cache')
    op.drop_table('product_search_cache')

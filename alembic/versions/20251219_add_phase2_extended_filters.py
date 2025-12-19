"""add phase 2 extended filter fields

Revision ID: add_phase2_filters
Revises: add_exact_match
Create Date: 2024-12-19

Phase 2 BETA features:
- purchase_number: search by tender number
- customer_inn: filter by customer INN
- excluded_customer_inns: blacklist by INN
- excluded_customer_keywords: blacklist by keywords
- execution_regions: execution region filter
- publication_days: days since publication
- primary_keywords: main keywords (2x weight)
- secondary_keywords: additional keywords (1x weight)
- search_in: where to search (title, description, etc.)
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_phase2_filters'
down_revision = 'add_exact_match'
branch_labels = None
depends_on = None


def upgrade():
    # Поиск по номеру закупки
    op.add_column('sniper_filters', sa.Column('purchase_number', sa.String(100), nullable=True))

    # ИНН заказчика (для фильтрации)
    op.add_column('sniper_filters', sa.Column('customer_inn', sa.JSON(), nullable=True))

    # Черный список заказчиков по ИНН
    op.add_column('sniper_filters', sa.Column('excluded_customer_inns', sa.JSON(), nullable=True))

    # Черный список заказчиков по ключевым словам
    op.add_column('sniper_filters', sa.Column('excluded_customer_keywords', sa.JSON(), nullable=True))

    # Регион исполнения (отличается от региона заказчика)
    op.add_column('sniper_filters', sa.Column('execution_regions', sa.JSON(), nullable=True))

    # Фильтр по дате публикации (дней назад)
    op.add_column('sniper_filters', sa.Column('publication_days', sa.Integer(), nullable=True))

    # Главные ключевые слова (вес 2x)
    op.add_column('sniper_filters', sa.Column('primary_keywords', sa.JSON(), nullable=True))

    # Дополнительные ключевые слова (вес 1x)
    op.add_column('sniper_filters', sa.Column('secondary_keywords', sa.JSON(), nullable=True))

    # Где искать: ['title', 'description', 'documents', 'customer_name']
    op.add_column('sniper_filters', sa.Column('search_in', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('sniper_filters', 'search_in')
    op.drop_column('sniper_filters', 'secondary_keywords')
    op.drop_column('sniper_filters', 'primary_keywords')
    op.drop_column('sniper_filters', 'publication_days')
    op.drop_column('sniper_filters', 'execution_regions')
    op.drop_column('sniper_filters', 'excluded_customer_keywords')
    op.drop_column('sniper_filters', 'excluded_customer_inns')
    op.drop_column('sniper_filters', 'customer_inn')
    op.drop_column('sniper_filters', 'purchase_number')

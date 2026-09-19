"""Артикул и поставщик в каталоге товаров.

Каталог задуман как источник «готовых позиций под тендер»: система
находит в номенклатуре компании подходящий товар и отдаёт его для КП.
Без артикула это невозможно — в предложение заказчику идёт именно он, а
не текстовое описание.

Поставщик нужен, когда в каталоге лежат прайсы нескольких производителей:
без него непонятно, у кого заказывать найденную позицию. Существующее
поле source для этого не годится — там пометка вида «ИМПОРТ»/«РФ».

Revision ID: 20260919_sku
Revises: 20260918_prod_search
Create Date: 2026-09-19

"""
from alembic import op
import sqlalchemy as sa


revision = '20260919_sku'
down_revision = '20260918_prod_search'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('own_products', sa.Column('sku', sa.String(80), nullable=True))
    op.add_column('own_products', sa.Column('supplier', sa.String(120), nullable=True))
    # Артикул уникален в пределах компании и поставщика — по нему идёт
    # повторный импорт прайса (обновление цен вместо дублей).
    op.create_index('ix_own_products_company_sku', 'own_products',
                    ['company_id', 'supplier', 'sku'])


def downgrade() -> None:
    op.drop_index('ix_own_products_company_sku', table_name='own_products')
    op.drop_column('own_products', 'supplier')
    op.drop_column('own_products', 'sku')

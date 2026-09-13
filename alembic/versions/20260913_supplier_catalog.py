"""Supplier catalog: website/contact_person, per-supplier product catalog,
and per-tender line items (multiple positions, possibly different suppliers)

Расширение поставщиков по запросу: раздельные сайт/контакт/имя контакта,
каталог позиций поставщика (наименование + цена за единицу) как база
знаний для переиспользования в новых тендерах, и превращение одного
"предложения" на карточке в позицию (наименование + цена за единицу +
количество), т.к. в одном тендере обычно несколько позиций, зачастую от
разных поставщиков.

Revision ID: 20260913_supplier_catalog
Revises: 20260913_suppliers_names
Create Date: 2026-09-13

"""
from alembic import op
import sqlalchemy as sa


revision = '20260913_supplier_catalog'
down_revision = '20260913_suppliers_names'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('suppliers', sa.Column('website', sa.String(length=255), nullable=True))
    op.add_column('suppliers', sa.Column('contact_person', sa.String(length=255), nullable=True))

    op.create_table(
        'supplier_products',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('supplier_id', sa.Integer(), sa.ForeignKey('suppliers.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(length=300), nullable=False),
        sa.Column('unit_price', sa.Numeric(14, 2), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('sniper_users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_supplier_products_supplier_id', 'supplier_products', ['supplier_id'])

    op.alter_column('pipeline_card_quotes', 'price', new_column_name='unit_price')
    op.add_column('pipeline_card_quotes',
                   sa.Column('quantity', sa.Numeric(14, 3), nullable=False, server_default='1'))
    op.add_column('pipeline_card_quotes', sa.Column('product_name', sa.String(length=300), nullable=True))
    op.add_column('pipeline_card_quotes',
                   sa.Column('supplier_product_id', sa.Integer(),
                             sa.ForeignKey('supplier_products.id', ondelete='SET NULL'), nullable=True))


def downgrade() -> None:
    op.drop_column('pipeline_card_quotes', 'supplier_product_id')
    op.drop_column('pipeline_card_quotes', 'product_name')
    op.drop_column('pipeline_card_quotes', 'quantity')
    op.alter_column('pipeline_card_quotes', 'unit_price', new_column_name='price')

    op.drop_index('ix_supplier_products_supplier_id', table_name='supplier_products')
    op.drop_table('supplier_products')

    op.drop_column('suppliers', 'contact_person')
    op.drop_column('suppliers', 'website')

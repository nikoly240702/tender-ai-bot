"""add own_products table — каталог товаров команды (для оценки тендера)

Revision ID: 20260504_own_products
Revises: 20260429_pipeline
Create Date: 2026-05-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20260504_own_products'
down_revision: Union[str, None] = '20260429_pipeline'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'own_products',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('company_id', sa.Integer(),
                  sa.ForeignKey('companies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('category', sa.String(40), nullable=False, server_default='siz'),
        sa.Column('name', sa.String(300), nullable=False),
        sa.Column('sizes', sa.String(200), nullable=True),
        sa.Column('params', sa.Text(), nullable=True),
        sa.Column('pack', sa.String(120), nullable=True),
        sa.Column('price', sa.Numeric(12, 2), nullable=True),
        sa.Column('price_unit', sa.String(40), nullable=True),
        sa.Column('price_text', sa.String(120), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('source', sa.String(40), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_own_products_company_id', 'own_products', ['company_id'])
    op.create_index('ix_own_products_company_category', 'own_products', ['company_id', 'category'])


def downgrade() -> None:
    op.drop_table('own_products')

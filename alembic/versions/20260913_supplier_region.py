"""Add region to suppliers (where the supply actually comes from)

Revision ID: 20260913_supplier_region
Revises: 20260913_supplier_catalog
Create Date: 2026-09-13

"""
from alembic import op
import sqlalchemy as sa


revision = '20260913_supplier_region'
down_revision = '20260913_supplier_catalog'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('suppliers', sa.Column('region', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('suppliers', 'region')

"""add error_count to sniper_filters

Revision ID: add_error_count
Revises: d4e5f6g7h8i9
Create Date: 2024-12-10

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_error_count'
down_revision = 'd4e5f6g7h8i9'
branch_labels = None
depends_on = None


def upgrade():
    # Добавляем поле error_count с значением по умолчанию 0
    op.add_column('sniper_filters', sa.Column('error_count', sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    # Удаляем поле error_count
    op.drop_column('sniper_filters', 'error_count')

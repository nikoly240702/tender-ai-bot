"""add exact_match to sniper_filters

Revision ID: add_exact_match
Revises: add_user_status_blocking
Create Date: 2024-12-19

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_exact_match'
down_revision = 'add_user_status_blocking'
branch_labels = None
depends_on = None


def upgrade():
    # Добавляем поле exact_match (режим поиска: False=расширенный, True=точный)
    op.add_column('sniper_filters', sa.Column('exact_match', sa.Boolean(), nullable=False, server_default='false'))


def downgrade():
    # Удаляем поле exact_match
    op.drop_column('sniper_filters', 'exact_match')

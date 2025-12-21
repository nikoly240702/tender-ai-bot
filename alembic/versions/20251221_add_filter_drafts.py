"""add filter drafts table

Revision ID: add_filter_drafts
Revises: add_phase2_filters
Create Date: 2024-12-21

BETA feature: Save filter creation progress to allow resuming after errors.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_filter_drafts'
down_revision = 'add_phase2_filters'
branch_labels = None
depends_on = None


def upgrade():
    # Таблица для хранения черновиков фильтров
    op.create_table(
        'filter_drafts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False),  # Для быстрого поиска
        sa.Column('draft_data', sa.JSON(), nullable=False),  # FSM state data
        sa.Column('current_step', sa.String(100), nullable=True),  # Текущий шаг wizard
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )

    # Индекс для быстрого поиска по telegram_id
    op.create_index('ix_filter_drafts_telegram_id', 'filter_drafts', ['telegram_id'])

    # Уникальный индекс - один черновик на пользователя
    op.create_index('ix_filter_drafts_user_unique', 'filter_drafts', ['user_id'], unique=True)


def downgrade():
    op.drop_index('ix_filter_drafts_user_unique', table_name='filter_drafts')
    op.drop_index('ix_filter_drafts_telegram_id', table_name='filter_drafts')
    op.drop_table('filter_drafts')

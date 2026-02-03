"""Add ai_intent field to sniper_filters and create ai_feedback table

Revision ID: 20260203_ai_intent
Revises: 347d2ff67401
Create Date: 2026-02-03

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260203_ai_intent'
down_revision = '347d2ff67401'
branch_labels = None
depends_on = None


def upgrade():
    """Add ai_intent column and create ai_feedback table."""

    # 1. Добавляем поле ai_intent в sniper_filters
    op.add_column(
        'sniper_filters',
        sa.Column('ai_intent', sa.Text(), nullable=True)
    )

    # 2. Создаём таблицу ai_feedback для обучения
    op.create_table(
        'ai_feedback',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('filter_id', sa.Integer(), sa.ForeignKey('sniper_filters.id', ondelete='SET NULL'), nullable=True),

        # Данные тендера
        sa.Column('tender_number', sa.String(100), nullable=False),
        sa.Column('tender_name', sa.Text(), nullable=False),

        # Контекст фильтра
        sa.Column('filter_keywords', sa.JSON(), nullable=True),
        sa.Column('filter_intent', sa.Text(), nullable=True),

        # AI решение
        sa.Column('ai_decision', sa.Boolean(), nullable=True),
        sa.Column('ai_confidence', sa.Integer(), nullable=True),
        sa.Column('ai_reason', sa.Text(), nullable=True),

        # Feedback
        sa.Column('feedback_type', sa.String(50), nullable=False),
        sa.Column('feedback_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),

        # Дополнительно
        sa.Column('subscription_tier', sa.String(50), nullable=True),
    )

    # Индексы для ai_feedback
    op.create_index('ix_ai_feedback_user_id', 'ai_feedback', ['user_id'])
    op.create_index('ix_ai_feedback_filter', 'ai_feedback', ['filter_id'])
    op.create_index('ix_ai_feedback_tender', 'ai_feedback', ['tender_number'])
    op.create_index('ix_ai_feedback_type', 'ai_feedback', ['feedback_type'])
    op.create_index('ix_ai_feedback_date', 'ai_feedback', ['feedback_at'])


def downgrade():
    """Remove ai_intent column and drop ai_feedback table."""

    # Удаляем индексы
    op.drop_index('ix_ai_feedback_date', 'ai_feedback')
    op.drop_index('ix_ai_feedback_type', 'ai_feedback')
    op.drop_index('ix_ai_feedback_tender', 'ai_feedback')
    op.drop_index('ix_ai_feedback_filter', 'ai_feedback')
    op.drop_index('ix_ai_feedback_user_id', 'ai_feedback')

    # Удаляем таблицу
    op.drop_table('ai_feedback')

    # Удаляем колонку
    op.drop_column('sniper_filters', 'ai_intent')

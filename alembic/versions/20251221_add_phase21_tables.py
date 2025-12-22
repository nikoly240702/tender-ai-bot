"""add Phase 2.1 tables for enhanced features

Revision ID: add_phase21_tables
Revises: add_filter_drafts
Create Date: 2024-12-21

Phase 2.1 BETA features:
- Search History tracking
- User Feedback (interesting/hidden/irrelevant)
- Subscription management
- Satisfaction Surveys
- Viewed Tenders tracking
- Quick Filter Templates (custom user templates)
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_phase21_tables'
down_revision = 'add_filter_drafts'
branch_labels = None
depends_on = None


def upgrade():
    # ============================================
    # Search History
    # ============================================
    op.create_table(
        'search_history',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('filter_id', sa.Integer(), sa.ForeignKey('sniper_filters.id', ondelete='SET NULL'), nullable=True),
        sa.Column('search_type', sa.String(50), nullable=False),  # instant_search, archive_search
        sa.Column('keywords', sa.JSON(), nullable=False),
        sa.Column('results_count', sa.Integer(), default=0),
        sa.Column('executed_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
    )
    op.create_index('ix_search_history_user_id', 'search_history', ['user_id'])
    op.create_index('ix_search_history_user_time', 'search_history', ['user_id', 'executed_at'])

    # ============================================
    # User Feedback
    # ============================================
    op.create_table(
        'user_feedback',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('filter_id', sa.Integer(), sa.ForeignKey('sniper_filters.id', ondelete='SET NULL'), nullable=True),
        sa.Column('tender_number', sa.String(100), nullable=False),
        sa.Column('feedback_type', sa.String(50), nullable=False),  # interesting, hidden, irrelevant
        sa.Column('tender_name', sa.Text(), nullable=True),
        sa.Column('matched_keywords', sa.JSON(), default=[]),
        sa.Column('original_score', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_user_feedback_user_id', 'user_feedback', ['user_id'])
    op.create_index('ix_user_feedback_filter_id', 'user_feedback', ['filter_id'])
    op.create_index('ix_user_feedback_tender_number', 'user_feedback', ['tender_number'])
    op.create_index('ix_user_feedback_user_type', 'user_feedback', ['user_id', 'feedback_type'])

    # ============================================
    # Subscriptions
    # ============================================
    op.create_table(
        'subscriptions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tier', sa.String(50), nullable=False, default='trial'),  # trial, basic, premium
        sa.Column('status', sa.String(50), nullable=False, default='active'),  # active, expired, cancelled
        sa.Column('started_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('cancelled_at', sa.DateTime(), nullable=True),
        sa.Column('max_filters', sa.Integer(), default=3),
        sa.Column('max_notifications_per_day', sa.Integer(), default=50),
        sa.Column('last_payment_id', sa.String(255), nullable=True),
        sa.Column('last_payment_at', sa.DateTime(), nullable=True),
        sa.Column('next_billing_date', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_subscriptions_user_id', 'subscriptions', ['user_id'], unique=True)

    # ============================================
    # Satisfaction Surveys
    # ============================================
    op.create_table(
        'satisfaction_surveys',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('rating', sa.Integer(), nullable=True),  # 1-5 stars
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('trigger', sa.String(100), nullable=True),  # after_10_notifications, weekly, manual
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_satisfaction_surveys_user_id', 'satisfaction_surveys', ['user_id'])

    # ============================================
    # Viewed Tenders
    # ============================================
    op.create_table(
        'viewed_tenders',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tender_number', sa.String(100), nullable=False),
        sa.Column('viewed_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_viewed_tenders_user_id', 'viewed_tenders', ['user_id'])
    op.create_index('ix_viewed_tenders_tender_number', 'viewed_tenders', ['tender_number'])
    op.create_index('ix_viewed_tenders_user_tender', 'viewed_tenders', ['user_id', 'tender_number'], unique=True)

    # ============================================
    # Quick Filter Templates (Custom User Templates)
    # ============================================
    op.create_table(
        'quick_filter_templates',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=True),  # NULL = system template
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('icon', sa.String(10), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('industry', sa.String(100), nullable=True),
        sa.Column('keywords', sa.JSON(), nullable=False),
        sa.Column('exclude_keywords', sa.JSON(), default=[]),
        sa.Column('price_min', sa.Float(), nullable=True),
        sa.Column('price_max', sa.Float(), nullable=True),
        sa.Column('regions', sa.JSON(), default=[]),
        sa.Column('is_public', sa.Boolean(), default=False),
        sa.Column('usage_count', sa.Integer(), default=0),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_quick_filter_templates_user_id', 'quick_filter_templates', ['user_id'])


def downgrade():
    # Drop in reverse order
    op.drop_index('ix_quick_filter_templates_user_id', table_name='quick_filter_templates')
    op.drop_table('quick_filter_templates')

    op.drop_index('ix_viewed_tenders_user_tender', table_name='viewed_tenders')
    op.drop_index('ix_viewed_tenders_tender_number', table_name='viewed_tenders')
    op.drop_index('ix_viewed_tenders_user_id', table_name='viewed_tenders')
    op.drop_table('viewed_tenders')

    op.drop_index('ix_satisfaction_surveys_user_id', table_name='satisfaction_surveys')
    op.drop_table('satisfaction_surveys')

    op.drop_index('ix_subscriptions_user_id', table_name='subscriptions')
    op.drop_table('subscriptions')

    op.drop_index('ix_user_feedback_user_type', table_name='user_feedback')
    op.drop_index('ix_user_feedback_tender_number', table_name='user_feedback')
    op.drop_index('ix_user_feedback_filter_id', table_name='user_feedback')
    op.drop_index('ix_user_feedback_user_id', table_name='user_feedback')
    op.drop_table('user_feedback')

    op.drop_index('ix_search_history_user_time', table_name='search_history')
    op.drop_index('ix_search_history_user_id', table_name='search_history')
    op.drop_table('search_history')

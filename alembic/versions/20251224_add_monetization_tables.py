"""Add monetization tables and fields.

Revision ID: 20251224_monetization
Revises: 20251219_add_exact_match_to_filters
Create Date: 2024-12-24

New tables:
- broadcast_messages
- promocodes
- payments
- referrals

New fields in sniper_users:
- trial_started_at
- trial_expires_at
- referral_code
- referred_by
- referral_bonus_days
"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime


# revision identifiers, used by Alembic.
revision = '20251224_monetization'
down_revision = 'add_phase21_tables'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ============================================
    # BROADCAST MESSAGES
    # ============================================
    op.create_table(
        'broadcast_messages',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('message_text', sa.Text(), nullable=False),
        sa.Column('target_tier', sa.String(50), default='all'),
        sa.Column('sent_at', sa.DateTime(), default=datetime.utcnow, nullable=False),
        sa.Column('total_recipients', sa.Integer(), default=0),
        sa.Column('successful', sa.Integer(), default=0),
        sa.Column('failed', sa.Integer(), default=0),
        sa.Column('created_by', sa.String(100), nullable=True),
    )

    # ============================================
    # PROMOCODES
    # ============================================
    op.create_table(
        'promocodes',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('code', sa.String(50), unique=True, nullable=False),
        sa.Column('tier', sa.String(50), nullable=False),
        sa.Column('days', sa.Integer(), nullable=False),
        sa.Column('max_uses', sa.Integer(), nullable=True),
        sa.Column('current_uses', sa.Integer(), default=0),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('created_at', sa.DateTime(), default=datetime.utcnow, nullable=False),
        sa.Column('created_by', sa.String(100), nullable=True),
    )
    op.create_index('ix_promocodes_code', 'promocodes', ['code'])

    # ============================================
    # PAYMENTS
    # ============================================
    op.create_table(
        'payments',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('yookassa_payment_id', sa.String(100), unique=True, nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('currency', sa.String(10), default='RUB'),
        sa.Column('tier', sa.String(50), nullable=False),
        sa.Column('status', sa.String(50), default='pending'),
        sa.Column('created_at', sa.DateTime(), default=datetime.utcnow, nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_payments_user_id', 'payments', ['user_id'])
    op.create_index('ix_payments_yookassa_payment_id', 'payments', ['yookassa_payment_id'])

    # ============================================
    # REFERRALS
    # ============================================
    op.create_table(
        'referrals',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('referrer_id', sa.Integer(), sa.ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('referred_id', sa.Integer(), sa.ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('bonus_given', sa.Boolean(), default=False),
        sa.Column('bonus_days', sa.Integer(), default=7),
        sa.Column('created_at', sa.DateTime(), default=datetime.utcnow, nullable=False),
    )
    op.create_index('ix_referrals_referrer_id', 'referrals', ['referrer_id'])
    op.create_index('ix_referrals_referred_id', 'referrals', ['referred_id'])

    # ============================================
    # SNIPER_USERS - новые поля
    # ============================================

    # Trial period
    op.add_column('sniper_users', sa.Column('trial_started_at', sa.DateTime(), nullable=True))
    op.add_column('sniper_users', sa.Column('trial_expires_at', sa.DateTime(), nullable=True))

    # Referral program
    op.add_column('sniper_users', sa.Column('referral_code', sa.String(20), nullable=True))
    op.add_column('sniper_users', sa.Column('referred_by', sa.Integer(), nullable=True))
    op.add_column('sniper_users', sa.Column('referral_bonus_days', sa.Integer(), default=0))

    # Create index for referral_code
    op.create_index('ix_sniper_users_referral_code', 'sniper_users', ['referral_code'], unique=True)


def downgrade() -> None:
    # Drop indexes
    op.drop_index('ix_sniper_users_referral_code', 'sniper_users')

    # Drop columns from sniper_users
    op.drop_column('sniper_users', 'referral_bonus_days')
    op.drop_column('sniper_users', 'referred_by')
    op.drop_column('sniper_users', 'referral_code')
    op.drop_column('sniper_users', 'trial_expires_at')
    op.drop_column('sniper_users', 'trial_started_at')

    # Drop tables
    op.drop_table('referrals')
    op.drop_table('payments')
    op.drop_table('promocodes')
    op.drop_table('broadcast_messages')

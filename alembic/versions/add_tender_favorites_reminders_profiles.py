"""add tender_favorites, hidden_tenders, reminders, profiles

Revision ID: a1b2c3d4e5f6
Revises: 347d2ff67401
Create Date: 2025-12-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '347d2ff67401'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### Create tender_favorites table ###
    op.create_table('tender_favorites',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('tender_number', sa.String(length=100), nullable=False),
        sa.Column('tender_name', sa.Text(), nullable=True),
        sa.Column('tender_price', sa.Float(), nullable=True),
        sa.Column('tender_url', sa.String(length=500), nullable=True),
        sa.Column('added_at', sa.DateTime(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['sniper_users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_tender_favorites_user_id', 'tender_favorites', ['user_id'], unique=False)
    op.create_index('ix_tender_favorites_tender_number', 'tender_favorites', ['tender_number'], unique=False)
    op.create_index('ix_tender_favorites_user_tender', 'tender_favorites', ['user_id', 'tender_number'], unique=True)

    # ### Create hidden_tenders table ###
    op.create_table('hidden_tenders',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('tender_number', sa.String(length=100), nullable=False),
        sa.Column('hidden_at', sa.DateTime(), nullable=False),
        sa.Column('reason', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['sniper_users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_hidden_tenders_user_id', 'hidden_tenders', ['user_id'], unique=False)
    op.create_index('ix_hidden_tenders_tender_number', 'hidden_tenders', ['tender_number'], unique=False)
    op.create_index('ix_hidden_tenders_user_tender', 'hidden_tenders', ['user_id', 'tender_number'], unique=True)

    # ### Create tender_reminders table ###
    op.create_table('tender_reminders',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('tender_number', sa.String(length=100), nullable=False),
        sa.Column('tender_name', sa.Text(), nullable=True),
        sa.Column('tender_url', sa.String(length=500), nullable=True),
        sa.Column('reminder_time', sa.DateTime(), nullable=False),
        sa.Column('days_before_deadline', sa.Integer(), nullable=True),
        sa.Column('sent', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['sniper_users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_tender_reminders_user_id', 'tender_reminders', ['user_id'], unique=False)
    op.create_index('ix_tender_reminders_tender_number', 'tender_reminders', ['tender_number'], unique=False)
    op.create_index('ix_tender_reminders_user_time', 'tender_reminders', ['user_id', 'reminder_time'], unique=False)
    op.create_index('ix_tender_reminders_sent', 'tender_reminders', ['sent', 'reminder_time'], unique=False)

    # ### Create user_profiles table ###
    op.create_table('user_profiles',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('specialization', sa.String(length=500), nullable=True),
        sa.Column('regions', sa.JSON(), nullable=True),
        sa.Column('amount_min', sa.Float(), nullable=True),
        sa.Column('amount_max', sa.Float(), nullable=True),
        sa.Column('licenses', sa.JSON(), nullable=True),
        sa.Column('experience_years', sa.Integer(), nullable=True),
        sa.Column('preferred_law_types', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['sniper_users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id')
    )
    op.create_index('ix_user_profiles_user_id', 'user_profiles', ['user_id'], unique=True)


def downgrade() -> None:
    # ### Drop tables in reverse order ###
    op.drop_index('ix_user_profiles_user_id', table_name='user_profiles')
    op.drop_table('user_profiles')

    op.drop_index('ix_tender_reminders_sent', table_name='tender_reminders')
    op.drop_index('ix_tender_reminders_user_time', table_name='tender_reminders')
    op.drop_index('ix_tender_reminders_tender_number', table_name='tender_reminders')
    op.drop_index('ix_tender_reminders_user_id', table_name='tender_reminders')
    op.drop_table('tender_reminders')

    op.drop_index('ix_hidden_tenders_user_tender', table_name='hidden_tenders')
    op.drop_index('ix_hidden_tenders_tender_number', table_name='hidden_tenders')
    op.drop_index('ix_hidden_tenders_user_id', table_name='hidden_tenders')
    op.drop_table('hidden_tenders')

    op.drop_index('ix_tender_favorites_user_tender', table_name='tender_favorites')
    op.drop_index('ix_tender_favorites_tender_number', table_name='tender_favorites')
    op.drop_index('ix_tender_favorites_user_id', table_name='tender_favorites')
    op.drop_table('tender_favorites')

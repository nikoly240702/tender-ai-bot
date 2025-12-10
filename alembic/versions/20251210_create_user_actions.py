"""create user_actions table for analytics

Revision ID: user_actions_log
Revises: add_error_count
Create Date: 2024-12-10

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'user_actions_log'
down_revision = 'add_error_count'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('user_actions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('action_type', sa.String(length=50), nullable=False),
        sa.Column('action_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['sniper_users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_user_actions_user_id', 'user_actions', ['user_id'])
    op.create_index('ix_user_actions_action_type', 'user_actions', ['action_type'])
    op.create_index('ix_user_actions_created_at', 'user_actions', ['created_at'])


def downgrade():
    op.drop_index('ix_user_actions_created_at', table_name='user_actions')
    op.drop_index('ix_user_actions_action_type', table_name='user_actions')
    op.drop_index('ix_user_actions_user_id', table_name='user_actions')
    op.drop_table('user_actions')

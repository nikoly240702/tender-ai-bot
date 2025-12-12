"""Add user status and blocking fields

Revision ID: e5f6g7h8i9j0
Revises: 20251210_create_user_actions
Create Date: 2024-12-12

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5f6g7h8i9j0'
down_revision = '20251210_create_user_actions'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add status and blocking columns to sniper_users."""

    # Add status column with default 'active'
    op.add_column(
        'sniper_users',
        sa.Column('status', sa.String(50), nullable=False, server_default='active')
    )

    # Add first_name and last_name if not exist
    op.add_column(
        'sniper_users',
        sa.Column('first_name', sa.String(255), nullable=True)
    )

    op.add_column(
        'sniper_users',
        sa.Column('last_name', sa.String(255), nullable=True)
    )

    # Add blocking-related columns
    op.add_column(
        'sniper_users',
        sa.Column('blocked_reason', sa.Text(), nullable=True)
    )

    op.add_column(
        'sniper_users',
        sa.Column('blocked_at', sa.DateTime(), nullable=True)
    )

    op.add_column(
        'sniper_users',
        sa.Column('blocked_by', sa.BigInteger(), nullable=True)
    )

    # Add index for status for faster filtering
    op.create_index('ix_sniper_users_status', 'sniper_users', ['status'])


def downgrade() -> None:
    """Remove status and blocking columns from sniper_users."""

    # Remove index
    op.drop_index('ix_sniper_users_status', table_name='sniper_users')

    # Remove columns
    op.drop_column('sniper_users', 'blocked_by')
    op.drop_column('sniper_users', 'blocked_at')
    op.drop_column('sniper_users', 'blocked_reason')
    op.drop_column('sniper_users', 'last_name')
    op.drop_column('sniper_users', 'first_name')
    op.drop_column('sniper_users', 'status')

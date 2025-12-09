"""add notifications_enabled to sniper_users

Revision ID: d4e5f6g7h8i9
Revises: c3d4e5f6g7h8
Create Date: 2025-12-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6g7h8i9'
down_revision: Union[str, None] = 'c3d4e5f6g7h8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add notifications_enabled column to sniper_users table."""
    conn = op.get_bind()
    inspector = inspect(conn)

    # Check if column exists
    columns = [col['name'] for col in inspector.get_columns('sniper_users')]
    if 'notifications_enabled' not in columns:
        op.add_column('sniper_users',
            sa.Column('notifications_enabled', sa.Boolean(), nullable=False, server_default='true')
        )


def downgrade() -> None:
    """Remove notifications_enabled column from sniper_users table."""
    op.drop_column('sniper_users', 'notifications_enabled')

"""add data column to sniper_users

Revision ID: 20260124_data
Revises: 93c388d2ce73
Create Date: 2026-01-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = '20260124_data'
down_revision: Union[str, None] = '93c388d2ce73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add data JSON column to sniper_users table."""
    conn = op.get_bind()
    inspector = inspect(conn)

    # Check if column exists
    columns = [col['name'] for col in inspector.get_columns('sniper_users')]
    if 'data' not in columns:
        op.add_column('sniper_users',
            sa.Column('data', sa.JSON(), nullable=True, server_default='{}')
        )


def downgrade() -> None:
    """Remove data column from sniper_users table."""
    op.drop_column('sniper_users', 'data')

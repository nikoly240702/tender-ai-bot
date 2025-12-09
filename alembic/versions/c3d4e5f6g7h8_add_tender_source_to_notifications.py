"""add tender_source to sniper_notifications

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2025-12-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6g7h8'
down_revision: Union[str, None] = 'b2c3d4e5f6g7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add tender_source column to sniper_notifications table."""
    conn = op.get_bind()
    inspector = inspect(conn)

    # Check if column exists
    columns = [col['name'] for col in inspector.get_columns('sniper_notifications')]
    if 'tender_source' not in columns:
        op.add_column('sniper_notifications',
            sa.Column('tender_source', sa.String(length=50), nullable=False, server_default='automonitoring')
        )


def downgrade() -> None:
    """Remove tender_source column from sniper_notifications table."""
    op.drop_column('sniper_notifications', 'tender_source')

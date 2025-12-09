"""add submission_deadline to sniper_notifications

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2025-12-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6g7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add submission_deadline column to sniper_notifications table."""
    # Add submission_deadline column (nullable for backward compatibility)
    op.add_column('sniper_notifications',
        sa.Column('submission_deadline', sa.DateTime(), nullable=True)
    )


def downgrade() -> None:
    """Remove submission_deadline column from sniper_notifications table."""
    op.drop_column('sniper_notifications', 'submission_deadline')

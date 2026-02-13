"""add ai analysis quota fields

Revision ID: 03a9d5a5edba
Revises: 283d540b9b7d
Create Date: 2026-02-13 11:05:27.160733

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '03a9d5a5edba'
down_revision: Union[str, Sequence[str], None] = '283d540b9b7d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add AI analysis quota fields to sniper_users."""
    op.add_column('sniper_users', sa.Column('ai_analyses_used_month', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('sniper_users', sa.Column('ai_analyses_month_reset', sa.DateTime(), nullable=True))
    op.add_column('sniper_users', sa.Column('has_ai_unlimited', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('sniper_users', sa.Column('ai_unlimited_expires_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Remove AI analysis quota fields."""
    op.drop_column('sniper_users', 'ai_unlimited_expires_at')
    op.drop_column('sniper_users', 'has_ai_unlimited')
    op.drop_column('sniper_users', 'ai_analyses_month_reset')
    op.drop_column('sniper_users', 'ai_analyses_used_month')

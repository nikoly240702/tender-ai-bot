"""Add expanded_keywords field to sniper_filters

Revision ID: 20260208_expanded_kw
Revises: 20260203_ai_intent
Create Date: 2026-02-08

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260208_expanded_kw'
down_revision = '20260203_ai_intent'
branch_labels = None
depends_on = None


def upgrade():
    """Add expanded_keywords column to sniper_filters."""
    op.add_column(
        'sniper_filters',
        sa.Column('expanded_keywords', sa.JSON(), nullable=True)
    )


def downgrade():
    """Remove expanded_keywords column."""
    op.drop_column('sniper_filters', 'expanded_keywords')

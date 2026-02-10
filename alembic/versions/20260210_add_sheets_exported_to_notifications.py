"""Add sheets_exported columns to sniper_notifications

Revision ID: 20260210_sheets_exp
Revises: 20260209_gsheets
Create Date: 2026-02-10

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260210_sheets_exp'
down_revision = '20260209_gsheets'
branch_labels = None
depends_on = None


def upgrade():
    """Add sheets_exported and sheets_exported_at to sniper_notifications."""
    op.add_column('sniper_notifications',
        sa.Column('sheets_exported', sa.Boolean(), server_default=sa.text('false'), nullable=False)
    )
    op.add_column('sniper_notifications',
        sa.Column('sheets_exported_at', sa.DateTime(), nullable=True)
    )


def downgrade():
    """Remove sheets_exported columns."""
    op.drop_column('sniper_notifications', 'sheets_exported_at')
    op.drop_column('sniper_notifications', 'sheets_exported')

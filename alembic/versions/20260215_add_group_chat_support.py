"""Add group chat support columns

Revision ID: 20260215_groups
Revises: 20260210_sheets_exp
Create Date: 2026-02-15

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260215_groups'
down_revision = '03a9d5a5edba'
branch_labels = None
depends_on = None


def upgrade():
    """Add is_group, group_admin_id to sniper_users and sheets_exported_by to sniper_notifications."""
    op.add_column('sniper_users',
        sa.Column('is_group', sa.Boolean(), server_default=sa.text('false'), nullable=True)
    )
    op.add_column('sniper_users',
        sa.Column('group_admin_id', sa.BigInteger(), nullable=True)
    )
    op.add_column('sniper_notifications',
        sa.Column('sheets_exported_by', sa.BigInteger(), nullable=True)
    )


def downgrade():
    """Remove group chat support columns."""
    op.drop_column('sniper_notifications', 'sheets_exported_by')
    op.drop_column('sniper_users', 'group_admin_id')
    op.drop_column('sniper_users', 'is_group')

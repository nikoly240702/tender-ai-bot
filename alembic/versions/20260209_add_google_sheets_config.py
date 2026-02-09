"""Add google_sheets_config table

Revision ID: 20260209_gsheets
Revises: 20260208_expanded_kw
Create Date: 2026-02-09

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260209_gsheets'
down_revision = '20260208_expanded_kw'
branch_labels = None
depends_on = None


def upgrade():
    """Create google_sheets_config table."""
    op.create_table(
        'google_sheets_config',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('spreadsheet_id', sa.String(255), nullable=False),
        sa.Column('sheet_name', sa.String(255), server_default='Тендеры'),
        sa.Column('columns', sa.JSON(), nullable=False),
        sa.Column('ai_enrichment', sa.Boolean(), server_default=sa.text('false')),
        sa.Column('enabled', sa.Boolean(), server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_google_sheets_config_user', 'google_sheets_config', ['user_id'], unique=True)


def downgrade():
    """Drop google_sheets_config table."""
    op.drop_index('ix_google_sheets_config_user', table_name='google_sheets_config')
    op.drop_table('google_sheets_config')

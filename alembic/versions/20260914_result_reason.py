"""Add result_reason to pipeline_cards — optional free-text explanation
of why a submitted procedure was won/lost, surfaced in the new
"Результаты" list view.

Revision ID: 20260914_result_reason
Revises: 20260913_notified_chat_unique
Create Date: 2026-09-14

"""
from alembic import op
import sqlalchemy as sa


revision = '20260914_result_reason'
down_revision = '20260913_notified_chat_unique'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('pipeline_cards', sa.Column('result_reason', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('pipeline_cards', 'result_reason')

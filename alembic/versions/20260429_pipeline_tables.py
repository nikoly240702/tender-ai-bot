"""add pipeline tables: companies, company_members, team_invites, pipeline_cards (+5 child)

Revision ID: 20260429_pipeline
Revises: 20260420_match_count
Create Date: 2026-04-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20260429_pipeline'
down_revision: Union[str, None] = '20260420_match_count'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'companies',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('owner_user_id', sa.Integer(), sa.ForeignKey('sniper_users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        'company_members',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('sniper_users.id'), nullable=False),
        sa.Column('role', sa.String(16), nullable=False),
        sa.Column('joined_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('user_id', name='uq_company_members_user'),
    )
    op.create_index('ix_company_members_company', 'company_members', ['company_id'])

    op.create_table(
        'team_invites',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('token', sa.String(64), nullable=False, unique=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('sniper_users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('max_uses', sa.Integer(), nullable=False, server_default='10'),
        sa.Column('used_count', sa.Integer(), nullable=False, server_default='0'),
    )

    op.create_table(
        'pipeline_cards',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('tender_number', sa.String(40), nullable=False),
        sa.Column('stage', sa.String(20), nullable=False, server_default='FOUND'),
        sa.Column('assignee_user_id', sa.Integer(), sa.ForeignKey('sniper_users.id'), nullable=True),
        sa.Column('filter_id', sa.Integer(), sa.ForeignKey('sniper_filters.id'), nullable=True),
        sa.Column('source', sa.String(20), nullable=False, server_default='feed'),
        sa.Column('result', sa.String(10), nullable=True),
        sa.Column('purchase_price', sa.Numeric(14, 2), nullable=True),
        sa.Column('sale_price', sa.Numeric(14, 2), nullable=True),
        sa.Column('ai_summary', sa.Text(), nullable=True),
        sa.Column('ai_recommendation', sa.String(40), nullable=True),
        sa.Column('ai_enriched_at', sa.DateTime(), nullable=True),
        sa.Column('archived_at', sa.DateTime(), nullable=True),
        sa.Column('data', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('sniper_users.id'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('company_id', 'tender_number', name='uq_pipeline_company_tender'),
    )
    op.create_index('ix_pipeline_cards_company_id', 'pipeline_cards', ['company_id'])
    op.create_index('ix_pipeline_cards_tender_number', 'pipeline_cards', ['tender_number'])
    op.create_index('ix_pipeline_company_stage', 'pipeline_cards', ['company_id', 'stage'])
    op.create_index('ix_pipeline_company_archived', 'pipeline_cards', ['company_id', 'archived_at'])

    op.create_table(
        'pipeline_card_history',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('card_id', sa.Integer(), sa.ForeignKey('pipeline_cards.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('sniper_users.id'), nullable=False),
        sa.Column('action', sa.String(40), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_pipeline_card_history_card_id', 'pipeline_card_history', ['card_id'])

    op.create_table(
        'pipeline_card_notes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('card_id', sa.Integer(), sa.ForeignKey('pipeline_cards.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('sniper_users.id'), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_pipeline_card_notes_card_id', 'pipeline_card_notes', ['card_id'])

    op.create_table(
        'pipeline_card_files',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('card_id', sa.Integer(), sa.ForeignKey('pipeline_cards.id', ondelete='CASCADE'), nullable=False),
        sa.Column('uploaded_by', sa.Integer(), sa.ForeignKey('sniper_users.id'), nullable=False),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('mime_type', sa.String(100), nullable=False),
        sa.Column('path', sa.String(500), nullable=False),
        sa.Column('is_generated', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('uploaded_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_pipeline_card_files_card_id', 'pipeline_card_files', ['card_id'])

    op.create_table(
        'pipeline_card_checklist',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('card_id', sa.Integer(), sa.ForeignKey('pipeline_cards.id', ondelete='CASCADE'), nullable=False),
        sa.Column('text', sa.String(500), nullable=False),
        sa.Column('done', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('sniper_users.id'), nullable=False),
        sa.Column('done_by', sa.Integer(), sa.ForeignKey('sniper_users.id'), nullable=True),
        sa.Column('done_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_pipeline_card_checklist_card_id', 'pipeline_card_checklist', ['card_id'])

    op.create_table(
        'pipeline_card_relations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('card_id', sa.Integer(), sa.ForeignKey('pipeline_cards.id', ondelete='CASCADE'), nullable=False),
        sa.Column('related_card_id', sa.Integer(), sa.ForeignKey('pipeline_cards.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(40), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('sniper_users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('card_id', 'related_card_id', name='uq_card_relation'),
    )
    op.create_index('ix_pipeline_card_relations_card_id', 'pipeline_card_relations', ['card_id'])


def downgrade() -> None:
    op.drop_table('pipeline_card_relations')
    op.drop_table('pipeline_card_checklist')
    op.drop_table('pipeline_card_files')
    op.drop_table('pipeline_card_notes')
    op.drop_table('pipeline_card_history')
    op.drop_table('pipeline_cards')
    op.drop_table('team_invites')
    op.drop_table('company_members')
    op.drop_table('companies')

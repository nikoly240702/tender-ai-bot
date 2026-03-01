"""Add company_profiles, generated_documents, web_sessions tables

Revision ID: 20260225_docs
Revises: 20260219_react
Create Date: 2026-02-25

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260225_docs'
down_revision = '20260219_react'
branch_labels = None
depends_on = None


def upgrade():
    """Add company_profiles, generated_documents, web_sessions tables."""

    # Company profiles
    op.create_table(
        'company_profiles',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('sniper_users.id', ondelete='CASCADE'), unique=True, nullable=False),
        sa.Column('company_name', sa.String(500), nullable=True),
        sa.Column('company_name_short', sa.String(255), nullable=True),
        sa.Column('legal_form', sa.String(50), nullable=True),
        sa.Column('inn', sa.String(12), nullable=True),
        sa.Column('kpp', sa.String(9), nullable=True),
        sa.Column('ogrn', sa.String(15), nullable=True),
        sa.Column('legal_address', sa.Text(), nullable=True),
        sa.Column('actual_address', sa.Text(), nullable=True),
        sa.Column('postal_address', sa.Text(), nullable=True),
        sa.Column('director_name', sa.String(255), nullable=True),
        sa.Column('director_position', sa.String(255), nullable=True),
        sa.Column('director_basis', sa.String(255), nullable=True),
        sa.Column('phone', sa.String(50), nullable=True),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('website', sa.String(255), nullable=True),
        sa.Column('bank_name', sa.String(500), nullable=True),
        sa.Column('bank_bik', sa.String(9), nullable=True),
        sa.Column('bank_account', sa.String(20), nullable=True),
        sa.Column('bank_corr_account', sa.String(20), nullable=True),
        sa.Column('smp_status', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('licenses_text', sa.Text(), nullable=True),
        sa.Column('experience_description', sa.Text(), nullable=True),
        sa.Column('is_complete', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_company_profiles_user_id', 'company_profiles', ['user_id'], unique=True)

    # Generated documents
    op.create_table(
        'generated_documents',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tender_number', sa.String(100), nullable=False),
        sa.Column('doc_type', sa.String(50), nullable=False),
        sa.Column('doc_name', sa.String(500), nullable=True),
        sa.Column('file_format', sa.String(10), server_default='docx', nullable=False),
        sa.Column('generation_status', sa.String(20), server_default='pending', nullable=False),
        sa.Column('ai_generated_content', sa.Text(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('downloaded_count', sa.Integer(), server_default='0', nullable=False),
    )
    op.create_index('ix_generated_documents_user_id', 'generated_documents', ['user_id'])
    op.create_index('ix_generated_documents_tender_number', 'generated_documents', ['tender_number'])
    op.create_index('ix_generated_docs_user_tender', 'generated_documents', ['user_id', 'tender_number'])

    # Web sessions
    op.create_table(
        'web_sessions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('session_token', sa.String(64), unique=True, nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('last_used', sa.DateTime(), nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
    )
    op.create_index('ix_web_sessions_user_id', 'web_sessions', ['user_id'])
    op.create_index('ix_web_sessions_session_token', 'web_sessions', ['session_token'], unique=True)


def downgrade():
    """Remove company_profiles, generated_documents, web_sessions tables."""
    op.drop_index('ix_web_sessions_session_token', table_name='web_sessions')
    op.drop_index('ix_web_sessions_user_id', table_name='web_sessions')
    op.drop_table('web_sessions')

    op.drop_index('ix_generated_docs_user_tender', table_name='generated_documents')
    op.drop_index('ix_generated_documents_tender_number', table_name='generated_documents')
    op.drop_index('ix_generated_documents_user_id', table_name='generated_documents')
    op.drop_table('generated_documents')

    op.drop_index('ix_company_profiles_user_id', table_name='company_profiles')
    op.drop_table('company_profiles')

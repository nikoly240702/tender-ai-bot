# alembic/versions/20260717_kp_generator.py
"""kp generator: proposals + profile kp fields

Revision ID: 20260717_kp
Revises: 20260504_own_products
Create Date: 2026-07-17

"""
from alembic import op
import sqlalchemy as sa

revision = '20260717_kp'
down_revision = '20260504_own_products'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('company_profiles', sa.Column('kp_number_prefix', sa.String(length=20), nullable=True))
    op.add_column('company_profiles', sa.Column('kp_counter', sa.Integer(), server_default='0', nullable=False))
    op.add_column('company_profiles', sa.Column('kp_default_validity_days', sa.Integer(), server_default='15', nullable=False))
    op.add_column('company_profiles', sa.Column('kp_default_payment_terms', sa.Text(), nullable=True))
    op.add_column('company_profiles', sa.Column('kp_default_delivery_terms', sa.Text(), nullable=True))
    op.add_column('company_profiles', sa.Column('kp_signer_name', sa.String(length=255), nullable=True))
    op.create_table('commercial_proposals',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('number', sa.String(length=100), nullable=False),
        sa.Column('recipient_kuda', sa.String(length=500), nullable=True),
        sa.Column('recipient_komu', sa.String(length=500), nullable=True),
        sa.Column('recipient_tel', sa.String(length=100), nullable=True),
        sa.Column('vat_mode', sa.String(length=10), server_default='none', nullable=False),
        sa.Column('delivery_time', sa.String(length=255), nullable=True),
        sa.Column('items', sa.JSON(), nullable=True),
        sa.Column('total', sa.Float(), nullable=False, server_default='0'),
        sa.Column('vat_amount', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table('commercial_proposals')
    for col in ('kp_signer_name', 'kp_default_delivery_terms', 'kp_default_payment_terms',
                'kp_default_validity_days', 'kp_counter', 'kp_number_prefix'):
        op.drop_column('company_profiles', col)

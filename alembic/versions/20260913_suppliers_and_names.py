"""Suppliers/quotes, custom member display names, system-authored history rows

Три независимых изменения схемы под один список доработок кабинета:

1. pipeline_card_history.user_id → nullable. Автопросрочка тендеров по
   истёкшему дедлайну (отдельная фоновая задача) переводит карточку в
   REJECTED сама, без участия человека — история должна это фиксировать,
   но "действующего" юзера для такой записи нет.
2. company_members.display_name — владелец задаёт участнику команды
   удобное имя (Telegram first_name часто мусорный: ник, эмодзи и т.п.),
   чтобы в истории карточки было видно "Коля"/"Артём", а не "User 104".
3. suppliers + pipeline_card_quotes — сравнение предложений разных
   поставщиков по одной карточке тендера (несколько предложений на
   карточку, как в реальном тендерном процессе).

Revision ID: 20260913_suppliers_names
Revises: 20260910_notif_company
Create Date: 2026-09-13

"""
from alembic import op
import sqlalchemy as sa


revision = '20260913_suppliers_names'
down_revision = '20260910_notif_company'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        'pipeline_card_history', 'user_id',
        existing_type=sa.Integer(),
        nullable=True,
    )

    op.add_column(
        'company_members',
        sa.Column('display_name', sa.String(length=255), nullable=True),
    )

    op.create_table(
        'suppliers',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('contact', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('sniper_users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_suppliers_company_id', 'suppliers', ['company_id'])

    op.create_table(
        'pipeline_card_quotes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('card_id', sa.Integer(),
                   sa.ForeignKey('pipeline_cards.id', ondelete='CASCADE'), nullable=False),
        sa.Column('supplier_id', sa.Integer(), sa.ForeignKey('suppliers.id'), nullable=False),
        sa.Column('price', sa.Numeric(14, 2), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('sniper_users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_pipeline_card_quotes_card_id', 'pipeline_card_quotes', ['card_id'])


def downgrade() -> None:
    op.drop_index('ix_pipeline_card_quotes_card_id', table_name='pipeline_card_quotes')
    op.drop_table('pipeline_card_quotes')
    op.drop_index('ix_suppliers_company_id', table_name='suppliers')
    op.drop_table('suppliers')
    op.drop_column('company_members', 'display_name')
    op.alter_column(
        'pipeline_card_history', 'user_id',
        existing_type=sa.Integer(),
        nullable=False,
    )

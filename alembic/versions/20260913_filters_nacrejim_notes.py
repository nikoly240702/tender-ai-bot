"""Add nacrejim + notes to sniper_filters (filters v2 importer needs both)

nacrejim: национальный режим по ПП РФ №1875 (preference_15/restriction_2nd/
ban) — регуляторная метка на фильтре, которую ТЗ хочет впоследствии выводить
в карточке тендера. notes: человеко-читаемое обоснование фильтра из YAML
(`ai_intent` для этого не подходит — оно про другое: описание намерения для
AI-проверки релевантности, а не редакторский комментарий).

Revision ID: 20260913_filters_nacrejim
Revises: 20260913_filters_v2_schema
Create Date: 2026-09-13

"""
from alembic import op
import sqlalchemy as sa


revision = '20260913_filters_nacrejim'
down_revision = '20260913_filters_v2_schema'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('sniper_filters', sa.Column('nacrejim', sa.String(length=30), nullable=True))
    op.add_column('sniper_filters', sa.Column('notes', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('sniper_filters', 'notes')
    op.drop_column('sniper_filters', 'nacrejim')

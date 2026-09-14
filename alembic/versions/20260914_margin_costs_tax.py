"""Расширение расчёта рентабельности карточки.

Раньше маржа считалась только как «наша цена − закупочная», без учёта
прочих затрат и налога, поэтому показывала завышенную прибыль.

Добавляет:
  - pipeline_cards.extra_costs     — доп. расходы по сделке
  - pipeline_cards.logistics_cost  — логистика/доставка
  - companies.tax_rate             — ставка налога с оборота, %
                                     (УСН 6% + страховые взносы ≈ 7%)

Ставка хранится на компании, а не на карточке: система налогообложения у
компании одна, вводить её в каждой карточке было бы лишней работой.

Revision ID: 20260914_margin_costs
Revises: 20260914_result_reason
Create Date: 2026-09-14

"""
from alembic import op
import sqlalchemy as sa


revision = '20260914_margin_costs'
down_revision = '20260914_result_reason'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('pipeline_cards', sa.Column('extra_costs', sa.Numeric(14, 2), nullable=True))
    op.add_column('pipeline_cards', sa.Column('logistics_cost', sa.Numeric(14, 2), nullable=True))
    op.add_column(
        'companies',
        sa.Column('tax_rate', sa.Numeric(5, 2), nullable=False, server_default='7'),
    )


def downgrade() -> None:
    op.drop_column('companies', 'tax_rate')
    op.drop_column('pipeline_cards', 'logistics_cost')
    op.drop_column('pipeline_cards', 'extra_costs')

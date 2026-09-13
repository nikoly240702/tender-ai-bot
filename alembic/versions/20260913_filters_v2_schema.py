"""Filters v2: slug (idempotent upsert key) + group/wave/status/config_version/updated_by

Часть Этапа 1 ТЗ пересборки фильтров (filters_v2.yaml). `status` дублирует
`is_active` осознанно, а не заменяет его: весь существующий код (матчер,
кабинет) читает только `is_active` — трогать десяток мест ради этой задачи
избыточно и рискованно. `status` даёт более тонкие состояния (staged,
archived) поверх той же булевой семантики; импортёр держит их в синхроне
(status='active' <=> is_active=True, всё остальное <=> is_active=False).

Revision ID: 20260913_filters_v2_schema
Revises: 20260913_notify_thread
Create Date: 2026-09-13

"""
from alembic import op
import sqlalchemy as sa


revision = '20260913_filters_v2_schema'
down_revision = '20260913_notify_thread'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('sniper_filters', sa.Column('slug', sa.String(length=100), nullable=True))
    op.create_unique_constraint('uq_sniper_filters_slug', 'sniper_filters', ['slug'])

    op.add_column('sniper_filters', sa.Column('group_id', sa.String(length=50), nullable=True))
    op.add_column('sniper_filters', sa.Column('owner', sa.String(length=50), nullable=True))
    op.add_column('sniper_filters', sa.Column('wave', sa.SmallInteger(), nullable=True))
    op.add_column('sniper_filters',
                   sa.Column('status', sa.String(length=20), nullable=False, server_default='active'))
    op.add_column('sniper_filters', sa.Column('config_version', sa.Integer(), nullable=True))
    op.add_column('sniper_filters',
                   sa.Column('updated_by', sa.Integer(), sa.ForeignKey('sniper_users.id'), nullable=True))

    # Бэкфилл status из is_active для существующих строк (до этого момента
    # status уже проставился в 'active' server_default'ом на все, включая
    # реально приостановленные — эта строка чинит несогласованность сразу).
    op.execute("UPDATE sniper_filters SET status = 'paused' WHERE is_active = false")


def downgrade() -> None:
    op.drop_column('sniper_filters', 'updated_by')
    op.drop_column('sniper_filters', 'config_version')
    op.drop_column('sniper_filters', 'status')
    op.drop_column('sniper_filters', 'wave')
    op.drop_column('sniper_filters', 'owner')
    op.drop_column('sniper_filters', 'group_id')
    op.drop_constraint('uq_sniper_filters_slug', 'sniper_filters', type_='unique')
    op.drop_column('sniper_filters', 'slug')

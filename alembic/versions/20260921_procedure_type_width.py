"""Способ закупки шире 60 знаков: одно значение роняло весь архив.

Замер 21.09.2026: при перезаливке истории 31 выгрузка извещений за
март не загрузилась ЦЕЛИКОМ — asyncpg вернул StringDataRightTruncation
на вставке пачки, и вместе с одной длинной строкой потерялись все
процедуры этих архивов.

Виновник — названия способов закупки вроде «Запрос предложений в
электронной форме, участниками которого могут быть только субъекты
малого предпринимательства и социально ориентированные некоммерческие
организации»: 60 знаков им мало.

Ширина поднята до 255, и в разборе значение дополнительно обрезается
(storage._clip). Обрезка нужна не вместо ширины, а вместе с ней:
классификатор способов закупки пополняется без нашего участия, и
следующее длинное название не должно снова уносить сотни строк.

Для varchar увеличение длины в PostgreSQL не переписывает таблицу,
поэтому миграция дешёвая даже на 88 тысячах строк.

Revision ID: 20260921_proc_type_w
Revises: 20260921_pool_descr
Create Date: 2026-09-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260921_proc_type_w'
down_revision: Union[str, None] = '20260921_pool_descr'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('procedure', 'procedure_type',
                    existing_type=sa.String(60),
                    type_=sa.String(255),
                    existing_nullable=True,
                    schema='eis')


def downgrade() -> None:
    # Значения длиннее 60 знаков существуют, и сузить колонку без потери
    # данных нельзя — обрезаем явно, иначе ALTER упадёт.
    op.execute("UPDATE eis.procedure SET procedure_type = left(procedure_type, 60) "
               "WHERE length(procedure_type) > 60")
    op.alter_column('procedure', 'procedure_type',
                    existing_type=sa.String(255),
                    type_=sa.String(60),
                    existing_nullable=True,
                    schema='eis')

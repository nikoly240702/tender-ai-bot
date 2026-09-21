"""Хранилище исторических данных ЕИС для аналитики ниш.

Отвечает на вопрос «стоит ли лезть в эту категорию»: сколько там бывает
участников и насколько роняют цену. Сегодняшняя система показывает
только то, какие тендеры есть, но не чем похожие заканчивались.

Отдельная схема `eis`, а не public, — сознательное отступление от того,
как устроен остальной проект. Причина в объёме: здесь миллионы строк
исторической выгрузки, которые живут по своим правилам (грузятся
пачками, переживают полную перезаливку, не участвуют в транзакциях
бота). Держать их вперемешку с полусотней операционных таблиц — значит
каждый раз при отладке продираться через них.

Данные приходят из интеграционного сервиса ЕИС
(tender_sniper/sources/eis_integration.py), заменившего отключённый
01.01.2025 FTP открытых данных.

Revision ID: 20260921_eis
Revises: 20260919_sku
Create Date: 2026-09-21

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '20260921_eis'
down_revision = '20260919_sku'
branch_labels = None
depends_on = None

SCHEMA = 'eis'


def upgrade() -> None:
    op.execute(f'CREATE SCHEMA IF NOT EXISTS {SCHEMA}')

    op.create_table(
        'procedure',
        # Реестровый номер закупки — он же ключ связи с протоколами и
        # контрактами. В контракте это поле зовётся notificationNumber.
        sa.Column('purchase_number', sa.String(30), primary_key=True),
        sa.Column('law', sa.SmallInteger(), nullable=False, server_default='44'),
        sa.Column('procedure_type', sa.String(60), nullable=True),
        sa.Column('customer_inn', sa.String(12), nullable=True),
        sa.Column('customer_name', sa.Text(), nullable=True),
        sa.Column('customer_region_code', sa.String(2), nullable=True),
        sa.Column('nmck', sa.Numeric(18, 2), nullable=True),
        sa.Column('currency', sa.String(3), nullable=True, server_default='RUB'),
        sa.Column('published_at', sa.Date(), nullable=True),
        # Все коды позиций и код с наибольшей суммой отдельно: агрегаты
        # считаются по основному, а поиск «где вообще встречается» — по
        # массиву. GIN-индекс ниже именно для второго.
        sa.Column('okpd2_codes', postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column('okpd2_primary', sa.String(20), nullable=True),
        sa.Column('delivery_region_code', sa.String(2), nullable=True),
        # Имя архива, откуда взята запись, — чтобы можно было вернуться к
        # исходнику при расхождении в цифрах.
        sa.Column('raw_source', sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    # Основной разрез аналитики: категория × регион × цена. Один составной
    # индекс под него вместо трёх отдельных — запросы всегда идут сразу по
    # всем трём полям.
    op.create_index('ix_eis_procedure_slice', 'procedure',
                    ['okpd2_primary', 'customer_region_code', 'nmck'], schema=SCHEMA)
    op.create_index('ix_eis_procedure_published', 'procedure',
                    ['published_at'], schema=SCHEMA)
    op.create_index('ix_eis_procedure_okpd2_codes', 'procedure', ['okpd2_codes'],
                    postgresql_using='gin', schema=SCHEMA)

    op.create_table(
        'protocol',
        sa.Column('purchase_number', sa.String(30), primary_key=True),
        # Лот: у многолотовых процедур результаты считаются по каждому
        # отдельно, иначе «две заявки на лот» превратятся в «шесть заявок».
        sa.Column('lot_number', sa.Integer(), primary_key=True, server_default='1'),
        sa.Column('protocol_date', sa.Date(), nullable=True),
        sa.Column('bids_submitted', sa.Integer(), nullable=True),
        sa.Column('bids_admitted', sa.Integer(), nullable=True),
        # ИНН и название победителя в протоколе ОТСУТСТВУЮТ: участник
        # обезличен номером заявки и раскрывается только в реестре
        # контрактов. Колонки заполняются из eis.contract, поэтому
        # nullable — до заключения контракта их знать неоткуда.
        sa.Column('winner_inn', sa.String(12), nullable=True),
        sa.Column('winner_name', sa.Text(), nullable=True),
        sa.Column('winner_price', sa.Numeric(18, 2), nullable=True),
        sa.Column('is_failed', sa.Boolean(), nullable=True),
        sa.Column('failure_reason', sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.create_index('ix_eis_protocol_winner', 'protocol', ['winner_inn'], schema=SCHEMA)
    op.create_index('ix_eis_protocol_date', 'protocol', ['protocol_date'], schema=SCHEMA)

    op.create_table(
        'contract',
        sa.Column('reg_num', sa.String(30), primary_key=True),
        sa.Column('purchase_number', sa.String(30), nullable=True),
        sa.Column('contract_price', sa.Numeric(18, 2), nullable=True),
        sa.Column('supplier_inn', sa.String(12), nullable=True),
        sa.Column('supplier_name', sa.Text(), nullable=True),
        sa.Column('sign_date', sa.Date(), nullable=True),
        sa.Column('execution_deadline', sa.Date(), nullable=True),
        sa.Column('actual_execution_date', sa.Date(), nullable=True),
        sa.Column('is_terminated', sa.Boolean(), nullable=True),
        sa.Column('okpd2_codes', postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column('raw_source', sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    # Связь с процедурой не через ForeignKey намеренно: контракт может
    # приехать раньше своей процедуры (выгрузки идут разными пачками), и
    # жёсткая ссылка отбрасывала бы такие строки вместо того, чтобы
    # дождаться второй половины данных.
    op.create_index('ix_eis_contract_purchase', 'contract',
                    ['purchase_number'], schema=SCHEMA)
    op.create_index('ix_eis_contract_supplier', 'contract',
                    ['supplier_inn'], schema=SCHEMA)

    op.create_table(
        'ingest_log',
        # Ключ — не URL архива: он одноразовый, с тикетом внутри, и при
        # повторном запросе тех же данных будет другим. Поэтому ключом
        # служит логический адрес выгрузки: подсистема/тип/регион/дата.
        sa.Column('archive_path', sa.Text(), primary_key=True),
        sa.Column('region', sa.String(2), nullable=True),
        sa.Column('period', sa.Date(), nullable=True),
        # sha256 содержимого архива: если ЕИС перевыпустил тот же срез с
        # изменениями, хеш отличается и запись перезагружается.
        sa.Column('sha256', sa.String(64), nullable=True),
        sa.Column('rows_loaded', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('rows_skipped', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('status', sa.String(20), nullable=False, server_default='ok'),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('loaded_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        schema=SCHEMA,
    )
    op.create_index('ix_eis_ingest_period', 'ingest_log',
                    ['period', 'region'], schema=SCHEMA)
    op.create_index('ix_eis_ingest_status', 'ingest_log', ['status'], schema=SCHEMA)

    op.create_table(
        'okpd2_dict',
        sa.Column('code', sa.String(20), primary_key=True),
        sa.Column('name', sa.Text(), nullable=False),
        # Уровень = число значащих групп в коде (2, 4, 6 знаков). Агрегаты
        # в ТЗ считаются на всех трёх уровнях сразу.
        sa.Column('level', sa.SmallInteger(), nullable=True),
        sa.Column('parent_code', sa.String(20), nullable=True),
        schema=SCHEMA,
    )
    op.create_index('ix_eis_okpd2_parent', 'okpd2_dict', ['parent_code'], schema=SCHEMA)


def downgrade() -> None:
    for table in ('okpd2_dict', 'ingest_log', 'contract', 'protocol', 'procedure'):
        op.drop_table(table, schema=SCHEMA)
    # Схему сносим только если пустая: CASCADE здесь удалил бы всё, что
    # кто-то мог положить в неё помимо этих таблиц.
    op.execute(f'DROP SCHEMA IF EXISTS {SCHEMA} RESTRICT')

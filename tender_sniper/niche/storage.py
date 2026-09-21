"""Таблицы схемы `eis` и разбор документов в строки.

Таблицы описаны отдельной MetaData, а не через Base из database.py, и
это намеренно: alembic по умолчанию смотрит только на схему по
умолчанию, поэтому `eis` в автогенерацию не попадает и не перетирается.
Схема создаётся миграцией 20260921_eis.
"""
import datetime as _dt
import logging
import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Integer, MetaData, Numeric, SmallInteger,
    String, Table, Text, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, insert as pg_insert

logger = logging.getLogger(__name__)

SCHEMA = "eis"
metadata = MetaData(schema=SCHEMA)

procedure = Table(
    "procedure", metadata,
    Column("purchase_number", String(30), primary_key=True),
    Column("law", SmallInteger),
    Column("procedure_type", String(60)),
    Column("customer_inn", String(12)),
    Column("customer_name", Text),
    Column("customer_region_code", String(2)),
    Column("nmck", Numeric(18, 2)),
    Column("currency", String(3)),
    Column("published_at", Date),
    Column("okpd2_codes", ARRAY(Text)),
    Column("okpd2_primary", String(20)),
    Column("delivery_region_code", String(2)),
    Column("raw_source", Text),
)

protocol = Table(
    "protocol", metadata,
    Column("purchase_number", String(30), primary_key=True),
    Column("lot_number", Integer, primary_key=True),
    Column("protocol_date", Date),
    Column("bids_submitted", Integer),
    Column("bids_admitted", Integer),
    Column("winner_inn", String(12)),
    Column("winner_name", Text),
    Column("winner_price", Numeric(18, 2)),
    Column("is_failed", Boolean),
    Column("failure_reason", Text),
)

contract = Table(
    "contract", metadata,
    Column("reg_num", String(30), primary_key=True),
    Column("purchase_number", String(30)),
    Column("contract_price", Numeric(18, 2)),
    Column("supplier_inn", String(12)),
    Column("supplier_name", Text),
    Column("sign_date", Date),
    Column("execution_deadline", Date),
    Column("actual_execution_date", Date),
    Column("is_terminated", Boolean),
    Column("okpd2_codes", ARRAY(Text)),
    Column("raw_source", Text),
)

ingest_log = Table(
    "ingest_log", metadata,
    Column("archive_path", Text, primary_key=True),
    Column("region", String(2)),
    Column("period", Date),
    Column("sha256", String(64)),
    Column("rows_loaded", Integer),
    Column("rows_skipped", Integer),
    Column("status", String(20)),
    Column("error", Text),
    Column("loaded_at", DateTime(timezone=True)),
)

okpd2_dict = Table(
    "okpd2_dict", metadata,
    Column("code", String(20), primary_key=True),
    Column("name", Text),
    Column("level", SmallInteger),
    Column("parent_code", String(20)),
)


def dedupe_by_key(table: Table, rows: List[Dict]) -> List[Dict]:
    """Оставляет по одной строке на первичный ключ — последнюю.

    Один архив ЕИС содержит НЕСКОЛЬКО версий одного документа: извещение
    правят, и в выгрузку попадают все редакции. PostgreSQL на такое
    отвечает «ON CONFLICT DO UPDATE command cannot affect row a second
    time» и роняет всю пачку целиком.

    Берём последнюю: внутри архива версии идут по возрастанию, а между
    архивами более поздняя редакция всё равно перезапишет раннюю через
    ON CONFLICT.
    """
    keys = [c.name for c in table.primary_key]
    unique: Dict[Tuple, Dict] = {}
    for row in rows:
        unique[tuple(row.get(k) for k in keys)] = row
    return list(unique.values())


def upsert(table: Table, rows: List[Dict]):
    """INSERT ... ON CONFLICT DO UPDATE по первичному ключу.

    Повторный запуск загрузки обязан быть безвредным: ЕИС регулярно
    перевыпускает те же срезы с уточнениями, и падать на дубликатах или
    плодить их одинаково плохо.
    """
    rows = dedupe_by_key(table, rows or [])
    if not rows:
        return None
    statement = pg_insert(table).values(rows)
    keys = [c.name for c in table.primary_key]
    updatable = {c.name: statement.excluded[c.name]
                 for c in table.columns if c.name not in keys}
    return statement.on_conflict_do_update(index_elements=keys, set_=updatable)


# --- разбор дат -----------------------------------------------------------

def to_date(value: Optional[str]) -> Optional[_dt.date]:
    """Дата из ЕИС-времени. Приходит и как `2026-09-20`, и как
    `2026-09-20T20:09:10+12:00` — в аналитике нужен только день."""
    if not value:
        return None
    try:
        return _dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def to_decimal(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


# --- разбор извещения -----------------------------------------------------

_OKPD_LEVELS = (2, 4, 6)


def okpd2_level(code: str) -> Optional[int]:
    """Уровень кода по числу значащих групп: 21 → 2, 21.20 → 4,
    21.20.10 → 6. Более длинные коды (21.20.10.134) считаем шестым
    уровнем: в ТЗ агрегаты выше шестого не поднимаются."""
    if not code:
        return None
    groups = len([g for g in code.split(".") if g])
    return {1: 2, 2: 4}.get(groups, 6)


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def _texts(root, tag: str) -> List[str]:
    return [n.text.strip() for n in root.iter()
            if _local(n.tag) == tag and (n.text or "").strip()]


def _first(root, tag: str) -> Optional[str]:
    values = _texts(root, tag)
    return values[0] if values else None


def parse_notice(root, *, region_code: str, source: str) -> Optional[Dict]:
    """Строка eis.procedure из извещения.

    Регион берётся из параметров запроса, а не из документа: в извещении
    лежит регистрационный номер заказчика в ЕИС, а не код КЛАДР, и
    выводить регион из него — лишний справочник ради данных, которые мы
    и так знаем (выгрузка идёт по одному региону за раз).

    `okpd2_primary` в ТЗ определён как «код с наибольшей суммой позиций»,
    но сумм по позициям в извещении нет — есть только НМЦК целиком.
    Берём самый частый код: для агрегатов по категориям это даёт тот же
    ответ везде, кроме процедур, где дорогая позиция единственная среди
    множества дешёвых. Такие случаи видны по okpd2_codes.
    """
    purchase_number = _first(root, "purchaseNumber")
    # В ИКЗ есть вложенный purchaseNumber из четырёх цифр — не он.
    numbers = [n for n in _texts(root, "purchaseNumber") if len(n) >= 15]
    purchase_number = numbers[0] if numbers else purchase_number
    if not purchase_number or len(purchase_number) < 15:
        return None

    codes = []
    for node in root.iter():
        if _local(node.tag) != "OKPD2":
            continue
        for child in node:
            if _local(child.tag) in ("OKPDCode", "code") and (child.text or "").strip():
                codes.append(child.text.strip())

    return {
        "purchase_number": purchase_number,
        "law": 44,
        "procedure_type": _first(root, "placingWayName") or _first(root, "placingWay"),
        "customer_inn": _first(root, "INN"),
        "customer_name": _first(root, "fullName"),
        "customer_region_code": region_code,
        "nmck": to_decimal(_first(root, "maxPrice")),
        "currency": _first(root, "code") if _first(root, "code") in ("RUB",) else "RUB",
        "published_at": to_date(_first(root, "publishDTInEIS")),
        "okpd2_codes": sorted(set(codes)) or None,
        "okpd2_primary": Counter(codes).most_common(1)[0][0] if codes else None,
        "delivery_region_code": region_code,
        "raw_source": source,
    }


def protocol_to_row(result, *, source: str) -> Optional[Dict]:
    """Строка eis.protocol из разобранного протокола.

    `lot_number` пока всегда 1: сервис отдаёт протокол одного лота
    отдельным документом, и номер лота в самом документе не встречается.
    Колонка в ключе нужна на случай многолотовых процедур — когда они
    появятся, поменяется только это место, а не схема.
    """
    if not result.purchase_number:
        return None
    return {
        "purchase_number": result.purchase_number,
        "lot_number": 1,
        "protocol_date": to_date(result.protocol_date),
        "bids_submitted": result.bids_submitted,
        "bids_admitted": result.bids_admitted,
        # Победитель обезличен в протоколе; заполняется из eis.contract.
        "winner_inn": None,
        "winner_name": None,
        "winner_price": result.winner_price,
        "is_failed": result.is_failed,
        "failure_reason": None,
    }


def contract_to_row(parsed: Dict, *, source: str) -> Optional[Dict]:
    if not parsed.get("reg_num"):
        return None
    return {
        "reg_num": parsed["reg_num"],
        "purchase_number": parsed.get("purchase_number"),
        "contract_price": to_decimal(parsed.get("price")),
        "supplier_inn": parsed.get("supplier_inn"),
        "supplier_name": parsed.get("supplier_name"),
        "sign_date": to_date(parsed.get("sign_date")),
        "execution_deadline": None,
        "actual_execution_date": None,
        "is_terminated": None,
        "okpd2_codes": parsed.get("okpd2") or None,
        "raw_source": source,
    }

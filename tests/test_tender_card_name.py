"""
Тесты разрешения названия в карточке тендера (живой формат уведомлений).

Карточка `bot/formatters/tender_card.py` — это то, что реально видит юзер в TG.
Раньше она показывала сырой тип процедуры («Запрос котировок в электронной
форме») вместо предмета закупки и не звала AI-генератор.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from bot.formatters.tender_card import _resolve_tender_name


def test_procedure_type_name_replaced_by_real_subject_from_ai_summary():
    tender = {
        "name": "Запрос котировок в электронной форме",
        "number": "0372200015125000456",
        "summary": "Расходные материалы для оргтехники",
    }
    match_info = {
        "ai_summary": "закупка картриджей лазерных в рамках запроса котировок",
    }
    name = _resolve_tender_name(tender, match_info).lower()

    assert "картридж" in name, f"Ожидали предмет закупки, получили: {name!r}"
    assert "запрос котировок в электронной форме" not in name


def test_procedure_type_name_replaced_by_object_from_summary():
    tender = {
        "name": "Электронный аукцион",
        "number": "0101",
        "summary": "<strong>Наименование объекта закупки: </strong>"
                   "Поставка мотор-редукторов",
    }
    name = _resolve_tender_name(tender, {}).lower()

    assert "мотор-редуктор" in name
    assert name != "электронный аукцион"


def test_good_name_is_kept_unchanged():
    tender = {"name": "Поставка оконных блоков", "number": "1"}
    assert _resolve_tender_name(tender, {}) == "Поставка оконных блоков"


def test_no_real_subject_falls_back_to_number_not_procedure_type():
    # Нет ни объекта в summary, ни AI-сводки → честный фолбэк на номер,
    # но НЕ сырой тип процедуры
    tender = {"name": "Запрос котировок в электронной форме", "number": "5550000"}
    name = _resolve_tender_name(tender, {})
    assert "запрос котировок в электронной форме" not in name.lower()
    assert "5550000" in name

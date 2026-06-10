"""
Тесты единого резолвера названия тендера (общий для TG-карточки, кабинета и Bitrix).

Цель: название = ПРЕДМЕТ закупки, а не тип процедуры. Один резолвер → одно и то же
имя в уведомлении, в Pipeline-карточке и в сделке Bitrix24.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tender_sniper.tender_name_resolver import resolve_tender_name, looks_like_junk_name


def test_procedure_type_name_resolved_to_real_subject_from_summary():
    tender = {
        "name": "Запрос котировок в электронной форме",
        "number": "0101",
        "summary": "<strong>Наименование объекта закупки: </strong>Поставка бумаги А4",
    }
    assert "бумаг" in resolve_tender_name(tender, {}).lower()


def test_bad_ai_simple_name_is_not_used_for_bitrix():
    # ai_simple_name тоже может быть пустышкой — не должны её отдавать в Bitrix
    tender = {"name": "Запрос котировок в электронной форме", "number": "0202",
              "summary": ""}
    match_info = {
        "ai_simple_name": "Запрос котировок в электронной форме",
        "ai_summary": "закупка насоса центробежного в рамках запроса котировок",
    }
    name = resolve_tender_name(tender, match_info).lower()
    assert "насос" in name
    assert "запрос котировок в электронной форме" not in name


def test_good_ai_simple_name_preferred_over_summary_sentence():
    tender = {"name": "Электронный аукцион", "number": "0303", "summary": ""}
    match_info = {"ai_simple_name": "Закупка клавиатуры и мыши",
                  "ai_summary": "поставка компьютерной периферии для нужд учреждения"}
    assert resolve_tender_name(tender, match_info) == "Закупка клавиатуры и мыши"


def test_good_raw_name_kept():
    tender = {"name": "Поставка оконных блоков", "number": "1"}
    assert resolve_tender_name(tender, {}) == "Поставка оконных блоков"


def test_never_returns_procedure_type_falls_back_to_number():
    tender = {"name": "Запрос котировок в электронной форме", "number": "9999000"}
    name = resolve_tender_name(tender, {})
    assert not looks_like_junk_name(name) or name.startswith("Тендер")
    assert "запрос котировок в электронной форме" not in name.lower()
    assert "9999000" in name

"""
Тесты заголовка сделки Bitrix: имя фильтра идёт префиксом в TITLE,
чтобы на канбане сразу было видно, по какому фильтру пришло совпадение.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.handlers.bitrix24 import build_deal_title


def test_filter_name_is_prefixed():
    title = build_deal_title("МРТ/КТ и лучевая диагностика", "Поставка томографа", "0101")
    assert title == "[МРТ/КТ и лучевая диагностика] Поставка томографа"


def test_no_filter_name_keeps_plain_name():
    assert build_deal_title("", "Поставка бумаги А4", "0202") == "Поставка бумаги А4"
    assert build_deal_title(None, "Поставка бумаги А4", "0202") == "Поставка бумаги А4"


def test_empty_tender_name_falls_back_to_number():
    title = build_deal_title("Электроника", "", "0303")
    assert title == "[Электроника] Тендер № 0303"


def test_no_filter_and_no_name_falls_back_to_number():
    assert build_deal_title("", "", "0404") == "Тендер № 0404"


def test_title_truncated_to_255():
    long_name = "А" * 400
    title = build_deal_title("Фильтр", long_name, "0505")
    assert len(title) <= 255
    assert title.startswith("[Фильтр] ")


def test_whitespace_is_trimmed():
    assert build_deal_title("  Бумага  ", "  Поставка  ", "0606") == "[Бумага] Поставка"

"""
Тесты качества названий тендеров.

Покрывают две жалобы:
1. Названия вида "Запрос котировок в электронной форме" (тип процедуры без предмета)
   не должны доходить до пользователя — нужно подставлять реальный объект закупки.
2. Название должно строго отражать реально закупаемое (без галлюцинаций).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.parsers.zakupki_rss_parser import _looks_like_junk_title
from tender_sniper.ai_name_generator import TenderNameGenerator


PROCEDURE_TYPE_TITLES = [
    "Запрос котировок в электронной форме",
    "Электронный аукцион",
    "Аукцион в электронной форме",
    "Запрос предложений в электронной форме",
    "Открытый конкурс в электронной форме",
    "Закупка у единственного поставщика",
    "Запрос котировок",
]


@pytest.fixture
def gen():
    g = TenderNameGenerator()
    g.llm = None  # офлайн: проверяем fallback-путь без обращения к LLM
    return g


# ============================================================
# Layer 1 — парсер должен распознавать "только тип процедуры" как мусор
# ============================================================

@pytest.mark.parametrize("title", PROCEDURE_TYPE_TITLES)
def test_procedure_type_only_title_is_junk(title):
    assert _looks_like_junk_title(title) is True, (
        f"Название из одного типа процедуры должно считаться мусором: {title!r}"
    )


def test_title_with_real_subject_is_not_junk():
    # Тип процедуры + реальный предмет — НЕ мусор, не трогаем
    assert _looks_like_junk_title("Электронный аукцион на поставку компьютеров") is False
    assert _looks_like_junk_title("Поставка оконных блоков") is False


# ============================================================
# Layer 2 — генератор подставляет реальный объект закупки из summary
# ============================================================

@pytest.mark.parametrize("title", PROCEDURE_TYPE_TITLES)
def test_procedure_type_title_uses_purchase_object_from_summary(gen, title):
    tender_data = {
        "summary": "<strong>Наименование объекта закупки: </strong>"
                   "Поставка картриджей для принтеров"
    }
    out = gen.generate_short_name(title, tender_data=tender_data).lower()

    # Должен появиться реальный предмет
    assert "картридж" in out, f"Ожидали предмет закупки, получили: {out!r}"
    # И не должно быть огрызков типа процедуры
    assert "электронной форме" not in out
    assert out != "электронной форме"


def test_strip_procedure_prefix_never_fabricates_form_fragment(gen):
    # Для строки-только-тип-процедуры метод не должен выдумывать
    # «предмет» вида "Электронной форме" — он обязан вернуть вход без изменений,
    # сигнализируя «предмет не найден» (его подставит summary).
    for title in PROCEDURE_TYPE_TITLES:
        stripped = gen._strip_procedure_prefix(title)
        assert stripped == title, (
            f"_strip_procedure_prefix сфабриковал огрызок {stripped!r} из {title!r}"
        )
        # И точно не «Электронной форме»
        assert stripped.lower() != "электронной форме"


# ============================================================
# Layer 3 — антигаллюцинация: название строго из источника
# ============================================================

def test_validator_rejects_hallucinated_subject(gen):
    # Классический кейс: оригинал про окна, выдумка про робота-мойщика
    assert gen._validate_no_hallucination(
        "Робот-мойщик окон", "Поставка оконных блоков", None
    ) is False


def test_validator_accepts_summary_derived_name(gen):
    tender_data = {"summary": "Наименование объекта закупки: Поставка картриджей для принтеров"}
    assert gen._validate_no_hallucination(
        "Поставка картриджей", "Запрос котировок в электронной форме", tender_data
    ) is True

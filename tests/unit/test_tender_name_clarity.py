"""Название тендера должно быть понятным всегда.

Замер боевого потока за неделю (1821 уведомление, 28.09.2026) показал три
источника невнятных имён: имя, оборванное на полуслове (5%), совпадение
только по обрезанному корню слова (13%) и зонтичное имя (8%).

Здесь закрыты первые два.
"""
import pytest

from tender_sniper.tender_name_resolver import (
    looks_truncated,
    resolve_tender_name,
    subject_from_summary,
)


@pytest.mark.unit
class TestLooksTruncated:
    """Имя, обрезанное на полуслове, показывать нельзя."""

    def test_cut_before_the_keyword(self):
        """Реальный случай: ИИ отдал первые четыре слова оригинала."""
        assert looks_truncated(
            "Выполнение работ по капитальному",
            original="Выполнение работ по капитальному ремонту помещений школы") is True

    def test_cut_on_preposition(self):
        assert looks_truncated("Поставка оборудования для") is True

    def test_cut_on_open_bracket(self):
        assert looks_truncated("Поставка сантехнических товаров (") is True

    def test_sensible_shortening_is_kept(self):
        """Отрезать «для нужд больницы» — это нормальное сокращение."""
        assert looks_truncated(
            "Поставка перчаток",
            original="Поставка перчаток для нужд больницы №3") is False

    def test_adjective_at_the_end_of_a_full_name_is_fine(self):
        """«Поставка перчаток хирургических» — обычный обратный порядок слов."""
        assert looks_truncated("Поставка перчаток хирургических") is False

    def test_full_name_is_never_truncated(self):
        assert looks_truncated("Ремонт мягкой кровли",
                               original="Ремонт мягкой кровли") is False


@pytest.mark.unit
class TestSubjectFromSummary:
    """Когда имя негодное, предмет закупки берётся из сводки — она есть
    в том же ответе модели и описывает суть."""

    def test_strips_lead_in_and_price_tail(self):
        assert subject_from_summary(
            "Тендер на выполнение капитального ремонта помещений "
            "с начальной ценой до 3 миллионов рублей."
        ) == "Выполнение капитального ремонта помещений"

    def test_strips_procurement_lead_in(self):
        assert subject_from_summary(
            "Закупка на поставку бумаги для офисной техники."
        ) == "Поставка бумаги для офисной техники"

    def test_keeps_plain_sentence(self):
        assert subject_from_summary(
            "Поставка снегоуборщика для нужд администрации."
        ) == "Поставка снегоуборщика для нужд администрации"

    def test_empty_gives_nothing(self):
        assert subject_from_summary("") is None
        assert subject_from_summary("   ") is None


@pytest.mark.unit
class TestResolverPrefersClearName:
    def test_truncated_ai_name_is_rejected_in_favour_of_summary(self):
        """Тендер 0137200001226007578 из боевого потока: имя процедуры
        негодное, ai_simple_name обрезан, а сводка описывает суть."""
        tender = {"name": "Электронный аукцион", "number": "0137200001226007578"}
        match_info = {
            "ai_simple_name": "Выполнение работ по текущему",
            "ai_summary": "Тендер на выполнение работ по ремонту актового зала.",
        }
        assert resolve_tender_name(tender, match_info) == \
            "Выполнение работ по ремонту актового зала"

    def test_good_raw_name_wins(self):
        tender = {"name": "Поставка перчаток хирургических", "number": "1"}
        assert resolve_tender_name(tender, {"ai_simple_name": "Перчатки"}) == \
            "Поставка перчаток хирургических"

    def test_never_returns_truncated_tail(self):
        """Если годного источника нет совсем — честный номер, а не огрызок."""
        tender = {"name": "Запрос котировок в электронной форме", "number": "123"}
        match_info = {"ai_simple_name": "Выполнение работ по"}
        assert resolve_tender_name(tender, match_info) == "Тендер №123"

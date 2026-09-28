"""Показ того, ПОЧЕМУ тендер попал в подборку.

8% уведомлений приходят с настоящим, но зонтичным именем — «Поставка
оборудования (25-ЭА-1-2-44-26-08)». Ключевое слово совпало внутри
спецификации, человек этого не видит и считает уведомление мусором
(диагноз 17.07.2026, замер 28.09.2026).
"""
import pytest

from tender_sniper.match_context import clean_keyword, match_snippet


@pytest.mark.unit
class TestCleanKeyword:
    def test_strips_markers(self):
        assert clean_keyword("Совпадение: 📌 бумага офисная") == "бумага офисная"
        assert clean_keyword("проектор (частичное)") == "проектор"
        assert clean_keyword("канцелярия (синоним: офис)") == "канцелярия"


@pytest.mark.unit
class TestMatchSnippet:
    def test_nothing_to_explain_when_keyword_is_in_the_name(self):
        assert match_snippet(["кондиционер"], "Поставка кондиционеров", "…") is None

    def test_shows_where_it_matched_in_the_description(self):
        snippet = match_snippet(
            ["пульсоксиметр"],
            "Поставка расходных материалов",
            "Лот 1: шприцы одноразовые, пульсоксиметр напальчный, бинты марлевые",
        )
        assert snippet is not None
        assert "пульсоксиметр" in snippet.lower()

    def test_word_forms_are_found(self):
        """Ключ «перчатки», в описании «перчаток» — это то же слово."""
        snippet = match_snippet(
            ["перчатки"],
            "Медицинские расходные материалы",
            "В составе поставки: 500 пар перчаток нитриловых, маски",
        )
        assert snippet is not None and "перчаток" in snippet.lower()

    def test_snippet_is_short(self):
        long_text = "прочее оборудование, " * 40 + "станок токарный, " + "иное, " * 40
        snippet = match_snippet(["станок"], "Поставка оборудования", long_text)
        assert snippet is not None
        assert len(snippet) <= 160, len(snippet)
        assert "станок" in snippet.lower()

    def test_html_is_stripped(self):
        snippet = match_snippet(
            ["насос"], "Поставка оборудования",
            "<p>Позиция 3: <b>насос</b> циркуляционный</p>")
        assert snippet is not None
        assert "<p>" not in snippet and "<b>насос</b>" not in snippet

    def test_no_description_no_snippet(self):
        assert match_snippet(["насос"], "Поставка оборудования", "") is None

    def test_keyword_missing_everywhere(self):
        assert match_snippet(["насос"], "Поставка мебели", "столы и стулья") is None

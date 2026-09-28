"""Частичное совпадение не должно ловить другое слово.

Матчер берёт корень как «ключевое слово минус два символа» и ищет его по
началу слова. Из-за этого «проектор» ловил «проект „Светлый путь"», а в
боевом потоке 13% уведомлений за неделю совпали ТОЛЬКО частично.

Правило: найденное слово не короче ключевого минус один символ —
словоформа «проекторы» длиннее «проектора», а самостоятельное слово
«проект» короче и потому не считается.
"""
import pytest

from tender_sniper.matching.smart_matcher import SmartMatcher


@pytest.fixture
def matcher():
    return SmartMatcher()


@pytest.mark.unit
class TestPartialMatch:
    def test_shorter_word_is_not_a_match(self, matcher):
        assert matcher._partial_match("проектор",
                                      'уличное освещение, проект "Светлый путь"') is False

    def test_word_forms_still_match(self, matcher):
        for text in ("поставка проекторов", "поставка проектора",
                     "проекторы для школы"):
            assert matcher._partial_match("проектор", text) is True, text

    def test_kadastr_case(self, matcher):
        """Из потока: «проектор» ловил кадастровые работы через «проект»."""
        assert matcher._partial_match(
            "проектор", "выполнение кадастровых работ, проектная документация") is False

    def test_short_keywords_are_not_partially_matched(self, matcher):
        """Для коротких слов корень слишком беден, чтобы что-то значить."""
        assert matcher._partial_match("нож", "ножницы канцелярские") is False

    def test_exact_word_matches(self, matcher):
        assert matcher._partial_match("снегоуборщик", "поставка снегоуборщика") is True

    def test_one_letter_apart_but_different_word(self, matcher):
        """«Проектов» и «проектор» расходятся одной буквой — правилом по
        длине их не развести, поэтому сравниваются начальные формы."""
        assert matcher._partial_match("проектор", "разработка проектов межевания") is False
        assert matcher._partial_match("проектор", "поставка проекторов") is True

    def test_adjective_is_not_the_noun(self, matcher):
        """Из потока: «автомобиль» ловил содержание автомобильных дорог."""
        assert matcher._partial_match(
            "автомобиль", "содержание автомобильных дорог") is False
        assert matcher._partial_match(
            "автомобиль", "поставка автомобиля скорой помощи") is True

    def test_unrelated_word_with_same_prefix(self, matcher):
        assert matcher._partial_match(
            "электропила", "медицинские изделия с электропитанием") is False

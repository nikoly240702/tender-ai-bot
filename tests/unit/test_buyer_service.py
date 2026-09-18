"""Цифровой закупщик: нормализация позиций, цены, сопоставление с каталогом."""
import pytest

from cabinet.buyer_service import (
    CATALOG_MATCH_THRESHOLD,
    build_query,
    cache_key,
    catalog_match_score,
    extract_positions,
    extract_price,
    normalize_position,
    tokenize,
)


@pytest.mark.unit
class TestNormalization:
    def test_word_order_does_not_matter(self):
        """В ТЗ порядок слов произвольный — иначе кэш не учился бы."""
        assert cache_key('бумага офисная А4') == cache_key('А4 офисная бумага')

    def test_grammatical_case_does_not_matter(self):
        """Главное требование владельца: повторяющаяся позиция не должна
        проходить весь цикл заново. В документации одна и та же позиция
        пишется в разных падежах."""
        assert cache_key('Поставка бумаги офисной А4') == cache_key('Бумага офисная А4')
        assert cache_key('ручки шариковые синие') == cache_key('ручка шариковая синяя')

    def test_filler_words_are_dropped(self):
        assert normalize_position('Поставка товара: бумага') == normalize_position('бумага')

    def test_short_words_are_not_over_stemmed(self):
        """У коротких слов усечение съело бы корень."""
        assert 'кран' in tokenize('кран шаровой')

    def test_digits_and_latin_survive(self):
        tokens = tokenize('бумага А4 80 г/м2 Xerox')
        assert 'а4' in tokens and '80' in tokens and 'xerox' in tokens

    def test_empty_input(self):
        assert normalize_position('') == ''
        assert tokenize('  ,,,  ') == []

    def test_fleeting_vowel_is_a_known_gap(self):
        """Документирует известное ограничение: «перчатки»/«перчаток» не
        схлопываются — для беглых гласных нужен словарь. Это не ошибка
        результата, а потерянная экономия на поиске."""
        assert cache_key('перчатки нитриловые') != cache_key('перчаток нитриловых')


@pytest.mark.unit
class TestExtractPrice:
    @pytest.mark.parametrize('text,expected', [
        ('Цена от 420 руб. за пачку', 420.0),
        ('1 250,50 ₽', 1250.5),
        ('стоимость 99 р.', 99.0),
        ('12 000 руб', 12000.0),
    ])
    def test_parses_prices(self, text, expected):
        assert extract_price(text) == expected

    def test_no_price_returns_none(self):
        assert extract_price('в наличии, доставка завтра') is None
        assert extract_price('') is None
        assert extract_price(None) is None

    def test_zero_is_not_a_price(self):
        assert extract_price('0 руб') is None


@pytest.mark.unit
class TestCatalogMatch:
    def test_exact_position_matches(self):
        score = catalog_match_score('бумага А4 80 г/м2',
                                    'Бумага офисная А4, 80 г/м2, белизна 146%')
        assert score >= CATALOG_MATCH_THRESHOLD

    def test_different_product_does_not_match(self):
        score = catalog_match_score('перчатки нитриловые',
                                    'Бумага офисная А4, 80 г/м2')
        assert score < CATALOG_MATCH_THRESHOLD

    def test_single_common_word_is_not_enough(self):
        """«Бумага туалетная» не должна совпасть с «бумага офисная А4» —
        ровно тот мусор, ради которого выставлен порог."""
        score = catalog_match_score('бумага туалетная двухслойная',
                                    'Бумага офисная А4 80 г/м2')
        assert score < CATALOG_MATCH_THRESHOLD

    def test_empty_sides(self):
        assert catalog_match_score('', 'бумага') == 0.0
        assert catalog_match_score('бумага', '') == 0.0


@pytest.mark.unit
class TestBuildQuery:
    def test_adds_buying_intent(self):
        """Без «купить» в выдачу лезут сами тендеры и нормативка."""
        q = build_query('Бумага офисная А4')
        assert 'купить' in q

    def test_long_position_is_trimmed(self):
        q = build_query(' '.join(f'слово{i}' for i in range(40)))
        assert len(q.split()) <= 12


@pytest.mark.unit
class TestExtractPositions:
    def test_reads_items_from_document_analysis(self):
        card = {'ai_analysis': {'fields': {
            'items_description': '1. Бумага А4 80 г/м2; 2. Ручка шариковая синяя'}}}
        assert extract_positions(card) == ['Бумага А4 80 г/м2', 'Ручка шариковая синяя']

    def test_falls_back_to_tender_name(self):
        """Разбора документации может не быть — искать по названию хуже,
        но лучше, чем не искать вовсе."""
        assert extract_positions({}, tender_name='Поставка бумаги') == ['Поставка бумаги']

    def test_placeholder_is_not_a_position(self):
        card = {'ai_analysis': {'fields': {'items_description': 'Не указано'}}}
        assert extract_positions(card, tender_name='Тендер') == ['Тендер']

    def test_duplicates_collapse(self):
        card = {'ai_analysis': {'fields': {
            'items_description': 'Бумага офисная А4; поставка бумаги офисной А4'}}}
        assert len(extract_positions(card)) == 1

    def test_limit_is_respected(self):
        items = '; '.join(f'Позиция номер {i} описание' for i in range(50))
        card = {'ai_analysis': {'fields': {'items_description': items}}}
        assert len(extract_positions(card, limit=15)) == 15

    def test_nothing_at_all(self):
        assert extract_positions({}, tender_name='') == []

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
    def test_intent_is_wholesale_not_retail(self):
        """Розничное намерение поднимает маркетплейсы, а их мы читать не
        можем — отвечают капчей. Замер 21.09.2026: «бахилы … купить
        цена» дал 73% маркетплейсов, «бахилы … оптом прайс» — 0%."""
        q = build_query('Бахилы водонепроницаемые')
        assert 'оптом' in q
        assert 'купить' not in q

    def test_long_position_is_trimmed(self):
        """Потолок считается ВМЕСТЕ со словами намерения: иначе их
        добавление незаметно удлиняет запрос сверх предела, а длинные
        запросы Яндекс отрабатывает хуже."""
        from cabinet.buyer_service import MAX_QUERY_WORDS
        q = build_query(' '.join(f'слово{i}' for i in range(40)))
        assert len(q.split()) == MAX_QUERY_WORDS

    def test_words_are_not_stemmed(self):
        """Усечение нужно ключу кэша, но не поисковой строке: замер
        21.09.2026 на закупке 0173300004226000004 дал «оперативн памят
        ddr4» и ноль результатов — такой строки нет ни на одной
        странице."""
        q = build_query('Ноутбук, оперативная память DDR4, накопитель SSD')
        assert 'оперативная' in q and 'память' in q
        assert 'оперативн' not in q.split() and 'памят' not in q.split()

    def test_numbers_survive_trimming(self):
        """Числа сужают выдачу до конкретной модели сильнее любого
        прилагательного, поэтому место под них резервируется до того,
        как бюджет разберут описательные слова."""
        q = build_query('Светильник светодиодный внутреннего освещения '
                        'настенно-потолочный квадратный рассеиватель '
                        'поликарбонат мощность 40 Вт длина 595 мм')
        assert '40' in q.split()
        assert '595' in q.split()

    def test_units_stay_with_their_number(self):
        """Голое число поисковику не говорит ничего: первый вариант
        резерва дал «... квадратный 40 595» вместо «40 вт 595 мм»."""
        q = build_query('Светильник светодиодный настенно-потолочный '
                        'квадратный рассеиватель поликарбонат '
                        'мощность 40 Вт длина 595 мм').split()
        assert q[q.index('40') + 1] == 'вт'
        assert q[q.index('595') + 1] == 'мм'

    def test_numbers_alone_do_not_crowd_out_the_noun(self):
        """Из одних чисел запрос тоже не ищется — поэтому резерв равен
        половине бюджета, а не всему."""
        q = build_query('Кабель 5 10 15 20 25 30 35 40 45 50 55 60')
        assert 'кабель' in q.split()


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


@pytest.mark.unit
class TestSpecVerification:
    """Проверяемость подбора: по каким характеристикам сошлось.

    Без построчной сверки «подходит» — утверждение без доказательства, и
    проверить его нельзя ни в вебе, ни в каталоге.
    """

    REQS = [
        {'name': 'материал', 'value': 'нитрил'},
        {'name': 'размер', 'value': 'M'},
    ]

    def test_confirms_matching_characteristics(self):
        from cabinet.buyer_service import check_against_text
        checks = {c['name']: c for c in check_against_text(
            self.REQS, 'Перчатки нитриловые смотровые размер M Голубой')}
        assert checks['материал']['ok'] is True
        assert checks['размер']['ok'] is True

    def test_flags_wrong_size_as_conflict(self):
        """Несовпадение размера должно быть видно как противоречие, а не
        как «не проверили»: под тендер на M нельзя предлагать XS."""
        from cabinet.buyer_service import check_against_text
        checks = {c['name']: c for c in check_against_text(
            self.REQS, 'Перчатки нитриловые смотровые размер XS')}
        assert checks['размер']['ok'] is False
        assert checks['размер']['found'] == 'xs'

    def test_material_matches_across_parts_of_speech(self):
        """Регрессия: требование пишут существительным («нитрил»), а
        описание — прилагательным («нитриловые»). Усечение даёт разные
        основы, и материал — самая важная характеристика — показывался
        как «не указано»."""
        from cabinet.buyer_service import check_against_text
        checks = {c['name']: c for c in check_against_text(
            [{'name': 'материал', 'value': 'нитрил'}], 'Перчатки нитриловые')}
        assert checks['материал']['ok'] is True

    def test_does_not_confuse_similar_roots(self):
        """«стол» не должен считаться корнем «столовой» — этот ложняк в
        проекте уже ловили на фильтрах."""
        from cabinet.buyer_service import _same_root
        assert _same_root('стол', 'столов') is False
        assert _same_root('нитрил', 'нитрилов') is True

    def test_unverifiable_is_not_reported_as_matching(self):
        """Характеристики нет в описании — это «не проверено», а не «сошлось»."""
        from cabinet.buyer_service import check_against_text
        checks = {c['name']: c for c in check_against_text(
            self.REQS, 'Перчатки смотровые размер M')}
        assert checks['материал']['ok'] is None

    def test_result_carries_audit_trail_fields(self):
        """Журнал перебора и требования должны доезжать до интерфейса,
        иначе проверить подбор нечем."""
        from cabinet.buyer_service import PositionResult
        d = PositionResult(position='тест').to_dict()
        assert 'considered' in d
        assert 'requirements' in d

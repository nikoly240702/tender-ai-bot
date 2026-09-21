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
        как бюджет разберут описательные слова.

        Резерв смотрит только на начало позиции: число, стоящее дальше,
        чем сама длина запроса, в названии товара уже не участвует — и
        «595» из хвоста сюда не попадает намеренно."""
        q = build_query('Светильник светодиодный внутреннего освещения '
                        'настенно-потолочный квадратный рассеиватель '
                        'поликарбонат мощность 40 Вт длина 595 мм')
        assert '40' in q.split() and 'вт' in q.split()

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


@pytest.mark.unit
class TestSilenceIsNotContradiction:
    """Каталог поставщика почти никогда не повторяет все строки ТЗ.

    Замер 21.09.2026 по закупке 0373100128326000093: отказы звучали как
    «материал не указан» и «не указаны характеристики товара» — ни один
    не был расхождением, и подбор вернул ноль предложений при том, что
    нужные светильники в выдаче были (1 179 ₽ и 3 117 ₽).
    """

    @staticmethod
    def _check(ok):
        from cabinet.offer_reader import SpecCheck
        return SpecCheck(name='материал', required='поликарбонат',
                         found='' if ok is None else 'поликарбонат', ok=ok)

    def test_unstated_requirement_is_not_a_contradiction(self):
        from cabinet.buyer_service import _is_contradicted
        assert _is_contradicted([self._check(None), self._check(True)]) is False

    def test_explicit_mismatch_is_a_contradiction(self):
        from cabinet.buyer_service import _is_contradicted
        assert _is_contradicted([self._check(True), self._check(False)]) is True

    def test_empty_checks_are_not_a_contradiction(self):
        from cabinet.buyer_service import _is_contradicted
        assert _is_contradicted([]) is False

    def test_reason_names_what_the_page_left_out(self):
        from cabinet.buyer_service import _unstated_reason
        assert 'материал' in _unstated_reason([self._check(None)])

    def test_unconfirmed_offers_rank_below_confirmed(self):
        """Неподтверждённые годятся как ориентир по рынку, но не как
        основание для ставки — значит, не выше подтверждённых."""
        from cabinet.buyer_service import Offer
        cheap_unconfirmed = Offer(title='a', url='a', price=100,
                                  unit_price=100, unconfirmed=True)
        pricey_confirmed = Offer(title='b', url='b', price=900, unit_price=900)
        ranked = sorted([cheap_unconfirmed, pricey_confirmed],
                        key=lambda o: (o.unconfirmed, o.unit_price is None,
                                       o.unit_price))
        assert ranked[0] is pricey_confirmed


@pytest.mark.unit
class TestReplyBudget:
    def test_budget_grows_with_requirements(self):
        """Плоские 250 токенов обрывали ответ тем вернее, чем подробнее
        позиция: на каждое требование модель пишет строку в checks.
        Замер 21.09.2026, 9 требований: при 250 оборвались все три
        страницы, при 900 разобрались все три."""
        from cabinet.offer_reader import _reply_budget
        assert _reply_budget(9) >= 900
        assert _reply_budget(9) > _reply_budget(1)

    def test_budget_is_capped(self):
        from cabinet.offer_reader import _reply_budget, _REPLY_CEILING
        assert _reply_budget(500) == _REPLY_CEILING


@pytest.mark.unit
class TestEmptyFoundIsNeverAMismatch:
    """Модель ставит ok=false при пустом found даже когда промпт это
    запрещает: замер 21.09.2026 по закупке 0373100128326000093 дал
    отказ «материал не указан» после прямого указания так не делать.
    Поэтому правило держит код, а не промпт."""

    @staticmethod
    def _parse(raw_checks, reqs):
        """Повторяет разбор checks из read_offer без сетевого вызова."""
        from cabinet.offer_reader import SpecCheck, _is_blank
        found_by_name = {str(c.get('name') or '').strip().lower(): c
                         for c in raw_checks}
        out = []
        for r in reqs:
            c = found_by_name.get(r['name'].strip().lower(), {})
            ok = c.get('ok')
            found = str(c.get('found') or '')[:80]
            if _is_blank(found):
                found, ok = '', None
            out.append(SpecCheck(name=r['name'], required=r['value'],
                                 found=found,
                                 ok=None if ok is None else bool(ok)))
        return out

    def test_empty_found_downgrades_false_to_unknown(self):
        from cabinet.buyer_service import _is_contradicted
        reqs = [{'name': 'материал', 'value': 'поликарбонат'}]
        checks = self._parse([{'name': 'материал', 'found': '', 'ok': False}], reqs)
        assert checks[0].ok is None
        assert _is_contradicted(checks) is False

    def test_stated_mismatch_still_rejects(self):
        from cabinet.buyer_service import _is_contradicted
        reqs = [{'name': 'материал', 'value': 'поликарбонат'}]
        checks = self._parse(
            [{'name': 'материал', 'found': 'сталь', 'ok': False}], reqs)
        assert checks[0].ok is False
        assert _is_contradicted(checks) is True

    def test_words_meaning_not_stated_count_as_blank(self):
        """Модель пишет отсутствие и словами: замер 21.09.2026 дал
        «материал не соответствует (поликарбонат vs. не указано)»."""
        from cabinet.buyer_service import _is_contradicted
        reqs = [{'name': 'материал', 'value': 'поликарбонат'}]
        checks = self._parse(
            [{'name': 'материал', 'found': 'не указано', 'ok': False}], reqs)
        assert checks[0].ok is None
        assert _is_contradicted(checks) is False


@pytest.mark.unit
class TestPositionSourcePriority:
    """Извещение точнее разбора ТЗ, а разбор — точнее названия тендера.

    Название тендера — это товарная категория: замер 21.09.2026 по
    закупке 0373100128326000093 дал по нему четыре разных товара от
    449 до 7 483 ₽ и ни одного нужного.
    """

    def test_notice_beats_ai_analysis(self):
        card = {
            'notice_positions': ['Светильник светодиодный, мощность 40 Вт'],
            'ai_analysis': {'fields': {'items_description': 'Светильник'}},
        }
        assert extract_positions(card, 'Поставка светильников') == [
            'Светильник светодиодный, мощность 40 Вт']

    def test_ai_analysis_used_when_notice_is_empty(self):
        """Пустой список значит «извещение прочитано, позиций нет» — это
        не повод молчать, если разбор ТЗ есть."""
        card = {'notice_positions': [],
                'ai_analysis': {'fields': {'items_description': 'Бумага А4'}}}
        assert extract_positions(card, 'Тендер') == ['Бумага А4']

    def test_tender_name_is_the_last_resort(self):
        assert extract_positions({'notice_positions': []},
                                 tender_name='Поставка бумаги') == ['Поставка бумаги']

    def test_notice_positions_respect_the_limit(self):
        card = {'notice_positions': [f'Позиция {i}' for i in range(40)]}
        assert len(extract_positions(card, limit=15)) == 15

    def test_range_words_do_not_reach_the_query(self):
        """Магазин пишет «40 Вт», а не «до 40 Вт» — служебные слова
        границ занимают место в запросе и ничего не находят."""
        q = build_query('Светильник светодиодный, мощность до 40 Вт').split()
        assert 'до' not in q
        assert '40' in q and 'вт' in q

    def test_numbers_are_taken_from_the_head_of_the_position(self):
        """Позиция длиннее запроса, и первым в ней идёт то, чем товар
        называют. Без окна резерв хватал числа из хвоста: «индекс
        цветопередачи 80 и менее 90» вытеснял «поликарбонат»."""
        q = build_query('Светильник светодиодный 40 Вт поликарбонат, '
                        'индекс цветопередачи не менее 80 и менее 90, '
                        'длина не менее 500 и менее 600 мм').split()
        assert 'поликарбонат' in q
        assert '500' not in q and '600' not in q

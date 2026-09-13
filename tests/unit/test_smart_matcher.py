"""
Unit тесты для SmartMatcher (scoring система).

Тестируем:
- Базовое совпадение по ключевым словам
- Частичное совпадение (корень слова)
- Синонимы
- Исключающие фильтры (exclude_keywords)
- Ценовые фильтры (price_min/price_max)
- Региональные фильтры
- Scoring (нормализация 0-100)
- Batch matching
"""

import pytest
import json
from datetime import datetime, timedelta
from tender_sniper.matching.smart_matcher import SmartMatcher


@pytest.fixture
def matcher():
    """Фикстура для SmartMatcher."""
    return SmartMatcher()


@pytest.fixture
def sample_tender():
    """Пример тендера для тестов."""
    return {
        'number': '0123456789',
        'name': 'Поставка компьютерного оборудования',
        'description': 'Поставка ноутбуков Dell и персональных компьютеров для офиса',
        'price': 2500000,
        'region': 'Москва',
        'purchase_type': 'товары',
        'customer_name': 'ООО "Тестовая компания"',
        'published_datetime': datetime.now().isoformat(),
        'url': '/purchase/123456'
    }


@pytest.fixture
def sample_filter():
    """Пример фильтра для тестов."""
    return {
        'id': 1,
        'name': 'IT оборудование',
        'keywords': json.dumps(['компьютер', 'ноутбук'], ensure_ascii=False),
        'exclude_keywords': json.dumps([], ensure_ascii=False),
        'price_min': 1000000,
        'price_max': 5000000,
        'regions': json.dumps(['Москва', 'Московская область'], ensure_ascii=False),
        'tender_types': json.dumps(['товары'], ensure_ascii=False)
    }


@pytest.mark.unit
class TestKeywordMatching:
    """Тесты совпадения по ключевым словам."""

    def test_exact_match(self, matcher, sample_tender, sample_filter):
        """Точное совпадение ключевого слова."""
        result = matcher.match_tender(sample_tender, sample_filter)

        assert result is not None
        assert result['score'] > 0
        assert result['filter_id'] == 1
        assert len(result['matched_keywords']) > 0

    def test_case_insensitive(self, matcher, sample_tender, sample_filter):
        """Регистронезависимый поиск."""
        # Изменяем регистр в фильтре
        sample_filter['keywords'] = json.dumps(['КОМПЬЮТЕР', 'НОУТБУК'], ensure_ascii=False)
        result = matcher.match_tender(sample_tender, sample_filter)

        assert result is not None
        assert result['score'] > 0

    def test_multiple_keywords(self, matcher, sample_tender, sample_filter):
        """Множественные ключевые слова увеличивают score."""
        # Фильтр с одним ключевым словом
        filter_one = sample_filter.copy()
        filter_one['keywords'] = json.dumps(['компьютер'], ensure_ascii=False)
        result_one = matcher.match_tender(sample_tender, filter_one)

        # Фильтр с двумя ключевыми словами
        filter_two = sample_filter.copy()
        filter_two['keywords'] = json.dumps(['компьютер', 'ноутбук'], ensure_ascii=False)
        result_two = matcher.match_tender(sample_tender, filter_two)

        assert result_two['score'] > result_one['score']

    def test_partial_match(self, matcher, sample_tender, sample_filter):
        """Частичное совпадение (корень слова)."""
        sample_filter['keywords'] = json.dumps(['компьют'], ensure_ascii=False)
        result = matcher.match_tender(sample_tender, sample_filter)

        assert result is not None
        assert result['score'] > 0

    def test_synonyms(self, matcher, sample_tender, sample_filter):
        """Поиск синонимов."""
        # В тендере есть "ноутбук", ищем по синониму из словаря
        sample_filter['keywords'] = json.dumps(['компьютер'], ensure_ascii=False)
        result = matcher.match_tender(sample_tender, sample_filter)

        # Должен найти через синонимы
        assert result is not None

    def test_no_keywords_match(self, matcher, sample_tender, sample_filter):
        """Без совпадений по ключевым словам возвращает None (улучшена релевантность)."""
        sample_filter['keywords'] = json.dumps(['медицина', 'больница'], ensure_ascii=False)
        result = matcher.match_tender(sample_tender, sample_filter)

        # ИЗМЕНЕНО: Теперь без совпадений тендер не включается
        # Это предотвращает нерелевантные результаты
        assert result is None

    def test_empty_keywords(self, matcher, sample_tender, sample_filter):
        """Фильтр без ключевых слов возвращает None (некорректный фильтр)."""
        sample_filter['keywords'] = json.dumps([], ensure_ascii=False)
        result = matcher.match_tender(sample_tender, sample_filter)

        # ИЗМЕНЕНО: Фильтр без ключевых слов некорректен
        assert result is None


@pytest.mark.unit
class TestExcludeKeywords:
    """Тесты исключающих ключевых слов."""

    def test_exclude_keyword_blocks(self, matcher, sample_tender, sample_filter):
        """Исключающее слово блокирует тендер."""
        sample_filter['exclude_keywords'] = json.dumps(['Dell'], ensure_ascii=False)
        result = matcher.match_tender(sample_tender, sample_filter)

        assert result is None

    def test_exclude_case_insensitive(self, matcher, sample_tender, sample_filter):
        """Исключение работает независимо от регистра."""
        sample_filter['exclude_keywords'] = json.dumps(['DELL'], ensure_ascii=False)
        result = matcher.match_tender(sample_tender, sample_filter)

        assert result is None

    def test_multiple_exclude_keywords(self, matcher, sample_tender, sample_filter):
        """Любое исключающее слово блокирует."""
        sample_filter['exclude_keywords'] = json.dumps(['HP', 'Dell', 'Lenovo'], ensure_ascii=False)
        result = matcher.match_tender(sample_tender, sample_filter)

        assert result is None

    def test_no_exclude_match(self, matcher, sample_tender, sample_filter):
        """Исключающее слово не найдено - тендер проходит."""
        sample_filter['exclude_keywords'] = json.dumps(['б/у', 'ремонт'], ensure_ascii=False)
        result = matcher.match_tender(sample_tender, sample_filter)

        assert result is not None


@pytest.mark.unit
class TestPriceFilters:
    """Тесты ценовых фильтров."""

    def test_price_in_range(self, matcher, sample_tender, sample_filter):
        """Цена в диапазоне."""
        sample_filter['price_min'] = 2000000
        sample_filter['price_max'] = 3000000
        result = matcher.match_tender(sample_tender, sample_filter)

        assert result is not None

    def test_price_too_low(self, matcher, sample_tender, sample_filter):
        """Цена ниже минимума."""
        sample_filter['price_min'] = 3000000
        result = matcher.match_tender(sample_tender, sample_filter)

        assert result is None

    def test_price_too_high(self, matcher, sample_tender, sample_filter):
        """Цена выше максимума."""
        sample_filter['price_max'] = 2000000
        result = matcher.match_tender(sample_tender, sample_filter)

        assert result is None

    def test_price_bonus_middle(self, matcher, sample_tender, sample_filter):
        """Бонус за цену в середине диапазона."""
        # Цена точно в середине
        sample_tender['price'] = 3000000
        sample_filter['price_min'] = 2000000
        sample_filter['price_max'] = 4000000

        result = matcher.match_tender(sample_tender, sample_filter)
        assert result is not None
        score_middle = result['score']

        # Цена на краю диапазона
        sample_tender['price'] = 2000000
        result_edge = matcher.match_tender(sample_tender, sample_filter)
        score_edge = result_edge['score']

        assert score_middle > score_edge

    def test_no_price_limits(self, matcher, sample_tender, sample_filter):
        """Без ценовых ограничений."""
        sample_filter['price_min'] = None
        sample_filter['price_max'] = None
        result = matcher.match_tender(sample_tender, sample_filter)

        assert result is not None

    def test_tender_without_price(self, matcher, sample_filter):
        """Тендер без цены."""
        tender_no_price = {
            'number': '999',
            'name': 'Поставка компьютеров и ноутбуков',  # Должны совпасть ключевые слова
            'price': None
        }

        result = matcher.match_tender(tender_no_price, sample_filter)
        # Не отклоняется, но и бонуса за цену нет
        assert result is not None


@pytest.mark.unit
class TestRegionFilters:
    """Тесты региональных фильтров."""

    def test_region_match(self, matcher, sample_tender, sample_filter):
        """Регион совпадает."""
        sample_filter['regions'] = json.dumps(['Москва'], ensure_ascii=False)
        result = matcher.match_tender(sample_tender, sample_filter)

        assert result is not None

    def test_region_partial_match(self, matcher, sample_tender, sample_filter):
        """Частичное совпадение региона."""
        sample_tender['region'] = 'Московская область'
        sample_filter['regions'] = json.dumps(['Москва'], ensure_ascii=False)
        result = matcher.match_tender(sample_tender, sample_filter)

        # Должен найти "Москва" в "Московская область"
        assert result is not None

    def test_region_no_match(self, matcher, sample_tender, sample_filter):
        """Регион не совпадает (но не блокирует)."""
        sample_tender['region'] = 'Санкт-Петербург'
        sample_filter['regions'] = json.dumps(['Москва'], ensure_ascii=False)
        result = matcher.match_tender(sample_tender, sample_filter)

        # В коде закомментирован return None - не блокирует
        assert result is not None

    def test_no_region_in_tender(self, matcher, sample_tender, sample_filter):
        """Тендер без региона."""
        sample_tender['region'] = ''
        result = matcher.match_tender(sample_tender, sample_filter)

        assert result is not None

    def test_empty_regions_filter(self, matcher, sample_tender, sample_filter):
        """Без региональных фильтров."""
        sample_filter['regions'] = json.dumps([], ensure_ascii=False)
        result = matcher.match_tender(sample_tender, sample_filter)

        assert result is not None


@pytest.mark.unit
class TestScoring:
    """Тесты системы scoring."""

    def test_score_range(self, matcher, sample_tender, sample_filter):
        """Score в диапазоне 0-100."""
        result = matcher.match_tender(sample_tender, sample_filter)

        assert result is not None
        assert 0 <= result['score'] <= 100

    def test_recent_tender_bonus(self, matcher, sample_tender, sample_filter):
        """Бонус за свежий тендер."""
        # Тендер опубликован сегодня
        sample_tender['published_datetime'] = datetime.now().isoformat()
        result_today = matcher.match_tender(sample_tender, sample_filter)

        # Тендер опубликован месяц назад
        old_date = (datetime.now() - timedelta(days=30)).isoformat()
        sample_tender['published_datetime'] = old_date
        result_old = matcher.match_tender(sample_tender, sample_filter)

        assert result_today['score'] > result_old['score']

    def test_score_normalization(self, matcher, sample_tender, sample_filter):
        """Нормализация score (не превышает 100)."""
        # Добавляем много ключевых слов для высокого score
        sample_filter['keywords'] = json.dumps(
            ['компьютер', 'ноутбук', 'оборудование', 'поставка', 'Dell'],
            ensure_ascii=False
        )
        sample_tender['published_datetime'] = datetime.now().isoformat()

        result = matcher.match_tender(sample_tender, sample_filter)

        assert result['score'] <= 100

    def test_score_comparison(self, matcher, sample_tender):
        """Более релевантный тендер имеет выше score."""
        # Фильтр 1: два ключевых слова совпадают
        filter_high = {
            'id': 1,
            'name': 'IT оборудование',
            'keywords': json.dumps(['компьютер', 'ноутбук'], ensure_ascii=False),
            'exclude_keywords': json.dumps([], ensure_ascii=False)
        }

        # Фильтр 2: одно ключевое слово совпадает
        filter_low = {
            'id': 2,
            'name': 'Только компьютеры',
            'keywords': json.dumps(['компьютер'], ensure_ascii=False),
            'exclude_keywords': json.dumps([], ensure_ascii=False)
        }

        result_high = matcher.match_tender(sample_tender, filter_high)
        result_low = matcher.match_tender(sample_tender, filter_low)

        assert result_high['score'] > result_low['score']


@pytest.mark.unit
class TestMatchResult:
    """Тесты структуры результата matching."""

    def test_result_structure(self, matcher, sample_tender, sample_filter):
        """Проверка полей в результате."""
        result = matcher.match_tender(sample_tender, sample_filter)

        assert result is not None
        assert 'filter_id' in result
        assert 'filter_name' in result
        assert 'score' in result
        assert 'matched_keywords' in result
        assert 'matched_at' in result
        assert 'tender_number' in result
        assert 'tender_name' in result
        assert 'tender_price' in result
        assert 'tender_url' in result

    def test_matched_keywords_list(self, matcher, sample_tender, sample_filter):
        """matched_keywords - это список строк."""
        result = matcher.match_tender(sample_tender, sample_filter)

        assert isinstance(result['matched_keywords'], list)
        assert all(isinstance(kw, str) for kw in result['matched_keywords'])


@pytest.mark.unit
class TestBatchMatching:
    """Тесты пакетной обработки."""

    def test_match_against_filters(self, matcher, sample_tender):
        """Проверка одного тендера против нескольких фильтров."""
        filters = [
            {
                'id': 1,
                'name': 'IT',
                'keywords': json.dumps(['компьютер'], ensure_ascii=False),
                'exclude_keywords': json.dumps([], ensure_ascii=False)
            },
            {
                'id': 2,
                'name': 'Офис',
                'keywords': json.dumps(['оборудование'], ensure_ascii=False),
                'exclude_keywords': json.dumps([], ensure_ascii=False)
            }
        ]

        matches = matcher.match_against_filters(sample_tender, filters, min_score=0)

        assert len(matches) > 0
        # Результаты отсортированы по score (убывание)
        if len(matches) > 1:
            assert matches[0]['score'] >= matches[1]['score']

    def test_min_score_filter(self, matcher, sample_tender):
        """Фильтрация по минимальному score."""
        filters = [
            {
                'id': 1,
                'name': 'Высокий score',
                'keywords': json.dumps(['компьютер', 'ноутбук'], ensure_ascii=False),
                'exclude_keywords': json.dumps([], ensure_ascii=False)
            },
            {
                'id': 2,
                'name': 'Средний score',
                'keywords': json.dumps(['оборудование'], ensure_ascii=False),  # Есть в sample_tender
                'exclude_keywords': json.dumps([], ensure_ascii=False)
            }
        ]

        matches_all = matcher.match_against_filters(sample_tender, filters, min_score=0)
        matches_filtered = matcher.match_against_filters(sample_tender, filters, min_score=50)

        assert len(matches_all) >= len(matches_filtered)

    def test_batch_match(self, matcher):
        """Пакетная обработка нескольких тендеров."""
        tenders = [
            {
                'number': '111',
                'name': 'Поставка компьютеров',
                'description': ''
            },
            {
                'number': '222',
                'name': 'Поставка медицинского оборудования',
                'description': ''
            }
        ]

        filters = [
            {
                'id': 1,
                'name': 'IT',
                'keywords': json.dumps(['компьютер'], ensure_ascii=False),
                'exclude_keywords': json.dumps([], ensure_ascii=False)
            }
        ]

        results = matcher.batch_match(tenders, filters, min_score=0)

        assert isinstance(results, dict)
        assert '111' in results  # Тендер 111 должен совпасть
        # Тендер 222 может не совпасть (медицина)


@pytest.mark.unit
class TestStatistics:
    """Тесты статистики matching."""

    def test_stats_increment(self, matcher, sample_tender, sample_filter):
        """Статистика обновляется."""
        initial_stats = matcher.get_stats()
        initial_total = initial_stats['total_matches']

        matcher.match_tender(sample_tender, sample_filter)

        updated_stats = matcher.get_stats()
        assert updated_stats['total_matches'] == initial_total + 1

    def test_stats_categories(self, matcher, sample_tender):
        """Статистика по категориям score."""
        # Высокий score
        filter_high = {
            'id': 1,
            'name': 'High',
            'keywords': json.dumps(['компьютер', 'ноутбук'], ensure_ascii=False),
            'exclude_keywords': json.dumps([], ensure_ascii=False)
        }

        # Средний score
        filter_medium = {
            'id': 2,
            'name': 'Medium',
            'keywords': json.dumps(['оборудование'], ensure_ascii=False),  # Есть в sample_tender
            'exclude_keywords': json.dumps([], ensure_ascii=False)
        }

        matcher.match_tender(sample_tender, filter_high)
        matcher.match_tender(sample_tender, filter_medium)

        stats = matcher.get_stats()

        assert stats['total_matches'] >= 2
        assert 'high_score_matches' in stats
        assert 'medium_score_matches' in stats
        assert 'low_score_matches' in stats


@pytest.mark.unit
class TestParseJsonField:
    """Тесты парсинга JSON полей."""

    def test_parse_json_string(self, matcher):
        """Парсинг JSON строки."""
        json_str = json.dumps(['keyword1', 'keyword2'], ensure_ascii=False)
        result = matcher._parse_json_field(json_str)

        assert result == ['keyword1', 'keyword2']

    def test_parse_list(self, matcher):
        """Парсинг уже распарсенного списка."""
        lst = ['keyword1', 'keyword2']
        result = matcher._parse_json_field(lst)

        assert result == lst

    def test_parse_invalid_json(self, matcher):
        """Парсинг невалидного JSON."""
        invalid = "not a json"
        result = matcher._parse_json_field(invalid)

        assert result == []

    def test_parse_none(self, matcher):
        """Парсинг None."""
        result = matcher._parse_json_field(None)

        assert result == []


@pytest.mark.unit
class TestEdgeCases:
    """Тесты граничных случаев."""

    def test_empty_tender_name(self, matcher, sample_filter):
        """Тендер с пустым названием."""
        tender = {
            'number': '999',
            'name': '',
            'description': 'Поставка компьютеров'
        }

        result = matcher.match_tender(tender, sample_filter)
        # Поиск в description тоже работает
        assert result is not None

    def test_missing_fields(self, matcher, sample_filter):
        """Тендер с отсутствующими полями."""
        tender = {
            'number': '999',
            'name': 'Поставка компьютеров'
            # Нет description, price, region и т.д.
        }

        result = matcher.match_tender(tender, sample_filter)
        # Не должно падать
        assert result is not None

    def test_special_characters_in_keywords(self, matcher, sample_tender):
        """Спецсимволы в ключевых словах."""
        filter_special = {
            'id': 1,
            'name': 'Special',
            'keywords': json.dumps(['компьютер!', 'ноутбук?'], ensure_ascii=False),
            'exclude_keywords': json.dumps([], ensure_ascii=False)
        }

        result = matcher.match_tender(sample_tender, filter_special)
        # Не должно падать
        assert result is not None

    def test_very_long_keyword(self, matcher, sample_tender, sample_filter):
        """Очень длинное ключевое слово."""
        long_keyword = 'a' * 1000
        sample_filter['keywords'] = json.dumps([long_keyword], ensure_ascii=False)

        result = matcher.match_tender(sample_tender, sample_filter)
        # Не должно падать
        assert result is not None

    def test_unicode_in_tender(self, matcher, sample_filter):
        """Unicode символы в тендере."""
        tender = {
            'number': '999',
            'name': 'Поставка 🖥️ компьютеров ✅',
            'description': 'Тендер с эмодзи'
        }

        result = matcher.match_tender(tender, sample_filter)
        # Не должно падать
        assert result is not None


@pytest.mark.unit
class TestWordBoundaries:
    """ТЗ §4.2/Этап 3.3: короткие ключевики (<4 симв.) не должны совпадать
    как подстрока другого слова — ни слева, ни справа."""

    @pytest.mark.parametrize('keyword,tender_name', [
        ('кран', 'Поставка запчастей для экрана телевизора'),
        ('мат', 'Печать в формате А4'),
        ('моп', 'Поставка компота фруктового'),
    ])
    def test_short_keyword_does_not_match_inside_longer_word(
        self, matcher, sample_filter, keyword, tender_name,
    ):
        sample_filter['keywords'] = json.dumps([keyword], ensure_ascii=False)
        sample_filter['exclude_keywords'] = json.dumps([], ensure_ascii=False)
        tender = {
            'number': '5', 'name': tender_name,
            'description': '', 'price': 2000000, 'region': 'Москва',
        }
        result = matcher.match_tender(tender, sample_filter)
        assert result is None

    def test_short_keyword_matches_as_whole_word(self, matcher, sample_filter):
        """Контрольный случай: то же короткое слово матчится, когда оно
        реально встречается как отдельное слово."""
        sample_filter['keywords'] = json.dumps(['кран'], ensure_ascii=False)
        sample_filter['exclude_keywords'] = json.dumps([], ensure_ascii=False)
        tender = {
            'number': '6', 'name': 'Поставка крана шарового для водопровода',
            'description': '', 'price': 2000000, 'region': 'Москва',
        }
        result = matcher.match_tender(tender, sample_filter)
        assert result is not None


@pytest.mark.unit
class TestYoNormalization:
    """ё/е нормализация — filters_v2.yaml требует, чтобы ключевик с «ё» и
    текст извещения без неё (и наоборот) считались одним и тем же словом."""

    def test_keyword_with_yo_matches_text_without_yo(self, matcher, sample_filter):
        """Ключевик «шуруповёрт» находит «шуруповерт» в тексте извещения
        (так пишут в подавляющем большинстве реальных извещений)."""
        sample_filter['keywords'] = json.dumps(['шуруповёрт'], ensure_ascii=False)
        sample_filter['exclude_keywords'] = json.dumps([], ensure_ascii=False)
        tender = {
            'number': '1', 'name': 'Поставка шуруповертов аккумуляторных',
            'description': '', 'price': 2000000, 'region': 'Москва',
        }
        result = matcher.match_tender(tender, sample_filter)
        assert result is not None

    def test_keyword_without_yo_matches_text_with_yo(self, matcher, sample_filter):
        """И наоборот: ключевик без «ё» находит текст, где она есть."""
        sample_filter['keywords'] = json.dumps(['счетчик'], ensure_ascii=False)
        sample_filter['exclude_keywords'] = json.dumps([], ensure_ascii=False)
        tender = {
            'number': '2', 'name': 'Поставка счётчиков электроэнергии',
            'description': '', 'price': 2000000, 'region': 'Москва',
        }
        result = matcher.match_tender(tender, sample_filter)
        assert result is not None


@pytest.mark.unit
class TestKeywordWinsOverExclusion:
    """Исключение не должно гасить ключевик, частью которого оно является
    (filters_v2.yaml §4.2, keyword_wins_over_exclusion)."""

    def test_exclusion_substring_of_matching_keyword_is_rescued(self, matcher, sample_filter):
        """«монтажная пена» матчится, несмотря на исключение «монтаж»,
        потому что «монтаж» — часть сработавшего ключевика."""
        sample_filter['keywords'] = json.dumps(['монтажная пена'], ensure_ascii=False)
        sample_filter['exclude_keywords'] = json.dumps(['монтаж'], ensure_ascii=False)
        tender = {
            'number': '3', 'name': 'Поставка товара: монтажная пена строительная',
            'description': '', 'price': 2000000, 'region': 'Москва',
        }
        result = matcher.match_tender(tender, sample_filter)
        assert result is not None

    def test_exclusion_not_part_of_any_keyword_still_blocks(self, matcher, sample_filter):
        """Контрольный случай: то же исключение «монтаж» без «спасающего»
        ключевика продолжает работать как раньше."""
        sample_filter['keywords'] = json.dumps(['компьютер'], ensure_ascii=False)
        sample_filter['exclude_keywords'] = json.dumps(['монтаж'], ensure_ascii=False)
        tender = {
            'number': '4', 'name': 'Монтаж компьютерного оборудования',
            'description': '', 'price': 2000000, 'region': 'Москва',
        }
        result = matcher.match_tender(tender, sample_filter)
        assert result is None

"""
Unit тесты для модуля regions.py

Тестируем:
- Поиск регионов (точное совпадение, алиасы, fuzzy matching)
- Парсинг множественных регионов через запятую
- Получение регионов по ФО
- Определение ФО по региону
"""

import pytest
from tender_sniper.regions import (
    normalize_region_name,
    find_region,
    parse_regions_input,
    get_regions_by_district,
    get_district_by_region,
    get_all_federal_districts,
    format_regions_list,
    FEDERAL_DISTRICTS,
    ALL_REGIONS
)


@pytest.mark.unit
class TestNormalizeRegionName:
    """Тесты нормализации названий регионов."""

    def test_lowercase_conversion(self):
        assert normalize_region_name("МОСКВА") == "москва"
        assert normalize_region_name("Санкт-Петербург") == "санкт-петербург"

    def test_whitespace_removal(self):
        assert normalize_region_name("  Москва   ") == "москва"
        assert normalize_region_name("Московская  область") == "московская область"

    def test_abbreviation_normalization(self):
        # Точки не удаляются полностью, только нормализуются без замены на пробелы
        result = normalize_region_name("Москов. обл.")
        assert "москов" in result and "обл" in result
        assert normalize_region_name("г. Москва") == "г москва"


@pytest.mark.unit
class TestFindRegion:
    """Тесты поиска регионов."""

    def test_exact_match(self):
        """Точное совпадение с официальным названием."""
        assert find_region("Москва") == "Москва"
        assert find_region("москва") == "Москва"
        assert find_region("Санкт-Петербург") == "Санкт-Петербург"

    def test_alias_matching(self):
        """Распознавание алиасов."""
        assert find_region("мск") == "Москва"
        assert find_region("МСК") == "Москва"
        assert find_region("спб") == "Санкт-Петербург"
        assert find_region("питер") == "Санкт-Петербург"
        assert find_region("хмао") == "Ханты-Мансийский автономный округ — Югра"
        assert find_region("екатеринбург") == "Свердловская область"

    def test_fuzzy_matching(self):
        """Нечеткое совпадение."""
        assert find_region("Нижний Новгород") == "Нижегородская область"
        assert find_region("краснодар") == "Краснодарский край"
        assert find_region("новосибирск") == "Новосибирская область"

    def test_invalid_region(self):
        """Несуществующий регион."""
        assert find_region("Invalid Region") is None
        assert find_region("Абракадабра") is None
        assert find_region("") is None

    def test_partial_match(self):
        """Частичное совпадение."""
        # "Московская область" найдёт точное совпадение
        result = find_region("московская область")
        assert result == "Московская область"


@pytest.mark.unit
class TestParseRegionsInput:
    """Тесты парсинга множественных регионов."""

    def test_single_region(self):
        """Один регион."""
        recognized, unrecognized = parse_regions_input("Москва")
        assert recognized == ["Москва"]
        assert unrecognized == []

    def test_multiple_regions(self):
        """Несколько регионов через запятую."""
        recognized, unrecognized = parse_regions_input("москва, спб, краснодар")
        assert len(recognized) == 3
        assert "Москва" in recognized
        assert "Санкт-Петербург" in recognized
        assert "Краснодарский край" in recognized
        assert unrecognized == []

    def test_mixed_valid_invalid(self):
        """Смесь валидных и невалидных регионов."""
        recognized, unrecognized = parse_regions_input("москва, InvalidRegion, спб")
        assert len(recognized) == 2
        assert "Москва" in recognized
        assert "Санкт-Петербург" in recognized
        assert "InvalidRegion" in unrecognized

    def test_empty_input(self):
        """Пустой ввод."""
        recognized, unrecognized = parse_regions_input("")
        assert recognized == []
        assert unrecognized == []

        recognized, unrecognized = parse_regions_input("   ")
        assert recognized == []
        assert unrecognized == []

    def test_whitespace_handling(self):
        """Обработка пробелов."""
        recognized, unrecognized = parse_regions_input("  москва  ,  спб  ,  краснодар  ")
        assert len(recognized) == 3

    def test_duplicate_regions(self):
        """Дубликаты регионов."""
        recognized, unrecognized = parse_regions_input("москва, мск, Москва")
        # Все три варианта должны распознаться как Москва
        assert "Москва" in recognized


@pytest.mark.unit
class TestGetRegionsByDistrict:
    """Тесты получения регионов по ФО."""

    def test_valid_district(self):
        """Валидный ФО."""
        regions = get_regions_by_district("Центральный")
        assert len(regions) > 0
        assert "Москва" in regions
        assert "Московская область" in regions

    def test_all_districts(self):
        """Проверка всех 8 ФО."""
        for district_name in FEDERAL_DISTRICTS.keys():
            regions = get_regions_by_district(district_name)
            assert len(regions) > 0

    def test_invalid_district(self):
        """Несуществующий ФО."""
        regions = get_regions_by_district("Invalid District")
        assert regions == []


@pytest.mark.unit
class TestGetDistrictByRegion:
    """Тесты определения ФО по региону."""

    def test_valid_region(self):
        """Валидный регион."""
        assert get_district_by_region("Москва") == "Центральный"
        assert get_district_by_region("Санкт-Петербург") == "Северо-Западный"
        assert get_district_by_region("Краснодарский край") == "Южный"

    def test_case_insensitive(self):
        """Регистронезависимость."""
        assert get_district_by_region("москва") == "Центральный"
        assert get_district_by_region("МОСКВА") == "Центральный"

    def test_invalid_region(self):
        """Несуществующий регион."""
        assert get_district_by_region("Invalid Region") is None


@pytest.mark.unit
class TestGetAllFederalDistricts:
    """Тесты получения списка всех ФО."""

    def test_count(self):
        """Должно быть 8 ФО."""
        districts = get_all_federal_districts()
        assert len(districts) == 8

    def test_structure(self):
        """Проверка структуры данных."""
        districts = get_all_federal_districts()
        for district in districts:
            assert "name" in district
            assert "code" in district
            assert "regions_count" in district
            assert district["regions_count"] > 0

    def test_codes(self):
        """Проверка кодов ФО."""
        districts = get_all_federal_districts()
        codes = [d["code"] for d in districts]
        assert "ЦФО" in codes
        assert "СЗФО" in codes
        assert "ЮФО" in codes


@pytest.mark.unit
class TestFormatRegionsList:
    """Тесты форматирования списка регионов."""

    def test_empty_list(self):
        """Пустой список."""
        assert format_regions_list([]) == "Не указаны"

    def test_short_list(self):
        """Короткий список (<=5)."""
        regions = ["Москва", "Санкт-Петербург", "Краснодарский край"]
        result = format_regions_list(regions)
        assert "Москва" in result
        assert "Санкт-Петербург" in result
        assert "Краснодарский край" in result
        assert "и еще" not in result

    def test_long_list(self):
        """Длинный список (>5)."""
        regions = ["Регион " + str(i) for i in range(10)]
        result = format_regions_list(regions, max_display=5)
        assert "и еще 5" in result

    def test_custom_max_display(self):
        """Кастомное количество для отображения."""
        regions = ["Регион " + str(i) for i in range(10)]
        result = format_regions_list(regions, max_display=3)
        assert "и еще 7" in result


@pytest.mark.unit
class TestDataIntegrity:
    """Тесты целостности данных."""

    def test_all_regions_unique(self):
        """Все регионы должны быть уникальными."""
        assert len(ALL_REGIONS) == len(set(ALL_REGIONS))

    def test_total_regions_count(self):
        """Проверка общего количества регионов (должно быть ~89)."""
        assert 85 <= len(ALL_REGIONS) <= 90

    def test_all_districts_have_regions(self):
        """У всех ФО должны быть регионы."""
        for district_name, district_data in FEDERAL_DISTRICTS.items():
            assert len(district_data["regions"]) > 0

    def test_no_duplicate_regions_across_districts(self):
        """Регионы не должны дублироваться между ФО."""
        all_regions_from_districts = []
        for district_data in FEDERAL_DISTRICTS.values():
            all_regions_from_districts.extend(district_data["regions"])

        assert len(all_regions_from_districts) == len(set(all_regions_from_districts))

    def test_region_to_district_mapping_complete(self):
        """Все регионы должны иметь ФО."""
        for region in ALL_REGIONS:
            district = get_district_by_region(region)
            assert district is not None
            assert district in FEDERAL_DISTRICTS


@pytest.mark.unit
class TestEdgeCases:
    """Тесты граничных случаев."""

    def test_region_with_special_characters(self):
        """Регион со специальными символами."""
        # Республика Северная Осетия — Алания (em dash)
        result = find_region("Республика Северная Осетия — Алания")
        assert result is not None

    def test_very_long_input(self):
        """Очень длинный ввод."""
        long_input = ", ".join(["Москва"] * 100)
        recognized, unrecognized = parse_regions_input(long_input)
        assert len(recognized) >= 1

    def test_special_characters_in_input(self):
        """Специальные символы во вводе."""
        recognized, _ = parse_regions_input("Москва; Санкт-Петербург")
        # Только запятая как разделитель, точка с запятой не работает
        assert len(recognized) == 1  # Только "Москва" распознается

    def test_numbers_in_input(self):
        """Цифры во вводе."""
        recognized, unrecognized = parse_regions_input("Москва, 12345, Санкт-Петербург")
        assert len(recognized) == 2
        assert "12345" in unrecognized

"""
Транслитератор для конвертации латинских названий брендов в кириллицу.
Необходим для работы с zakupki.gov.ru RSS API, который не поддерживает латиницу.
"""

import re
from typing import Dict, List, Optional


class Transliterator:
    """Транслитератор латинских названий в кириллицу."""

    # Словарь известных брендов (латиница → кириллица)
    BRAND_DICT = {
        # Компрессоры и пневматическое оборудование
        'atlas copco': 'Атлас Копко',
        'atlas': 'Атлас',
        'copco': 'Копко',

        # Электрооборудование
        'siemens': 'Сименс',
        'schneider electric': 'Шнейдер Электрик',
        'schneider': 'Шнейдер',
        'abb': 'АББ',
        'legrand': 'Легранд',
        'iek': 'ИЭК',

        # Инструменты
        'bosch': 'Бош',
        'makita': 'Макита',
        'dewalt': 'Деволт',
        'hilti': 'Хилти',
        'metabo': 'Метабо',

        # Климатическое оборудование
        'danfoss': 'Данфосс',
        'daikin': 'Дайкин',
        'mitsubishi': 'Митсубиси',
        'fujitsu': 'Фуджитсу',

        # Насосное оборудование
        'grundfos': 'Грундфос',
        'wilo': 'Вило',
        'ebara': 'Эбара',

        # Автоматизация
        'honeywell': 'Хонейвелл',
        'omron': 'Омрон',
        'yokogawa': 'Йокогава',

        # IT оборудование
        'cisco': 'Циско',
        'hewlett packard': 'Хьюлетт Паккард',
        'hp': 'ХП',
        'dell': 'Делл',
        'ibm': 'АйБиЭм',
        'lenovo': 'Леново',

        # Автомобили и транспорт
        'mercedes': 'Мерседес',
        'volkswagen': 'Фольксваген',
        'toyota': 'Тойота',
        'volvo': 'Вольво',
        'scania': 'Скания',
        'man': 'МАН',

        # Медицинское оборудование
        'philips': 'Филипс',
        'ge healthcare': 'Джи И Хелскеа',
        'mindray': 'Миндрей',

        # Другие известные бренды
        'caterpillar': 'Катерпиллер',
        'cat': 'Кат',
        'komatsu': 'Комацу',
        'hitachi': 'Хитачи',
        'samsung': 'Самсунг',
        'lg': 'Эл Джи',
    }

    # Таблица транслитерации по буквам (для неизвестных слов)
    CHAR_MAP = {
        'a': 'а', 'b': 'б', 'v': 'в', 'g': 'г', 'd': 'д',
        'e': 'е', 'yo': 'ё', 'zh': 'ж', 'z': 'з', 'i': 'и',
        'y': 'й', 'k': 'к', 'l': 'л', 'm': 'м', 'n': 'н',
        'o': 'о', 'p': 'п', 'r': 'р', 's': 'с', 't': 'т',
        'u': 'у', 'f': 'ф', 'h': 'х', 'ts': 'ц', 'ch': 'ч',
        'sh': 'ш', 'sch': 'щ', 'yu': 'ю', 'ya': 'я',
        'w': 'в', 'x': 'кс', 'j': 'дж', 'c': 'к', 'q': 'к'
    }

    @classmethod
    def transliterate(cls, text: str) -> str:
        """
        Транслитерирует текст из латиницы в кириллицу.

        Args:
            text: Текст для транслитерации

        Returns:
            Транслитерированный текст
        """
        if not text:
            return text

        # Сохраняем оригинальный регистр первого символа
        original_text = text
        text_lower = text.lower()

        # Сначала ищем известные бренды
        for brand_lat, brand_cyr in cls.BRAND_DICT.items():
            if brand_lat in text_lower:
                # Заменяем с учетом регистра
                text = cls._replace_case_insensitive(text, brand_lat, brand_cyr)

        return text

    @classmethod
    def _replace_case_insensitive(cls, text: str, old: str, new: str) -> str:
        """Замена подстроки без учета регистра."""
        pattern = re.compile(re.escape(old), re.IGNORECASE)
        return pattern.sub(new, text)

    @classmethod
    def has_latin(cls, text: str) -> bool:
        """
        Проверяет, содержит ли текст латинские символы.

        Args:
            text: Текст для проверки

        Returns:
            True если содержит латиницу
        """
        return bool(re.search(r'[a-zA-Z]', text))

    @classmethod
    def has_cyrillic(cls, text: str) -> bool:
        """
        Проверяет, содержит ли текст кириллические символы.

        Args:
            text: Текст для проверки

        Returns:
            True если содержит кириллицу
        """
        return bool(re.search(r'[а-яА-ЯёЁ]', text))

    @classmethod
    def generate_variants(cls, query: str) -> List[str]:
        """
        Генерирует варианты запроса (латиница + кириллица).

        Args:
            query: Исходный запрос

        Returns:
            Список вариантов (включая оригинал и транслитерацию)
        """
        variants = [query]  # Оригинал всегда первый

        if cls.has_latin(query):
            # Добавляем транслитерированный вариант
            transliterated = cls.transliterate(query)
            if transliterated != query and transliterated not in variants:
                variants.append(transliterated)

        return variants

    @classmethod
    def get_brand_info(cls, query: str) -> Optional[Dict[str, str]]:
        """
        Получает информацию о бренде из запроса.

        Args:
            query: Поисковый запрос

        Returns:
            Словарь с информацией о бренде или None
        """
        query_lower = query.lower()

        for brand_lat, brand_cyr in cls.BRAND_DICT.items():
            if brand_lat in query_lower:
                return {
                    'latin': brand_lat,
                    'cyrillic': brand_cyr,
                    'found_in': query
                }

        return None


def main():
    """Тестирование транслитератора."""
    print("="*80)
    print("ТЕСТ ТРАНСЛИТЕРАТОРА")
    print("="*80)

    test_queries = [
        "Atlas Copco",
        "компрессоры Atlas Copco",
        "Siemens автоматика",
        "Bosch перфоратор",
        "насосы Grundfos",
        "Cisco роутер",
        "уже на русском",
        "mixed латиница and кириллица"
    ]

    trans = Transliterator()

    for query in test_queries:
        print(f"\n📝 Запрос: '{query}'")
        print(f"   Есть латиница: {trans.has_latin(query)}")
        print(f"   Есть кириллица: {trans.has_cyrillic(query)}")

        transliterated = trans.transliterate(query)
        print(f"   Транслитерация: '{transliterated}'")

        variants = trans.generate_variants(query)
        print(f"   Варианты ({len(variants)}):")
        for i, variant in enumerate(variants, 1):
            print(f"      {i}. {variant}")

        brand_info = trans.get_brand_info(query)
        if brand_info:
            print(f"   Бренд найден: {brand_info['latin']} → {brand_info['cyrillic']}")

    print("\n" + "="*80)
    print("✅ ТЕСТ ЗАВЕРШЕН")
    print("="*80)


if __name__ == "__main__":
    main()

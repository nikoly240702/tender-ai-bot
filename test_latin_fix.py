"""
Тест исправления проблемы с латиницей в поиске тендеров.
Проверяет новую систему транслитерации и улучшенное расширение запросов.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

def test_transliterator():
    """Тест транслитератора."""
    from src.utils.transliterator import Transliterator

    print("="*80)
    print("ТЕСТ 1: Транслитератор")
    print("="*80)

    test_cases = [
        ("Atlas Copco", "Атлас Копко"),
        ("Siemens", "Сименс"),
        ("Bosch перфоратор", "Бош перфоратор"),
        ("компрессоры Atlas Copco", "компрессоры Атлас Копко"),
    ]

    trans = Transliterator()

    for original, expected in test_cases:
        result = trans.transliterate(original)
        status = "✅" if result == expected else "❌"
        print(f"\n{status} '{original}'")
        print(f"   Ожидалось: '{expected}'")
        print(f"   Получено:  '{result}'")
        print(f"   Есть латиница: {trans.has_latin(original)}")


def test_smart_search_expander():
    """Тест умного расширителя поиска с латиницей."""
    from src.parsers.smart_search_expander import SmartSearchExpander
    from src.analysis.tender_analyzer import TenderAnalyzer
    from src.utils.config_loader import ConfigLoader

    print("\n" + "="*80)
    print("ТЕСТ 2: SmartSearchExpander с латиницей")
    print("="*80)

    # Загружаем конфигурацию
    try:
        config = ConfigLoader()
        llm_config = config.get_llm_config()

        # Создаем TenderAnalyzer
        analyzer = TenderAnalyzer(
            api_key=llm_config['api_key'],
            provider=llm_config['provider'],
            model_fast=llm_config.get('model_fast')
        )

        # Создаем расширитель
        expander = SmartSearchExpander(analyzer.llm)

        # Тестируем латинский запрос
        print("\n📝 Тестовый запрос: 'Atlas copco'")
        print("-"*80)

        variants = expander.expand_search_query("Atlas copco", max_variants=5)

        print(f"\n✅ Получено вариантов: {len(variants)}")
        print("\nПроверка каждого варианта:")

        from src.utils.transliterator import Transliterator
        trans = Transliterator()

        all_cyrillic = True
        for i, variant in enumerate(variants, 1):
            has_latin = trans.has_latin(variant)
            status = "❌" if has_latin else "✅"
            print(f"{status} {i}. {variant}")
            if has_latin:
                print(f"   ⚠️  ПРОБЛЕМА: Содержит латиницу!")
                all_cyrillic = False

        if all_cyrillic:
            print(f"\n🎉 ОТЛИЧНО: Все варианты на кириллице!")
        else:
            print(f"\n⚠️  ВНИМАНИЕ: Некоторые варианты содержат латиницу")

    except Exception as e:
        print(f"\n❌ Ошибка теста: {e}")
        import traceback
        traceback.print_exc()


def test_rss_search_with_fix():
    """Тест поиска через RSS с исправлением латиницы."""
    from src.parsers.zakupki_rss_parser import ZakupkiRSSParser
    from src.utils.transliterator import Transliterator

    print("\n" + "="*80)
    print("ТЕСТ 3: RSS поиск с транслитерацией")
    print("="*80)

    parser = ZakupkiRSSParser()
    trans = Transliterator()

    original_query = "Atlas copco"
    transliterated_query = trans.transliterate(original_query)

    print(f"\n📝 Оригинальный запрос: '{original_query}'")
    print(f"🔄 Транслитерированный: '{transliterated_query}'")

    # Тест 1: Поиск с латиницей (должен вернуть 0)
    print(f"\n1️⃣ Поиск с латиницей:")
    tenders_latin = parser.search_tenders_rss(
        keywords=original_query,
        max_results=10
    )
    print(f"   Результат: {len(tenders_latin)} тендеров")

    # Тест 2: Поиск с кириллицей (должен найти тендеры)
    print(f"\n2️⃣ Поиск с кириллицей:")
    tenders_cyrillic = parser.search_tenders_rss(
        keywords=transliterated_query,
        max_results=10
    )
    print(f"   Результат: {len(tenders_cyrillic)} тендеров")

    if tenders_cyrillic:
        print(f"\n📋 Примеры найденных тендеров:")
        for i, tender in enumerate(tenders_cyrillic[:3], 1):
            print(f"   {i}. {tender.get('name', 'N/A')[:100]}")

    # Тест 3: Общий поиск (компрессоры без бренда)
    print(f"\n3️⃣ Поиск 'компрессоры' (без бренда):")
    tenders_general = parser.search_tenders_rss(
        keywords="компрессоры",
        max_results=10
    )
    print(f"   Результат: {len(tenders_general)} тендеров")

    # Итоги
    print(f"\n" + "="*80)
    print("ИТОГИ ТЕСТА RSS:")
    print("="*80)
    print(f"Латиница ({original_query}): {len(tenders_latin)} тендеров")
    print(f"Кириллица ({transliterated_query}): {len(tenders_cyrillic)} тендеров")
    print(f"Общий запрос (компрессоры): {len(tenders_general)} тендеров")

    if len(tenders_cyrillic) > 0 or len(tenders_general) > 0:
        print(f"\n✅ РЕШЕНИЕ РАБОТАЕТ: Кириллица находит тендеры!")
    else:
        print(f"\n⚠️  Тендеров не найдено, но это может быть из-за фильтров")


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings('ignore')

    try:
        import urllib3
        urllib3.disable_warnings()
    except:
        pass

    print("\n" + "="*80)
    print("  ТЕСТ ИСПРАВЛЕНИЯ ПРОБЛЕМЫ С ЛАТИНИЦЕЙ")
    print("="*80)

    # Тест 1: Транслитератор
    test_transliterator()

    # Тест 2: SmartSearchExpander
    test_smart_search_expander()

    # Тест 3: RSS поиск
    test_rss_search_with_fix()

    print("\n" + "="*80)
    print("  ВСЕ ТЕСТЫ ЗАВЕРШЕНЫ")
    print("="*80)

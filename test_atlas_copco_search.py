"""
Тест поиска тендеров по запросу "Atlas Copco".
Проверяет почему RSS возвращает 0 результатов.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from src.parsers.zakupki_rss_parser import ZakupkiRSSParser
import requests
from urllib.parse import urlencode, quote_plus

def test_direct_url():
    """Тестируем прямой URL RSS."""
    print("="*80)
    print("ТЕСТ 1: Проверка прямого URL RSS")
    print("="*80)

    # Формируем URL точно так же, как в коде
    params = {
        'morphology': 'on',
        'search-filter': 'Дате размещения',
        'sortDirection': 'false',
        'sortBy': 'UPDATE_DATE',
        'fz44': 'on',
        'fz223': 'on',
        'af': 'on',
        'currencyIdGeneral': '-1',
        'searchString': 'Atlas copco',  # Оригинальный запрос
        'priceFromGeneral': '1000000',
        'priceToGeneral': '3000000'
    }

    query_string = urlencode(params, quote_via=quote_plus)
    rss_url = f"https://zakupki.gov.ru/epz/order/extendedsearch/rss.html?{query_string}"

    print(f"\n📡 URL: {rss_url}\n")

    try:
        response = requests.get(rss_url, timeout=30, verify=False)
        print(f"✅ Статус: {response.status_code}")
        print(f"📊 Размер ответа: {len(response.content)} байт")

        # Проверяем содержимое
        content = response.text

        if '<item>' in content:
            item_count = content.count('<item>')
            print(f"✅ Найдено тендеров в RSS: {item_count}")
        else:
            print("⚠️  RSS не содержит тендеров (<item> не найден)")

        # Проверяем на ошибки
        if 'error' in content.lower() or 'ошибка' in content.lower():
            print("❌ В RSS найдены упоминания об ошибке")

        # Показываем первые 500 символов
        print(f"\n📄 Первые 500 символов ответа:")
        print("-"*80)
        print(content[:500])
        print("-"*80)

    except Exception as e:
        print(f"❌ Ошибка: {e}")


def test_without_price_filter():
    """Тестируем без фильтра по цене."""
    print("\n" + "="*80)
    print("ТЕСТ 2: Поиск БЕЗ фильтра по цене")
    print("="*80)

    params = {
        'morphology': 'on',
        'search-filter': 'Дате размещения',
        'sortDirection': 'false',
        'sortBy': 'UPDATE_DATE',
        'fz44': 'on',
        'fz223': 'on',
        'af': 'on',
        'currencyIdGeneral': '-1',
        'searchString': 'Atlas copco'
    }

    query_string = urlencode(params, quote_via=quote_plus)
    rss_url = f"https://zakupki.gov.ru/epz/order/extendedsearch/rss.html?{query_string}"

    print(f"\n📡 URL: {rss_url}\n")

    try:
        response = requests.get(rss_url, timeout=30, verify=False)
        content = response.text

        if '<item>' in content:
            item_count = content.count('<item>')
            print(f"✅ Найдено тендеров в RSS: {item_count}")
        else:
            print("⚠️  RSS не содержит тендеров")

    except Exception as e:
        print(f"❌ Ошибка: {e}")


def test_simplified_query():
    """Тестируем упрощенный запрос."""
    print("\n" + "="*80)
    print("ТЕСТ 3: Упрощенный запрос (только 'компрессор')")
    print("="*80)

    params = {
        'morphology': 'on',
        'search-filter': 'Дате размещения',
        'sortDirection': 'false',
        'sortBy': 'UPDATE_DATE',
        'fz44': 'on',
        'fz223': 'on',
        'af': 'on',
        'currencyIdGeneral': '-1',
        'searchString': 'компрессор'
    }

    query_string = urlencode(params, quote_via=quote_plus)
    rss_url = f"https://zakupki.gov.ru/epz/order/extendedsearch/rss.html?{query_string}"

    print(f"\n📡 URL: {rss_url}\n")

    try:
        response = requests.get(rss_url, timeout=30, verify=False)
        content = response.text

        if '<item>' in content:
            item_count = content.count('<item>')
            print(f"✅ Найдено тендеров в RSS: {item_count}")

            # Показываем несколько заголовков
            import re
            titles = re.findall(r'<title>(.*?)</title>', content)
            if len(titles) > 1:  # Первый title - это заголовок канала
                print(f"\n📋 Примеры найденных тендеров:")
                for i, title in enumerate(titles[1:6], 1):  # Пропускаем заголовок канала
                    print(f"   {i}. {title}")
        else:
            print("⚠️  RSS не содержит тендеров")

    except Exception as e:
        print(f"❌ Ошибка: {e}")


def test_using_parser():
    """Тестируем через класс парсера."""
    print("\n" + "="*80)
    print("ТЕСТ 4: Использование ZakupkiRSSParser")
    print("="*80)

    parser = ZakupkiRSSParser()

    # Тест 1: С фильтрами по цене
    print("\n🔍 Поиск с фильтром по цене (1-3 млн):")
    tenders = parser.search_tenders_rss(
        keywords='Atlas copco',
        price_min=1000000,
        price_max=3000000,
        max_results=10
    )
    print(f"   Результат: {len(tenders)} тендеров")

    # Тест 2: Без фильтра по цене
    print("\n🔍 Поиск БЕЗ фильтра по цене:")
    tenders = parser.search_tenders_rss(
        keywords='Atlas copco',
        max_results=10
    )
    print(f"   Результат: {len(tenders)} тендеров")

    if tenders:
        print(f"\n📋 Примеры найденных тендеров:")
        for i, tender in enumerate(tenders[:3], 1):
            print(f"   {i}. {tender.get('name', 'N/A')[:100]}")
            print(f"      Номер: {tender.get('number', 'N/A')}")
            print(f"      Цена: {tender.get('price_formatted', 'N/A')}")

    # Тест 3: Просто "компрессор"
    print("\n🔍 Поиск 'компрессор' (без Atlas Copco):")
    tenders = parser.search_tenders_rss(
        keywords='компрессор',
        max_results=10
    )
    print(f"   Результат: {len(tenders)} тендеров")


def test_web_interface():
    """Проверяем, есть ли вообще тендеры через веб-интерфейс."""
    print("\n" + "="*80)
    print("ТЕСТ 5: Проверка через веб-интерфейс (HTML)")
    print("="*80)

    # URL для обычного поиска (не RSS)
    params = {
        'morphology': 'on',
        'search-filter': 'Дате размещения',
        'pageNumber': '1',
        'sortDirection': 'false',
        'recordsPerPage': '_10',
        'sortBy': 'UPDATE_DATE',
        'fz44': 'on',
        'fz223': 'on',
        'af': 'on',
        'currencyIdGeneral': '-1',
        'searchString': 'Atlas copco'
    }

    query_string = urlencode(params, quote_via=quote_plus)
    search_url = f"https://zakupki.gov.ru/epz/order/extendedsearch/results.html?{query_string}"

    print(f"\n🌐 URL: {search_url}\n")

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }
        response = requests.get(search_url, headers=headers, timeout=30, verify=False)
        content = response.text

        print(f"✅ Статус: {response.status_code}")

        # Проверяем количество результатов
        import re

        # Ищем "Найдено X записей"
        match = re.search(r'Найдено.*?(\d+).*?запис', content, re.IGNORECASE)
        if match:
            count = match.group(1)
            print(f"✅ Найдено тендеров через веб: {count}")
        else:
            print("⚠️  Не удалось определить количество результатов")

        # Ищем записи о тендерах
        if 'registry-entry__header-top__title' in content or 'search-registry-entry-block' in content:
            print("✅ В HTML найдены записи о тендерах")
        else:
            print("⚠️  В HTML не найдены записи о тендерах")

    except Exception as e:
        print(f"❌ Ошибка: {e}")


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings('ignore')

    try:
        import urllib3
        urllib3.disable_warnings()
    except:
        pass

    print("\n" + "="*80)
    print("  ДИАГНОСТИКА ПОИСКА 'ATLAS COPCO' НА ZAKUPKI.GOV.RU")
    print("="*80)

    test_direct_url()
    test_without_price_filter()
    test_simplified_query()
    test_using_parser()
    test_web_interface()

    print("\n" + "="*80)
    print("  ДИАГНОСТИКА ЗАВЕРШЕНА")
    print("="*80)

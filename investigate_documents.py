#!/usr/bin/env python3
"""
Исследование структуры документов на zakupki.gov.ru.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

import requests
from bs4 import BeautifulSoup
import warnings

warnings.filterwarnings('ignore')
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except:
    pass


def investigate_tender_page(tender_url):
    """Детально исследует страницу тендера."""

    full_url = f"https://zakupki.gov.ru{tender_url}"

    print(f"\n{'='*70}")
    print(f"ИССЛЕДОВАНИЕ ТЕНДЕРА")
    print(f"{'='*70}")
    print(f"URL: {full_url}\n")

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })

    try:
        response = session.get(full_url, timeout=30, verify=False)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')

        # Ищем все ссылки на файлы
        print("📄 ВСЕ ССЫЛКИ НА ФАЙЛЫ:\n")

        all_links = soup.find_all('a', href=True)
        doc_links = []

        for link in all_links:
            href = link.get('href', '')
            text = link.get_text(strip=True)

            # Ищем ссылки на документы
            if any(ext in href.lower() for ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip', '.rar']):
                doc_links.append({
                    'href': href,
                    'text': text
                })
                print(f"   ✅ {text}")
                print(f"      URL: {href[:80]}...")
                print()

        if not doc_links:
            print("   ❌ Файлы документов не найдены через прямые ссылки")

        print(f"\n{'─'*70}")
        print("🔍 ПОИСК СЕКЦИИ 'ДОКУМЕНТЫ':\n")

        # Ищем секции с документами
        doc_sections = soup.find_all(['div', 'section', 'table'],
                                     class_=lambda x: x and 'doc' in x.lower())

        for i, section in enumerate(doc_sections[:5], 1):
            print(f"Секция {i}:")
            print(f"   Класс: {section.get('class')}")
            print(f"   Текст: {section.get_text()[:100]}...")
            print()

        print(f"\n{'─'*70}")
        print("🔍 ПОИСК ПО КЛЮЧЕВЫМ СЛОВАМ:\n")

        keywords = ['Документация', 'документы', 'Техническое задание', 'Проект контракта', 'Извещение']

        for keyword in keywords:
            elements = soup.find_all(text=lambda t: t and keyword in t)
            if elements:
                print(f"   ✅ Найдено '{keyword}': {len(elements)} упоминаний")
                # Показываем контекст
                for elem in elements[:2]:
                    parent = elem.parent
                    if parent:
                        print(f"      Контекст: {str(parent)[:150]}...")
            else:
                print(f"   ❌ '{keyword}' не найдено")

        print(f"\n{'─'*70}")
        print("🔍 IFRAME И ВЛОЖЕННЫЕ СТРАНИЦЫ:\n")

        iframes = soup.find_all('iframe')
        if iframes:
            print(f"   ✅ Найдено {len(iframes)} iframe(s)")
            for i, iframe in enumerate(iframes, 1):
                src = iframe.get('src', '')
                print(f"   {i}. {src}")
        else:
            print("   ❌ iframe не найдены")

        print(f"\n{'─'*70}")
        print("📋 ТАБЛИЦЫ С ДАННЫМИ:\n")

        tables = soup.find_all('table')
        print(f"   Найдено таблиц: {len(tables)}")

        for i, table in enumerate(tables[:3], 1):
            print(f"\nТаблица {i}:")
            rows = table.find_all('tr')
            print(f"   Строк: {len(rows)}")
            if rows:
                for row in rows[:3]:
                    cells = row.find_all(['td', 'th'])
                    if cells:
                        text = ' | '.join(c.get_text(strip=True) for c in cells)
                        print(f"      {text[:80]}...")

        # Сохраняем HTML для детального изучения
        debug_file = Path(__file__).parent / 'output' / 'debug_tender_page.html'
        debug_file.parent.mkdir(parents=True, exist_ok=True)

        with open(debug_file, 'w', encoding='utf-8') as f:
            f.write(soup.prettify())

        print(f"\n{'='*70}")
        print(f"💾 Полный HTML сохранен: {debug_file}")
        print(f"{'='*70}\n")

        return doc_links

    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return []


def main():
    print("\n" + "="*70)
    print("  ИССЛЕДОВАНИЕ ДОСТУПА К ДОКУМЕНТАМ")
    print("="*70 + "\n")

    # Используем один из найденных тендеров
    test_tenders = [
        "/epz/order/notice/zk20/view/common-info.html?regNumber=0322200027425000278",
        "/epz/order/notice/ea20/view/common-info.html?regNumber=0352100025025000104",
    ]

    for tender_url in test_tenders:
        doc_links = investigate_tender_page(tender_url)

        if doc_links:
            print(f"\n✅ Найдено {len(doc_links)} документов")
        else:
            print(f"\n⚠️  Документы не найдены или требуется авторизация")

        print("\n" + "="*70 + "\n")

        # Исследуем только первый тендер для начала
        break


if __name__ == "__main__":
    main()

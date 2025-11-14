#!/usr/bin/env python3
"""
Проверка вкладки "Документы" на zakupki.gov.ru.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

import requests
from bs4 import BeautifulSoup
import warnings
import re

warnings.filterwarnings('ignore')
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except:
    pass


def check_documents_tab(tender_number, tender_type):
    """
    Проверяет вкладку документов.

    tender_type: zk20 (223-ФЗ котировки), ea20 (электронные аукционы), etc.
    """

    # Формируем URL вкладки документов
    docs_url = f"https://zakupki.gov.ru/epz/order/notice/{tender_type}/view/documents.html?regNumber={tender_number}"

    print(f"\n{'='*70}")
    print(f"ПРОВЕРКА ВКЛАДКИ 'ДОКУМЕНТЫ'")
    print(f"{'='*70}")
    print(f"Тендер: {tender_number}")
    print(f"URL: {docs_url}\n")

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })

    try:
        response = session.get(docs_url, timeout=30, verify=False)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')

        # Ищем все ссылки на документы
        print("📄 НАЙДЕННЫЕ ДОКУМЕНТЫ:\n")

        doc_links = []

        # Метод 1: Ищем все ссылки на файлы
        all_links = soup.find_all('a', href=True)

        for link in all_links:
            href = link.get('href', '')
            text = link.get_text(strip=True)

            # Фильтруем только документы
            if any(ext in href.lower() for ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip', '.rar', '.rtf']):
                # Пропускаем служебные файлы
                if 'zakupki-traffic' not in href and 'cookie' not in href.lower():
                    doc_links.append({
                        'href': href,
                        'text': text,
                        'type': 'file'
                    })
                    print(f"   ✅ {text}")
                    print(f"      URL: {href}")
                    print()

        # Метод 2: Ищем специфичную структуру zakupki.gov.ru
        doc_containers = soup.find_all(['div', 'table'],
                                       class_=lambda x: x and ('document' in str(x).lower() or 'attach' in str(x).lower()))

        print(f"\n{'─'*70}")
        print(f"📦 КОНТЕЙНЕРЫ ДОКУМЕНТОВ: {len(doc_containers)}\n")

        for i, container in enumerate(doc_containers[:5], 1):
            print(f"Контейнер {i}:")
            print(f"   Tag: {container.name}")
            print(f"   Class: {container.get('class')}")

            # Ищем ссылки внутри контейнера
            links = container.find_all('a', href=True)
            if links:
                print(f"   Ссылок внутри: {len(links)}")
                for link in links[:3]:
                    print(f"      - {link.get_text(strip=True)}: {link.get('href')[:60]}...")
            print()

        # Метод 3: Поиск по структуре таблиц с документами
        print(f"\n{'─'*70}")
        print("📋 ТАБЛИЦЫ С ДОКУМЕНТАМИ:\n")

        tables = soup.find_all('table')
        for i, table in enumerate(tables, 1):
            # Проверяем, есть ли в таблице ссылки на файлы
            table_links = table.find_all('a', href=True)
            file_links = [l for l in table_links if any(ext in l.get('href', '').lower()
                         for ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx'])]

            if file_links:
                print(f"Таблица {i} (содержит {len(file_links)} документов):")
                for link in file_links[:5]:
                    text = link.get_text(strip=True)
                    href = link.get('href')
                    print(f"   - {text}")
                    print(f"     {href}")
                print()

        # Проверяем, требуется ли авторизация
        print(f"\n{'─'*70}")
        print("🔐 ПРОВЕРКА ТРЕБОВАНИЯ АВТОРИЗАЦИИ:\n")

        auth_indicators = [
            'авториз', 'войти', 'вход', 'login', 'auth',
            'зарегистр', 'личный кабинет', 'доступ ограничен'
        ]

        page_text = soup.get_text().lower()
        auth_required = any(indicator in page_text for indicator in auth_indicators)

        if auth_required:
            print("   ⚠️  Возможно требуется авторизация")
            # Ищем точные сообщения
            for indicator in auth_indicators:
                if indicator in page_text:
                    # Находим контекст
                    idx = page_text.find(indicator)
                    context = page_text[max(0, idx-50):min(len(page_text), idx+50)]
                    print(f"      Найдено: '{indicator}' - контекст: ...{context}...")
        else:
            print("   ✅ Признаки требования авторизации не найдены")

        # Сохраняем HTML
        debug_file = Path(__file__).parent / 'output' / f'debug_documents_tab_{tender_number}.html'
        debug_file.parent.mkdir(parents=True, exist_ok=True)

        with open(debug_file, 'w', encoding='utf-8') as f:
            f.write(soup.prettify())

        print(f"\n{'='*70}")
        print(f"💾 HTML сохранен: {debug_file}")
        print(f"{'='*70}\n")

        return doc_links

    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return []


def main():
    print("\n" + "="*70)
    print("  ПРОВЕРКА ДОСТУПА К ВКЛАДКЕ 'ДОКУМЕНТЫ'")
    print("="*70 + "\n")

    # Тестируем на разных типах тендеров
    test_cases = [
        ("0322200027425000278", "zk20"),  # Запрос котировок 223-ФЗ
        ("0352100025025000104", "ea20"),  # Электронный аукцион
    ]

    for tender_number, tender_type in test_cases:
        doc_links = check_documents_tab(tender_number, tender_type)

        if doc_links:
            print(f"\n✅ Найдено {len(doc_links)} документов для скачивания")
        else:
            print(f"\n⚠️  Документы не найдены")
            print(f"💡 Возможные причины:")
            print(f"   - Документы еще не загружены заказчиком")
            print(f"   - Требуется авторизация на zakupki.gov.ru")
            print(f"   - Документы находятся в защищенном разделе")

        print("\n" + "="*70 + "\n")


if __name__ == "__main__":
    main()

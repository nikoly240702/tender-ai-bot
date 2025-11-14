#!/usr/bin/env python3
"""
Тестовый скрипт для проверки интегрированной системы.
Тестирует поиск и скачивание документов без полного анализа.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from parsers.zakupki_enhanced_parser import ZakupkiEnhancedParser
from parsers.smart_search_expander import SmartSearchExpander
from parsers.zakupki_document_downloader import ZakupkiDocumentDownloader
from analyzers.tender_analyzer import TenderAnalyzer
from utils.config_loader import ConfigLoader
from datetime import datetime


def test_search_only():
    """Тест 1: Только поиск тендеров."""
    print("\n" + "="*70)
    print("  ТЕСТ 1: ПОИСК ТЕНДЕРОВ")
    print("="*70 + "\n")

    # Инициализация
    config = ConfigLoader()
    llm_config = config.get_llm_config()

    tender_analyzer = TenderAnalyzer(
        api_key=llm_config['api_key'],
        provider=llm_config['provider'],
        model_fast=llm_config.get('model_fast')
    )

    search_expander = SmartSearchExpander(tender_analyzer.llm)
    enhanced_parser = ZakupkiEnhancedParser(tender_analyzer.llm)

    # Поиск
    search_query = "компьютерное оборудование"
    print(f"🔍 Поисковый запрос: '{search_query}'")

    # Расширяем запрос
    expanded_queries = search_expander.expand_search_query(search_query, max_variants=3)

    # Ищем тендеры
    all_tenders = []
    seen_numbers = set()

    for query in expanded_queries:
        print(f"\n🔎 Поиск по: '{query}'")
        tenders = enhanced_parser.search_with_details(
            keywords=query,
            price_min=500000,
            price_max=3000000,
            max_results=2,
            extract_details=False
        )

        for tender in tenders:
            number = tender.get('number')
            if number and number not in seen_numbers:
                seen_numbers.add(number)
                all_tenders.append(tender)

    print(f"\n✅ Найдено уникальных тендеров: {len(all_tenders)}")

    # Выводим результаты
    for i, tender in enumerate(all_tenders, 1):
        print(f"\n{i}. {tender.get('number')}")
        print(f"   {tender.get('name', 'N/A')[:70]}...")
        print(f"   💰 {tender.get('price_formatted', 'N/A')}")
        print(f"   📍 {tender.get('region', 'N/A')}")
        print(f"   ⏰ Срок подачи: {tender.get('submission_deadline', 'N/A')}")
        print(f"   🏆 Определение победителя: {tender.get('winner_determination_date', 'N/A')}")

    return all_tenders


def test_document_download(tender):
    """Тест 2: Скачивание документов из одного тендера."""
    print("\n" + "="*70)
    print("  ТЕСТ 2: СКАЧИВАНИЕ ДОКУМЕНТОВ")
    print("="*70 + "\n")

    print(f"📄 Тендер: {tender.get('number')}")
    print(f"🔗 URL: https://zakupki.gov.ru{tender.get('url', '')}")

    downloader = ZakupkiDocumentDownloader()

    # Получаем список документов (без скачивания)
    documents = downloader.get_tender_documents(
        tender_url=tender['url'],
        tender_number=tender['number']
    )

    if not documents:
        print("\n❌ Документы не найдены в карточке тендера")
        print("⚠️  Возможно, требуется авторизация или документы в защищенном разделе")
        return None

    print(f"\n📋 Типы найденных документов:")
    doc_types = {}
    for doc in documents:
        doc_type = doc['type']
        doc_types[doc_type] = doc_types.get(doc_type, 0) + 1

    for doc_type, count in doc_types.items():
        print(f"   {doc_type}: {count}")

    # Спрашиваем, скачивать ли
    print("\n" + "─"*70)
    response = input("Скачать документы? (y/n): ")

    if response.lower() == 'y':
        result = downloader.download_documents(
            tender_url=tender['url'],
            tender_number=tender['number']
        )

        print(f"\n✅ Результат:")
        print(f"   📁 Папка: {result['tender_dir']}")
        print(f"   📥 Скачано: {result['downloaded']}/{result['total_documents']}")
        print(f"   ❌ Ошибок: {result['failed']}")

        return result
    else:
        print("\n⏭️  Скачивание пропущено")
        return None


def main():
    """Главная тестовая функция."""
    print("\n" + "="*70)
    print("  ТЕСТИРОВАНИЕ ИНТЕГРИРОВАННОЙ СИСТЕМЫ")
    print("="*70 + "\n")

    try:
        # Тест 1: Поиск
        tenders = test_search_only()

        if not tenders:
            print("\n❌ Тендеры не найдены. Тест прерван.")
            return

        # Тест 2: Скачивание документов из первого тендера
        if tenders:
            print("\n" + "="*70)
            print(f"Выбран первый тендер для теста скачивания:")
            print(f"  {tenders[0].get('number')}")
            print("="*70)

            test_document_download(tenders[0])

        print("\n" + "="*70)
        print("✅ ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
        print("="*70 + "\n")

    except Exception as e:
        print(f"\n❌ ОШИБКА ПРИ ТЕСТИРОВАНИИ: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()

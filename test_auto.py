#!/usr/bin/env python3
"""
Автоматический тест интегрированной системы (без интерактивного ввода).
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
import json


def main():
    """Автоматический тест всей системы."""
    print("\n" + "="*70)
    print("  АВТОМАТИЧЕСКИЙ ТЕСТ ИНТЕГРИРОВАННОЙ СИСТЕМЫ")
    print("="*70 + "\n")

    # Инициализация
    print("🔧 Инициализация компонентов...")
    config = ConfigLoader()
    llm_config = config.get_llm_config()

    tender_analyzer = TenderAnalyzer(
        api_key=llm_config['api_key'],
        provider=llm_config['provider'],
        model_fast=llm_config.get('model_fast')
    )

    search_expander = SmartSearchExpander(tender_analyzer.llm)
    enhanced_parser = ZakupkiEnhancedParser(tender_analyzer.llm)
    downloader = ZakupkiDocumentDownloader()

    # Этап 1: Поиск
    print("\n" + "="*70)
    print("  ЭТАП 1: УМНЫЙ ПОИСК")
    print("="*70 + "\n")

    search_query = "компьютерное оборудование"
    print(f"🔍 Запрос: '{search_query}'")

    expanded_queries = search_expander.expand_search_query(search_query, max_variants=3)

    all_tenders = []
    seen_numbers = set()

    for query in expanded_queries:
        print(f"\n🔎 Поиск: '{query}'")
        tenders = enhanced_parser.search_with_details(
            keywords=query,
            price_min=500000,
            price_max=3000000,
            max_results=2,
            extract_details=False
        )

        for tender in tenders:
            number = tender.get('number')
            if number and number not in seen_numbers and len(all_tenders) < 3:
                seen_numbers.add(number)
                all_tenders.append(tender)

    print(f"\n✅ Найдено: {len(all_tenders)} тендеров")

    # Этап 2: Извлечение документов
    print("\n" + "="*70)
    print("  ЭТАП 2: ИЗВЛЕЧЕНИЕ ДОКУМЕНТОВ")
    print("="*70 + "\n")

    results = []

    for i, tender in enumerate(all_tenders[:2], 1):  # Ограничиваем 2 тендерами
        print(f"\n{'─'*70}")
        print(f"ТЕНДЕР {i}: {tender.get('number')}")
        print(f"{'─'*70}")

        tender_result = {
            'tender_info': tender,
            'documents_found': 0,
            'documents': []
        }

        # Получаем список документов
        try:
            documents = downloader.get_tender_documents(
                tender_url=tender['url'],
                tender_number=tender['number']
            )

            tender_result['documents_found'] = len(documents)
            tender_result['documents'] = documents

            if documents:
                print(f"\n📋 Типы документов:")
                doc_types = {}
                for doc in documents:
                    doc_type = doc['type']
                    doc_types[doc_type] = doc_types.get(doc_type, 0) + 1

                for doc_type, count in doc_types.items():
                    print(f"   • {doc_type}: {count}")
            else:
                print(f"\n⚠️  Документы не найдены")

        except Exception as e:
            print(f"\n❌ Ошибка: {e}")

        results.append(tender_result)

    # Итоговый отчет
    print("\n" + "="*70)
    print("  ИТОГОВЫЙ ОТЧЕТ")
    print("="*70)

    total_documents = sum(r['documents_found'] for r in results)

    print(f"\n✅ Обработано тендеров: {len(results)}")
    print(f"📄 Найдено документов: {total_documents}")

    if total_documents > 0:
        print(f"\n📊 Детальная статистика:")
        for i, result in enumerate(results, 1):
            tender = result['tender_info']
            print(f"\n{i}. {tender.get('number')}")
            print(f"   Название: {tender.get('name', 'N/A')[:60]}...")
            print(f"   Документов: {result['documents_found']}")
            if result['documents_found'] > 0:
                print(f"   Типы:")
                doc_types = {}
                for doc in result['documents']:
                    doc_type = doc['type']
                    doc_types[doc_type] = doc_types.get(doc_type, 0) + 1
                for doc_type, count in doc_types.items():
                    print(f"      - {doc_type}: {count}")

    # Сохраняем результаты
    output_file = Path(__file__).parent / 'output' / 'test_results.json'
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, 'w', encoding='utf-8') as f:
        # Убираем объекты, которые не сериализуются в JSON
        json_results = []
        for r in results:
            json_result = {
                'tender_number': r['tender_info'].get('number'),
                'tender_name': r['tender_info'].get('name'),
                'documents_found': r['documents_found'],
                'document_types': list(set(doc['type'] for doc in r['documents']))
            }
            json_results.append(json_result)

        json.dump({
            'test_date': datetime.now().isoformat(),
            'search_query': search_query,
            'tenders_found': len(all_tenders),
            'tenders_processed': len(results),
            'total_documents': total_documents,
            'results': json_results
        }, f, indent=2, ensure_ascii=False)

    print(f"\n💾 Результаты сохранены: {output_file}")

    print("\n" + "="*70)
    print("✅ ТЕСТ ЗАВЕРШЕН УСПЕШНО")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()

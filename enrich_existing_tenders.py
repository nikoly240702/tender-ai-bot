#!/usr/bin/env python3
"""
Скрипт для обогащения существующих тендеров полными данными.
Использует уже найденные тендеры, обогащает их и показывает интеграцию с анализом.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from parsers.zakupki_enhanced_parser import ZakupkiEnhancedParser
from parsers.zakupki_document_downloader import ZakupkiDocumentDownloader
from analyzers.tender_analyzer import TenderAnalyzer
from utils.config_loader import ConfigLoader
from datetime import datetime
import json
import webbrowser

def main():
    print("\n" + "="*70)
    print("  ОБОГАЩЕНИЕ СУЩЕСТВУЮЩИХ ТЕНДЕРОВ")
    print("="*70 + "\n")

    # Инициализация
    config = ConfigLoader()
    llm_config = config.get_llm_config()

    tender_analyzer = TenderAnalyzer(
        api_key=llm_config['api_key'],
        provider=llm_config['provider'],
        model_fast=llm_config.get('model_fast')
    )

    enhanced_parser = ZakupkiEnhancedParser(tender_analyzer.llm)
    downloader = ZakupkiDocumentDownloader()

    # Шаг 1: Получаем несколько тендеров из существующего поиска
    print("📥 Получение тендеров из RSS...")
    tenders = enhanced_parser.search_with_details(
        keywords="компьютерное оборудование",
        price_min=500000,
        price_max=5000000,
        max_results=3,  # Ограничиваем для демонстрации
        extract_details=False
    )

    if not tenders:
        print("❌ Тендеры не найдены")
        return

    print(f"✅ Получено {len(tenders)} тендеров\n")

    # Шаг 2: Обогащаем каждый тендер данными из полной карточки
    print("="*70)
    print("  ОБОГАЩЕНИЕ ДАННЫМИ ИЗ ПОЛНЫХ КАРТОЧЕК")
    print("="*70 + "\n")

    enriched_tenders = []

    for i, tender in enumerate(tenders, 1):
        print(f"{'─'*70}")
        print(f"ТЕНДЕР {i}/{len(tenders)}: {tender.get('number')}")
        print(f"{'─'*70}")
        print(f"📝 {tender.get('name', 'N/A')[:70]}...")

        # Обогащаем данными из полной карточки
        tender_enriched = enhanced_parser.enrich_with_full_card(tender)

        # Показываем что извлечено
        print(f"\n📊 Извлеченные данные:")
        print(f"   💰 Цена: {tender_enriched.get('price_formatted', 'N/A')}")
        print(f"   📍 Регион: {tender_enriched.get('region', 'N/A')}")
        print(f"   🏛️ Тип заказчика: {tender_enriched.get('customer_type', 'N/A')}")
        print(f"   ⏰ Срок подачи: {tender_enriched.get('submission_deadline', '❌ Не найдено')}")
        print(f"   🏆 Определение победителя: {tender_enriched.get('winner_determination_date', '❌ Не найдено')}")
        if tender_enriched.get('payment_terms'):
            print(f"   💳 Условия оплаты: {tender_enriched.get('payment_terms', 'N/A')[:80]}...")

        enriched_tenders.append(tender_enriched)
        print()

        # Небольшая пауза между запросами
        import time
        time.sleep(1)

    # Шаг 3: Показываем интеграцию с системой анализа документов
    print("\n" + "="*70)
    print("  ДЕМОНСТРАЦИЯ ИНТЕГРАЦИИ С АНАЛИЗОМ ДОКУМЕНТОВ")
    print("="*70 + "\n")

    # Берем первый тендер для демонстрации
    demo_tender = enriched_tenders[0]

    print(f"Демо-тендер: {demo_tender.get('number')}")
    print(f"URL: https://zakupki.gov.ru{demo_tender.get('url', '')}\n")

    # Получаем список документов (не скачиваем)
    print("📄 Проверка доступных документов...")
    documents = downloader.get_tender_documents(
        tender_url=demo_tender['url'],
        tender_number=demo_tender['number']
    )

    if documents:
        print(f"\n✅ Найдено {len(documents)} документов:")
        for i, doc in enumerate(documents, 1):
            print(f"   {i}. [{doc['type']}] {doc['title'][:60]} ({doc['extension']})")

        print(f"\n📋 Типы документов:")
        doc_types = {}
        for doc in documents:
            doc_type = doc['type']
            doc_types[doc_type] = doc_types.get(doc_type, 0) + 1
        for doc_type, count in doc_types.items():
            print(f"   • {doc_type}: {count}")

        print("\n💡 ИНТЕГРАЦИЯ:")
        print("   1. Система автоматически скачивает документы из карточки")
        print("   2. TenderAnalyzer анализирует каждый документ")
        print("   3. Оценивается соответствие профилю компании")
        print("   4. Генерируется итоговый отчет с рекомендациями")

        print("\n📌 Для полного анализа используйте:")
        print("   python3 integrated_tender_system.py")
    else:
        print("\n⚠️  Документы не найдены в карточке или требуется авторизация")

    # Шаг 4: Создаем итоговый HTML отчет
    print("\n" + "="*70)
    print("  ГЕНЕРАЦИЯ ИТОГОВОГО ОТЧЕТА")
    print("="*70 + "\n")

    from smart_tender_search import create_enhanced_html_report

    search_params = {
        'original_query': 'компьютерное оборудование',
        'expanded_queries': ['компьютерное оборудование (обогащенные данные)'],
        'price_min': 500000,
        'price_max': 5000000,
        'regions': None,
        'time': 0
    }

    html_content = create_enhanced_html_report(enriched_tenders, search_params)

    # Сохраняем
    output_dir = Path(__file__).parent / 'output' / 'reports'
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    html_file = output_dir / f'enriched_tenders_{timestamp}.html'

    with open(html_file, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"✅ HTML отчет создан: {html_file}")

    # Открываем в браузере
    print("\n🌐 Открываем отчет в браузере...")
    webbrowser.open(f'file://{html_file.absolute()}')

    # Сохраняем JSON с полными данными
    json_file = output_dir.parent / f'enriched_tenders_{timestamp}.json'

    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'total_tenders': len(enriched_tenders),
            'tenders': enriched_tenders
        }, f, indent=2, ensure_ascii=False, default=str)

    print(f"💾 JSON данные сохранены: {json_file}")

    # Итоговая статистика
    print("\n" + "="*70)
    print("  ИТОГОВАЯ СТАТИСТИКА")
    print("="*70)

    tenders_with_deadline = sum(1 for t in enriched_tenders if t.get('submission_deadline'))
    tenders_with_winner_date = sum(1 for t in enriched_tenders if t.get('winner_determination_date'))
    tenders_with_price = sum(1 for t in enriched_tenders if t.get('price'))

    print(f"\n✅ Обработано тендеров: {len(enriched_tenders)}")
    print(f"💰 Извлечена цена: {tenders_with_price}/{len(enriched_tenders)}")
    print(f"⏰ Извлечен срок подачи: {tenders_with_deadline}/{len(enriched_tenders)}")
    print(f"🏆 Извлечен срок определения: {tenders_with_winner_date}/{len(enriched_tenders)}")

    print("\n" + "="*70)
    print("✅ ОБОГАЩЕНИЕ ЗАВЕРШЕНО")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()

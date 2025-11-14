#!/usr/bin/env python3
"""
Полный анализ тендеров на поставку бумаги для ЦФО.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from integrated_tender_system import IntegratedTenderSystem
from datetime import datetime

def filter_by_deadline(tenders, deadline_str="20.11.2025"):
    """Фильтрует тендеры по сроку подачи."""
    from datetime import datetime

    deadline = datetime.strptime(deadline_str, "%d.%m.%Y")
    filtered = []

    for tender in tenders:
        submission_deadline = tender.get('submission_deadline')
        if submission_deadline:
            try:
                # Парсим дату из формата "20.11.2025 08:00"
                tender_date = datetime.strptime(submission_deadline.split()[0], "%d.%m.%Y")
                if tender_date <= deadline:
                    filtered.append(tender)
                    print(f"   ✅ {tender.get('number')} - срок {submission_deadline}")
                else:
                    print(f"   ❌ {tender.get('number')} - срок {submission_deadline} (позже {deadline_str})")
            except:
                print(f"   ⚠️  {tender.get('number')} - не удалось распарсить дату: {submission_deadline}")
        else:
            print(f"   ⚠️  {tender.get('number')} - срок не указан")

    return filtered


def main():
    print("\n" + "="*70)
    print("  ПОЛНЫЙ АНАЛИЗ ТЕНДЕРОВ НА ПОСТАВКУ БУМАГИ")
    print("="*70 + "\n")

    # Параметры поиска
    search_query = "поставка бумаги"
    price_min = 10000
    price_max = 1000000
    deadline = "20.11.2025"
    max_tenders_to_find = 10  # Найдем больше, чтобы было из чего выбрать
    target_tenders = 5  # Целевое количество для анализа

    # Регионы ЦФО
    regions_cfo = [
        'Москва',
        'Московская область',
        'Белгородская область',
        'Брянская область',
        'Владимирская область',
        'Воронежская область',
        'Ивановская область',
        'Калужская область',
        'Костромская область',
        'Курская область',
        'Липецкая область',
        'Орловская область',
        'Рязанская область',
        'Смоленская область',
        'Тамбовская область',
        'Тверская область',
        'Тульская область',
        'Ярославская область'
    ]

    print(f"🎯 ПАРАМЕТРЫ ПОИСКА:")
    print(f"   📝 Запрос: {search_query}")
    print(f"   💰 Цена: до {price_max:,} руб")
    print(f"   📅 Срок подачи: до {deadline}")
    print(f"   📍 Округ: Центральный федеральный")
    print(f"   🎯 Целевое кол-во: {target_tenders} тендеров\n")

    # Создаем систему
    system = IntegratedTenderSystem()

    # ЭТАП 1: Расширенный поиск
    print("="*70)
    print("  ЭТАП 1: РАСШИРЕННЫЙ ПОИСК ТЕНДЕРОВ")
    print("="*70 + "\n")

    from parsers.smart_search_expander import SmartSearchExpander
    from parsers.zakupki_enhanced_parser import ZakupkiEnhancedParser

    search_expander = SmartSearchExpander(system.tender_analyzer.llm)
    enhanced_parser = ZakupkiEnhancedParser(system.tender_analyzer.llm)

    # Расширяем запрос
    print(f"🧠 Расширение запроса через AI...")
    expanded_queries = search_expander.expand_search_query(search_query, max_variants=4)

    # Ищем тендеры
    all_tenders = []
    seen_numbers = set()

    for query in expanded_queries:
        print(f"\n🔎 Поиск: '{query}'")

        tenders = enhanced_parser.search_with_details(
            keywords=query,
            price_min=price_min,
            price_max=price_max,
            max_results=5,
            extract_details=False
        )

        # Обогащаем и дедуплицируем
        for tender in tenders:
            number = tender.get('number')
            if number and number not in seen_numbers:
                # Обогащаем данными из полной карточки
                tender = enhanced_parser.enrich_with_full_card(tender)

                seen_numbers.add(number)
                all_tenders.append(tender)

                if len(all_tenders) >= max_tenders_to_find:
                    break

        if len(all_tenders) >= max_tenders_to_find:
            break

    print(f"\n✅ Найдено тендеров: {len(all_tenders)}")

    # ЭТАП 2: Фильтрация по региону и срокам
    print("\n" + "="*70)
    print("  ЭТАП 2: ФИЛЬТРАЦИЯ ПО РЕГИОНУ И СРОКАМ")
    print("="*70 + "\n")

    # Фильтр по региону
    print("📍 Фильтрация по ЦФО...")
    cfo_tenders = []
    for tender in all_tenders:
        region = tender.get('region', '')
        if region and any(r in region for r in regions_cfo):
            cfo_tenders.append(tender)
            print(f"   ✅ {tender.get('number')} - {region}")
        else:
            print(f"   ❌ {tender.get('number')} - {region if region else 'регион не определен'}")

    print(f"\n✅ Тендеров в ЦФО: {len(cfo_tenders)}")

    # Фильтр по срокам
    print(f"\n📅 Фильтрация по сроку подачи (до {deadline})...")
    filtered_tenders = filter_by_deadline(cfo_tenders, deadline)

    print(f"\n✅ Тендеров после фильтрации: {len(filtered_tenders)}")

    if len(filtered_tenders) == 0:
        print("\n⚠️  Не найдено тендеров, соответствующих всем критериям.")
        print("💡 Попробуйте изменить параметры поиска:")
        print("   - Увеличить максимальную цену")
        print("   - Продлить срок подачи")
        print("   - Расширить регионы")
        return

    # Берем только целевое количество
    selected_tenders = filtered_tenders[:target_tenders]

    print(f"\n🎯 Выбрано для анализа: {len(selected_tenders)} тендеров")

    # ЭТАП 3: Скачивание и анализ документов
    print("\n" + "="*70)
    print("  ЭТАП 3: СКАЧИВАНИЕ И АНАЛИЗ ДОКУМЕНТОВ")
    print("="*70 + "\n")

    from parsers.zakupki_document_downloader import ZakupkiDocumentDownloader
    from main import TenderAnalysisAgent

    downloader = ZakupkiDocumentDownloader()
    analyzed_results = []

    for i, tender in enumerate(selected_tenders, 1):
        print(f"\n{'─'*70}")
        print(f"ТЕНДЕР {i}/{len(selected_tenders)}: {tender.get('number')}")
        print(f"{'─'*70}")
        print(f"📝 {tender.get('name', 'N/A')[:70]}...")
        print(f"💰 {tender.get('price_formatted', 'N/A')}")
        print(f"⏰ Срок подачи: {tender.get('submission_deadline', 'N/A')}")
        print(f"📍 Регион: {tender.get('region', 'N/A')}")

        tender_result = {
            'tender_info': tender,
            'documents_downloaded': [],
            'analysis_result': None,
            'download_success': False,
            'analysis_success': False
        }

        # Скачиваем документы
        try:
            print(f"\n📥 Скачивание документов...")
            download_result = downloader.download_documents(
                tender_url=tender['url'],
                tender_number=tender['number']
            )

            tender_result['documents_downloaded'] = download_result.get('files', [])
            tender_result['download_success'] = download_result['downloaded'] > 0
            tender_result['tender_dir'] = download_result.get('tender_dir')

            print(f"✅ Скачано: {download_result['downloaded']} документов")

            # Анализируем через AI
            if tender_result['download_success']:
                print(f"\n🤖 Анализ документов через AI...")

                file_paths = [doc['path'] for doc in tender_result['documents_downloaded']]

                # Создаем агента для анализа
                agent = TenderAnalysisAgent()

                # Выполняем полный анализ
                analysis = agent.analyze_tender(file_paths)

                tender_result['analysis_result'] = analysis
                tender_result['analysis_success'] = True

                print(f"✅ Анализ завершен")

                # Показываем краткие результаты
                if analysis.get('analysis_summary'):
                    summary = analysis['analysis_summary']
                    print(f"\n📊 Результат анализа:")
                    if summary.get('is_suitable') is not None:
                        suitability = "✅ ПОДХОДИТ" if summary['is_suitable'] else "❌ НЕ ПОДХОДИТ"
                        print(f"   {suitability}")
                    if summary.get('confidence_score'):
                        print(f"   🎯 Уверенность: {summary['confidence_score']:.1f}%")
                    if summary.get('summary_text'):
                        print(f"   📝 {summary['summary_text'][:100]}...")

        except Exception as e:
            print(f"\n❌ Ошибка: {e}")

        analyzed_results.append(tender_result)

        # Пауза между тендерами
        import time
        time.sleep(2)

    # ЭТАП 4: Генерация итогового отчета
    print("\n" + "="*70)
    print("  ЭТАП 4: ГЕНЕРАЦИЯ ИТОГОВОГО ОТЧЕТА")
    print("="*70 + "\n")

    from smart_tender_search import create_enhanced_html_report

    # Формируем данные для отчета
    tenders_for_report = []
    for result in analyzed_results:
        tender = result['tender_info'].copy()

        # Добавляем информацию о документах
        if result['documents_downloaded']:
            tender['documents_count'] = len(result['documents_downloaded'])
            tender['documents'] = result['documents_downloaded']

        # Добавляем анализ
        if result['analysis_success'] and result['analysis_result']:
            analysis = result['analysis_result']
            tender['analysis'] = {
                'suitable': analysis.get('analysis_summary', {}).get('is_suitable'),
                'confidence': analysis.get('analysis_summary', {}).get('confidence_score'),
                'summary': analysis.get('analysis_summary', {}).get('summary_text', ''),
                'key_findings': analysis.get('key_findings', [])
            }

        tenders_for_report.append(tender)

    search_params = {
        'original_query': search_query,
        'expanded_queries': expanded_queries,
        'price_min': price_min,
        'price_max': price_max,
        'regions': ['Центральный федеральный округ'],
        'deadline': deadline,
        'time': 0
    }

    html_content = create_enhanced_html_report(tenders_for_report, search_params)

    # Сохраняем отчет
    output_dir = Path(__file__).parent / 'output' / 'reports'
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    html_file = output_dir / f'paper_supply_analysis_{timestamp}.html'

    with open(html_file, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"✅ HTML отчет создан: {html_file}")

    # Открываем в браузере
    import webbrowser
    print("\n🌐 Открываем отчет в браузере...")
    webbrowser.open(f'file://{html_file.absolute()}')

    # Итоговая статистика
    print("\n" + "="*70)
    print("  ИТОГОВАЯ СТАТИСТИКА")
    print("="*70)

    suitable_count = sum(
        1 for r in analyzed_results
        if r.get('analysis_result', {}).get('analysis_summary', {}).get('is_suitable')
    )

    print(f"\n🔍 Найдено тендеров: {len(all_tenders)}")
    print(f"📍 В ЦФО: {len(cfo_tenders)}")
    print(f"📅 Со сроком до {deadline}: {len(filtered_tenders)}")
    print(f"🎯 Отобрано для анализа: {len(selected_tenders)}")
    print(f"📥 Скачано документов: {sum(len(r['documents_downloaded']) for r in analyzed_results)}")
    print(f"🤖 Проанализировано: {sum(1 for r in analyzed_results if r['analysis_success'])}")
    print(f"✅ Подходит для участия: {suitable_count}")

    print("\n" + "="*70)
    print("✅ АНАЛИЗ ЗАВЕРШЕН")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()

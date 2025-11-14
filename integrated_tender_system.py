#!/usr/bin/env python3
"""
Интегрированная система поиска, скачивания и анализа тендеров.

Полный цикл:
1. Умный поиск тендеров (AI-расширение запросов)
2. Автоматическое скачивание документов из карточек
3. Анализ документов через TenderAnalyzer
4. Генерация итогового отчета
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
import webbrowser
from typing import List, Dict, Any, Optional


class IntegratedTenderSystem:
    """Интегрированная система для полного цикла работы с тендерами."""

    def __init__(self, config_loader: ConfigLoader = None):
        """
        Инициализация системы.

        Args:
            config_loader: Загрузчик конфигурации (если None, создается новый)
        """
        print("\n" + "="*70)
        print("  ИНТЕГРИРОВАННАЯ СИСТЕМА АНАЛИЗА ТЕНДЕРОВ")
        print("="*70 + "\n")

        # Загружаем конфигурацию
        self.config = config_loader or ConfigLoader()
        llm_config = self.config.get_llm_config()

        # Инициализируем AI
        print("🤖 Инициализация AI...")

        # Проверяем Level 2 настройки
        import os
        use_level2 = os.getenv('USE_LEVEL2_ANALYSIS', 'false').lower() == 'true'
        openai_api_key = os.getenv('OPENAI_API_KEY') if use_level2 else None

        self.tender_analyzer = TenderAnalyzer(
            api_key=llm_config['api_key'],
            provider=llm_config['provider'],
            model=llm_config.get('model'),
            model_fast=llm_config.get('model_fast'),
            model_premium=llm_config.get('model_premium'),
            use_level2=use_level2,
            openai_api_key=openai_api_key
        )

        # Компоненты системы
        print("🔧 Инициализация компонентов...")
        self.search_expander = SmartSearchExpander(self.tender_analyzer.llm)
        self.enhanced_parser = ZakupkiEnhancedParser(self.tender_analyzer.llm)
        self.document_downloader = ZakupkiDocumentDownloader()

        print("✅ Система инициализирована\n")

    def search_and_analyze(
        self,
        search_query: str,
        price_min: int = 100000,
        price_max: int = 10000000,
        max_tenders: int = 5,
        regions: Optional[List[str]] = None,
        analyze_documents: bool = True,
        download_documents: bool = True
    ) -> Dict[str, Any]:
        """
        Полный цикл: поиск → скачивание → анализ.

        Args:
            search_query: Поисковый запрос
            price_min: Минимальная цена
            price_max: Максимальная цена
            max_tenders: Максимум тендеров для анализа
            regions: Список регионов (опционально)
            analyze_documents: Анализировать ли документы через AI
            download_documents: Скачивать ли документы

        Returns:
            Словарь с результатами работы системы
        """
        start_time = datetime.now()

        print("="*70)
        print("  ЭТАП 1: УМНЫЙ ПОИСК ТЕНДЕРОВ")
        print("="*70 + "\n")

        print(f"🔍 Оригинальный запрос: '{search_query}'")
        print(f"💰 Цена: {price_min:,} - {price_max:,} руб")
        if regions:
            print(f"📍 Регионы: {', '.join(regions)}")

        # Расширяем поисковый запрос через AI
        print("\n🧠 Расширение запроса через AI...")
        expanded_queries = self.search_expander.expand_search_query(search_query, max_variants=4)

        # Вычисляем, сколько тендеров искать на каждый запрос
        # Добавляем 50% запас для дедупликации
        results_per_query = max(3, int(max_tenders / len(expanded_queries) * 1.5))

        # Ищем тендеры по всем расширенным запросам
        all_tenders = []
        seen_numbers = set()

        for query in expanded_queries:
            print(f"\n🔎 Поиск по запросу: '{query}'")

            tenders = self.enhanced_parser.search_with_details(
                keywords=query,
                price_min=price_min,
                price_max=price_max,
                max_results=results_per_query,  # Динамическое значение
                regions=regions,  # Передаем регионы
                extract_details=False  # Отключаем LLM для скорости
            )

            # Дедупликация
            for tender in tenders:
                number = tender.get('number')
                if number and number not in seen_numbers:
                    seen_numbers.add(number)
                    all_tenders.append(tender)

            # Продолжаем поиск по всем вариантам запроса,
            # чтобы найти максимум возможных тендеров

        search_elapsed = (datetime.now() - start_time).total_seconds()

        # Сохраняем информацию о том, сколько тендеров было найдено всего
        total_found = len(all_tenders)

        # Ограничиваем до запрошенного количества
        all_tenders = all_tenders[:max_tenders]

        print(f"\n✅ Найдено уникальных тендеров: {total_found}")
        if total_found > max_tenders:
            print(f"   (используется {max_tenders} из {total_found})")
        elif total_found < max_tenders:
            print(f"   ⚠️  Запрошено {max_tenders}, но найдено только {total_found}")
        print(f"⏱️  Время поиска: {search_elapsed:.2f} сек\n")

        # Обогащаем тендеры данными из полных карточек
        print("🔄 Обогащение данными из полных карточек...")
        for i, tender in enumerate(all_tenders, 1):
            print(f"   [{i}/{len(all_tenders)}] {tender.get('number')}")
            all_tenders[i-1] = self.enhanced_parser.enrich_with_full_card(tender)
        print()

        if not all_tenders:
            print("❌ Тендеры не найдены")
            return {
                'search_params': {
                    'query': search_query,
                    'price_min': price_min,
                    'price_max': price_max,
                    'regions': regions,
                    'requested_count': max_tenders,
                    'total_found': 0
                },
                'tenders_found': 0,
                'tenders_analyzed': 0,
                'results': []
            }

        # ЭТАП 2: Скачивание и анализ документов
        if download_documents or analyze_documents:
            print("="*70)
            print("  ЭТАП 2: СКАЧИВАНИЕ И АНАЛИЗ ДОКУМЕНТОВ")
            print("="*70 + "\n")

            analyzed_tenders = []

            for i, tender in enumerate(all_tenders, 1):
                print(f"\n{'─'*70}")
                print(f"ТЕНДЕР {i}/{len(all_tenders)}: {tender.get('number')}")
                print(f"{'─'*70}")
                print(f"📝 {tender.get('name', 'Без названия')[:80]}")
                print(f"💰 {tender.get('price_formatted', 'N/A')}\n")

                tender_result = {
                    'tender_info': tender,
                    'documents_downloaded': [],
                    'analysis_result': None,
                    'download_success': False,
                    'analysis_success': False
                }

                # Скачиваем документы
                if download_documents and tender.get('url'):
                    try:
                        download_result = self.document_downloader.download_documents(
                            tender_url=tender['url'],
                            tender_number=tender['number'],
                            doc_types=None  # Скачиваем все документы
                        )

                        tender_result['documents_downloaded'] = download_result.get('files', [])
                        tender_result['download_success'] = download_result['downloaded'] > 0
                        tender_result['tender_dir'] = download_result.get('tender_dir')

                        print(f"\n📥 Скачано документов: {download_result['downloaded']}")

                    except Exception as e:
                        print(f"\n⚠️  Ошибка скачивания документов: {e}")

                # Анализируем документы через AI
                if analyze_documents and tender_result['download_success']:
                    print(f"\n🤖 Анализ документов через AI...")

                    try:
                        # Получаем пути к скачанным файлам
                        file_paths = [doc['path'] for doc in tender_result['documents_downloaded']]

                        # Импортируем главный класс для полного анализа
                        from main import TenderAnalysisAgent

                        # Создаем агента (он уже имеет все компоненты)
                        agent = TenderAnalysisAgent()

                        # Выполняем полный анализ
                        analysis = agent.analyze_tender(file_paths)

                        tender_result['analysis_result'] = analysis
                        tender_result['analysis_success'] = True

                        print(f"\n✅ Анализ завершен")

                        # Показываем краткие результаты
                        if analysis.get('analysis_summary'):
                            print(f"\n📊 Краткое резюме:")
                            summary = analysis['analysis_summary']
                            if summary.get('is_suitable') is not None:
                                suitability = "✅ Подходит" if summary['is_suitable'] else "❌ Не подходит"
                                print(f"   {suitability}")
                            if summary.get('confidence_score'):
                                print(f"   🎯 Уверенность: {summary['confidence_score']:.1f}%")

                    except Exception as e:
                        print(f"\n⚠️  Ошибка анализа: {e}")

                analyzed_tenders.append(tender_result)

                # Небольшая пауза между тендерами
                import time
                time.sleep(1)

        else:
            # Если не скачиваем и не анализируем, просто формируем результаты
            analyzed_tenders = [{
                'tender_info': t,
                'documents_downloaded': [],
                'analysis_result': None,
                'download_success': False,
                'analysis_success': False
            } for t in all_tenders]

        total_elapsed = (datetime.now() - start_time).total_seconds()

        print(f"\n{'='*70}")
        print(f"✅ ОБРАБОТКА ЗАВЕРШЕНА")
        print(f"{'='*70}")
        print(f"⏱️  Общее время: {total_elapsed:.1f} сек")
        print(f"🔍 Найдено тендеров: {len(all_tenders)}")
        print(f"📥 Скачано документов: {sum(len(t['documents_downloaded']) for t in analyzed_tenders)}")
        print(f"🤖 Проанализировано: {sum(1 for t in analyzed_tenders if t['analysis_success'])}")
        print(f"{'='*70}\n")

        # Формируем итоговый результат
        result = {
            'search_params': {
                'original_query': search_query,  # Исправлено: было 'query'
                'query': search_query,
                'expanded_queries': expanded_queries,
                'price_min': price_min,
                'price_max': price_max,
                'regions': regions,
                'time': total_elapsed,
                'requested_count': max_tenders,  # Сколько запросили
                'total_found': total_found  # Сколько нашли всего
            },
            'tenders_found': len(all_tenders),
            'tenders_analyzed': sum(1 for t in analyzed_tenders if t['analysis_success']),
            'results': analyzed_tenders
        }

        # Создаем итоговый HTML отчет
        print("📄 Создание итогового отчета...")
        html_path = self._create_integrated_report(result)
        result['report_path'] = str(html_path)  # Добавляем путь к отчету

        return result

    def _create_integrated_report(self, result: Dict[str, Any]) -> Path:
        """Создает итоговый HTML отчет."""
        from smart_tender_search import create_enhanced_html_report

        # Преобразуем результаты в формат для существующего генератора
        tenders_for_report = []
        for item in result['results']:
            tender = item['tender_info'].copy()

            # Добавляем информацию о документах и анализе
            if item['documents_downloaded']:
                tender['documents_count'] = len(item['documents_downloaded'])
                tender['documents'] = item['documents_downloaded']

            if item['analysis_success'] and item['analysis_result']:
                analysis = item['analysis_result']
                tender['analysis'] = {
                    'suitable': analysis.get('analysis_summary', {}).get('is_suitable'),
                    'confidence': analysis.get('analysis_summary', {}).get('confidence_score'),
                    'summary': analysis.get('analysis_summary', {}).get('summary_text', ''),
                    'tender_info': analysis.get('tender_info', {}),
                    'requirements': analysis.get('requirements', {}),
                    'gaps': analysis.get('gaps', []),
                    'questions': analysis.get('questions', {}),
                    'contacts': analysis.get('contacts', {})
                }

            tenders_for_report.append(tender)

        # Генерируем отчет
        html_content = create_enhanced_html_report(tenders_for_report, result['search_params'])

        # Сохраняем
        output_dir = Path(__file__).parent / 'output' / 'reports'
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        html_file = output_dir / f'integrated_analysis_{timestamp}.html'

        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(html_content)

        print(f"✅ Отчет сохранен: {html_file}")

        # Открываем в браузере
        print("🌐 Открываем отчет в браузере...")
        webbrowser.open(f'file://{html_file.absolute()}')

        return html_file  # Возвращаем путь к отчету


def main():
    """Главная функция."""
    print("\n" + "="*70)
    print("  ЗАПУСК ИНТЕГРИРОВАННОЙ СИСТЕМЫ")
    print("="*70 + "\n")

    # Создаем систему
    system = IntegratedTenderSystem()

    # Параметры поиска
    search_query = "компьютерное оборудование"
    price_min = 500000
    price_max = 3000000
    max_tenders = 2  # Ограничиваем для теста
    regions = None  # Можно указать ['Москва', 'Санкт-Петербург']

    print(f"🎯 ПАРАМЕТРЫ ПОИСКА:")
    print(f"   📝 Запрос: {search_query}")
    print(f"   💰 Цена: {price_min:,} - {price_max:,} руб")
    print(f"   📊 Макс. тендеров: {max_tenders}")
    if regions:
        print(f"   📍 Регионы: {', '.join(regions)}")
    print()

    # Запускаем полный цикл
    result = system.search_and_analyze(
        search_query=search_query,
        price_min=price_min,
        price_max=price_max,
        max_tenders=max_tenders,
        regions=regions,
        download_documents=True,  # Скачивать документы
        analyze_documents=False   # Отключаем полный AI анализ для скорости
    )

    # Выводим итоговую статистику
    print("\n" + "="*70)
    print("  ИТОГОВАЯ СТАТИСТИКА")
    print("="*70)
    print(f"🔍 Найдено тендеров: {result['tenders_found']}")
    print(f"🤖 Проанализировано: {result['tenders_analyzed']}")

    suitable_count = sum(
        1 for t in result['results']
        if t.get('analysis_result') and
           t['analysis_result'].get('analysis_summary', {}).get('is_suitable')
    )
    print(f"✅ Подходящих тендеров: {suitable_count}")
    print(f"⏱️  Общее время: {result['search_params']['time']:.1f} сек")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()

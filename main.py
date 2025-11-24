#!/usr/bin/env python3
"""
ИИ-агент для анализа тендеров (MVP)
Главный модуль для запуска анализа тендерной документации.
"""

import sys
import os
import argparse
import webbrowser
from pathlib import Path
from typing import List, Dict, Any

# Добавляем src в путь для импортов
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from tqdm import tqdm
import colorama
from colorama import Fore, Style

from utils.config_loader import ConfigLoader
from document_processor.text_extractor import TextExtractor
from analyzers.tender_analyzer import TenderAnalyzer
from communication.contact_extractor import ContactExtractor
from communication.template_generator import TemplateGenerator
from reporting.report_generator import ReportGenerator
from search.tender_searcher import TenderSearcher

# Инициализация colorama для цветного вывода
colorama.init()


class TenderAnalysisAgent:
    """Главный класс ИИ-агента для анализа тендеров."""

    def __init__(self):
        """Инициализация агента."""
        print(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}  ИИ-АГЕНТ ДЛЯ АНАЛИЗА ТЕНДЕРОВ (MVP){Style.RESET_ALL}")
        print(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")

        # Загружаем конфигурацию
        print(f"{Fore.YELLOW}Загрузка конфигурации...{Style.RESET_ALL}")
        self.config_loader = ConfigLoader()

        # База данных для кэширования (будет инициализирована асинхронно)
        self.db = None

        try:
            self.company_profile = self.config_loader.load_company_profile()
            self.settings = self.config_loader.load_settings()
            self.llm_config = self.config_loader.get_llm_config()
            self.paths = self.config_loader.get_paths()

            # Выводим информацию о провайдере
            provider = self.llm_config.get('provider', 'groq')
            model = self.llm_config.get('model') or 'рекомендуемая'
            print(f"{Fore.GREEN}✓ Конфигурация загружена{Style.RESET_ALL}")
            print(f"{Fore.CYAN}  LLM провайдер: {provider.upper()}{Style.RESET_ALL}")
            print(f"{Fore.CYAN}  Модель: {model}{Style.RESET_ALL}\n")
        except Exception as e:
            print(f"{Fore.RED}✗ Ошибка загрузки конфигурации: {e}{Style.RESET_ALL}")
            sys.exit(1)

        # Инициализируем компоненты
        self.text_extractor = TextExtractor()
        self.tender_analyzer = TenderAnalyzer(
            api_key=self.llm_config.get('api_key'),
            provider=self.llm_config.get('provider', 'groq'),
            model=self.llm_config.get('model'),
            model_premium=self.llm_config.get('model_premium'),
            model_fast=self.llm_config.get('model_fast'),
            max_tokens=self.llm_config.get('max_tokens', 4096),
            temperature=self.llm_config.get('temperature', 0.3),
            max_retries=self.llm_config.get('max_retries', 3),
            retry_delay=self.llm_config.get('retry_delay', 2),
            ollama_base_url=self.llm_config.get('ollama_base_url'),
            use_multi_stage=True  # ⭐ Активация нового многоэтапного анализа
        )
        self.contact_extractor = ContactExtractor()
        self.template_generator = TemplateGenerator()
        self.report_generator = ReportGenerator(str(self.paths['output']))
        self.tender_searcher = TenderSearcher(self.tender_analyzer)

    async def analyze_tender(
        self,
        file_paths: List[str],
        tender_number: str = None,
        use_cache: bool = True
    ) -> Dict[str, Any]:
        """
        Выполняет полный анализ тендера с поддержкой кэширования.

        Args:
            file_paths: Список путей к файлам тендерной документации
            tender_number: Номер тендера для кэширования (опционально)
            use_cache: Использовать ли кэш (по умолчанию True)

        Returns:
            Полный словарь с результатами анализа
        """
        print(f"{Fore.CYAN}\nНачинаем анализ тендера...{Style.RESET_ALL}\n")

        # ============================================================
        # ПРОВЕРКА КЭША (V2.0)
        # ============================================================
        if use_cache and tender_number and self.db:
            try:
                # Извлекаем документацию для вычисления хэша
                extracted = self.text_extractor.extract_from_multiple_files(file_paths)
                documentation = [
                    {'filename': f['file_name'], 'content': f.get('text', '')}
                    for f in extracted['files']
                ]

                # Вычисляем хэш документации
                from bot.db import Database
                doc_hash = Database.compute_documentation_hash(documentation)

                # Пробуем получить из кэша
                cached = await self.db.get_cached_analysis(tender_number, doc_hash)

                if cached:
                    print(f"{Fore.GREEN}✅ НАЙДЕН КЭШИРОВАННЫЙ АНАЛИЗ!{Style.RESET_ALL}")
                    print(f"{Fore.GREEN}   Сохранено ~70% токенов LLM{Style.RESET_ALL}\n")

                    # Восстанавливаем результаты из кэша
                    results = cached['analysis_result']
                    results['from_cache'] = True
                    results['cache_created_at'] = cached['created_at']
                    return results

            except Exception as e:
                print(f"{Fore.YELLOW}⚠️  Ошибка проверки кэша: {e}{Style.RESET_ALL}")
                # Продолжаем обычный анализ при ошибке кэширования

        # Создаем прогресс-бар
        steps = [
            "Извлечение текста из документов",
            "Приоритетный анализ проекта контракта",
            "ИИ-анализ документации тендера",
            "Детекция пробелов в информации",
            "Генерация вопросов для заказчика",
            "Извлечение контактов",
            "Генерация отчетов"
        ]

        results = {}

        with tqdm(total=len(steps), bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt}') as pbar:
            # Шаг 1: Извлечение текста
            pbar.set_description(f"{Fore.YELLOW}{steps[0]}{Style.RESET_ALL}")
            try:
                extracted = self.text_extractor.extract_from_multiple_files(file_paths)
                results['extracted_text'] = extracted['combined_text']
                results['files_info'] = extracted['files']
                pbar.update(1)
            except Exception as e:
                print(f"\n{Fore.RED}✗ Ошибка извлечения текста: {e}{Style.RESET_ALL}")
                raise

            # Шаг 2: Приоритетный анализ проекта контракта
            pbar.set_description(f"{Fore.YELLOW}{steps[1]}{Style.RESET_ALL}")
            contract_terms = None
            try:
                # Ищем файл проекта контракта
                contract_file = None
                for file_info in results['files_info']:
                    file_name = file_info.get('file_name', '').lower()
                    if 'контракт' in file_name or 'договор' in file_name:
                        # Нашли проект контракта - извлекаем его текст отдельно
                        for fp in file_paths:
                            if file_info['file_name'] in fp:
                                contract_file = fp
                                break
                        break

                if contract_file:
                    # Извлекаем полный текст контракта без обрезки
                    contract_extracted = self.text_extractor.extract_text(contract_file)
                    contract_text = contract_extracted['text']
                    # Анализируем финансовые условия
                    contract_terms = self.tender_analyzer.analyze_contract_terms(contract_text)
                    # Небольшая задержка перед следующим запросом (rate limit)
                    import time
                    time.sleep(2)
                pbar.update(1)
            except Exception as e:
                print(f"\n{Fore.YELLOW}⚠ Не удалось проанализировать проект контракта: {e}{Style.RESET_ALL}")
                pbar.update(1)

            # Шаг 3: Анализ через Claude
            pbar.set_description(f"{Fore.YELLOW}{steps[2]}{Style.RESET_ALL}")
            try:
                # Ограничиваем размер текста для предотвращения превышения лимита токенов
                # Примерно 1 токен ≈ 4 символа, 20000 токенов ≈ 80000 символов
                max_chars = 80000
                extracted_text = results['extracted_text']
                if len(extracted_text) > max_chars:
                    print(f"\n{Fore.YELLOW}⚠️  Текст слишком большой ({len(extracted_text)} символов), обрезаем до {max_chars}{Style.RESET_ALL}")
                    extracted_text = extracted_text[:max_chars] + "\n\n[... текст обрезан из-за ограничений API ...]"

                analysis = self.tender_analyzer.analyze_documentation(
                    extracted_text,
                    self.company_profile
                )
                results['tender_info'] = analysis.get('tender_info', {})
                results['requirements'] = analysis.get('requirements', {})

                # Если есть данные из анализа контракта - объединяем их
                if contract_terms:
                    # Обновляем условия оплаты из контракта
                    if 'payment_terms' in contract_terms:
                        results['tender_info']['payment_terms'] = contract_terms['payment_terms']
                    # Обновляем обеспечения если они найдены
                    if contract_terms.get('guarantee_application'):
                        results['tender_info']['guarantee_application'] = contract_terms['guarantee_application']
                    if contract_terms.get('guarantee_contract'):
                        results['tender_info']['guarantee_contract'] = contract_terms['guarantee_contract']

                pbar.update(1)
            except Exception as e:
                print(f"\n{Fore.RED}✗ Ошибка анализа: {e}{Style.RESET_ALL}")
                # Используем заглушки для продолжения
                results['tender_info'] = {'name': 'Анализируемый тендер', 'customer': 'Н/Д', 'nmck': 0}
                results['requirements'] = {'technical': [], 'qualification': []}

            # Шаг 4: Детекция пробелов
            pbar.set_description(f"{Fore.YELLOW}{steps[3]}{Style.RESET_ALL}")
            try:
                gaps = self.tender_analyzer.detect_gaps(results['extracted_text'])
                results['gaps'] = gaps
                pbar.update(1)
            except Exception as e:
                print(f"\n{Fore.RED}✗ Ошибка детекции пробелов: {e}{Style.RESET_ALL}")
                results['gaps'] = []

            # Шаг 5: Генерация вопросов (УБРАНО - не нужно)
            # pbar.set_description(f"{Fore.YELLOW}{steps[4]}{Style.RESET_ALL}")
            # try:
            #     questions = self.tender_analyzer.generate_questions(
            #         results['gaps'],
            #         results['extracted_text']
            #     )
            #     results['questions'] = questions
            #     pbar.update(1)
            # except Exception as e:
            #     print(f"\n{Fore.RED}✗ Ошибка генерации вопросов: {e}{Style.RESET_ALL}")
            #     results['questions'] = {'critical': [], 'important': [], 'optional': []}
            results['questions'] = {}  # Пустой словарь вместо генерации
            pbar.update(1)  # Пропускаем шаг

            # Шаг 6: Извлечение контактов
            pbar.set_description(f"{Fore.YELLOW}{steps[5]}{Style.RESET_ALL}")
            try:
                contacts = self.contact_extractor.extract_contacts(results['extracted_text'])
                results['contacts'] = contacts
                pbar.update(1)
            except Exception as e:
                print(f"\n{Fore.RED}✗ Ошибка извлечения контактов: {e}{Style.RESET_ALL}")
                results['contacts'] = {'emails': [], 'phones': [], 'has_contacts': False}

            # Шаг 7: Генерация отчетов
            pbar.set_description(f"{Fore.YELLOW}{steps[6]}{Style.RESET_ALL}")
            try:
                tender_info = results.get('tender_info')
                if tender_info and isinstance(tender_info, dict):
                    tender_name = tender_info.get('name', 'tender')
                else:
                    tender_name = 'tender'
                report_paths = self.report_generator.generate_all_reports(results, tender_name)
                results['report_paths'] = report_paths
                print(f"{Fore.GREEN}✓ Отчеты созданы: {report_paths}{Style.RESET_ALL}")
                pbar.update(1)
            except Exception as e:
                import traceback
                error_details = traceback.format_exc()
                print(f"\n{Fore.RED}✗ Ошибка генерации отчетов: {e}{Style.RESET_ALL}")
                print(f"{Fore.RED}Детали ошибки:\n{error_details}{Style.RESET_ALL}")
                results['report_paths'] = {}
                results['report_generation_error'] = str(e)

        # ============================================================
        # СОХРАНЕНИЕ В КЭШ (V2.0)
        # ============================================================
        if use_cache and tender_number and self.db:
            try:
                # Вычисляем хэш для сохранения
                from bot.db import Database
                documentation = [
                    {'filename': f['file_name'], 'content': f.get('text', '')}
                    for f in results.get('files_info', [])
                ]
                doc_hash = Database.compute_documentation_hash(documentation)

                # Извлекаем метаданные для индексации
                tender_info = results.get('tender_info', {})
                nmck = tender_info.get('nmck')

                # Извлекаем score и recommendation если есть
                score = None
                recommendation = None
                if 'analysis_summary' in results:
                    summary = results['analysis_summary']
                    if isinstance(summary.get('confidence_score'), (int, float)):
                        score = int(summary['confidence_score'])
                    # Определяем рекомендацию на основе is_suitable
                    if summary.get('is_suitable'):
                        recommendation = 'participate' if score and score > 80 else 'consider'
                    else:
                        recommendation = 'skip'

                # Сохраняем в кэш с TTL 14 дней
                await self.db.save_analysis(
                    tender_number=tender_number,
                    doc_hash=doc_hash,
                    analysis_result=results,
                    score=score,
                    recommendation=recommendation,
                    nmck=nmck,
                    ttl_days=14
                )

                print(f"{Fore.GREEN}💾 Результаты сохранены в кэш (TTL: 14 дней){Style.RESET_ALL}")

            except Exception as e:
                print(f"{Fore.YELLOW}⚠️  Ошибка сохранения в кэш: {e}{Style.RESET_ALL}")
                # Не прерываем выполнение при ошибке кэширования

        return results

    def display_summary(self, results: Dict[str, Any]):
        """Отображает краткую сводку результатов в консоли."""
        print(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}  РЕЗУЛЬТАТЫ АНАЛИЗА{Style.RESET_ALL}")
        print(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")

        tender_info = results.get('tender_info', {})
        gaps = results.get('gaps', [])

        print(f"{Fore.WHITE}Тендер:{Style.RESET_ALL} {tender_info.get('name', 'Н/Д')}")
        print(f"{Fore.WHITE}Заказчик:{Style.RESET_ALL} {tender_info.get('customer', 'Н/Д')}")
        nmck = tender_info.get('nmck') or 0
        print(f"{Fore.WHITE}НМЦК:{Style.RESET_ALL} {nmck:,.0f} руб." if nmck else f"{Fore.WHITE}НМЦК:{Style.RESET_ALL} Н/Д")

        # Сроки
        if tender_info.get('deadline_submission'):
            print(f"{Fore.WHITE}Срок подачи заявок:{Style.RESET_ALL} {tender_info.get('deadline_submission', 'Н/Д')}")
        if tender_info.get('deadline_execution'):
            print(f"{Fore.WHITE}Срок исполнения:{Style.RESET_ALL} {tender_info.get('deadline_execution', 'Н/Д')}\n")

        # Обеспечения
        guarantee_app = tender_info.get('guarantee_application')
        guarantee_contract = tender_info.get('guarantee_contract')
        if guarantee_app and isinstance(guarantee_app, (int, float)) and guarantee_app > 0:
            print(f"{Fore.WHITE}Обеспечение заявки:{Style.RESET_ALL} {guarantee_app:,.0f} руб.")
        if guarantee_contract and isinstance(guarantee_contract, (int, float)) and guarantee_contract > 0:
            print(f"{Fore.WHITE}Обеспечение контракта:{Style.RESET_ALL} {guarantee_contract:,.0f} руб.\n")

        # Подсчет пробелов
        gaps_count = {
            'critical': len([g for g in gaps if g.get('criticality') == 'CRITICAL']),
            'high': len([g for g in gaps if g.get('criticality') == 'HIGH']),
            'medium': len([g for g in gaps if g.get('criticality') == 'MEDIUM']),
            'low': len([g for g in gaps if g.get('criticality') == 'LOW'])
        }

        print(f"{Fore.WHITE}Выявлено пробелов в документации:{Style.RESET_ALL}")
        print(f"  {Fore.RED}Критичных:{Style.RESET_ALL} {gaps_count['critical']}")
        print(f"  {Fore.YELLOW}Важных:{Style.RESET_ALL} {gaps_count['high']}")
        print(f"  {Fore.CYAN}Средних:{Style.RESET_ALL} {gaps_count['medium']}")
        print(f"  {Fore.WHITE}Низких:{Style.RESET_ALL} {gaps_count['low']}\n")

        # Отчеты
        report_paths = results.get('report_paths', {})
        if report_paths:
            print(f"{Fore.GREEN}Отчеты созданы:{Style.RESET_ALL}")
            for format, path in report_paths.items():
                print(f"  {format.upper()}: {path}")

        print(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")

    def open_html_report(self, html_path: str):
        """Открывает HTML отчет в браузере."""
        if os.path.exists(html_path):
            try:
                webbrowser.open(f'file://{os.path.abspath(html_path)}')
                print(f"{Fore.GREEN}HTML отчет открыт в браузере{Style.RESET_ALL}")
            except Exception as e:
                print(f"{Fore.YELLOW}Не удалось открыть браузер: {e}{Style.RESET_ALL}")

    def search_tenders(self, criteria_text: str, max_results: int = 10, min_score: int = 50):
        """
        Ищет тендеры по текстовым критериям.

        Args:
            criteria_text: Текстовое описание критериев поиска
            max_results: Максимальное количество результатов
            min_score: Минимальный балл релевантности
        """
        print(f"{Fore.CYAN}\nПоиск тендеров по критериям...{Style.RESET_ALL}\n")

        # Выполняем поиск и анализ
        results = self.tender_searcher.search_and_analyze(
            criteria_text=criteria_text,
            max_results=max_results,
            min_relevance_score=min_score
        )

        # Отображаем результаты
        self.tender_searcher.display_results(results)

        # Сохраняем результаты
        output_dir = self.paths['output'] / 'search_results'
        output_dir.mkdir(exist_ok=True)

        timestamp = results.get('timestamp', '').replace(':', '-').replace('.', '-')
        output_file = output_dir / f'tender_search_{timestamp}.json'

        self.tender_searcher.export_results(results, str(output_file))

        return results


def main():
    """Главная функция."""
    parser = argparse.ArgumentParser(
        description='ИИ-агент для анализа тендерной документации',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  python main.py document.pdf
  python main.py doc1.pdf doc2.docx doc3.pdf
  python main.py --path /path/to/tender/docs/*.pdf
        """
    )

    parser.add_argument(
        'files',
        nargs='*',
        help='Пути к файлам тендерной документации (PDF, DOCX)'
    )

    parser.add_argument(
        '--path',
        help='Путь к директории или файлам (альтернатива positional аргументам)'
    )

    parser.add_argument(
        '--no-browser',
        action='store_true',
        help='Не открывать HTML отчет в браузере'
    )

    parser.add_argument(
        '--search',
        type=str,
        help='Поиск тендеров по текстовым критериям (например: "компьютерное оборудование в Москве от 500 тыс до 5 млн")'
    )

    parser.add_argument(
        '--max-results',
        type=int,
        default=10,
        help='Максимальное количество результатов поиска (по умолчанию: 10)'
    )

    parser.add_argument(
        '--min-score',
        type=int,
        default=50,
        help='Минимальный балл релевантности 0-100 (по умолчанию: 50)'
    )

    args = parser.parse_args()

    # Создаем агента
    try:
        agent = TenderAnalysisAgent()
    except Exception as e:
        print(f"{Fore.RED}Критическая ошибка инициализации: {e}{Style.RESET_ALL}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Режим поиска тендеров
    if args.search:
        try:
            agent.search_tenders(
                criteria_text=args.search,
                max_results=args.max_results,
                min_score=args.min_score
            )
            print(f"{Fore.GREEN}Поиск завершен!{Style.RESET_ALL}\n")
        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}Поиск прерван пользователем{Style.RESET_ALL}")
            sys.exit(0)
        except Exception as e:
            print(f"\n{Fore.RED}Критическая ошибка поиска: {e}{Style.RESET_ALL}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
        return

    # Режим анализа документации
    # Определяем файлы для анализа
    file_paths = args.files

    if args.path:
        path = Path(args.path)
        if path.is_dir():
            # Ищем все PDF и DOCX в директории
            file_paths = list(path.glob('*.pdf')) + list(path.glob('*.docx'))
            file_paths = [str(f) for f in file_paths]
        elif path.is_file():
            file_paths = [str(path)]
        else:
            print(f"{Fore.RED}Ошибка: {args.path} не является файлом или директорией{Style.RESET_ALL}")
            sys.exit(1)

    if not file_paths:
        print(f"{Fore.YELLOW}Не указаны файлы для анализа{Style.RESET_ALL}")
        print(f"Используйте: python main.py <файл1> <файл2> ...")
        print(f"Или: python main.py --path /path/to/files")
        sys.exit(1)

    # Проверяем существование файлов
    valid_files = []
    for fp in file_paths:
        if os.path.exists(fp):
            valid_files.append(fp)
        else:
            print(f"{Fore.YELLOW}Предупреждение: файл не найден - {fp}{Style.RESET_ALL}")

    if not valid_files:
        print(f"{Fore.RED}Ошибка: не найдено ни одного валидного файла{Style.RESET_ALL}")
        sys.exit(1)

    print(f"{Fore.CYAN}Файлы для анализа ({len(valid_files)}):{Style.RESET_ALL}")
    for fp in valid_files:
        print(f"  - {fp}")

    try:
        # Инициализируем БД для кэширования
        import asyncio
        from bot.db import get_database
        agent.db = asyncio.run(get_database())

        # Запускаем анализ (теперь асинхронный)
        results = asyncio.run(agent.analyze_tender(valid_files))

        # Отображаем сводку
        agent.display_summary(results)

        # Открываем HTML отчет
        if not args.no_browser and results.get('report_paths', {}).get('html'):
            agent.open_html_report(results['report_paths']['html'])

        print(f"{Fore.GREEN}Анализ завершен успешно!{Style.RESET_ALL}\n")

    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Анализ прерван пользователем{Style.RESET_ALL}")
        sys.exit(0)
    except Exception as e:
        print(f"\n{Fore.RED}Критическая ошибка: {e}{Style.RESET_ALL}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

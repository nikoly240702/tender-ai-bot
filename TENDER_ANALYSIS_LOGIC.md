# Логика AI-анализа тендеров

## Обзор системы

Система анализа тендеров состоит из нескольких компонентов, которые работают последовательно:

1. **Извлечение текста из документов** (PDF/DOCX)
2. **Приоритетный анализ проекта контракта** (если есть)
3. **Основной анализ документации**
4. **Детекция пробелов в информации**
5. **Генерация вопросов для заказчика**
6. **Извлечение контактов**
7. **Генерация отчетов** (HTML, JSON, Markdown)

---

## Файл 1: main.py - Основной оркестратор

```python
# /Users/nikolaichizhik/tender-ai-agent/main.py

class TenderAnalysisAgent:
    """
    Главный класс агента для анализа тендеров.
    Координирует работу всех компонентов.
    """
    
    def analyze_tender(self, file_paths: List[str]) -> Dict[str, Any]:
        """
        Полный анализ тендера.
        
        Этапы:
        1. Извлечение текста из всех документов
        2. Приоритетный анализ проекта контракта (если есть)
        3. Анализ всей документации через Claude
        4. Детекция пробелов в информации
        5. Генерация вопросов для заказчика
        6. Извлечение контактной информации
        7. Генерация отчетов
        """
        pass
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
            ollama_base_url=self.llm_config.get('ollama_base_url')
        )
        self.contact_extractor = ContactExtractor()
        self.template_generator = TemplateGenerator()
        self.report_generator = ReportGenerator(str(self.paths['output']))
        self.tender_searcher = TenderSearcher(self.tender_analyzer)

    def analyze_tender(self, file_paths: List[str]) -> Dict[str, Any]:
        """
        Выполняет полный анализ тендера.

        Args:
            file_paths: Список путей к файлам тендерной документации

        Returns:
            Полный словарь с результатами анализа
        """
        print(f"{Fore.CYAN}\nНачинаем анализ тендера...{Style.RESET_ALL}\n")

        # Создаем прогресс-бар
        steps = [
            "Извлечение текста из документов",
            "Приоритетный анализ проекта контракта",
            "Анализ документации через Claude",
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
                analysis = self.tender_analyzer.analyze_documentation(
                    results['extracted_text'],
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

            # Шаг 5: Генерация вопросов
            pbar.set_description(f"{Fore.YELLOW}{steps[4]}{Style.RESET_ALL}")
            try:
                questions = self.tender_analyzer.generate_questions(
                    results['gaps'],
                    results['extracted_text']
                )
                results['questions'] = questions
                pbar.update(1)
            except Exception as e:
                print(f"\n{Fore.RED}✗ Ошибка генерации вопросов: {e}{Style.RESET_ALL}")
                results['questions'] = {'critical': [], 'important': [], 'optional': []}

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
                tender_name = results['tender_info'].get('name', 'tender')
                report_paths = self.report_generator.generate_all_reports(results, tender_name)
                results['report_paths'] = report_paths
                pbar.update(1)
            except Exception as e:
                print(f"\n{Fore.RED}✗ Ошибка генерации отчетов: {e}{Style.RESET_ALL}")
                results['report_paths'] = {}

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
        # Запускаем анализ
        results = agent.analyze_tender(valid_files)

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

---

## Файл 2: tender_analyzer.py - Ключевые промпты для LLM

```python
# /Users/nikolaichizhik/tender-ai-agent/src/analyzers/tender_analyzer.py
"""
Модуль для анализа тендерной документации с использованием LLM API.
Поддерживает: Anthropic Claude, OpenAI, Groq, Google Gemini, Ollama.
"""

import json
import time
from typing import Dict, Any, Optional, List

try:
    from .llm_adapter import LLMFactory, LLMAdapter
except ImportError:
    from llm_adapter import LLMFactory, LLMAdapter


class TenderAnalyzer:
    """Универсальный анализатор тендерной документации с поддержкой различных LLM провайдеров."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        provider: str = "groq",
        model: Optional[str] = None,
        model_premium: Optional[str] = None,
        model_fast: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        timeout: int = 120,
        max_retries: int = 3,
        retry_delay: int = 2,
        **kwargs
    ):
        """
        Инициализация анализатора с поддержкой различных LLM провайдеров.
        Поддерживает гибридный режим с двумя моделями: премиум для критичных задач и быстрая для простых.

        Args:
            api_key: API ключ (не требуется для Ollama)
            provider: Провайдер LLM ('anthropic', 'openai', 'groq', 'gemini', 'ollama')
            model: Название модели (если None, используется рекомендуемая) - для обратной совместимости
            model_premium: Модель для критичных задач (анализ контракта, основной анализ)
            model_fast: Модель для простых задач (детекция пробелов, генерация вопросов)
            max_tokens: Максимальное количество токенов в ответе
            temperature: Температура генерации (0-1)
            timeout: Таймаут запроса в секундах
            max_retries: Максимальное количество попыток при ошибке
            retry_delay: Задержка между попытками в секундах
        """
        self.provider = provider
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.kwargs = kwargs

        # Определяем модели для гибридной системы
        if model_premium and model_fast:
            # Гибридный режим
            self.model_premium = model_premium
            self.model_fast = model_fast
            self.hybrid_mode = True
            print(f"🔀 Гибридный режим: {model_premium} (критичные) + {model_fast} (простые)")
        else:
            # Обычный режим - одна модель для всех задач
            self.model = model or LLMFactory.RECOMMENDED_MODELS.get(provider)
            self.model_premium = self.model
            self.model_fast = self.model
            self.hybrid_mode = False

        # Создаем основной LLM адаптер (премиум)
        self.llm_premium = LLMFactory.create(
            provider=provider,
            api_key=api_key,
            model=self.model_premium,
            max_tokens=max_tokens,
            temperature=temperature,
            max_retries=max_retries,
            retry_delay=retry_delay,
            **kwargs
        )

        # Если гибридный режим - создаем второй адаптер (быстрый)
        if self.hybrid_mode:
            self.llm_fast = LLMFactory.create(
                provider=provider,
                api_key=api_key,
                model=self.model_fast,
                max_tokens=max_tokens,
                temperature=temperature,
                max_retries=max_retries,
                retry_delay=retry_delay,
                **kwargs
            )
        else:
            self.llm_fast = self.llm_premium

        # Для обратной совместимости
        self.llm = self.llm_premium

    def detect_tender_type(self, documentation_text: str) -> str:
        """
        Определяет тип закупки: товары, работы или услуги.

        Args:
            documentation_text: Текст документации

        Returns:
            'GOODS' (товары), 'WORKS' (работы), или 'SERVICES' (услуги)
        """
        # Простая эвристика по ключевым словам
        doc_lower = documentation_text.lower()

        # Подсчет упоминаний ключевых слов
        goods_keywords = ['поставк', 'товар', 'оборудовани', 'комплектующ', 'материал']
        works_keywords = ['выполнен работ', 'строител', 'ремонт', 'монтаж', 'установк']
        services_keywords = ['оказан услуг', 'обслуживан', 'сопровожден', 'консультаци', 'поддержк']

        goods_score = sum(doc_lower.count(kw) for kw in goods_keywords)
        works_score = sum(doc_lower.count(kw) for kw in works_keywords)
        services_score = sum(doc_lower.count(kw) for kw in services_keywords)

        # Определяем тип по максимальному количеству упоминаний
        max_score = max(goods_score, works_score, services_score)

        if max_score == 0:
            return 'SERVICES'  # По умолчанию
        elif max_score == goods_score:
            return 'GOODS'
        elif max_score == works_score:
            return 'WORKS'
        else:
            return 'SERVICES'

    def analyze_contract_terms(self, contract_text: str) -> Dict[str, Any]:
        """
        Извлекает финансовые условия из проекта контракта/договора.
        КРИТИЧНАЯ ЗАДАЧА - использует премиум модель.

        Args:
            contract_text: Полный текст проекта контракта

        Returns:
            Словарь с условиями оплаты и обеспечениями
        """
        system_prompt = """Ты — эксперт по государственным контрактам РФ с глубоким знанием 44-ФЗ и типовых форм контрактов."""

        # Ограничиваем размер текста для gpt-4o (rate limit 30k токенов/мин)
        # ~4 символа = 1 токен, оставляем запас на промпт
        max_chars = 40000  # ~10k токенов
        contract_text_limited = contract_text[:max_chars]

        user_prompt = f"""# ЗАДАЧА
Извлечь из проекта контракта ТОЧНЫЕ финансовые условия.

# ПРОЕКТ КОНТРАКТА:
{contract_text_limited}

# ЧТО НУЖНО НАЙТИ:

1. **УСЛОВИЯ ОПЛАТЫ** - ищи раздел "Цена контракта и порядок расчетов" или "Порядок расчетов":
   - Срок оплаты (например: "не позднее 7 рабочих дней", "в течение 30 календарных дней")
   - Момент оплаты (после приемки, после поставки, в течение...)
   - Наличие аванса/предоплаты и процент (например: "30% аванс")
   - Порядок оплаты (единовременно, частями, поэтапно)

2. **ОБЕСПЕЧЕНИЕ ЗАЯВКИ** - ищи в разделе об обеспечении:
   - Размер в рублях или процент от НМЦК

3. **ОБЕСПЕЧЕНИЕ КОНТРАКТА** - ищи в разделе об обеспечении:
   - Размер в рублях или процент от цены контракта

# ФОРМАТ ОТВЕТА (JSON):

{{
    "payment_terms": {{
        "payment_deadline": "ТОЧНЫЙ срок из контракта (например: '7 рабочих дней', '30 календарных дней') или 'Не указан'",
        "payment_moment": "когда производится оплата (например: 'после приемки товара', 'после подписания акта')",
        "prepayment_percent": числовое_значение_процента_аванса_или_0,
        "payment_schedule": "описание порядка (например: 'единовременно', 'в 2 этапа: 30% аванс, 70% после поставки')"
    }},
    "guarantee_application": числовое_значение_в_рублях_или_null,
    "guarantee_contract": числовое_значение_в_рублях_или_null
}}

КРИТИЧНО: Найди КОНКРЕТНЫЙ срок оплаты с указанием количества дней. Не пиши "после приемки" - ищи "7 рабочих дней", "30 календарных дней" и т.д.

Верни ТОЛЬКО JSON без комментариев."""

        response_text = self._make_api_call(system_prompt, user_prompt, response_format="json")

        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                return json.loads(response_text[json_start:json_end])
            raise ValueError("Не удалось распарсить условия контракта")

    def _make_api_call(
        self,
        system_prompt: str,
        user_prompt: str,
        response_format: str = "text",
        use_premium: bool = True
    ) -> str:
        """
        Выполняет запрос к LLM API с обработкой ошибок.

        Args:
            system_prompt: Системный промпт
            user_prompt: Пользовательский промпт
            response_format: Формат ответа ('text' или 'json')
            use_premium: Использовать премиум модель (True) или быструю (False)

        Returns:
            Ответ от LLM

        Raises:
            Exception: При ошибке API после всех попыток
        """
        # Выбираем модель в зависимости от важности задачи
        llm = self.llm_premium if use_premium else self.llm_fast

        try:
            response_text = llm.generate(system_prompt, user_prompt)

            # Проверяем на пустой ответ
            if not response_text or not response_text.strip():
                raise ValueError("Получен пустой ответ от LLM")

            # Валидируем JSON если требуется
            if response_format == "json":
                # Убираем markdown code blocks если есть
                cleaned_text = response_text.strip()
                if cleaned_text.startswith('```json'):
                    cleaned_text = cleaned_text[7:]
                if cleaned_text.startswith('```'):
                    cleaned_text = cleaned_text[3:]
                if cleaned_text.endswith('```'):
                    cleaned_text = cleaned_text[:-3]
                cleaned_text = cleaned_text.strip()

                try:
                    json.loads(cleaned_text)
                    return cleaned_text
                except json.JSONDecodeError as e:
                    # Пытаемся извлечь JSON из ответа
                    json_start = cleaned_text.find('{')
                    json_end = cleaned_text.rfind('}') + 1
                    if json_start < 0:
                        json_start = cleaned_text.find('[')
                        json_end = cleaned_text.rfind(']') + 1

                    if json_start >= 0 and json_end > json_start:
                        json_text = cleaned_text[json_start:json_end]
                        try:
                            json.loads(json_text)
                            return json_text
                        except:
                            pass
                    raise ValueError(f"Ответ LLM не является валидным JSON: {str(e)}\nОтвет: {response_text[:200]}")

            return response_text

        except Exception as e:
            raise Exception(f"Ошибка при запросе к {self.provider} API: {str(e)}")

    def analyze_documentation(
        self,
        documentation_text: str,
        company_profile: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Выполняет полный анализ тендерной документации.

        Args:
            documentation_text: Текст извлеченной документации
            company_profile: Профиль компании из конфигурации

        Returns:
            Словарь с результатами анализа:
            {
                'tender_info': {...},
                'requirements': {...},
                'gaps': [...],
                'risks': [...],
                'recommendations': str
            }
        """
        system_prompt = """Ты — эксперт по анализу тендерной документации в России с 15-летним опытом работы в госзакупках.
Твоя задача - ПОЛНОСТЬЮ ЗАМЕНИТЬ человеческий анализ, предоставив исчерпывающую информацию для принятия решения об участии."""

        user_prompt = f"""# СОБЫТИЕ
Компания получила тендерную документацию и должна принять решение об участии в государственной закупке.

# ПРЕДШЕСТВУЮЩИЙ КОНТЕКСТ
Компания специализируется на участии в тендерах и использует автоматизированную систему для первичного анализа документации. Необходимо быстро оценить соответствие тендера возможностям компании и выявить ключевые параметры для принятия решения. Анализ должен быть настолько качественным, чтобы ПОЛНОСТЬЮ заменить работу человека-аналитика.

# СУБЪЕКТ
Ты — эксперт-аналитик по государственным закупкам в России, который помогает компаниям оценивать тендеры.

# ОБЪЕКТ
Тендерная документация и профиль компании-участника.

ТЕНДЕРНАЯ ДОКУМЕНТАЦИЯ:
{documentation_text[:60000]}

ПРОФИЛЬ КОМПАНИИ:
{json.dumps(company_profile, ensure_ascii=False, indent=2)}

# ЦЕЛЬ
Провести комплексный анализ тендерной документации для принятия обоснованного решения об участии в закупке.

# ЗАДАЧА
Извлечь из документации все критически важные параметры: информацию о заказчике, сроки, требования, финансовые условия, риски. Сопоставить требования тендера с возможностями компании. Выявить потенциальные проблемы и риски.

# СРЕДСТВА
- Тендерная документация (контракты, технические задания, извещения)
- Профиль компании с указанием компетенций и ресурсов
- Знание законодательства о госзакупках РФ (44-ФЗ, 223-ФЗ)
- Опыт анализа тысяч тендеров

# ДЕЙСТВИЯ

## Этап 1: МЕТОДИЧНЫЙ ПОИСК ИНФОРМАЦИИ В РАЗДЕЛАХ ДОКУМЕНТАЦИИ

1. **АНАЛИЗ СТРУКТУРЫ ДОКУМЕНТАЦИИ** - определи все разделы и файлы:
   - Извещение о закупке
   - Техническое задание (ТЗ) / Спецификация / Описание объекта закупки
   - Проект контракта / Проект договора
   - Инструкция по заполнению заявки
   - Приложения (графики, формы, образцы)
   - Другие документы

2. **ИЗВЛЕЧЕНИЕ БАЗОВОЙ ИНФОРМАЦИИ О ТЕНДЕРЕ**:
   - Название закупки
   - Полное наименование заказчика с ИНН
   - НМЦК (начальная максимальная цена контракта)
   - Размер обеспечения заявки и обеспечения контракта

3. **КРИТИЧНО: ИЗВЛЕЧЕНИЕ СРОКОВ - ИЩИ В СЛЕДУЮЩИХ МЕСТАХ**:

   a) **Срок подачи заявок**:
      - Извещение о закупке - раздел "Порядок подачи заявок"
      - Может быть указан как дата и время (например: "до 15.03.2024 10:00 МСК")

   b) **Срок исполнения контракта / Срок поставки / Срок выполнения работ**:
      - ПРОЕКТ КОНТРАКТА - раздел "Срок действия контракта" или "Сроки поставки"
      - ТЕХНИЧЕСКОЕ ЗАДАНИЕ - раздел "Сроки выполнения работ" или "График поставки"
      - ПРИЛОЖЕНИЯ - "График выполнения работ", "Календарный план", "График поставки"
      - Ищи конкретные даты ИЛИ относительные сроки (например: "в течение 30 календарных дней с даты заключения контракта")

   c) **Промежуточные сроки и этапы** (если есть):
      - Графики выполнения работ в приложениях
      - Календарные планы
      - Сроки по этапам в техническом задании
      - Даты контрольных точек

4. **ИЗВЛЕЧЕНИЕ ДЕТАЛЬНОГО ОПИСАНИЯ ТОВАРОВ/УСЛУГ/РАБОТ**:

   Ищи в следующих разделах (по приоритету):
   - ТЕХНИЧЕСКОЕ ЗАДАНИЕ - основной источник требований
   - СПЕЦИФИКАЦИЯ / Приложение "Описание объекта закупки"
   - Проект контракта - раздел "Предмет контракта"

   Для КАЖДОГО товара/услуги извлеки:
   - Точное наименование
   - Количество и единицу измерения
   - ВСЕ технические характеристики и параметры
   - Требования к качеству, сертификации, стандартам
   - Особые условия (гарантия, обучение, монтаж и т.д.)

5. **КРИТИЧНО: УСЛОВИЯ ОПЛАТЫ - ИЩИ ТОЛЬКО В ПРОЕКТЕ КОНТРАКТА**:

   Раздел для поиска: "Цена контракта и порядок расчетов" или "Порядок расчетов"

   Извлеки:
   - **Срок оплаты** - ТОЧНЫЙ срок в днях (например: "не позднее 10 рабочих дней", "в течение 30 календарных дней")
   - **Момент оплаты** - от какого события отсчитывается срок (например: "после подписания акта приемки", "после поставки товара")
   - **Аванс/предоплата** - процент и условия (например: "30% аванс в течение 5 рабочих дней после заключения контракта")
   - **Порядок оплаты** - единовременно или частями, привязка к этапам

   ВАЖНО: Если информации об оплате НЕТ в проекте контракта - укажи null, а не выдумывай!

6. **СИСТЕМАТИЗАЦИЯ ТРЕБОВАНИЙ**:
   - Технические требования (из ТЗ)
   - Квалификационные требования (опыт, лицензии, персонал)
   - Финансовые требования (обеспечения, оборот)
   - Документальные требования (сертификаты, декларации)

7. **СОПОСТАВЛЕНИЕ С ВОЗМОЖНОСТЯМИ КОМПАНИИ**:
   - Сравни каждое требование с профилем компании
   - Выяви несоответствия и пробелы
   - Оцени критичность каждого пробела

8. **ОЦЕНКА РИСКОВ**:
   - Финансовые риски (размер обеспечений, условия оплаты)
   - Риски исполнения (сжатые сроки, сложные требования)
   - Репутационные риски (штрафы, неустойки)
   - Юридические риски (спорные формулировки)

9. **ФОРМИРОВАНИЕ РЕКОМЕНДАЦИИ**:
   - Участвовать / Не участвовать / Уточнить у заказчика
   - Обоснование решения
   - Ключевые факторы решения

# РЕЗУЛЬТАТ
Верни структурированный анализ в формате JSON:

{{
    "tender_info": {{
        "name": "Точное название тендера из документации",
        "customer": "Полное наименование заказчика",
        "customer_inn": "ИНН заказчика если найден",
        "nmck": числовое_значение_без_пробелов,
        "deadline_submission": "YYYY-MM-DD или YYYY-MM-DD HH:MM если указано время",
        "deadline_execution": "YYYY-MM-DD ИЛИ описание относительного срока (например: '30 календарных дней с даты заключения контракта')",
        "execution_stages": [
            {{
                "stage_name": "Название этапа",
                "deadline": "Срок выполнения этапа",
                "description": "Описание что должно быть сделано"
            }}
        ],
        "guarantee_application": числовое_значение,
        "guarantee_contract": числовое_значение,
        "payment_terms": {{
            "prepayment_percent": числовое_значение_или_0,
            "payment_schedule": "Описание порядка оплаты",
            "payment_deadline": "ТОЧНЫЙ срок оплаты (например: '10 рабочих дней после приемки')",
            "payment_moment": "От какого события отсчитывается срок"
        }},
        "products_or_services": [
            {{
                "name": "Наименование товара/услуги",
                "quantity": числовое_значение,
                "unit": "единица измерения",
                "specifications": {{
                    "параметр1": "значение1",
                    "параметр2": "значение2"
                }},
                "description": "Полное описание из документации"
            }}
        ]
    }},
    "requirements": {{
        "technical": ["Конкретное техническое требование 1", "Конкретное техническое требование 2"],
        "qualification": ["Требование к опыту/лицензиям 1", "Требование к опыту/лицензиям 2"],
        "financial": ["Финансовое требование 1", "Финансовое требование 2"],
        "documentation": ["Обязательный документ 1", "Обязательный документ 2"]
    }},
    "gaps": [
        {{
            "category": "технические требования|квалификация|финансы|сроки|документация",
            "issue": "Четкое описание несоответствия или пробела",
            "impact": "Конкретное влияние на возможность участия",
            "criticality": "CRITICAL|HIGH|MEDIUM|LOW"
        }}
    ],
    "risks": [
        {{
            "type": "Финансовый|Репутационный|Исполнения|Юридический",
            "description": "Подробное описание риска",
            "probability": "HIGH|MEDIUM|LOW",
            "impact": "HIGH|MEDIUM|LOW"
        }}
    ],
    "recommendations": "Развернутая рекомендация: участвовать/не участвовать/уточнить, с обоснованием"
}}

ВАЖНО:
- Верни ТОЛЬКО валидный JSON, без markdown разметки и дополнительного текста
- Все числовые значения должны быть числами, не строками
- Даты в формате YYYY-MM-DD
- Если информация не найдена, укажи null или пустой массив []"""

        response_text = self._make_api_call(system_prompt, user_prompt, response_format="json")

        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            # Пытаемся извлечь JSON из ответа
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                return json.loads(response_text[json_start:json_end])
            raise ValueError("Не удалось распарсить JSON из ответа Claude")

    def detect_gaps(self, documentation_text: str) -> List[Dict[str, Any]]:
        """
        Детектирует пробелы и недостающую информацию в документации.
        ПРОСТАЯ ЗАДАЧА - использует быструю модель.

        Args:
            documentation_text: Текст документации

        Returns:
            Список пробелов с метаданными
        """
        system_prompt = """Ты — эксперт по экспертизе тендерной документации с опытом работы в контрольно-ревизионных органах в сфере госзакупок.
КРИТИЧНО ВАЖНО: Ты должен выявлять ТОЛЬКО реальные пробелы - информацию, которой ДЕЙСТВИТЕЛЬНО НЕТ в документации. НЕ задавай вопросы о том, что уже указано в документах!"""

        user_prompt = f"""# СОБЫТИЕ
Компания получила тендерную документацию и перед подачей заявки необходимо выявить все пробелы, неясности и противоречия в документах.

# ПРЕДШЕСТВУЮЩИЙ КОНТЕКСТ
В тендерной документации часто встречаются недостающие данные, противоречивые требования, неясные формулировки. Невыявленные пробелы могут привести к отклонению заявки, некорректному ценообразованию или невозможности исполнения контракта. Критически важно выявить все проблемы ДО подачи заявки.

ПРОБЛЕМА: Система часто формирует вопросы об информации, которая УЖЕ ЕСТЬ в документации, но в других разделах. Это создает лишнюю работу и показывает непрофессионализм.

ЗАДАЧА: Найти ТОЛЬКО то, чего РЕАЛЬНО НЕТ в документации. Перед тем как отметить пробел - ДВАЖДЫ ПРОВЕРЬ, что этой информации точно нет нигде в документах.

# СУБЪЕКТ
Ты — эксперт по аудиту тендерной документации, который проверяет полноту и корректность документов перед участием в закупке.

# ОБЪЕКТ
Тендерная документация, которую необходимо проверить на наличие пробелов.

ДОКУМЕНТАЦИЯ:
{documentation_text[:60000]}

# ЦЕЛЬ
Выявить ВСЕ пробелы, противоречия, неясности и недостающую информацию в тендерной документации.

# ЗАДАЧА
Провести детальный аудит документации и составить полный список проблем, которые могут помешать успешному участию в тендере. Определить критичность каждого пробела для принятия решения.

# СРЕДСТВА
- Полный текст тендерной документации
- Знание требований 44-ФЗ и 223-ФЗ к документации
- Понимание типичных проблем в тендерах
- Опыт анализа успешных и неуспешных заявок

# ДЕЙСТВИЯ

## КРИТИЧНО: ДВУХЭТАПНАЯ ПРОВЕРКА ПРОБЕЛОВ

### Этап 1: ПОИСК ИНФОРМАЦИИ ПО ВСЕЙ ДОКУМЕНТАЦИИ

Перед тем как отметить информацию как отсутствующую, ПРОВЕРЬ ВСЕ РАЗДЕЛЫ:

1. **Сроки подачи заявок**:
   - Извещение о закупке
   - Документация (первые страницы)

2. **Сроки исполнения контракта**:
   - Проект контракта - раздел "Сроки"
   - Техническое задание - раздел "Сроки выполнения работ"
   - Приложения с графиками

3. **Технические характеристики**:
   - Техническое задание
   - Спецификация
   - Приложения к ТЗ

4. **Условия оплаты**:
   - Проект контракта - раздел "Порядок расчетов"
   - НЕ извещение, НЕ ТЗ - только проект контракта!

5. **Обеспечения (заявки, контракта)**:
   - Извещение о закупке
   - Документация - раздел об обеспечении
   - Проект контракта

6. **Критерии оценки**:
   - Документация - раздел "Критерии оценки заявок"
   - Инструкция по заполнению заявки

7. **Штрафы и ответственность**:
   - Проект контракта - раздел "Ответственность сторон"

### Этап 2: ФИКСАЦИЯ РЕАЛЬНЫХ ПРОБЕЛОВ

ТОЛЬКО после того, как ты проверил ВСЕ разделы и убедился, что информации ДЕЙСТВИТЕЛЬНО НЕТ нигде:

1. Опиши пробел ТОЧНО - что именно отсутствует
2. Укажи где искал и не нашел
3. Оцени критичность:
   - CRITICAL: Без этого невозможно подать заявку или рассчитать цену
   - HIGH: Важная информация, существенно влияющая на решение
   - MEDIUM: Желательно уточнить для снижения рисков
   - LOW: Не критично, но полезно

4. Сформулируй вопрос для заказчика

### Этап 3: ДОПОЛНИТЕЛЬНЫЕ ПРОВЕРКИ

1. Найди противоречия между разделами (если одно и то же указано по-разному)
2. Найди неясные формулировки (можно понять двояко)
3. Найди недостающую информацию для ценообразования
4. Проверь наличие контактов для уточнений

# РЕЗУЛЬТАТ
Верни список всех выявленных пробелов в формате JSON:

[
    {{
        "category": "сроки|технические_требования|квалификация|финансы|документация|контакты|критерии_оценки|приемка|штрафы",
        "issue": "Точное описание пробела или противоречия с указанием что именно отсутствует",
        "impact": "Конкретное влияние на участие: что нельзя сделать или какие риски возникают",
        "question": "Четко сформулированный вопрос для заказчика в деловом стиле",
        "criticality": "CRITICAL|HIGH|MEDIUM|LOW",
        "reference": "Указание на раздел/пункт документации где выявлен пробел"
    }}
]

Уровни критичности:
- CRITICAL: Без этой информации невозможно подать заявку или рассчитать цену
- HIGH: Важная информация, существенно влияющая на решение об участии
- MEDIUM: Желательно уточнить для снижения рисков
- LOW: Не критично, но полезно для полноты информации

КРИТИЧНО ВАЖНЫЕ ПРАВИЛА:

1. **НЕ ВКЛЮЧАЙ В СПИСОК**, если информация ЕСТЬ где-то в документации:
   - Если срок оплаты указан в проекте контракта - НЕ спрашивай о нем
   - Если характеристики указаны в ТЗ - НЕ спрашивай о них
   - Если сроки исполнения есть в графике - НЕ спрашивай о них

2. **ОБЯЗАТЕЛЬНО ПРОВЕРЬ** перед добавлением пробела:
   - Проверил ли я ВСЕ разделы документации?
   - Точно ли этой информации НЕТ нигде?
   - Может она указана под другим названием?

3. **КАЧЕСТВО ВАЖНЕЕ КОЛИЧЕСТВА**:
   - Лучше 3 реальных пробела, чем 10 ложных вопросов
   - Каждый пробел должен быть проверяемым
   - Вопросы должны быть профессиональными

4. **ФОРМАТ ОТВЕТА**:
   - Верни ТОЛЬКО JSON массив, без markdown разметки
   - Если пробелов НЕТ - верни пустой массив []
   - Это нормально и правильно - значит документация хорошо подготовлена!

ПОМНИ: Задавать вопросы о том, что УЖЕ ЕСТЬ в документации - признак непрофессионализма!"""

        response_text = self._make_api_call(system_prompt, user_prompt, response_format="json", use_premium=False)

        try:
            gaps = json.loads(response_text)
            if not isinstance(gaps, list):
                # Если вернулся объект, пытаемся найти массив внутри
                for value in gaps.values():
                    if isinstance(value, list):
                        return value
                return []
            return gaps
        except json.JSONDecodeError:
            # Пытаемся извлечь JSON массив
            json_start = response_text.find('[')
            json_end = response_text.rfind(']') + 1
            if json_start >= 0 and json_end > json_start:
                return json.loads(response_text[json_start:json_end])
            raise ValueError("Не удалось распарсить список пробелов")

    def generate_questions(
        self,
        gaps: List[Dict[str, Any]],
        documentation_text: str
    ) -> Dict[str, List[str]]:
        """
        Генерирует вопросы для заказчика на основе выявленных пробелов.
        ПРОСТАЯ ЗАДАЧА - использует быструю модель.

        Args:
            gaps: Список пробелов из detect_gaps
            documentation_text: Исходная документация

        Returns:
            Словарь с вопросами по приоритетам:
            {
                'critical': [...],
                'important': [...],
                'optional': [...]
            }
        """
        system_prompt = """Ты — опытный специалист по взаимодействию с заказчиками в сфере государственных закупок, эксперт по деловой переписке."""

        user_prompt = f"""# СОБЫТИЕ
Выявлены пробелы в тендерной документации. Необходимо направить официальный запрос заказчику для получения разъяснений.

# ПРЕДШЕСТВУЮЩИЙ КОНТЕКСТ
По законодательству РФ (44-ФЗ, 223-ФЗ) участники закупки имеют право запрашивать у заказчика разъяснения по документации. Правильно сформулированные вопросы помогают:
- Получить недостающую информацию для корректной подготовки заявки
- Уточнить неясные требования и избежать отклонения заявки
- Снизить риски при исполнении контракта
- Продемонстрировать профессионализм и заинтересованность

Вопросы должны быть максимально конкретными, корректными и профессиональными.

# СУБЪЕКТ
Ты — специалист по деловой коммуникации в сфере госзакупок, который готовит официальные запросы заказчикам.

# ОБЪЕКТ
Список выявленных пробелов в документации и сама документация для контекста.

ВЫЯВЛЕННЫЕ ПРОБЕЛЫ:
{json.dumps(gaps, ensure_ascii=False, indent=2)}

КОНТЕКСТ (фрагмент документации):
{documentation_text[:10000]}

# ЦЕЛЬ
Сформулировать профессиональные вопросы для официального запроса разъяснений у заказчика.

# ЗАДАЧА
Преобразовать выявленные пробелы в четкие, конкретные, профессиональные вопросы. Расставить приоритеты: какие вопросы критичны, какие важны, какие желательны.

# СРЕДСТВА
- Список выявленных пробелов с указанием критичности
- Фрагмент документации для точных ссылок
- Знание норм деловой переписки и законодательства о закупках
- Понимание типичных формулировок в запросах разъяснений

# ДЕЙСТВИЯ
1. Изучи каждый выявленный пробел
2. Для каждого пробела сформулируй вопрос:
   - Используй деловой профессиональный стиль
   - Укажи конкретную ссылку на раздел/пункт документации
   - Сформулируй так, чтобы ответ был однозначным и полным
   - Избегай двусмысленностей и общих формулировок
3. Распределить вопросы по приоритетам:
   - critical: CRITICAL и HIGH пробелы - без ответов невозможно участие
   - important: MEDIUM пробелы - влияют на решение и ценообразование
   - optional: LOW пробелы - желательно уточнить, но не критично
4. Устрани дубликаты и объедини похожие вопросы
5. Упорядочь вопросы логически в каждой категории

# РЕЗУЛЬТАТ
Верни структурированный список вопросов в формате JSON:

{{
    "critical": [
        "Просим уточнить [конкретный вопрос со ссылкой на пункт документации]",
        "Просим разъяснить [конкретный вопрос]"
    ],
    "important": [
        "Просим предоставить информацию о [конкретный вопрос]",
        "Необходимо уточнить [конкретный вопрос]"
    ],
    "optional": [
        "Будем признательны за разъяснение [конкретный вопрос]",
        "Просим дополнительно пояснить [конкретный вопрос]"
    ]
}}

Требования к вопросам:
- Официальный деловой стиль (избегать "мы хотим", использовать "просим", "необходимо уточнить")
- Максимальная конкретность - указывать что именно нужно уточнить
- Ссылки на пункты документации в формате: "согласно п. X.X документации", "в разделе Y"
- Один вопрос = одна тема (не объединять несколько тем в один вопрос)
- Избегать очевидных вопросов, ответы на которые есть в документации
- Формулировать так, чтобы нельзя было ответить просто "да" или "нет"

ВАЖНО:
- Верни ТОЛЬКО валидный JSON, без markdown разметки
- Если для категории нет вопросов - верни пустой массив []
- Каждый вопрос должен быть самодостаточным и понятным без контекста"""

        response_text = self._make_api_call(system_prompt, user_prompt, response_format="json", use_premium=False)

        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                return json.loads(response_text[json_start:json_end])
            return {"critical": [], "important": [], "optional": []}


def main():
    """Пример использования TenderAnalyzer."""
    import os
    from dotenv import load_dotenv

    load_dotenv()

    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        print("Ошибка: ANTHROPIC_API_KEY не найден")
        return

    analyzer = TenderAnalyzer(api_key=api_key)

    # Тестовый пример
    test_doc = """
    ИЗВЕЩЕНИЕ О ПРОВЕДЕНИИ ЭЛЕКТРОННОГО АУКЦИОНА

    Заказчик: ООО "Тестовая компания"
    Предмет: Поставка компьютерного оборудования
    НМЦК: 5 000 000 руб.

    Требования:
    - Наличие сертификата ISO 9001
    - Опыт выполнения аналогичных контрактов
    """

    company_profile = {"company_info": {"name": "Тест"}}

    print("Анализ документации...")
    result = analyzer.analyze_documentation(test_doc, company_profile)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

---

## Файл 3: contract_analyzer.py - Анализ проекта контракта

```python
# /Users/nikolaichizhik/tender-ai-agent/src/analyzers/contract_analyzer.py

---

## Файл 4: questions_generator.py - Генерация вопросов

```python
# /Users/nikolaichizhik/tender-ai-agent/src/analyzers/questions_generator.py

---

## Файл 5: contact_extractor.py - Извлечение контактов

```python
# /Users/nikolaichizhik/tender-ai-agent/src/analyzers/contact_extractor.py

---

## Конфигурация LLM

```python
# /Users/nikolaichizhik/tender-ai-agent/config/llm_config.yaml

# Конец файла

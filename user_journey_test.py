#!/usr/bin/env python3
"""
Тестирование пользовательского пути (User Journey) системы поиска и анализа тендеров.
Имитация действий реального пользователя для выявления проблем и возможностей улучшения.
"""

import sys
import os
from pathlib import Path

# Добавляем src в путь для импортов
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from parsers.zakupki_rss_parser import ZakupkiRSSParser
from parsers.zakupki_document_downloader import ZakupkiDocumentDownloader
from document_processor.text_extractor import TextExtractor
from analyzers.multi_stage_analyzer import MultiStageAnalyzer
from utils.config_loader import ConfigLoader
import time

class UserJourneyTest:
    """Тестирование пользовательского пути."""

    def __init__(self):
        print("="*70)
        print("🧪 ТЕСТИРОВАНИЕ ПОЛЬЗОВАТЕЛЬСКОГО ПУТИ")
        print("="*70)

        self.parser = ZakupkiRSSParser()
        self.downloader = ZakupkiDocumentDownloader()
        self.extractor = TextExtractor()

        # Анализатор не нужен для тестирования поиска
        self.analyzer = None  # Пропускаем анализ чтобы не тратить токены

        self.issues = []  # Список найденных проблем
        self.improvements = []  # Список возможных улучшений

    def log_issue(self, issue: str, severity: str = "MEDIUM"):
        """Логирование найденной проблемы."""
        self.issues.append({"issue": issue, "severity": severity})
        print(f"   ❌ [{severity}] {issue}")

    def log_improvement(self, improvement: str):
        """Логирование возможного улучшения."""
        self.improvements.append(improvement)
        print(f"   💡 {improvement}")

    def test_search_scenario(self, query: str, price_min: int, price_max: int):
        """Тест сценария поиска тендеров."""
        print(f"\n📍 Сценарий: Поиск '{query}' ({price_min:,} - {price_max:,} руб)")
        print("-"*60)

        # 1. ПОИСК ТЕНДЕРОВ
        print("\n1️⃣ ЭТАП ПОИСКА:")
        start_time = time.time()

        tenders = self.parser.search_tenders_rss(
            keywords=query,
            price_min=price_min,
            price_max=price_max,
            max_results=5,
            tender_type="товары"
        )

        search_time = time.time() - start_time
        print(f"   ⏱️ Время поиска: {search_time:.2f} сек")

        if search_time > 10:
            self.log_issue(f"Поиск занимает слишком много времени ({search_time:.2f} сек)", "HIGH")
            self.log_improvement("Добавить кэширование результатов поиска на 5-10 минут")

        if not tenders:
            self.log_issue(f"Не найдено тендеров по запросу '{query}'", "CRITICAL")
            self.log_improvement("Добавить fuzzy search и исправление опечаток")
            self.log_improvement("Добавить поиск по синонимам автоматически")
            return

        print(f"   ✅ Найдено тендеров: {len(tenders)}")

        # Проверка качества результатов
        for i, tender in enumerate(tenders[:3], 1):
            print(f"\n   Тендер {i}: {tender.get('name', 'Без названия')[:60]}...")

            # Проверка полноты данных
            if not tender.get('price'):
                self.log_issue(f"Отсутствует цена в тендере {tender.get('number', 'N/A')}", "HIGH")

            if not tender.get('published_datetime'):
                self.log_issue(f"Отсутствует дата публикации", "MEDIUM")

            if not tender.get('url'):
                self.log_issue(f"Отсутствует URL тендера", "CRITICAL")

        # 2. СКАЧИВАНИЕ ДОКУМЕНТОВ
        if tenders:
            print("\n2️⃣ ЭТАП СКАЧИВАНИЯ ДОКУМЕНТОВ:")
            tender = tenders[0]
            tender_url = tender.get('url', '')

            if not tender_url.startswith('http'):
                tender_url = f"https://zakupki.gov.ru{tender_url}"

            print(f"   📥 Скачиваем документы для: {tender.get('number', 'N/A')}")

            start_time = time.time()
            try:
                result = self.downloader.download_documents(
                    tender_url=tender_url,
                    tender_number=tender.get('number', 'unknown')
                )
                download_time = time.time() - start_time

                print(f"   ⏱️ Время скачивания: {download_time:.2f} сек")

                if download_time > 30:
                    self.log_issue(f"Скачивание занимает слишком много времени ({download_time:.2f} сек)", "HIGH")
                    self.log_improvement("Добавить параллельное скачивание документов")

                files = result.get('files', [])
                if not files:
                    self.log_issue("Не удалось скачать документы", "CRITICAL")
                else:
                    print(f"   ✅ Скачано файлов: {len(files)}")

                    # 3. ИЗВЛЕЧЕНИЕ ТЕКСТА
                    print("\n3️⃣ ЭТАП ИЗВЛЕЧЕНИЯ ТЕКСТА:")
                    total_chars = 0
                    for file_info in files:
                        file_path = file_info.get('path', '')
                        if file_path:
                            try:
                                extracted = self.extractor.extract_text(file_path)
                                chars = extracted.get('char_count', 0)
                                total_chars += chars
                                print(f"   📄 {extracted.get('file_name', 'N/A')}: {chars:,} символов")
                            except Exception as e:
                                self.log_issue(f"Ошибка извлечения текста: {str(e)}", "HIGH")

                    if total_chars < 5000:
                        self.log_issue(f"Извлечено слишком мало текста ({total_chars} символов)", "HIGH")
                        self.log_improvement("Добавить OCR для сканированных документов")

                    # 4. АНАЛИЗ (пропускаем чтобы не тратить токены)
                    print("\n4️⃣ ЭТАП АНАЛИЗА:")
                    print("   ⏭️ Пропущен для экономии токенов LLM")
                    self.log_improvement("Добавить preview анализа без использования LLM")

            except Exception as e:
                self.log_issue(f"Ошибка при обработке: {str(e)}", "CRITICAL")

    def run_full_journey(self):
        """Запуск полного тестирования."""

        # Сценарий 1: Медицинские товары
        self.test_search_scenario(
            query="Костыли и трости",
            price_min=50000,
            price_max=500000
        )

        # Сценарий 2: IT оборудование
        self.test_search_scenario(
            query="Компьютеры и ноутбуки",
            price_min=100000,
            price_max=1000000
        )

        # Сценарий 3: Канцтовары
        self.test_search_scenario(
            query="Канцелярские товары бумага",
            price_min=10000,
            price_max=100000
        )

        # Сценарий 4: Некорректный запрос
        self.test_search_scenario(
            query="asdfghjkl",  # Заведомо некорректный
            price_min=1000,
            price_max=10000
        )

        # Дополнительные проверки UX
        print("\n\n5️⃣ ПРОВЕРКА USER EXPERIENCE:")
        print("-"*60)

        # Проверка обработки ошибок
        self.log_improvement("Добавить более понятные сообщения об ошибках для пользователя")
        self.log_improvement("Добавить прогресс-бар с ETA для долгих операций")
        self.log_improvement("Добавить возможность отмены операции")

        # Проверка функциональности
        self.log_improvement("Добавить сохранение истории поисков")
        self.log_improvement("Добавить избранные тендеры")
        self.log_improvement("Добавить уведомления о новых тендерах по подписке")
        self.log_improvement("Добавить экспорт в Excel с расширенной аналитикой")
        self.log_improvement("Добавить сравнение нескольких тендеров")

        # Проверка аналитики
        self.log_improvement("Добавить автоматический расчет вероятности победы")
        self.log_improvement("Добавить анализ конкурентов по истории тендеров")
        self.log_improvement("Добавить прогноз сложности документооборота")

        self.print_summary()

    def print_summary(self):
        """Вывод итогового отчета."""
        print("\n\n" + "="*70)
        print("📊 ИТОГОВЫЙ ОТЧЕТ")
        print("="*70)

        print(f"\n🔴 НАЙДЕННЫЕ ПРОБЛЕМЫ ({len(self.issues)}):")
        print("-"*60)

        # Группировка по severity
        critical = [i for i in self.issues if i['severity'] == 'CRITICAL']
        high = [i for i in self.issues if i['severity'] == 'HIGH']
        medium = [i for i in self.issues if i['severity'] == 'MEDIUM']

        if critical:
            print("\nКРИТИЧНЫЕ:")
            for issue in critical:
                print(f"  ❌ {issue['issue']}")

        if high:
            print("\nВЫСОКИЕ:")
            for issue in high:
                print(f"  ⚠️ {issue['issue']}")

        if medium:
            print("\nСРЕДНИЕ:")
            for issue in medium:
                print(f"  ⚡ {issue['issue']}")

        print(f"\n\n💡 ВОЗМОЖНЫЕ УЛУЧШЕНИЯ ({len(self.improvements)}):")
        print("-"*60)

        # Группировка улучшений по категориям
        search_improv = []
        analysis_improv = []
        ux_improv = []
        feature_improv = []

        for imp in self.improvements:
            if any(word in imp.lower() for word in ['поиск', 'search', 'fuzzy', 'синоним']):
                search_improv.append(imp)
            elif any(word in imp.lower() for word in ['анализ', 'расчет', 'вероятност', 'конкурент']):
                analysis_improv.append(imp)
            elif any(word in imp.lower() for word in ['сообщен', 'прогресс', 'отмен', 'preview']):
                ux_improv.append(imp)
            else:
                feature_improv.append(imp)

        if search_improv:
            print("\n🔍 Улучшения поиска:")
            for imp in set(search_improv):
                print(f"  • {imp}")

        if analysis_improv:
            print("\n📈 Улучшения анализа:")
            for imp in set(analysis_improv):
                print(f"  • {imp}")

        if ux_improv:
            print("\n🎨 Улучшения UX:")
            for imp in set(ux_improv):
                print(f"  • {imp}")

        if feature_improv:
            print("\n⚡ Новые функции:")
            for imp in set(feature_improv):
                print(f"  • {imp}")

        print("\n" + "="*70)


if __name__ == "__main__":
    tester = UserJourneyTest()
    tester.run_full_journey()
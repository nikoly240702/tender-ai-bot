"""
Основной модуль поиска тендеров.
Объединяет парсинг zakupki.gov.ru и анализ релевантности через LLM.
"""

from typing import List, Dict, Any
import json
from datetime import datetime

try:
    from ..parsers.zakupki_parser import ZakupkiParser
    from .criteria_analyzer import CriteriaAnalyzer
    from ..analyzers.tender_analyzer import TenderAnalyzer
except ImportError:
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent.parent))
    from parsers.zakupki_parser import ZakupkiParser
    from search.criteria_analyzer import CriteriaAnalyzer
    from analyzers.tender_analyzer import TenderAnalyzer


class TenderSearcher:
    """Поисковик тендеров с автоматическим анализом релевантности."""

    def __init__(self, tender_analyzer: TenderAnalyzer):
        """
        Инициализация поисковика.

        Args:
            tender_analyzer: Экземпляр TenderAnalyzer для работы с LLM
        """
        self.parser = ZakupkiParser()
        self.criteria_analyzer = CriteriaAnalyzer(tender_analyzer)
        self.tender_analyzer = tender_analyzer

    def search_and_analyze(
        self,
        criteria_text: str,
        max_results: int = 10,
        min_relevance_score: int = 50
    ) -> Dict[str, Any]:
        """
        Полный цикл поиска и анализа тендеров.

        Args:
            criteria_text: Текстовое описание критериев поиска
            max_results: Максимальное количество результатов
            min_relevance_score: Минимальный балл релевантности (0-100)

        Returns:
            Результаты поиска с оценкой релевантности
        """
        print("\n" + "="*70)
        print("🔍 ПОИСК ТЕНДЕРОВ")
        print("="*70)

        # Шаг 1: Анализ критериев
        print("\n📋 Шаг 1: Анализ критериев поиска...")
        criteria = self.criteria_analyzer.parse_criteria(criteria_text)

        print(f"   Ключевые слова: {criteria.get('keywords', 'не указаны')}")
        print(f"   Ценовой диапазон: {criteria.get('price_min', 0):,} - {criteria.get('price_max', 0):,} руб.")
        print(f"   Регионы: {', '.join(criteria.get('regions', [])) or 'все'}")

        # Шаг 2: Поиск тендеров
        print("\n🌐 Шаг 2: Поиск тендеров на zakupki.gov.ru...")

        tenders = self.parser.search_tenders(
            keywords=criteria.get('keywords'),
            price_min=criteria.get('price_min'),
            price_max=criteria.get('price_max'),
            regions=criteria.get('regions'),
            page_limit=3  # Ограничиваем 3 страницами
        )

        if not tenders:
            print("   ⚠️  Тендеры не найдены")
            return {
                'criteria': criteria,
                'tenders_found': 0,
                'relevant_tenders': [],
                'timestamp': datetime.now().isoformat()
            }

        # Шаг 3: Анализ релевантности
        print(f"\n🎯 Шаг 3: Анализ релевантности ({len(tenders)} тендеров)...")

        relevant_tenders = []

        for i, tender in enumerate(tenders[:max_results], 1):
            print(f"\n   [{i}/{min(len(tenders), max_results)}] Анализ: {tender.get('name', '')[:60]}...")

            # Оцениваем релевантность
            relevance = self.criteria_analyzer.analyze_relevance(tender, criteria)

            score = relevance.get('score', 0)

            if score >= min_relevance_score:
                tender['relevance'] = relevance
                relevant_tenders.append(tender)

                # Цветной вывод в зависимости от score
                if score >= 80:
                    indicator = "🟢"
                elif score >= 60:
                    indicator = "🟡"
                else:
                    indicator = "🟠"

                print(f"      {indicator} Релевантность: {score}/100")
                print(f"      💡 {relevance.get('summary', '')}")
            else:
                print(f"      ⚫ Пропущен (score: {score}/100)")

        # Сортируем по релевантности
        relevant_tenders.sort(key=lambda x: x['relevance']['score'], reverse=True)

        # Результаты
        print("\n" + "="*70)
        print(f"✅ РЕЗУЛЬТАТЫ: Найдено {len(relevant_tenders)} релевантных тендеров")
        print("="*70)

        return {
            'criteria': criteria,
            'tenders_found': len(tenders),
            'relevant_tenders': relevant_tenders,
            'timestamp': datetime.now().isoformat()
        }

    def display_results(self, results: Dict[str, Any]):
        """
        Отображает результаты поиска в читаемом виде.

        Args:
            results: Результаты поиска из search_and_analyze
        """
        relevant = results.get('relevant_tenders', [])

        if not relevant:
            print("\n❌ Релевантных тендеров не найдено")
            return

        print(f"\n📊 ТОП-{len(relevant)} РЕЛЕВАНТНЫХ ТЕНДЕРОВ:\n")

        for i, tender in enumerate(relevant, 1):
            relevance = tender.get('relevance', {})
            score = relevance.get('score', 0)

            # Эмодзи в зависимости от score
            if score >= 80:
                emoji = "🌟"
            elif score >= 60:
                emoji = "⭐"
            else:
                emoji = "✨"

            print(f"{emoji} {i}. {tender.get('name', 'Без названия')}")
            print(f"   Номер: {tender.get('number')}")
            print(f"   Цена: {tender.get('price_formatted')}")
            print(f"   Заказчик: {tender.get('customer')}")
            print(f"   Дедлайн: {tender.get('deadline')}")
            print(f"   URL: {tender.get('url')}\n")

            print(f"   📊 Оценка релевантности: {score}/100")
            print(f"   ✅ Соответствие: {', '.join(relevance.get('match_reasons', [])[:2])}")

            if relevance.get('concerns'):
                print(f"   ⚠️  Замечания: {', '.join(relevance.get('concerns', [])[:2])}")

            print(f"   💡 Рекомендация: {relevance.get('recommendation', 'изучить').upper()}")
            print(f"   📝 {relevance.get('summary', '')}\n")
            print("-" * 70)

    def export_results(self, results: Dict[str, Any], output_path: str):
        """
        Экспортирует результаты поиска в JSON файл.

        Args:
            results: Результаты поиска
            output_path: Путь для сохранения файла
        """
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"\n💾 Результаты сохранены: {output_path}")
        except Exception as e:
            print(f"\n❌ Ошибка сохранения: {e}")


def main():
    """Пример использования поисковика тендеров."""
    import os
    from dotenv import load_dotenv

    load_dotenv()

    # Инициализация
    from analyzers.llm_adapter import LLMFactory

    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        print("Ошибка: OPENAI_API_KEY не найден")
        return

    # Создаем анализатор
    from analyzers.tender_analyzer import TenderAnalyzer

    analyzer = TenderAnalyzer(
        api_key=api_key,
        provider='openai',
        model_premium='gpt-4o',
        model_fast='gpt-4o-mini'
    )

    # Создаем поисковик
    searcher = TenderSearcher(analyzer)

    # Тестовые критерии
    criteria_text = """
    Ищу тендеры на поставку компьютерного оборудования в Москве.
    Интересуют видеокарты, мониторы, серверное оборудование.
    Цена от 500 тысяч до 3 миллионов рублей.
    """

    # Выполняем поиск
    results = searcher.search_and_analyze(
        criteria_text=criteria_text,
        max_results=5,
        min_relevance_score=50
    )

    # Отображаем результаты
    searcher.display_results(results)

    # Сохраняем в файл
    searcher.export_results(results, 'search_results.json')


if __name__ == "__main__":
    main()

"""
Модуль для пакетной обработки тендеров (V2.0).

Основные возможности:
- Параллельный анализ до 20 тендеров
- Автоматическое использование кэша
- Приоритизация по score
- Возврат TOP-N рекомендаций
"""

import asyncio
from typing import List, Dict, Any, Optional
from pathlib import Path
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class BatchTenderProcessor:
    """Процессор для пакетной обработки тендеров."""

    def __init__(
        self,
        agent,
        db,
        max_concurrent: int = 3
    ):
        """
        Инициализация процессора.

        Args:
            agent: TenderAnalysisAgent для анализа
            db: Database для кэширования
            max_concurrent: Максимум параллельных анализов (по умолчанию 3)
        """
        self.agent = agent
        self.db = db
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def analyze_single_tender(
        self,
        tender_info: Dict[str, Any],
        file_paths: List[str],
        tender_index: int
    ) -> Dict[str, Any]:
        """
        Анализ одного тендера с ограничением параллелизма.

        Args:
            tender_info: Информация о тендере
            file_paths: Пути к документам
            tender_index: Индекс тендера в батче

        Returns:
            Словарь с результатами анализа
        """
        async with self.semaphore:
            tender_num = tender_info.get('number', 'unknown')
            tender_name = tender_info.get('name', 'Без названия')[:60]

            logger.info(f"📦 [{tender_index + 1}] Начинаем анализ: {tender_num}")
            print(f"📦 [{tender_index + 1}] {tender_name}")

            try:
                # Анализируем с кэшированием
                analysis = await self.agent.analyze_tender(
                    file_paths=file_paths,
                    tender_number=tender_num,
                    use_cache=True
                )

                # Добавляем метаданные
                result = {
                    'tender_info': tender_info,
                    'analysis': analysis,
                    'success': True,
                    'error': None,
                    'from_cache': analysis.get('from_cache', False)
                }

                # Извлекаем score для сортировки
                if 'analysis_summary' in analysis:
                    summary = analysis['analysis_summary']
                    result['score'] = summary.get('confidence_score', 0)
                    result['is_suitable'] = summary.get('is_suitable', False)
                else:
                    result['score'] = 0
                    result['is_suitable'] = False

                cache_status = "💚 CACHE HIT" if result['from_cache'] else "🔄 NEW ANALYSIS"
                logger.info(f"✅ [{tender_index + 1}] {cache_status} - Score: {result['score']}")

                return result

            except Exception as e:
                logger.error(f"❌ [{tender_index + 1}] Ошибка анализа {tender_num}: {e}")
                return {
                    'tender_info': tender_info,
                    'analysis': None,
                    'success': False,
                    'error': str(e),
                    'score': 0,
                    'is_suitable': False
                }

    async def analyze_batch(
        self,
        tenders_data: List[Dict[str, Any]],
        top_n: int = 5,
        min_score: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Пакетный анализ тендеров.

        Args:
            tenders_data: Список словарей с данными тендеров
                Каждый словарь должен содержать:
                - 'tender_info': информация о тендере
                - 'file_paths': пути к документам
            top_n: Количество лучших тендеров для возврата (по умолчанию 5)
            min_score: Минимальный score для фильтрации (опционально)

        Returns:
            Словарь с результатами батч-анализа:
            - 'results': все результаты
            - 'top_tenders': TOP-N рекомендаций
            - 'statistics': статистика выполнения
        """
        start_time = datetime.now()

        print(f"\n{'='*70}")
        print(f"  📦 ПАКЕТНЫЙ АНАЛИЗ ТЕНДЕРОВ (V2.0)")
        print(f"{'='*70}")
        print(f"📊 Всего тендеров: {len(tenders_data)}")
        print(f"⚡ Параллельность: {self.max_concurrent}")
        print(f"🎯 Топ результатов: {top_n}")
        if min_score:
            print(f"🎚️  Минимальный score: {min_score}")
        print(f"{'='*70}\n")

        # Создаем задачи для параллельного выполнения
        tasks = []
        for i, tender_data in enumerate(tenders_data):
            task = self.analyze_single_tender(
                tender_info=tender_data['tender_info'],
                file_paths=tender_data['file_paths'],
                tender_index=i
            )
            tasks.append(task)

        # Выполняем параллельно
        results = await asyncio.gather(*tasks)

        # Фильтруем успешные результаты
        successful = [r for r in results if r['success']]
        failed = [r for r in results if not r['success']]

        # Применяем фильтр по минимальному score
        if min_score:
            filtered = [r for r in successful if r['score'] >= min_score]
            excluded_by_score = len(successful) - len(filtered)
            successful = filtered
        else:
            excluded_by_score = 0

        # Сортируем по score (убывание)
        successful.sort(key=lambda x: x['score'], reverse=True)

        # Берем TOP-N
        top_tenders = successful[:top_n]

        # Статистика
        cache_hits = sum(1 for r in results if r.get('from_cache', False))
        new_analyses = len(successful) - cache_hits

        elapsed = (datetime.now() - start_time).total_seconds()

        statistics = {
            'total_tenders': len(tenders_data),
            'successful': len(successful),
            'failed': len(failed),
            'cache_hits': cache_hits,
            'new_analyses': new_analyses,
            'excluded_by_score': excluded_by_score,
            'top_n': len(top_tenders),
            'elapsed_seconds': elapsed,
            'avg_score': sum(r['score'] for r in successful) / len(successful) if successful else 0
        }

        # Выводим итоги
        print(f"\n{'='*70}")
        print(f"  ✅ БАТЧ-АНАЛИЗ ЗАВЕРШЕН")
        print(f"{'='*70}")
        print(f"⏱️  Время: {elapsed:.1f} сек")
        print(f"✅ Успешно: {statistics['successful']}/{statistics['total_tenders']}")
        print(f"❌ Ошибок: {statistics['failed']}")
        print(f"💚 Из кэша: {cache_hits} (сэкономлено ~{cache_hits * 70}% токенов)")
        print(f"🔄 Новых анализов: {new_analyses}")
        if excluded_by_score > 0:
            print(f"🎚️  Отфильтровано по score: {excluded_by_score}")
        print(f"📊 Средний score: {statistics['avg_score']:.1f}")
        print(f"🏆 TOP-{len(top_tenders)} рекомендаций готовы")
        print(f"{'='*70}\n")

        return {
            'results': results,
            'top_tenders': top_tenders,
            'statistics': statistics,
            'failed': failed
        }

    async def get_recommendations_summary(
        self,
        top_tenders: List[Dict[str, Any]]
    ) -> str:
        """
        Генерирует текстовую сводку рекомендаций.

        Args:
            top_tenders: Список топовых тендеров

        Returns:
            Форматированная строка с рекомендациями
        """
        if not top_tenders:
            return "❌ Нет подходящих тендеров"

        lines = ["🏆 РЕКОМЕНДОВАННЫЕ ТЕНДЕРЫ:\n"]

        for i, tender_result in enumerate(top_tenders, 1):
            tender_info = tender_result['tender_info']
            score = tender_result['score']
            is_suitable = tender_result['is_suitable']

            # Эмодзи для рейтинга
            if score >= 80:
                emoji = "🟢"
            elif score >= 60:
                emoji = "🟡"
            else:
                emoji = "🔴"

            # Рекомендация
            if is_suitable and score >= 80:
                recommendation = "✅ УЧАСТВОВАТЬ"
            elif is_suitable and score >= 60:
                recommendation = "⚡ РАССМОТРЕТЬ"
            else:
                recommendation = "⚠️  НИЗКИЙ ПРИОРИТЕТ"

            lines.append(f"\n{i}. {emoji} {tender_info.get('number', 'N/A')}")
            lines.append(f"   📋 {tender_info.get('name', 'Без названия')[:70]}")
            lines.append(f"   💰 {tender_info.get('price_formatted', 'N/A')}")
            lines.append(f"   🎯 Score: {score:.1f}/100")
            lines.append(f"   💡 {recommendation}")

            # Показываем краткое обоснование
            analysis = tender_result.get('analysis', {})
            if analysis and 'analysis_summary' in analysis:
                summary = analysis['analysis_summary']
                if summary.get('main_risks'):
                    risks = summary['main_risks'][:2]  # Первые 2 риска
                    lines.append(f"   ⚠️  Риски: {', '.join(risks)}")

        return '\n'.join(lines)

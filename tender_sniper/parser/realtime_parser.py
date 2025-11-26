"""
Real-time Parser для мониторинга новых тендеров zakupki.gov.ru.

Периодически опрашивает RSS-фиды и уведомляет об новых тендерах.
"""

import asyncio
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime, timedelta
import logging

# Добавляем src в путь для импорта существующего парсера
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.parsers.zakupki_rss_parser import ZakupkiRSSParser

logger = logging.getLogger(__name__)


class RealtimeParser:
    """
    Real-time парсер новых тендеров.

    Особенности:
    - Асинхронный мониторинг RSS-фидов
    - Детекция новых тендеров (не дубликатов)
    - Callback система для обработки найденных тендеров
    - Graceful shutdown
    """

    def __init__(
        self,
        poll_interval: int = 300,  # 5 минут
        max_tenders_per_poll: int = 100
    ):
        """
        Инициализация real-time парсера.

        Args:
            poll_interval: Интервал опроса в секундах (по умолчанию 5 минут)
            max_tenders_per_poll: Максимальное количество тендеров за один запрос
        """
        self.poll_interval = poll_interval
        self.max_tenders_per_poll = max_tenders_per_poll

        # Используем существующий RSS парсер
        self.rss_parser = ZakupkiRSSParser()

        # Callback функции для обработки новых тендеров
        self.callbacks: List[Callable] = []

        # Кэш просмотренных тендеров (номер → datetime)
        self.seen_tenders: Dict[str, datetime] = {}

        # Флаг для остановки
        self._running = False

        # Статистика
        self.stats = {
            'polls': 0,
            'tenders_found': 0,
            'new_tenders': 0,
            'duplicates_skipped': 0,
            'errors': 0,
            'last_poll': None,
            'started_at': None
        }

    def add_callback(self, callback: Callable[[List[Dict[str, Any]]], None]):
        """
        Добавление callback функции для обработки новых тендеров.

        Args:
            callback: Асинхронная функция, которая принимает список тендеров
        """
        self.callbacks.append(callback)
        logger.info(f"✅ Добавлен callback: {callback.__name__}")

    def remove_callback(self, callback: Callable):
        """Удаление callback функции."""
        if callback in self.callbacks:
            self.callbacks.remove(callback)
            logger.info(f"➖ Удален callback: {callback.__name__}")

    async def start(
        self,
        keywords: Optional[str] = None,
        price_min: Optional[int] = None,
        price_max: Optional[int] = None,
        regions: Optional[List[str]] = None,
        tender_type: Optional[str] = None
    ):
        """
        Запуск real-time мониторинга.

        Args:
            keywords: Ключевые слова для поиска
            price_min: Минимальная цена
            price_max: Максимальная цена
            regions: Список регионов
            tender_type: Тип закупки
        """
        self._running = True
        self.stats['started_at'] = datetime.now()

        logger.info("="*70)
        logger.info("🎯 ЗАПУСК REAL-TIME МОНИТОРИНГА ТЕНДЕРОВ")
        logger.info("="*70)
        logger.info(f"Ключевые слова: {keywords or 'Все'}")
        logger.info(f"Диапазон цен: {price_min:,} - {price_max:,} руб" if price_min and price_max else "Без ограничений")
        logger.info(f"Регионы: {', '.join(regions) if regions else 'Все'}")
        logger.info(f"Тип закупки: {tender_type or 'Все'}")
        logger.info(f"Интервал опроса: {self.poll_interval} сек ({self.poll_interval // 60} мин)")
        logger.info(f"Callbacks: {len(self.callbacks)}")
        logger.info("="*70)

        try:
            while self._running:
                await self._poll_and_process(
                    keywords=keywords,
                    price_min=price_min,
                    price_max=price_max,
                    regions=regions,
                    tender_type=tender_type
                )

                # Ждем следующий интервал
                if self._running:
                    logger.info(f"⏳ Следующий опрос через {self.poll_interval} сек...")
                    await asyncio.sleep(self.poll_interval)

        except KeyboardInterrupt:
            logger.info("\n🛑 Остановка мониторинга по запросу пользователя")
        except Exception as e:
            logger.error(f"❌ Критическая ошибка мониторинга: {e}", exc_info=True)
            self.stats['errors'] += 1
        finally:
            self.stop()

    def stop(self):
        """Остановка мониторинга."""
        self._running = False
        logger.info("🛑 Мониторинг остановлен")
        self._print_stats()

    async def _poll_and_process(
        self,
        keywords: Optional[str] = None,
        price_min: Optional[int] = None,
        price_max: Optional[int] = None,
        regions: Optional[List[str]] = None,
        tender_type: Optional[str] = None
    ):
        """Однократный опрос и обработка новых тендеров."""
        try:
            logger.info(f"\n📡 Опрос #{self.stats['polls'] + 1} ({datetime.now().strftime('%H:%M:%S')})")

            # Получаем тендеры через RSS
            tenders = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.rss_parser.search_tenders_rss(
                    keywords=keywords,
                    price_min=price_min,
                    price_max=price_max,
                    max_results=self.max_tenders_per_poll,
                    regions=regions,
                    tender_type=tender_type
                )
            )

            self.stats['polls'] += 1
            self.stats['last_poll'] = datetime.now()
            self.stats['tenders_found'] += len(tenders)

            if not tenders:
                logger.info("   ℹ️  Новых тендеров не найдено")
                return

            logger.info(f"   ✅ Получено тендеров: {len(tenders)}")

            # Фильтруем новые тендеры
            new_tenders = self._filter_new_tenders(tenders)

            if not new_tenders:
                logger.info("   ℹ️  Все тендеры уже были обработаны ранее")
                return

            logger.info(f"   🆕 Новых тендеров: {len(new_tenders)}")
            self.stats['new_tenders'] += len(new_tenders)

            # Вызываем все callback функции
            await self._notify_callbacks(new_tenders)

        except Exception as e:
            logger.error(f"❌ Ошибка при опросе: {e}", exc_info=True)
            self.stats['errors'] += 1

    def _filter_new_tenders(self, tenders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Фильтрация новых тендеров (исключение дубликатов).

        Args:
            tenders: Список всех тендеров

        Returns:
            Список только новых тендеров
        """
        new_tenders = []
        now = datetime.now()

        # Очищаем старые записи из кэша (старше 7 дней)
        cutoff_time = now - timedelta(days=7)
        self.seen_tenders = {
            num: seen_at
            for num, seen_at in self.seen_tenders.items()
            if seen_at > cutoff_time
        }

        for tender in tenders:
            tender_number = tender.get('number')

            if not tender_number:
                continue

            # Проверяем, видели ли мы этот тендер ранее
            if tender_number not in self.seen_tenders:
                new_tenders.append(tender)
                self.seen_tenders[tender_number] = now
            else:
                self.stats['duplicates_skipped'] += 1

        return new_tenders

    async def _notify_callbacks(self, new_tenders: List[Dict[str, Any]]):
        """Уведомление всех зарегистрированных callbacks."""
        if not self.callbacks:
            logger.warning("   ⚠️  Нет зарегистрированных callbacks для обработки тендеров")
            return

        logger.info(f"   📢 Вызов {len(self.callbacks)} callback(s)...")

        for callback in self.callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(new_tenders)
                else:
                    callback(new_tenders)
            except Exception as e:
                logger.error(f"❌ Ошибка в callback {callback.__name__}: {e}", exc_info=True)

    def _print_stats(self):
        """Вывод статистики мониторинга."""
        logger.info("\n" + "="*70)
        logger.info("📊 СТАТИСТИКА МОНИТОРИНГА")
        logger.info("="*70)

        if self.stats['started_at']:
            uptime = datetime.now() - self.stats['started_at']
            hours = uptime.total_seconds() / 3600
            logger.info(f"⏱️  Время работы: {uptime}")
            logger.info(f"📡 Всего опросов: {self.stats['polls']}")
            logger.info(f"📄 Всего тендеров: {self.stats['tenders_found']}")
            logger.info(f"🆕 Новых тендеров: {self.stats['new_tenders']}")
            logger.info(f"🔄 Дубликатов пропущено: {self.stats['duplicates_skipped']}")
            logger.info(f"❌ Ошибок: {self.stats['errors']}")

            if self.stats['polls'] > 0:
                logger.info(f"📊 Среднее тендеров/опрос: {self.stats['tenders_found'] / self.stats['polls']:.1f}")

            if hours > 0:
                logger.info(f"📊 Новых тендеров/час: {self.stats['new_tenders'] / hours:.1f}")

        logger.info("="*70)

    def get_stats(self) -> Dict[str, Any]:
        """Получение статистики."""
        return self.stats.copy()

    @property
    def is_running(self) -> bool:
        """Проверка, запущен ли мониторинг."""
        return self._running


# ============================================
# ПРИМЕР ИСПОЛЬЗОВАНИЯ
# ============================================

async def example_callback(new_tenders: List[Dict[str, Any]]):
    """Пример callback функции."""
    logger.info(f"   📨 Callback получил {len(new_tenders)} новых тендеров:")
    for tender in new_tenders[:3]:  # Показываем только первые 3
        logger.info(f"      • {tender.get('number')}: {tender.get('name', '')[:60]}...")


async def main_example():
    """Пример запуска real-time парсера."""
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Создаем парсер
    parser = RealtimeParser(
        poll_interval=60,  # Опрос каждую минуту (для демо)
        max_tenders_per_poll=50
    )

    # Добавляем callback
    parser.add_callback(example_callback)

    # Запускаем мониторинг
    await parser.start(
        keywords="компьютеры ноутбуки",
        price_min=100_000,
        price_max=5_000_000,
        tender_type="товары"
    )


if __name__ == '__main__':
    asyncio.run(main_example())

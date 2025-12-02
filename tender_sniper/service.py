"""
Tender Sniper Service - главный модуль координации.

Объединяет Real-time Parser, Smart Matcher, Database и Telegram Notifier
в единую систему мониторинга и уведомлений.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

# Импортируем компоненты Tender Sniper
from tender_sniper.parser import RealtimeParser
from tender_sniper.matching import SmartMatcher
from tender_sniper.database import get_sniper_db, init_subscription_plans, get_plan_limits
from tender_sniper.notifications.telegram_notifier import TelegramNotifier
from tender_sniper.config import is_tender_sniper_enabled, is_component_enabled

logger = logging.getLogger(__name__)


class TenderSniperService:
    """
    Главный сервис Tender Sniper.

    Workflow:
    1. Real-time Parser находит новые тендеры
    2. Smart Matcher проверяет их против пользовательских фильтров
    3. Database сохраняет матчи и проверяет квоты
    4. Telegram Notifier отправляет уведомления пользователям
    """

    def __init__(
        self,
        bot_token: str,
        db_path: Optional[Path] = None,
        poll_interval: int = 300,  # 5 минут
        max_tenders_per_poll: int = 100
    ):
        """
        Инициализация Tender Sniper Service.

        Args:
            bot_token: Telegram Bot Token
            db_path: Путь к базе данных (опционально)
            poll_interval: Интервал опроса в секундах
            max_tenders_per_poll: Максимум тендеров за один опрос
        """
        self.bot_token = bot_token
        self.db_path = db_path or Path(__file__).parent / 'database' / 'sniper.db'
        self.poll_interval = poll_interval
        self.max_tenders_per_poll = max_tenders_per_poll

        # Компоненты
        self.parser: Optional[RealtimeParser] = None
        self.matcher: Optional[SmartMatcher] = None
        self.db = None
        self.notifier: Optional[TelegramNotifier] = None

        # Статистика
        self.stats = {
            'started_at': None,
            'tenders_processed': 0,
            'matches_found': 0,
            'notifications_sent': 0,
            'errors': 0
        }

        self._running = False

    async def initialize(self):
        """Инициализация всех компонентов."""
        logger.info("="*70)
        logger.info("🚀 ИНИЦИАЛИЗАЦИЯ TENDER SNIPER SERVICE")
        logger.info("="*70)

        # 1. Проверяем feature flags
        if not is_tender_sniper_enabled():
            logger.error("❌ Tender Sniper отключен в config/features.yaml")
            raise RuntimeError("Tender Sniper disabled in features config")

        logger.info("✅ Tender Sniper включен в конфигурации")

        # 2. Инициализируем базу данных
        logger.info("🗄️  Инициализация базы данных...")
        self.db = await get_sniper_db(self.db_path)

        # Инициализируем тарифные планы
        await init_subscription_plans(self.db_path)
        logger.info("✅ База данных готова")

        # 3. Инициализируем компоненты
        if is_component_enabled('realtime_parser'):
            logger.info("📡 Инициализация Real-time Parser...")
            self.parser = RealtimeParser(
                poll_interval=self.poll_interval,
                max_tenders_per_poll=self.max_tenders_per_poll
            )
            self.parser.add_callback(self._process_new_tenders)
            logger.info("✅ Real-time Parser готов")

        if is_component_enabled('smart_matching'):
            logger.info("🎯 Инициализация Smart Matcher...")
            self.matcher = SmartMatcher()
            logger.info("✅ Smart Matcher готов")

        if is_component_enabled('instant_notifications'):
            logger.info("📱 Инициализация Telegram Notifier...")
            self.notifier = TelegramNotifier(self.bot_token)
            logger.info("✅ Telegram Notifier готов")

        logger.info("="*70)
        logger.info("✅ ВСЕ КОМПОНЕНТЫ ИНИЦИАЛИЗИРОВАНЫ")
        logger.info("="*70)

    async def start(
        self,
        keywords: Optional[str] = None,
        price_min: Optional[int] = None,
        price_max: Optional[int] = None,
        regions: Optional[List[str]] = None,
        tender_type: Optional[str] = None
    ):
        """
        Запуск мониторинга.

        Args:
            keywords: Ключевые слова для поиска (опционально)
            price_min: Минимальная цена
            price_max: Максимальная цена
            regions: Список регионов
            tender_type: Тип закупки
        """
        if not self.parser:
            raise RuntimeError("Real-time Parser not initialized")

        self._running = True
        self.stats['started_at'] = datetime.now()

        logger.info("🎯 ЗАПУСК МОНИТОРИНГА TENDER SNIPER")

        try:
            await self.parser.start(
                keywords=keywords,
                price_min=price_min,
                price_max=price_max,
                regions=regions,
                tender_type=tender_type
            )
        except KeyboardInterrupt:
            logger.info("\n🛑 Остановка по запросу пользователя")
        except Exception as e:
            logger.error(f"❌ Критическая ошибка сервиса: {e}", exc_info=True)
            self.stats['errors'] += 1
        finally:
            await self.stop()

    async def stop(self):
        """Остановка сервиса."""
        self._running = False

        logger.info("\n🛑 Остановка Tender Sniper Service...")

        if self.parser:
            self.parser.stop()

        if self.notifier:
            await self.notifier.close()

        self._print_stats()

    async def _process_new_tenders(self, new_tenders: List[Dict[str, Any]]):
        """
        Callback для обработки новых тендеров.

        Args:
            new_tenders: Список новых тендеров от парсера
        """
        try:
            logger.info(f"\n🔄 Обработка {len(new_tenders)} новых тендеров...")

            self.stats['tenders_processed'] += len(new_tenders)

            if not self.matcher or not self.db:
                logger.warning("⚠️  Matcher или DB не инициализированы")
                return

            # 1. Получаем все активные фильтры пользователей из ОСНОВНОЙ базы бота
            from bot.db import get_database
            bot_db = await get_database()
            filters = await bot_db.get_all_filters()  # Получаем все фильтры из основной БД
            logger.info(f"   📋 Активных фильтров: {len(filters)}")

            if not filters:
                logger.info("   ℹ️  Нет активных фильтров для проверки")
                return

            # 2. Проверяем каждый тендер против фильтров
            notifications_to_send = []

            for tender in new_tenders:
                tender_number = tender.get('number')

                # Сохраняем тендер в базу мониторинга
                await self.db.add_or_update_tender(
                    tender_number=tender_number,
                    name=tender.get('name'),
                    customer_name=tender.get('customer_name'),
                    nmck=tender.get('price'),
                    published_date=tender.get('published_datetime'),
                    url=tender.get('url'),
                    region=tender.get('region'),
                    tender_type=tender.get('purchase_type'),
                    raw_data=tender
                )

                # Проверяем против фильтров
                matches = self.matcher.match_against_filters(tender, filters, min_score=40)

                if matches:
                    logger.info(f"   ✅ Тендер {tender_number}: {len(matches)} совпадений")
                    self.stats['matches_found'] += len(matches)

                    # Для каждого совпадения готовим уведомление
                    for match in matches:
                        filter_id = match['filter_id']
                        user_id = None

                        # Находим user_id для этого фильтра
                        for f in filters:
                            if f['id'] == filter_id:
                                user_id = f['user_id']
                                telegram_id = f.get('telegram_id')
                                subscription_tier = f.get('subscription_tier', 'free')
                                break

                        if not telegram_id:
                            continue

                        # Проверяем, не отправляли ли уже уведомление
                        already_notified = await self.db.is_tender_notified(
                            tender_number, user_id
                        )

                        if already_notified:
                            logger.debug(f"   ⏭️  Уведомление уже отправлено ранее")
                            continue

                        # Получаем лимиты тарифа
                        plan_limits = await get_plan_limits(self.db_path, subscription_tier)
                        daily_limit = plan_limits.get('max_notifications_daily', 10)

                        # Проверяем квоту
                        has_quota = await self.db.check_notification_quota(
                            user_id, daily_limit
                        )

                        if not has_quota:
                            logger.warning(f"   ⚠️  Квота исчерпана для user {user_id}")

                            # Отправляем уведомление о превышении квоты
                            if self.notifier:
                                await self.notifier.send_quota_exceeded_notification(
                                    telegram_id=telegram_id,
                                    current_limit=daily_limit
                                )
                            continue

                        # Добавляем в очередь на отправку
                        notifications_to_send.append({
                            'user_id': user_id,
                            'telegram_id': telegram_id,
                            'tender': tender,
                            'match_info': match,
                            'filter_id': filter_id,
                            'filter_name': match.get('filter_name', 'Фильтр')
                        })

            # 3. Отправляем уведомления
            if notifications_to_send and self.notifier:
                logger.info(f"   📤 Отправка {len(notifications_to_send)} уведомлений...")

                for notif in notifications_to_send:
                    success = await self.notifier.send_tender_notification(
                        telegram_id=notif['telegram_id'],
                        tender=notif['tender'],
                        match_info=notif['match_info'],
                        filter_name=notif['filter_name']
                    )

                    if success:
                        # Сохраняем в базу
                        await self.db.save_notification(
                            user_id=notif['user_id'],
                            tender_number=notif['tender'].get('number'),
                            filter_id=notif['filter_id'],
                            notification_type='match'
                        )

                        # Увеличиваем счетчик квоты
                        await self.db.increment_notification_quota(notif['user_id'])

                        self.stats['notifications_sent'] += 1

                    # Небольшая задержка между уведомлениями
                    await asyncio.sleep(0.1)

                logger.info(f"   ✅ Уведомления отправлены: {self.stats['notifications_sent']}")

        except Exception as e:
            logger.error(f"❌ Ошибка обработки тендеров: {e}", exc_info=True)
            self.stats['errors'] += 1

    def _print_stats(self):
        """Вывод статистики работы сервиса."""
        logger.info("\n" + "="*70)
        logger.info("📊 СТАТИСТИКА TENDER SNIPER SERVICE")
        logger.info("="*70)

        if self.stats['started_at']:
            uptime = datetime.now() - self.stats['started_at']
            logger.info(f"⏱️  Время работы: {uptime}")

        logger.info(f"📄 Обработано тендеров: {self.stats['tenders_processed']}")
        logger.info(f"🎯 Найдено совпадений: {self.stats['matches_found']}")
        logger.info(f"📱 Отправлено уведомлений: {self.stats['notifications_sent']}")
        logger.info(f"❌ Ошибок: {self.stats['errors']}")

        if self.parser:
            parser_stats = self.parser.get_stats()
            logger.info(f"\n📡 Parser статистика:")
            logger.info(f"   Опросов: {parser_stats.get('polls', 0)}")
            logger.info(f"   Новых тендеров: {parser_stats.get('new_tenders', 0)}")

        if self.matcher:
            matcher_stats = self.matcher.get_stats()
            logger.info(f"\n🎯 Matcher статистика:")
            logger.info(f"   Всего матчей: {matcher_stats.get('total_matches', 0)}")
            logger.info(f"   High score (≥70): {matcher_stats.get('high_score_matches', 0)}")

        if self.notifier:
            notifier_stats = self.notifier.get_stats()
            logger.info(f"\n📱 Notifier статистика:")
            logger.info(f"   Отправлено: {notifier_stats.get('notifications_sent', 0)}")
            logger.info(f"   Ошибок: {notifier_stats.get('notifications_failed', 0)}")

        logger.info("="*70)


# ============================================
# ГЛАВНАЯ ФУНКЦИЯ ЗАПУСКА
# ============================================

async def main():
    """Главная функция запуска Tender Sniper Service."""
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(Path(__file__).parent / 'tender_sniper.log')
        ]
    )

    # Загружаем .env
    load_dotenv()

    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        logger.error("❌ TELEGRAM_BOT_TOKEN не найден в .env")
        return

    # Создаем и запускаем сервис
    service = TenderSniperService(
        bot_token=bot_token,
        poll_interval=300,  # 5 минут
        max_tenders_per_poll=100
    )

    try:
        await service.initialize()

        # Запускаем мониторинг (можно настроить параметры)
        await service.start(
            # keywords="компьютеры ноутбуки",
            # price_min=100_000,
            # price_max=10_000_000,
            # tender_type="товары"
        )

    except Exception as e:
        logger.error(f"❌ Ошибка запуска сервиса: {e}", exc_info=True)


if __name__ == '__main__':
    asyncio.run(main())

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
from tender_sniper.instant_search import InstantSearch
from tender_sniper.monitoring import send_error_to_telegram
from tender_sniper.ai_name_generator import generate_tender_name  # AI генератор названий
from bot.config import BotConfig  # Для проверки админа
import json

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

    # Московское время (UTC+3)
    MOSCOW_TZ_OFFSET = 3

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
        logger.info("📋 Шаг 1/4: Проверка feature flags...")
        if not is_tender_sniper_enabled():
            logger.error("❌ Tender Sniper отключен в config/features.yaml")
            raise RuntimeError("Tender Sniper disabled in features config")

        logger.info("✅ Tender Sniper включен в конфигурации")

        # 2. Инициализируем базу данных
        logger.info("📋 Шаг 2/4: Инициализация базы данных...")
        logger.info("   Попытка подключения к Sniper DB...")
        self.db = await get_sniper_db()
        logger.info("   ✅ Sniper DB подключена")

        # Инициализируем тарифные планы (ВРЕМЕННО ОТКЛЮЧЕНО - требует миграции на PostgreSQL)
        # await init_subscription_plans(self.db_path)
        logger.info("✅ База данных готова")

        # 3. Инициализируем компоненты
        logger.info("📋 Шаг 3/4: Инициализация компонентов...")

        if is_component_enabled('realtime_parser'):
            logger.info("   📡 Создание Real-time Parser...")
            self.parser = RealtimeParser(
                poll_interval=self.poll_interval,
                max_tenders_per_poll=self.max_tenders_per_poll
            )
            logger.info("   ➕ Добавление callback...")
            self.parser.add_callback(self._process_new_tenders)
            logger.info("   ✅ Real-time Parser готов")

        if is_component_enabled('smart_matching'):
            logger.info("   🎯 Создание Smart Matcher...")
            self.matcher = SmartMatcher()
            logger.info("   ✅ Smart Matcher готов")

        if is_component_enabled('instant_notifications'):
            logger.info("   📱 Создание Telegram Notifier...")
            self.notifier = TelegramNotifier(self.bot_token)
            logger.info("   ✅ Telegram Notifier готов")

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
            # Отправляем критическую ошибку в Telegram админу
            await send_error_to_telegram(e, context="Tender Sniper Service")
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

    async def run_single_poll(self):
        """
        Выполнить одну итерацию мониторинга.
        Используется для ручного запуска из админ-панели.
        """
        logger.info("🔄 Ручной запуск мониторинга...")
        try:
            await self._process_new_tenders([])
            logger.info("✅ Ручной мониторинг завершён")
        except Exception as e:
            logger.error(f"❌ Ошибка ручного мониторинга: {e}", exc_info=True)
            raise

    async def _process_new_tenders(self, new_tenders: List[Dict[str, Any]]):
        """
        Callback для обработки новых тендеров.

        НОВАЯ ЛОГИКА: Вместо матчинга всех тендеров против фильтров,
        делаем целевой RSS запрос для каждого фильтра (как в instant_search).

        Args:
            new_tenders: Список новых тендеров от парсера (ИГНОРИРУЕТСЯ в новой логике)
        """
        try:
            logger.info(f"\n🔄 Проверка активных фильтров...")

            if not self.db:
                logger.warning("⚠️  DB не инициализирована")
                return

            # 1. Получаем все активные фильтры пользователей
            filters = await self.db.get_all_active_filters()
            logger.info(f"   📋 Активных фильтров: {len(filters)}")

            if not filters:
                logger.info("   ℹ️  Нет активных фильтров для проверки")
                return

            # 2. Для КАЖДОГО фильтра делаем целевой поиск
            searcher = InstantSearch()
            notifications_to_send = []
            # Дедупликация: один тендер → одно уведомление для пользователя
            seen_tenders = set()  # (user_id, tender_number)

            # Кэш данных пользователей и статистика отправки (persistent across filters)
            user_data_cache = {}
            sent_count = 0
            failed_count = 0

            for filter_data in filters:
                filter_id = filter_data['id']
                filter_name = filter_data['name']
                user_id = filter_data['user_id']
                telegram_id = filter_data.get('telegram_id')
                subscription_tier = filter_data.get('subscription_tier', 'trial')

                # Per-filter routing: определяем куда отправлять
                notify_chat_ids = filter_data.get('notify_chat_ids') or []
                if not notify_chat_ids:
                    target_chat_ids = [telegram_id]
                else:
                    target_chat_ids = notify_chat_ids

                logger.info(f"\n   🔍 Проверка фильтра: {filter_name} (ID: {filter_id})")

                # Парсим keywords из JSON
                keywords_raw = filter_data.get('keywords', '[]')
                try:
                    keywords = json.loads(keywords_raw) if isinstance(keywords_raw, str) else keywords_raw
                except:
                    keywords = []

                if not keywords:
                    logger.warning(f"      ⚠️  Нет ключевых слов, пропускаем")
                    continue

                # Читаем AI-расширенные ключевые слова (сохранены при создании фильтра)
                expanded_keywords_raw = filter_data.get('expanded_keywords', [])
                if isinstance(expanded_keywords_raw, str):
                    try:
                        expanded_keywords = json.loads(expanded_keywords_raw)
                    except:
                        expanded_keywords = []
                else:
                    expanded_keywords = expanded_keywords_raw or []

                # Делаем поиск по фильтру (С AI проверкой релевантности и расширенными словами)
                try:
                    search_results = await searcher.search_by_filter(
                        filter_data=filter_data,
                        max_tenders=25,  # Больше тендеров для лучшего покрытия
                        expanded_keywords=expanded_keywords,  # AI-расширенные ключевые слова
                        use_ai_check=True,  # AI проверка релевантности
                        user_id=user_id,
                        subscription_tier=subscription_tier
                    )

                    matches = search_results.get('matches', [])
                    logger.info(f"      ✅ Найдено совпадений: {len(matches)}")

                    # Сбрасываем счетчик ошибок при успешном поиске
                    await self.db.reset_filter_error_count(filter_id)

                    # Единый порог для composite score (SmartMatcher + AI boost)
                    # AI boost добавляется в instant_search: +15 (conf>=60), +10 (conf>=40)
                    MIN_SCORE_FOR_NOTIFICATION = 35

                    # Фильтруем только новые тендеры (которых еще не уведомляли)
                    for match in matches:
                        # match УЖЕ содержит данные тендера + composite match_score
                        tender = match
                        tender_number = tender.get('number')
                        tender_name = tender.get('name', '')[:50]
                        score = tender.get('match_score', 0)

                        # === ФИЛЬТР ПО COMPOSITE SCORE ===
                        if score < MIN_SCORE_FOR_NOTIFICATION or not tender_number:
                            continue

                        # === ПРОВЕРКА: дедлайн не просрочен ===
                        deadline = tender.get('submission_deadline') or tender.get('deadline') or tender.get('end_date')
                        if deadline:
                            try:
                                deadline_date = None
                                deadline_str = str(deadline).strip()
                                for fmt in ['%d.%m.%Y %H:%M', '%d.%m.%Y', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d']:
                                    try:
                                        deadline_date = datetime.strptime(deadline_str, fmt)
                                        break
                                    except ValueError:
                                        continue
                                if deadline_date and deadline_date < datetime.now():
                                    continue
                            except Exception:
                                pass

                        # Проверяем, не отправляли ли уже (БД)
                        already_notified = await self.db.is_tender_notified(tender_number, user_id)
                        if already_notified:
                            continue

                        # Проверяем квоту (админы имеют неограниченный доступ)
                        is_admin = BotConfig.ADMIN_USER_ID and telegram_id == BotConfig.ADMIN_USER_ID

                        if not is_admin:
                            # Лимиты хардкод (пока не мигрирован на PostgreSQL)
                            daily_limit = 20 if subscription_tier == 'trial' else (50 if subscription_tier == 'basic' else 100)
                            has_quota = await self.db.check_notification_quota(user_id, daily_limit)

                            if not has_quota:
                                logger.warning(f"         ⚠️  Квота исчерпана для user {user_id}")
                                if self.notifier:
                                    await self.notifier.send_quota_exceeded_notification(
                                        telegram_id=telegram_id,
                                        current_limit=daily_limit
                                    )
                                break  # Выходим из цикла для этого фильтра
                        else:
                            logger.info(f"         👑 Админ {telegram_id}: неограниченный доступ")

                        # Добавляем в очередь на отправку для каждого target_chat_id
                        for target_chat_id in target_chat_ids:
                            dedup_key = (target_chat_id, tender_number)
                            if dedup_key in seen_tenders:
                                continue
                            seen_tenders.add(dedup_key)

                            notifications_to_send.append({
                                'user_id': user_id,
                                'telegram_id': target_chat_id,
                                'tender': tender,
                                'match_info': {
                                    'score': score,
                                    'matched_keywords': tender.get('match_reasons', []),
                                    'red_flags': tender.get('red_flags', []),
                                    'ai_verified': tender.get('ai_verified', False),
                                    'ai_confidence': tender.get('ai_confidence'),
                                    'ai_reason': tender.get('ai_reason', ''),
                                },
                                'filter_id': filter_id,
                                'filter_name': filter_name,
                                'score': score,
                                'subscription_tier': subscription_tier
                            })

                        logger.info(f"         📤 Готово к отправке: {tender_number} (score: {score}, targets: {len(target_chat_ids)})")

                except Exception as e:
                    logger.error(f"      ❌ Ошибка поиска для фильтра {filter_id}: {e}", exc_info=True)

                    # Увеличиваем счетчик ошибок
                    error_count = await self.db.increment_filter_error_count(filter_id)

                    # Если 3 последовательные ошибки - уведомляем пользователя
                    if error_count >= 3 and self.notifier and telegram_id:
                        error_type = "Прокси" if "proxy" in str(e).lower() or "timeout" in str(e).lower() else "RSS"
                        await self.notifier.send_monitoring_error_notification(
                            telegram_id=telegram_id,
                            filter_name=filter_name,
                            error_type=error_type,
                            error_count=error_count
                        )
                        logger.info(f"      📧 Отправлено уведомление об ошибке пользователю {telegram_id}")

                    continue

                # === НЕМЕДЛЕННАЯ ОТПРАВКА уведомлений этого фильтра ===
                # Отправляем сразу после каждого фильтра, а не копим до конца цикла.
                # Защита от потери уведомлений при сбоях/рестартах.
                if notifications_to_send and self.notifier:
                    logger.info(f"      📤 Отправка {len(notifications_to_send)} уведомлений фильтра «{filter_name}»...")

                    for notif in notifications_to_send:
                      try:
                        ntf_telegram_id = notif['telegram_id']
                        tender_number = notif['tender'].get('number', '?')

                        # Проверяем тихие часы (получаем данные пользователя из кэша или БД)
                        if ntf_telegram_id not in user_data_cache:
                            user_data_cache[ntf_telegram_id] = await self.db.get_user_by_telegram_id(ntf_telegram_id)

                        user_data = user_data_cache.get(ntf_telegram_id, {})
                        is_quiet_hours = not await self._should_send_notification(user_data)

                        tender = notif['tender']

                        # Генерируем AI-название ОДИН РАЗ (для уведомления и БД)
                        original_name = tender.get('name', '')
                        short_name = generate_tender_name(
                            original_name,
                            tender_data=tender,
                            max_length=80
                        )
                        # Заменяем название в тендере на короткое
                        tender['name'] = short_name

                        # Нормализуем данные тендера (маппинг из InstantSearch формата в БД формат)
                        tender_data = {
                            'number': tender.get('number', ''),
                            'name': short_name,
                            'price': tender.get('price'),
                            'url': tender.get('url', ''),
                            'region': tender.get('customer_region', tender.get('region', '')),
                            'customer_name': tender.get('customer', tender.get('customer_name', '')),
                            'published_date': tender.get('published', tender.get('published_date', '')),
                            'submission_deadline': tender.get('submission_deadline', '')
                        }

                        if is_quiet_hours:
                            logger.info(f"      🌙 Тихие часы для {ntf_telegram_id} — сохраняем без отправки")
                            await self.db.save_notification(
                                user_id=notif['user_id'],
                                filter_id=notif['filter_id'],
                                filter_name=notif['filter_name'],
                                tender_data=tender_data,
                                score=notif['score'],
                                matched_keywords=notif['match_info'].get('matched_keywords', [])
                            )
                            continue

                        success = await self.notifier.send_tender_notification(
                            telegram_id=ntf_telegram_id,
                            tender=tender,
                            match_info=notif['match_info'],
                            filter_name=notif['filter_name'],
                            is_auto_notification=True,
                            subscription_tier=notif.get('subscription_tier', 'trial')
                        )

                        if success:
                            logger.info(f"      ✅ Отправлено: {tender_number} → {ntf_telegram_id}")

                            await self.db.save_notification(
                                user_id=notif['user_id'],
                                filter_id=notif['filter_id'],
                                filter_name=notif['filter_name'],
                                tender_data=tender_data,
                                score=notif['score'],
                                matched_keywords=notif['match_info'].get('matched_keywords', [])
                            )

                            is_admin = BotConfig.ADMIN_USER_ID and ntf_telegram_id == BotConfig.ADMIN_USER_ID
                            if not is_admin:
                                await self.db.increment_notification_quota(notif['user_id'])

                            sent_count += 1
                            self.stats['notifications_sent'] += 1
                        else:
                            logger.warning(f"      ❌ Не удалось отправить: {tender_number} → {ntf_telegram_id}")
                            failed_count += 1

                            # Если пользователь заблокировал бота — помечаем и деактивируем фильтры
                            if ntf_telegram_id in self.notifier.blocked_chat_ids:
                                await self.db.mark_user_bot_blocked(ntf_telegram_id)
                                break  # Нет смысла отправлять остальные уведомления этому получателю

                      except Exception as e:
                        failed_count += 1
                        t_num = notif.get('tender', {}).get('number', '?')
                        logger.error(f"      ❌ Ошибка отправки уведомления {t_num}: {e}", exc_info=True)

                      # Небольшая задержка между уведомлениями
                      await asyncio.sleep(0.1)

                    notifications_to_send = []  # Очищаем после отправки

            # 3. Итоги цикла
            logger.info(f"\n   📊 Итого за цикл: {sent_count} отправлено, {failed_count} ошибок")

            # Очищаем кэш обогащения после каждого цикла (экономия памяти)
            cache_stats = InstantSearch.get_cache_stats()
            if cache_stats['size'] > 100:
                InstantSearch.clear_cache()

        except Exception as e:
            logger.error(f"❌ Ошибка обработки тендеров: {e}", exc_info=True)
            self.stats['errors'] += 1

    async def _should_send_notification(self, user_data: dict) -> bool:
        """
        Check if notification should be sent based on quiet hours and notification mode.

        Args:
            user_data: User data dict containing quiet_hours and notification_mode settings

        Returns:
            True if notification should be sent, False otherwise
        """
        # Получаем настройки из data пользователя
        data = user_data.get('data', {}) or {}

        # Проверяем режим уведомлений
        notification_mode = data.get('notification_mode', 'instant')
        if notification_mode == 'digest':
            # Режим "только дайджест" - не отправляем мгновенные уведомления
            logger.debug(f"   📬 Режим 'только дайджест' - пропускаем мгновенное уведомление")
            return False

        # Проверяем тихие часы
        if not data.get('quiet_hours_enabled', False):
            return True

        now = datetime.utcnow() + timedelta(hours=self.MOSCOW_TZ_OFFSET)  # Moscow time
        current_hour = now.hour
        start = data.get('quiet_hours_start', 22)
        end = data.get('quiet_hours_end', 8)

        # Handle overnight range (e.g., 22:00 - 08:00)
        if start > end:
            # If current hour is >= start (e.g., 22, 23) OR < end (e.g., 0-7)
            is_quiet = current_hour >= start or current_hour < end
        else:
            # Normal range (e.g., 1:00 - 6:00)
            is_quiet = start <= current_hour < end

        if is_quiet:
            logger.debug(f"   🌙 Тихие часы ({start}:00-{end}:00), текущее время МСК: {current_hour}:00")
            return False

        return True

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

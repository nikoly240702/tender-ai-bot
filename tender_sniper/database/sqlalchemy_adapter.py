"""
SQLAlchemy adapter для tender_sniper/database.

Обертка над unified database.py для обратной совместимости.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from sqlalchemy import select, update, delete, func, and_, or_
from sqlalchemy.exc import IntegrityError

# Импортируем из unified database
from database import (
    SniperUser as SniperUserModel,
    SniperFilter as SniperFilterModel,
    SniperNotification as SniperNotificationModel,
    TenderCache as TenderCacheModel,
    FilterDraft as FilterDraftModel,  # 🧪 БЕТА: Черновики фильтров
    # Phase 2.1 models
    SearchHistory as SearchHistoryModel,
    UserFeedback as UserFeedbackModel,
    Subscription as SubscriptionModel,
    SatisfactionSurvey as SatisfactionSurveyModel,
    ViewedTender as ViewedTenderModel,
    get_session,
    DatabaseSession
)

logger = logging.getLogger(__name__)


def serialize_for_json(obj: Any) -> Any:
    """Рекурсивная сериализация для JSON."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: serialize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [serialize_for_json(item) for item in obj]
    return obj


class TenderSniperDB:
    """
    SQLAlchemy adapter для Tender Sniper DB.

    Совместим с интерфейсом aiosqlite версии.
    """

    def __init__(self, db_path=None):
        """Инициализация (db_path игнорируется - используется DATABASE_URL)."""
        pass

    async def init_db(self):
        """Инициализация БД (таблицы создаются автоматически через Alembic)."""
        logger.info("Database уже инициализирована через Alembic")

    # ============================================
    # USERS
    # ============================================

    async def create_or_update_user(
        self,
        telegram_id: int,
        username: Optional[str] = None,
        subscription_tier: str = 'trial',  # Новые пользователи получают trial
        **kwargs
    ) -> int:
        """Создание или обновление пользователя."""
        async with DatabaseSession() as session:
            # Проверяем существование
            result = await session.execute(
                select(SniperUserModel).where(SniperUserModel.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()

            if user:
                # Обновляем (не меняем tier для существующих!)
                user.username = username
                user.last_activity = datetime.utcnow()
                return user.id
            else:
                # Создаем нового с триалом на 14 дней
                now = datetime.utcnow()
                trial_expires = now + timedelta(days=14)

                user = SniperUserModel(
                    telegram_id=telegram_id,
                    username=username,
                    subscription_tier='trial',
                    filters_limit=3,  # Trial лимиты
                    notifications_limit=20,
                    trial_started_at=now,
                    trial_expires_at=trial_expires
                )
                session.add(user)
                await session.flush()
                logger.info(f"New user {telegram_id} created with 14-day trial (expires {trial_expires})")
                return user.id

    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        """Получение пользователя по telegram_id."""
        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperUserModel).where(SniperUserModel.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()

            if not user:
                return None

            return {
                'id': user.id,
                'telegram_id': user.telegram_id,
                'username': user.username,
                'subscription_tier': user.subscription_tier,
                'filters_limit': user.filters_limit,
                'notifications_limit': user.notifications_limit,
                'notifications_sent_today': user.notifications_sent_today,
                'notifications_enabled': user.notifications_enabled,
                'last_notification_reset': user.last_notification_reset.isoformat() if user.last_notification_reset else None,
                'created_at': user.created_at.isoformat() if user.created_at else None
            }

    async def get_monitoring_status(self, telegram_id: int) -> bool:
        """Получение статуса автомониторинга пользователя."""
        user = await self.get_user_by_telegram_id(telegram_id)
        if not user:
            return True  # По умолчанию включен
        return user.get('notifications_enabled', True)

    async def pause_monitoring(self, telegram_id: int) -> bool:
        """Приостановить автомониторинг для пользователя."""
        async with DatabaseSession() as session:
            await session.execute(
                update(SniperUserModel)
                .where(SniperUserModel.telegram_id == telegram_id)
                .values(notifications_enabled=False)
            )
            await session.commit()
            return True

    async def resume_monitoring(self, telegram_id: int) -> bool:
        """Возобновить автомониторинг для пользователя."""
        async with DatabaseSession() as session:
            await session.execute(
                update(SniperUserModel)
                .where(SniperUserModel.telegram_id == telegram_id)
                .values(notifications_enabled=True)
            )
            await session.commit()
            return True

    async def set_monitoring_status(self, telegram_id: int, enabled: bool) -> bool:
        """Установить статус автомониторинга для пользователя."""
        async with DatabaseSession() as session:
            await session.execute(
                update(SniperUserModel)
                .where(SniperUserModel.telegram_id == telegram_id)
                .values(notifications_enabled=enabled)
            )
            await session.commit()
            return True

    async def reset_daily_notifications(self, user_id: int):
        """Сброс счетчика уведомлений."""
        async with DatabaseSession() as session:
            await session.execute(
                update(SniperUserModel)
                .where(SniperUserModel.id == user_id)
                .values(
                    notifications_sent_today=0,
                    last_notification_reset=datetime.utcnow()
                )
            )

    async def increment_notifications_count(self, user_id: int):
        """Инкремент счетчика уведомлений."""
        async with DatabaseSession() as session:
            await session.execute(
                update(SniperUserModel)
                .where(SniperUserModel.id == user_id)
                .values(notifications_sent_today=SniperUserModel.notifications_sent_today + 1)
            )

    async def check_notification_quota(self, user_id: int, daily_limit: int) -> bool:
        """Проверка квоты уведомлений пользователя."""
        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperUserModel).where(SniperUserModel.id == user_id)
            )
            user = result.scalar_one_or_none()

            if not user:
                return False

            # Проверяем, нужно ли сбросить счетчик (прошел день)
            from datetime import timedelta
            if user.last_notification_reset:
                time_since_reset = datetime.utcnow() - user.last_notification_reset
                if time_since_reset > timedelta(days=1):
                    # Сбрасываем счетчик
                    await self.reset_daily_notifications(user_id)
                    return True

            # Проверяем квоту
            return user.notifications_sent_today < daily_limit

    async def increment_notification_quota(self, user_id: int):
        """Алиас для increment_notifications_count (для обратной совместимости)."""
        await self.increment_notifications_count(user_id)

    # ============================================
    # FILTERS
    # ============================================

    async def create_filter(self, user_id: int, name: str, **kwargs) -> int:
        """Создание фильтра."""
        async with DatabaseSession() as session:
            filter_obj = SniperFilterModel(
                user_id=user_id,
                name=name,
                keywords=kwargs.get('keywords', []),
                exclude_keywords=kwargs.get('exclude_keywords', []),
                price_min=kwargs.get('price_min'),
                price_max=kwargs.get('price_max'),
                regions=kwargs.get('regions', []),
                customer_types=kwargs.get('customer_types', []),
                tender_types=kwargs.get('tender_types', []),
                law_type=kwargs.get('law_type'),
                purchase_stage=kwargs.get('purchase_stage'),
                purchase_method=kwargs.get('purchase_method'),
                okpd2_codes=kwargs.get('okpd2_codes', []),
                min_deadline_days=kwargs.get('min_deadline_days'),
                customer_keywords=kwargs.get('customer_keywords', []),
                exact_match=kwargs.get('exact_match', False),  # Режим поиска
                # 🧪 БЕТА: Фаза 2 - Расширенные фильтры
                purchase_number=kwargs.get('purchase_number'),
                customer_inn=kwargs.get('customer_inn', []),
                excluded_customer_inns=kwargs.get('excluded_customer_inns', []),
                excluded_customer_keywords=kwargs.get('excluded_customer_keywords', []),
                execution_regions=kwargs.get('execution_regions', []),
                publication_days=kwargs.get('publication_days'),
                primary_keywords=kwargs.get('primary_keywords', []),
                secondary_keywords=kwargs.get('secondary_keywords', []),
                search_in=kwargs.get('search_in', []),
                is_active=kwargs.get('is_active', True)  # По умолчанию активен
            )
            session.add(filter_obj)
            await session.flush()
            return filter_obj.id

    async def get_user_filters(self, user_id: int, active_only: bool = True) -> List[Dict[str, Any]]:
        """Получение фильтров пользователя."""
        async with DatabaseSession() as session:
            query = select(SniperFilterModel).where(SniperFilterModel.user_id == user_id)

            if active_only:
                query = query.where(SniperFilterModel.is_active == True)

            result = await session.execute(query.order_by(SniperFilterModel.created_at.desc()))
            filters = result.scalars().all()

            return [self._filter_to_dict(f) for f in filters]

    async def get_active_filters(self, user_id: int) -> List[Dict[str, Any]]:
        """Алиас для get_user_filters (для обратной совместимости)."""
        return await self.get_user_filters(user_id, active_only=True)

    async def get_filter_by_id(self, filter_id: int) -> Optional[Dict[str, Any]]:
        """Получение фильтра по ID."""
        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperFilterModel).where(SniperFilterModel.id == filter_id)
            )
            filter_obj = result.scalar_one_or_none()

            if not filter_obj:
                return None

            return self._filter_to_dict(filter_obj)

    async def update_filter(self, filter_id: int, **kwargs):
        """Обновление фильтра.

        Все переданные kwargs будут обновлены (включая None для очистки поля).
        Если поле не должно обновляться, просто не передавайте его.
        """
        async with DatabaseSession() as session:
            # Включаем все переданные kwargs (включая None для очистки полей)
            values = dict(kwargs)
            values['updated_at'] = datetime.utcnow()

            await session.execute(
                update(SniperFilterModel)
                .where(SniperFilterModel.id == filter_id)
                .values(**values)
            )

    async def delete_filter(self, filter_id: int):
        """Удаление фильтра."""
        async with DatabaseSession() as session:
            await session.execute(
                delete(SniperFilterModel).where(SniperFilterModel.id == filter_id)
            )

    async def get_all_active_filters(self) -> List[Dict[str, Any]]:
        """Получение всех активных фильтров с информацией о пользователе."""
        async with DatabaseSession() as session:
            # JOIN с SniperUser чтобы получить telegram_id и subscription_tier
            # ВАЖНО: проверяем и is_active фильтра И notifications_enabled пользователя
            result = await session.execute(
                select(SniperFilterModel, SniperUserModel)
                .join(SniperUserModel, SniperFilterModel.user_id == SniperUserModel.id)
                .where(
                    and_(
                        SniperFilterModel.is_active == True,
                        SniperUserModel.notifications_enabled == True  # Пауза автомониторинга
                    )
                )
            )
            filter_user_pairs = result.all()

            filters = []
            for filter_obj, user_obj in filter_user_pairs:
                filter_dict = self._filter_to_dict(filter_obj)
                # Добавляем telegram_id и subscription_tier из user
                filter_dict['telegram_id'] = user_obj.telegram_id
                filter_dict['subscription_tier'] = user_obj.subscription_tier
                filters.append(filter_dict)

            return filters

    def _filter_to_dict(self, filter_obj: SniperFilterModel) -> Dict[str, Any]:
        """Конвертация фильтра в dict."""
        def safe_list(value):
            """Безопасное преобразование в список."""
            if value is None:
                return []
            if isinstance(value, list):
                return value
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except:
                    return []
            return []

        return {
            'id': filter_obj.id,
            'user_id': filter_obj.user_id,
            'name': filter_obj.name,
            'keywords': safe_list(filter_obj.keywords),
            'exclude_keywords': safe_list(filter_obj.exclude_keywords),
            'price_min': filter_obj.price_min,
            'price_max': filter_obj.price_max,
            'regions': safe_list(filter_obj.regions),
            'customer_types': safe_list(filter_obj.customer_types),
            'tender_types': safe_list(filter_obj.tender_types),
            'law_type': filter_obj.law_type,
            'purchase_stage': filter_obj.purchase_stage,
            'purchase_method': filter_obj.purchase_method,
            'okpd2_codes': safe_list(filter_obj.okpd2_codes),
            'min_deadline_days': filter_obj.min_deadline_days,
            'customer_keywords': safe_list(filter_obj.customer_keywords),
            'exact_match': getattr(filter_obj, 'exact_match', False),
            # 🧪 БЕТА: Фаза 2 - Расширенные фильтры
            'purchase_number': getattr(filter_obj, 'purchase_number', None),
            'customer_inn': safe_list(getattr(filter_obj, 'customer_inn', [])),
            'excluded_customer_inns': safe_list(getattr(filter_obj, 'excluded_customer_inns', [])),
            'excluded_customer_keywords': safe_list(getattr(filter_obj, 'excluded_customer_keywords', [])),
            'execution_regions': safe_list(getattr(filter_obj, 'execution_regions', [])),
            'publication_days': getattr(filter_obj, 'publication_days', None),
            'primary_keywords': safe_list(getattr(filter_obj, 'primary_keywords', [])),
            'secondary_keywords': safe_list(getattr(filter_obj, 'secondary_keywords', [])),
            'search_in': safe_list(getattr(filter_obj, 'search_in', [])),
            'is_active': filter_obj.is_active,
            'created_at': filter_obj.created_at.isoformat() if filter_obj.created_at else None,
            'updated_at': filter_obj.updated_at.isoformat() if filter_obj.updated_at else None
        }

    # ============================================
    # NOTIFICATIONS
    # ============================================

    async def save_notification(
        self,
        user_id: int,
        filter_id: int,
        filter_name: str,
        tender_data: Dict[str, Any],
        score: int,
        matched_keywords: List[str],
        telegram_message_id: Optional[int] = None,
        source: str = 'automonitoring'
    ) -> int:
        """Сохранение уведомления."""
        async with DatabaseSession() as session:
            # DEBUG: Логируем что именно сохраняем
            logger.debug(f"   💾 save_notification: number={tender_data.get('number')}, "
                        f"region='{tender_data.get('region')}', customer='{tender_data.get('customer_name')}'")

            # Парсинг даты публикации (поддержка RFC 2822 и ISO форматов)
            published_date = None
            if tender_data.get('published_date'):
                date_str = tender_data['published_date']
                try:
                    # Сначала пробуем ISO формат
                    published_date = datetime.fromisoformat(date_str)
                except (ValueError, TypeError):
                    try:
                        # Если не ISO, пробуем RFC 2822 (GMT формат)
                        from email.utils import parsedate_to_datetime
                        published_date = parsedate_to_datetime(date_str)
                    except Exception as e:
                        logger.warning(f"   ⚠️  Не удалось распарсить дату '{date_str}': {e}")

                # КРИТИЧНО: PostgreSQL TIMESTAMP WITHOUT TIME ZONE не принимает timezone
                # Убираем timezone если есть
                if published_date and published_date.tzinfo is not None:
                    published_date = published_date.replace(tzinfo=None)

            # Парсинг срока подачи заявки (submission_deadline)
            submission_deadline = None
            if tender_data.get('submission_deadline') or tender_data.get('deadline') or tender_data.get('end_date'):
                deadline_str = tender_data.get('submission_deadline') or tender_data.get('deadline') or tender_data.get('end_date')
                try:
                    # Пробуем ISO формат
                    submission_deadline = datetime.fromisoformat(deadline_str)
                except (ValueError, TypeError):
                    try:
                        # Пробуем RFC 2822
                        from email.utils import parsedate_to_datetime
                        submission_deadline = parsedate_to_datetime(deadline_str)
                    except:
                        # Пробуем распространенные форматы даты
                        for fmt in ['%d.%m.%Y', '%Y-%m-%d', '%d.%m.%Y %H:%M', '%Y-%m-%d %H:%M']:
                            try:
                                submission_deadline = datetime.strptime(deadline_str, fmt)
                                break
                            except:
                                continue

                # Убираем timezone если есть
                if submission_deadline and submission_deadline.tzinfo is not None:
                    submission_deadline = submission_deadline.replace(tzinfo=None)

            notification = SniperNotificationModel(
                user_id=user_id,
                filter_id=filter_id,
                filter_name=filter_name,
                tender_number=tender_data.get('number', ''),
                tender_name=tender_data.get('name', ''),
                tender_price=tender_data.get('price'),
                tender_url=tender_data.get('url'),
                tender_region=tender_data.get('region'),
                tender_customer=tender_data.get('customer_name'),
                score=score,
                matched_keywords=matched_keywords,
                published_date=published_date,
                submission_deadline=submission_deadline,
                tender_source=source,
                telegram_message_id=telegram_message_id
            )
            session.add(notification)
            await session.flush()

            # DEBUG: Логируем что сохранилось
            logger.debug(f"   ✅ Saved notification id={notification.id}, "
                        f"tender_region='{notification.tender_region}', tender_customer='{notification.tender_customer}'")

            return notification.id

    async def get_user_tenders(self, user_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        """Получение тендеров пользователя."""
        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperNotificationModel)
                .where(SniperNotificationModel.user_id == user_id)
                .order_by(SniperNotificationModel.sent_at.desc())
                .limit(limit)
            )
            notifications = result.scalars().all()

            logger.info(f"📊 get_user_tenders: найдено {len(notifications)} уведомлений для user_id={user_id}")

            # DEBUG: Показываем первое уведомление
            if notifications:
                first = notifications[0]
                logger.debug(f"   🔍 Первое уведомление: number={first.tender_number}, "
                           f"region='{first.tender_region}', customer='{first.tender_customer}'")

            tenders = [{
                'number': n.tender_number,
                'name': n.tender_name,
                'price': n.tender_price,
                'url': n.tender_url,
                'region': n.tender_region,
                'customer_name': n.tender_customer,
                'filter_name': n.filter_name,
                'score': n.score,
                'published_date': n.published_date.isoformat() if n.published_date else None,
                'submission_deadline': n.submission_deadline.isoformat() if n.submission_deadline else None,
                'source': n.tender_source,
                'sent_at': n.sent_at.isoformat() if n.sent_at else None
            } for n in notifications]

            return tenders

    async def is_tender_notified(self, tender_number: str, user_id: int) -> bool:
        """Проверка, было ли уже отправлено уведомление о тендере пользователю."""
        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperNotificationModel).where(
                    and_(
                        SniperNotificationModel.tender_number == tender_number,
                        SniperNotificationModel.user_id == user_id
                    )
                )
            )
            return result.scalar_one_or_none() is not None

    async def get_user_stats(self, user_id: int) -> Dict[str, Any]:
        """
        Получение статистики пользователя.

        Args:
            user_id: Внутренний ID пользователя (не telegram_id)

        Returns:
            Словарь со статистикой:
            - notifications_today: уведомлений сегодня
            - total_notifications: всего уведомлений
            - total_matches: всего совпадений (алиас для total_notifications)
            - active_filters: активных фильтров
            - notifications_limit: лимит уведомлений пользователя
        """
        async with DatabaseSession() as session:
            # Получаем данные пользователя для лимита
            user_result = await session.execute(
                select(SniperUserModel).where(SniperUserModel.id == user_id)
            )
            user = user_result.scalar_one_or_none()
            notifications_limit = user.notifications_limit if user else 15

            # Общее количество уведомлений
            total_result = await session.execute(
                select(func.count()).select_from(SniperNotificationModel).where(
                    SniperNotificationModel.user_id == user_id
                )
            )
            total_notifications = total_result.scalar() or 0

            # Уведомлений за сегодня (с начала дня UTC)
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            today_result = await session.execute(
                select(func.count()).select_from(SniperNotificationModel).where(
                    and_(
                        SniperNotificationModel.user_id == user_id,
                        SniperNotificationModel.sent_at >= today_start
                    )
                )
            )
            notifications_today = today_result.scalar() or 0

            # Количество активных фильтров
            filters_result = await session.execute(
                select(func.count()).select_from(SniperFilterModel).where(
                    and_(
                        SniperFilterModel.user_id == user_id,
                        SniperFilterModel.is_active == True
                    )
                )
            )
            active_filters = filters_result.scalar() or 0

            return {
                'notifications_today': notifications_today,
                'total_notifications': total_notifications,
                'total_matches': total_notifications,  # алиас для совместимости
                'active_filters': active_filters,
                'notifications_limit': notifications_limit
            }

    # ============================================
    # TENDER CACHE
    # ============================================

    async def is_tender_processed(self, tender_number: str, tender_hash: str) -> bool:
        """Проверка, был ли тендер обработан."""
        async with DatabaseSession() as session:
            result = await session.execute(
                select(TenderCacheModel).where(
                    and_(
                        TenderCacheModel.tender_number == tender_number,
                        TenderCacheModel.tender_hash == tender_hash
                    )
                )
            )
            return result.scalar_one_or_none() is not None

    async def mark_tender_processed(self, tender_number: str, tender_hash: str):
        """Отметить тендер как обработанный."""
        async with DatabaseSession() as session:
            # Проверяем существование
            result = await session.execute(
                select(TenderCacheModel).where(TenderCacheModel.tender_number == tender_number)
            )
            existing = result.scalar_one_or_none()

            if existing:
                # Обновляем
                existing.tender_hash = tender_hash
                existing.last_seen = datetime.utcnow()
                existing.times_matched += 1
            else:
                # Создаем новый
                cache_entry = TenderCacheModel(
                    tender_number=tender_number,
                    tender_hash=tender_hash
                )
                session.add(cache_entry)

    # ============================================
    # ОЧИСТКА ИСТОРИИ
    # ============================================

    async def clear_all_notifications(self, telegram_id: int) -> int:
        """
        Удалить все уведомления пользователя.

        Args:
            telegram_id: Telegram ID пользователя

        Returns:
            Количество удаленных записей
        """
        async with DatabaseSession() as session:
            # Получаем внутренний user_id по telegram_id
            user_result = await session.execute(
                select(SniperUserModel.id).where(SniperUserModel.telegram_id == telegram_id)
            )
            user_row = user_result.first()

            if not user_row:
                return 0

            user_id = user_row[0]

            # Получаем count перед удалением
            count_result = await session.execute(
                select(func.count()).select_from(SniperNotificationModel).where(
                    SniperNotificationModel.user_id == user_id
                )
            )
            count = count_result.scalar()

            # Удаляем все уведомления пользователя
            await session.execute(
                delete(SniperNotificationModel).where(
                    SniperNotificationModel.user_id == user_id
                )
            )
            await session.commit()

            return count

    async def clear_old_notifications(self, telegram_id: int, days: int) -> int:
        """
        Удалить уведомления старше указанного количества дней.

        Args:
            telegram_id: Telegram ID пользователя
            days: Количество дней (удаляются записи старше этого периода)

        Returns:
            Количество удаленных записей
        """
        from datetime import timedelta

        async with DatabaseSession() as session:
            # Получаем внутренний user_id по telegram_id
            user_result = await session.execute(
                select(SniperUserModel.id).where(SniperUserModel.telegram_id == telegram_id)
            )
            user_row = user_result.first()

            if not user_row:
                return 0

            user_id = user_row[0]

            cutoff_date = datetime.utcnow() - timedelta(days=days)

            # Получаем count перед удалением
            count_result = await session.execute(
                select(func.count()).select_from(SniperNotificationModel).where(
                    and_(
                        SniperNotificationModel.user_id == user_id,
                        SniperNotificationModel.sent_at < cutoff_date
                    )
                )
            )
            count = count_result.scalar()

            # Удаляем старые уведомления
            await session.execute(
                delete(SniperNotificationModel).where(
                    and_(
                        SniperNotificationModel.user_id == user_id,
                        SniperNotificationModel.sent_at < cutoff_date
                    )
                )
            )
            await session.commit()

            return count

    # ============================================
    # УПРАВЛЕНИЕ АВТОМОНИТОРИНГОМ
    # ============================================

    async def pause_filter(self, filter_id: int) -> bool:
        """
        Приостановить мониторинг конкретного фильтра.

        Args:
            filter_id: ID фильтра

        Returns:
            True если успешно
        """
        async with DatabaseSession() as session:
            await session.execute(
                update(SniperFilterModel)
                .where(SniperFilterModel.id == filter_id)
                .values(is_active=False)
            )
            await session.commit()
            return True

    async def resume_filter(self, filter_id: int) -> bool:
        """
        Возобновить мониторинг конкретного фильтра.

        Args:
            filter_id: ID фильтра

        Returns:
            True если успешно
        """
        async with DatabaseSession() as session:
            await session.execute(
                update(SniperFilterModel)
                .where(SniperFilterModel.id == filter_id)
                .values(is_active=True)
            )
            await session.commit()
            return True

    async def get_filter_status(self, filter_id: int) -> Optional[bool]:
        """
        Получить статус фильтра (активен или на паузе).

        Args:
            filter_id: ID фильтра

        Returns:
            True если активен, False если на паузе, None если не найден
        """
        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperFilterModel.is_active)
                .where(SniperFilterModel.id == filter_id)
            )
            row = result.first()
            return row[0] if row else None

    # ============================================
    # ОБРАБОТКА ОШИБОК МОНИТОРИНГА
    # ============================================

    async def increment_filter_error_count(self, filter_id: int) -> int:
        """
        Увеличить счетчик ошибок фильтра.

        Args:
            filter_id: ID фильтра

        Returns:
            Новое значение счетчика ошибок
        """
        async with DatabaseSession() as session:
            # Получаем текущее значение
            result = await session.execute(
                select(SniperFilterModel.error_count)
                .where(SniperFilterModel.id == filter_id)
            )
            row = result.first()
            current_count = row[0] if row else 0

            # Увеличиваем на 1
            new_count = current_count + 1

            await session.execute(
                update(SniperFilterModel)
                .where(SniperFilterModel.id == filter_id)
                .values(error_count=new_count)
            )
            await session.commit()

            return new_count

    async def reset_filter_error_count(self, filter_id: int) -> None:
        """
        Сбросить счетчик ошибок фильтра.

        Args:
            filter_id: ID фильтра
        """
        async with DatabaseSession() as session:
            await session.execute(
                update(SniperFilterModel)
                .where(SniperFilterModel.id == filter_id)
                .values(error_count=0)
            )
            await session.commit()

    # ============================================
    # 🧪 БЕТА: Черновики фильтров
    # ============================================

    async def save_filter_draft(
        self,
        telegram_id: int,
        draft_data: Dict[str, Any],
        current_step: str = None
    ) -> int:
        """
        Сохранить черновик фильтра.

        Args:
            telegram_id: Telegram ID пользователя
            draft_data: Данные состояния FSM
            current_step: Текущий шаг wizard

        Returns:
            ID черновика
        """
        async with DatabaseSession() as session:
            # Получаем user_id
            result = await session.execute(
                select(SniperUserModel).where(SniperUserModel.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()

            if not user:
                logger.warning(f"User not found for telegram_id {telegram_id}")
                return None

            # Проверяем существующий черновик
            result = await session.execute(
                select(FilterDraftModel).where(FilterDraftModel.user_id == user.id)
            )
            existing = result.scalar_one_or_none()

            if existing:
                # Обновляем существующий
                await session.execute(
                    update(FilterDraftModel)
                    .where(FilterDraftModel.id == existing.id)
                    .values(
                        draft_data=serialize_for_json(draft_data),
                        current_step=current_step,
                        updated_at=datetime.utcnow()
                    )
                )
                await session.commit()
                logger.debug(f"📝 Черновик обновлен для пользователя {telegram_id}")
                return existing.id
            else:
                # Создаём новый
                draft = FilterDraftModel(
                    user_id=user.id,
                    telegram_id=telegram_id,
                    draft_data=serialize_for_json(draft_data),
                    current_step=current_step
                )
                session.add(draft)
                await session.commit()
                await session.refresh(draft)
                logger.debug(f"📝 Черновик создан для пользователя {telegram_id}")
                return draft.id

    async def get_filter_draft(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        """
        Получить черновик фильтра пользователя.

        Args:
            telegram_id: Telegram ID пользователя

        Returns:
            Dict с данными черновика или None
        """
        async with DatabaseSession() as session:
            result = await session.execute(
                select(FilterDraftModel).where(FilterDraftModel.telegram_id == telegram_id)
            )
            draft = result.scalar_one_or_none()

            if draft:
                return {
                    'id': draft.id,
                    'user_id': draft.user_id,
                    'telegram_id': draft.telegram_id,
                    'draft_data': draft.draft_data,
                    'current_step': draft.current_step,
                    'created_at': draft.created_at,
                    'updated_at': draft.updated_at
                }
            return None

    async def delete_filter_draft(self, telegram_id: int) -> bool:
        """
        Удалить черновик фильтра.

        Args:
            telegram_id: Telegram ID пользователя

        Returns:
            True если удалён, False если не найден
        """
        async with DatabaseSession() as session:
            result = await session.execute(
                delete(FilterDraftModel).where(FilterDraftModel.telegram_id == telegram_id)
            )
            await session.commit()
            deleted = result.rowcount > 0
            if deleted:
                logger.debug(f"🗑️ Черновик удалён для пользователя {telegram_id}")
            return deleted

    # ============================================
    # 🧪 БЕТА: Search History (Phase 2.1)
    # ============================================

    async def save_search_history(
        self,
        user_id: int,
        search_type: str,
        keywords: List[str],
        results_count: int = 0,
        filter_id: Optional[int] = None,
        duration_ms: Optional[int] = None
    ) -> int:
        """
        Сохранить историю поиска.

        Args:
            user_id: ID пользователя (sniper_users.id)
            search_type: Тип поиска (instant_search, archive_search)
            keywords: Список ключевых слов
            results_count: Количество результатов
            filter_id: ID фильтра (опционально)
            duration_ms: Длительность в миллисекундах

        Returns:
            ID записи истории
        """
        async with DatabaseSession() as session:
            history = SearchHistoryModel(
                user_id=user_id,
                filter_id=filter_id,
                search_type=search_type,
                keywords=keywords,
                results_count=results_count,
                duration_ms=duration_ms
            )
            session.add(history)
            await session.flush()
            logger.debug(f"📊 Search history saved: user={user_id}, type={search_type}, results={results_count}")
            return history.id

    async def get_search_history(
        self,
        user_id: int,
        limit: int = 20,
        search_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Получить историю поисков пользователя.

        Args:
            user_id: ID пользователя
            limit: Максимальное количество записей
            search_type: Фильтр по типу поиска

        Returns:
            Список записей истории
        """
        async with DatabaseSession() as session:
            query = select(SearchHistoryModel).where(
                SearchHistoryModel.user_id == user_id
            )

            if search_type:
                query = query.where(SearchHistoryModel.search_type == search_type)

            query = query.order_by(SearchHistoryModel.executed_at.desc()).limit(limit)

            result = await session.execute(query)
            history = result.scalars().all()

            return [{
                'id': h.id,
                'search_type': h.search_type,
                'keywords': h.keywords,
                'results_count': h.results_count,
                'executed_at': h.executed_at.isoformat() if h.executed_at else None,
                'duration_ms': h.duration_ms
            } for h in history]

    async def get_popular_keywords(
        self,
        user_id: int,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Получить популярные ключевые слова пользователя.

        Args:
            user_id: ID пользователя
            limit: Максимальное количество

        Returns:
            Список популярных ключевых слов с частотой
        """
        async with DatabaseSession() as session:
            result = await session.execute(
                select(SearchHistoryModel.keywords)
                .where(SearchHistoryModel.user_id == user_id)
                .order_by(SearchHistoryModel.executed_at.desc())
                .limit(100)
            )
            rows = result.all()

            # Подсчёт частоты ключевых слов
            keyword_counts = {}
            for row in rows:
                keywords = row[0] or []
                for kw in keywords:
                    keyword_counts[kw] = keyword_counts.get(kw, 0) + 1

            # Сортировка по частоте
            sorted_keywords = sorted(
                keyword_counts.items(),
                key=lambda x: x[1],
                reverse=True
            )[:limit]

            return [{'keyword': kw, 'count': count} for kw, count in sorted_keywords]

    # ============================================
    # 🧪 БЕТА: User Feedback (Phase 2.1)
    # ============================================

    async def save_user_feedback(
        self,
        user_id: int,
        tender_number: str,
        feedback_type: str,
        filter_id: Optional[int] = None,
        tender_name: Optional[str] = None,
        matched_keywords: Optional[List[str]] = None,
        original_score: Optional[int] = None
    ) -> int:
        """
        Сохранить feedback пользователя на тендер.

        Args:
            user_id: ID пользователя
            tender_number: Номер тендера
            feedback_type: Тип feedback (interesting, hidden, irrelevant)
            filter_id: ID фильтра
            tender_name: Название тендера
            matched_keywords: Совпавшие ключевые слова
            original_score: Исходный score

        Returns:
            ID записи feedback
        """
        async with DatabaseSession() as session:
            feedback = UserFeedbackModel(
                user_id=user_id,
                filter_id=filter_id,
                tender_number=tender_number,
                feedback_type=feedback_type,
                tender_name=tender_name,
                matched_keywords=matched_keywords or [],
                original_score=original_score
            )
            session.add(feedback)
            await session.flush()
            logger.debug(f"👍 Feedback saved: user={user_id}, tender={tender_number}, type={feedback_type}")
            return feedback.id

    async def get_user_feedback_stats(self, user_id: int) -> Dict[str, int]:
        """
        Получить статистику feedback пользователя.

        Args:
            user_id: ID пользователя

        Returns:
            Словарь с количеством по типам feedback
        """
        async with DatabaseSession() as session:
            result = await session.execute(
                select(
                    UserFeedbackModel.feedback_type,
                    func.count(UserFeedbackModel.id)
                )
                .where(UserFeedbackModel.user_id == user_id)
                .group_by(UserFeedbackModel.feedback_type)
            )
            rows = result.all()

            return {row[0]: row[1] for row in rows}

    async def get_feedback_for_filter(
        self,
        filter_id: int,
        feedback_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Получить feedback для конкретного фильтра.

        Args:
            filter_id: ID фильтра
            feedback_type: Фильтр по типу feedback

        Returns:
            Список записей feedback
        """
        async with DatabaseSession() as session:
            query = select(UserFeedbackModel).where(
                UserFeedbackModel.filter_id == filter_id
            )

            if feedback_type:
                query = query.where(UserFeedbackModel.feedback_type == feedback_type)

            result = await session.execute(query.order_by(UserFeedbackModel.created_at.desc()))
            feedbacks = result.scalars().all()

            return [{
                'id': f.id,
                'tender_number': f.tender_number,
                'feedback_type': f.feedback_type,
                'tender_name': f.tender_name,
                'matched_keywords': f.matched_keywords,
                'original_score': f.original_score,
                'created_at': f.created_at.isoformat() if f.created_at else None
            } for f in feedbacks]

    # ============================================
    # 🧪 БЕТА: Subscriptions (Phase 2.1)
    # ============================================

    async def create_subscription(
        self,
        user_id: int,
        tier: str = 'trial',
        days: int = 14,
        max_filters: int = 3,
        max_notifications_per_day: int = 50
    ) -> int:
        """
        Создать подписку для пользователя.

        Args:
            user_id: ID пользователя
            tier: Тип подписки (trial, basic, premium)
            days: Длительность в днях
            max_filters: Максимум фильтров
            max_notifications_per_day: Максимум уведомлений в день

        Returns:
            ID подписки
        """
        async with DatabaseSession() as session:
            # Проверяем существующую подписку
            result = await session.execute(
                select(SubscriptionModel).where(SubscriptionModel.user_id == user_id)
            )
            existing = result.scalar_one_or_none()

            expires_at = datetime.utcnow() + timedelta(days=days)

            if existing:
                # Обновляем существующую
                existing.tier = tier
                existing.status = 'active'
                existing.expires_at = expires_at
                existing.max_filters = max_filters
                existing.max_notifications_per_day = max_notifications_per_day
                await session.commit()
                logger.info(f"📦 Subscription updated: user={user_id}, tier={tier}, expires={expires_at}")
                return existing.id
            else:
                # Создаём новую
                subscription = SubscriptionModel(
                    user_id=user_id,
                    tier=tier,
                    status='active',
                    expires_at=expires_at,
                    max_filters=max_filters,
                    max_notifications_per_day=max_notifications_per_day
                )
                session.add(subscription)
                await session.flush()
                logger.info(f"📦 Subscription created: user={user_id}, tier={tier}, expires={expires_at}")
                return subscription.id

    async def get_subscription(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Получить подписку пользователя.

        Args:
            user_id: ID пользователя

        Returns:
            Данные подписки или None
        """
        async with DatabaseSession() as session:
            result = await session.execute(
                select(SubscriptionModel).where(SubscriptionModel.user_id == user_id)
            )
            sub = result.scalar_one_or_none()

            if not sub:
                return None

            return {
                'id': sub.id,
                'user_id': sub.user_id,
                'tier': sub.tier,
                'status': sub.status,
                'started_at': sub.started_at.isoformat() if sub.started_at else None,
                'expires_at': sub.expires_at.isoformat() if sub.expires_at else None,
                'max_filters': sub.max_filters,
                'max_notifications_per_day': sub.max_notifications_per_day,
                'is_active': sub.is_active(),
                'is_trial': sub.is_trial(),
                'days_remaining': sub.days_remaining()
            }

    async def check_subscription_active(self, user_id: int) -> bool:
        """
        Проверить активна ли подписка пользователя.

        Args:
            user_id: ID пользователя

        Returns:
            True если подписка активна
        """
        sub = await self.get_subscription(user_id)
        return sub is not None and sub.get('is_active', False)

    async def expire_subscription(self, user_id: int) -> bool:
        """
        Пометить подписку как истекшую.

        Args:
            user_id: ID пользователя

        Returns:
            True если успешно
        """
        async with DatabaseSession() as session:
            await session.execute(
                update(SubscriptionModel)
                .where(SubscriptionModel.user_id == user_id)
                .values(status='expired')
            )
            await session.commit()
            logger.info(f"📦 Subscription expired: user={user_id}")
            return True

    # ============================================
    # 🧪 БЕТА: Viewed Tenders (Phase 2.1)
    # ============================================

    async def mark_tender_viewed(self, user_id: int, tender_number: str) -> bool:
        """
        Пометить тендер как просмотренный.

        Args:
            user_id: ID пользователя
            tender_number: Номер тендера

        Returns:
            True если успешно
        """
        async with DatabaseSession() as session:
            try:
                viewed = ViewedTenderModel(
                    user_id=user_id,
                    tender_number=tender_number
                )
                session.add(viewed)
                await session.commit()
                logger.debug(f"👁️ Tender marked as viewed: user={user_id}, tender={tender_number}")
                return True
            except IntegrityError:
                # Уже помечен как просмотренный
                await session.rollback()
                return True

    async def is_tender_viewed(self, user_id: int, tender_number: str) -> bool:
        """
        Проверить просмотрен ли тендер.

        Args:
            user_id: ID пользователя
            tender_number: Номер тендера

        Returns:
            True если просмотрен
        """
        async with DatabaseSession() as session:
            result = await session.execute(
                select(ViewedTenderModel).where(
                    and_(
                        ViewedTenderModel.user_id == user_id,
                        ViewedTenderModel.tender_number == tender_number
                    )
                )
            )
            return result.scalar_one_or_none() is not None

    async def get_viewed_tenders_count(self, user_id: int) -> int:
        """
        Получить количество просмотренных тендеров.

        Args:
            user_id: ID пользователя

        Returns:
            Количество просмотренных тендеров
        """
        async with DatabaseSession() as session:
            result = await session.execute(
                select(func.count())
                .select_from(ViewedTenderModel)
                .where(ViewedTenderModel.user_id == user_id)
            )
            return result.scalar() or 0

    # ============================================
    # 🧪 БЕТА: Satisfaction Surveys (Phase 2.1)
    # ============================================

    async def save_satisfaction_survey(
        self,
        user_id: int,
        rating: int,
        comment: Optional[str] = None,
        trigger: str = 'manual'
    ) -> int:
        """
        Сохранить опрос удовлетворённости.

        Args:
            user_id: ID пользователя
            rating: Оценка 1-5
            comment: Комментарий
            trigger: Триггер опроса (after_10_notifications, weekly, manual)

        Returns:
            ID записи
        """
        async with DatabaseSession() as session:
            survey = SatisfactionSurveyModel(
                user_id=user_id,
                rating=rating,
                comment=comment,
                trigger=trigger
            )
            session.add(survey)
            await session.flush()
            logger.info(f"⭐ Survey saved: user={user_id}, rating={rating}, trigger={trigger}")
            return survey.id

    async def get_average_rating(self) -> float:
        """
        Получить средний рейтинг удовлетворённости.

        Returns:
            Средний рейтинг
        """
        async with DatabaseSession() as session:
            result = await session.execute(
                select(func.avg(SatisfactionSurveyModel.rating))
            )
            avg = result.scalar()
            return round(avg, 2) if avg else 0.0


# Глобальный singleton
_sniper_db_instance = None


async def get_sniper_db() -> TenderSniperDB:
    """Получение singleton instance sniper database."""
    global _sniper_db_instance

    if _sniper_db_instance is None:
        _sniper_db_instance = TenderSniperDB()
        await _sniper_db_instance.init_db()

    return _sniper_db_instance


__all__ = ['TenderSniperDB', 'get_sniper_db', 'serialize_for_json']

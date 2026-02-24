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
    HiddenTender as HiddenTenderModel,  # Для feedback learning
    AIFeedback as AIFeedbackModel,  # AI семантика feedback
    # Phase 2.1 models
    SearchHistory as SearchHistoryModel,
    UserFeedback as UserFeedbackModel,
    Subscription as SubscriptionModel,
    SatisfactionSurvey as SatisfactionSurveyModel,
    ViewedTender as ViewedTenderModel,
    GoogleSheetsConfig as GoogleSheetsConfigModel,
    CacheEntry as CacheEntryModel,
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
                'created_at': user.created_at.isoformat() if user.created_at else None,
                'data': user.data if hasattr(user, 'data') and user.data else {},  # Данные настроек (quiet hours, etc.)
                'is_group': getattr(user, 'is_group', False),
                'group_admin_id': getattr(user, 'group_admin_id', None),
            }

    async def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Получение пользователя по первичному ключу (id)."""
        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperUserModel).where(SniperUserModel.id == user_id)
            )
            user = result.scalar_one_or_none()
            if not user:
                return None
            return {
                'id': user.id,
                'telegram_id': user.telegram_id,
                'subscription_tier': user.subscription_tier,
                'ai_analyses_used_month': user.ai_analyses_used_month,
                'ai_analyses_month_reset': user.ai_analyses_month_reset,
            }

    async def increment_ai_analyses_count(self, user_id: int) -> None:
        """Атомарно увеличивает счётчик AI-проверок (по первичному ключу)."""
        async with DatabaseSession() as session:
            await session.execute(
                update(SniperUserModel)
                .where(SniperUserModel.id == user_id)
                .values(ai_analyses_used_month=SniperUserModel.ai_analyses_used_month + 1)
            )

    async def mark_user_bot_blocked(self, telegram_id: int) -> bool:
        """Пометить пользователя как заблокировавшего бота + деактивировать его фильтры."""
        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperUserModel).where(SniperUserModel.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            if not user:
                return False

            # Сохраняем флаг в JSON data
            user_data = user.data if isinstance(user.data, dict) else {}
            user_data['bot_blocked'] = True
            user_data['bot_blocked_at'] = datetime.utcnow().isoformat()
            user.data = user_data

            # Деактивируем все фильтры пользователя (бессмысленно мониторить)
            await session.execute(
                update(SniperFilterModel).where(
                    and_(
                        SniperFilterModel.user_id == user.id,
                        SniperFilterModel.is_active == True,
                        SniperFilterModel.deleted_at.is_(None)
                    )
                ).values(is_active=False)
            )

            await session.commit()
            logger.info(f"⛔ Пользователь {telegram_id} помечен как заблокировавший бота, фильтры деактивированы")
            return True

    async def unmark_user_bot_blocked(self, telegram_id: int) -> bool:
        """Снять пометку блокировки бота (если пользователь вернулся)."""
        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperUserModel).where(SniperUserModel.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            if not user:
                return False

            user_data = user.data if isinstance(user.data, dict) else {}
            user_data.pop('bot_blocked', None)
            user_data.pop('bot_blocked_at', None)
            user.data = user_data
            await session.commit()
            return True

    async def get_user_subscription_info(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        """
        Получение информации о подписке пользователя из sniper_users.

        Args:
            telegram_id: Telegram ID пользователя

        Returns:
            Словарь с данными подписки или None
        """
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
                'subscription_tier': user.subscription_tier,
                'filters_limit': user.filters_limit,
                'notifications_limit': user.notifications_limit,
                'trial_started_at': user.trial_started_at,
                'trial_expires_at': user.trial_expires_at,
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

    async def get_user_groups(self, admin_telegram_id: int) -> List[Dict]:
        """Получение групп, где пользователь является админом."""
        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperUserModel).where(
                    and_(
                        SniperUserModel.is_group == True,
                        SniperUserModel.group_admin_id == admin_telegram_id,
                        SniperUserModel.status == 'active'
                    )
                )
            )
            groups = result.scalars().all()
            return [
                {
                    'id': g.id,
                    'telegram_id': g.telegram_id,
                    'name': g.first_name or f'Группа {g.telegram_id}'
                }
                for g in groups
            ]

    async def get_all_active_groups(self) -> List[Dict]:
        """Все активные группы где бот присутствует."""
        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperUserModel).where(
                    and_(
                        SniperUserModel.is_group == True,
                        SniperUserModel.status == 'active'
                    )
                )
            )
            groups = result.scalars().all()
            return [
                {
                    'id': g.id,
                    'telegram_id': g.telegram_id,
                    'name': g.first_name or f'Группа {g.telegram_id}',
                    'group_admin_id': g.group_admin_id
                }
                for g in groups
            ]

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
        """Получение фильтров пользователя (исключая удалённые)."""
        async with DatabaseSession() as session:
            query = select(SniperFilterModel).where(
                and_(
                    SniperFilterModel.user_id == user_id,
                    SniperFilterModel.deleted_at.is_(None)
                )
            )

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
        """Мягкое удаление фильтра (перемещение в корзину)."""
        async with DatabaseSession() as session:
            await session.execute(
                update(SniperFilterModel)
                .where(SniperFilterModel.id == filter_id)
                .values(deleted_at=datetime.utcnow(), is_active=False)
            )

    async def permanently_delete_filter(self, filter_id: int):
        """Безвозвратное удаление фильтра из БД."""
        async with DatabaseSession() as session:
            await session.execute(
                delete(SniperFilterModel).where(SniperFilterModel.id == filter_id)
            )

    async def restore_filter(self, filter_id: int):
        """Восстановление фильтра из корзины."""
        async with DatabaseSession() as session:
            await session.execute(
                update(SniperFilterModel)
                .where(SniperFilterModel.id == filter_id)
                .values(deleted_at=None, is_active=True)
            )

    async def get_deleted_filters(self, user_id: int) -> List[Dict[str, Any]]:
        """Получение удалённых фильтров пользователя (корзина)."""
        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperFilterModel)
                .where(
                    and_(
                        SniperFilterModel.user_id == user_id,
                        SniperFilterModel.deleted_at.isnot(None)
                    )
                )
                .order_by(SniperFilterModel.deleted_at.desc())
            )
            filters = result.scalars().all()
            return [self._filter_to_dict(f) for f in filters]

    async def permanently_delete_all_deleted_filters(self, user_id: int) -> int:
        """Безвозвратное удаление всех фильтров из корзины. Возвращает количество удалённых."""
        async with DatabaseSession() as session:
            result = await session.execute(
                delete(SniperFilterModel)
                .where(
                    and_(
                        SniperFilterModel.user_id == user_id,
                        SniperFilterModel.deleted_at.isnot(None)
                    )
                )
            )
            return result.rowcount

    async def duplicate_filter(self, filter_id: int, new_name: Optional[str] = None) -> Optional[int]:
        """
        Дублировать фильтр.

        Args:
            filter_id: ID фильтра для дублирования
            new_name: Новое имя для копии (если None, добавляется "(копия)")

        Returns:
            ID нового фильтра или None если исходный не найден
        """
        async with DatabaseSession() as session:
            # Получаем исходный фильтр
            result = await session.execute(
                select(SniperFilterModel).where(SniperFilterModel.id == filter_id)
            )
            original = result.scalar_one_or_none()

            if not original:
                return None

            # Создаём копию с новым именем
            copy_name = new_name or f"{original.name} (копия)"

            # Создаём новый фильтр с теми же параметрами
            new_filter = SniperFilterModel(
                user_id=original.user_id,
                name=copy_name,
                keywords=original.keywords,
                exclude_keywords=original.exclude_keywords,
                price_min=original.price_min,
                price_max=original.price_max,
                regions=original.regions,
                customer_types=original.customer_types,
                tender_types=original.tender_types,
                law_type=original.law_type,
                purchase_stage=original.purchase_stage,
                purchase_method=original.purchase_method,
                okpd2_codes=original.okpd2_codes,
                min_deadline_days=original.min_deadline_days,
                customer_keywords=original.customer_keywords,
                exact_match=getattr(original, 'exact_match', False),
                # Расширенные настройки
                purchase_number=None,  # Не копируем номер закупки
                customer_inn=getattr(original, 'customer_inn', []),
                excluded_customer_inns=getattr(original, 'excluded_customer_inns', []),
                excluded_customer_keywords=getattr(original, 'excluded_customer_keywords', []),
                execution_regions=getattr(original, 'execution_regions', []),
                publication_days=getattr(original, 'publication_days', None),
                primary_keywords=getattr(original, 'primary_keywords', []),
                secondary_keywords=getattr(original, 'secondary_keywords', []),
                search_in=getattr(original, 'search_in', []),
                is_active=True  # Новый фильтр активен по умолчанию
            )

            session.add(new_filter)
            await session.flush()
            new_id = new_filter.id
            await session.commit()

            logger.info(f"📋 Filter duplicated: {original.name} -> {copy_name} (id={new_id})")
            return new_id

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
                        SniperFilterModel.deleted_at.is_(None),
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
                filter_dict['notifications_limit'] = user_obj.notifications_limit
                filter_dict['trial_expires_at'] = user_obj.trial_expires_at
                # Добавляем user data для quiet hours/notification mode (избегаем N+1 запросов)
                filter_dict['user_data'] = {
                    'id': user_obj.id,
                    'telegram_id': user_obj.telegram_id,
                    'data': user_obj.data if hasattr(user_obj, 'data') and user_obj.data else {},
                }
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
                except (json.JSONDecodeError, ValueError, TypeError):
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
            # Per-filter notification targets
            'notify_chat_ids': getattr(filter_obj, 'notify_chat_ids', None),
            # AI семантика
            'ai_intent': getattr(filter_obj, 'ai_intent', None),
            'expanded_keywords': safe_list(getattr(filter_obj, 'expanded_keywords', [])),
            'is_active': filter_obj.is_active,
            'created_at': filter_obj.created_at.isoformat() if filter_obj.created_at else None,
            'updated_at': filter_obj.updated_at.isoformat() if filter_obj.updated_at else None,
            'deleted_at': filter_obj.deleted_at.isoformat() if getattr(filter_obj, 'deleted_at', None) else None
        }

    # ============================================
    # AI INTENT & FEEDBACK
    # ============================================

    async def get_filters_without_intent(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """
        Получает фильтры, у которых нет AI intent.

        Используется для background job генерации intent.
        """
        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperFilterModel)
                .where(
                    and_(
                        SniperFilterModel.is_active == True,
                        SniperFilterModel.deleted_at.is_(None),
                        or_(
                            SniperFilterModel.ai_intent == None,
                            SniperFilterModel.ai_intent == ''
                        )
                    )
                )
                .limit(limit)
            )
            filters = result.scalars().all()
            return [self._filter_to_dict(f) for f in filters]

    async def update_filter_intent(self, filter_id: int, intent: str) -> bool:
        """
        Обновляет AI intent для фильтра.

        Args:
            filter_id: ID фильтра
            intent: Сгенерированный AI intent

        Returns:
            True если успешно обновлено
        """
        async with DatabaseSession() as session:
            result = await session.execute(
                update(SniperFilterModel)
                .where(SniperFilterModel.id == filter_id)
                .values(ai_intent=intent, updated_at=datetime.utcnow())
            )
            await session.commit()
            return result.rowcount > 0

    async def update_filter_expanded_keywords(self, filter_id: int, expanded_keywords: list) -> bool:
        """Сохраняет AI-расширенные ключевые слова для фильтра."""
        async with DatabaseSession() as session:
            result = await session.execute(
                update(SniperFilterModel)
                .where(SniperFilterModel.id == filter_id)
                .values(expanded_keywords=expanded_keywords, updated_at=datetime.utcnow())
            )
            await session.commit()
            return result.rowcount > 0

    async def save_ai_feedback(
        self,
        user_id: int,
        tender_number: str,
        tender_name: str,
        feedback_type: str,
        filter_id: int = None,
        filter_keywords: List[str] = None,
        filter_intent: str = None,
        ai_decision: bool = None,
        ai_confidence: int = None,
        ai_reason: str = None,
        subscription_tier: str = None
    ) -> int:
        """
        Сохраняет feedback для обучения AI.

        Args:
            user_id: ID пользователя
            tender_number: Номер тендера
            tender_name: Название тендера
            feedback_type: 'hidden', 'favorited', 'clicked', 'applied'
            filter_id: ID фильтра (опционально)
            filter_keywords: Ключевые слова фильтра
            filter_intent: AI intent фильтра
            ai_decision: Решение AI (True/False)
            ai_confidence: Уверенность AI (0-100)
            ai_reason: Причина от AI
            subscription_tier: Тариф пользователя

        Returns:
            ID созданной записи
        """
        from database import AIFeedback as AIFeedbackModel

        async with DatabaseSession() as session:
            feedback = AIFeedbackModel(
                user_id=user_id,
                filter_id=filter_id,
                tender_number=tender_number,
                tender_name=tender_name,
                filter_keywords=filter_keywords,
                filter_intent=filter_intent,
                ai_decision=ai_decision,
                ai_confidence=ai_confidence,
                ai_reason=ai_reason,
                feedback_type=feedback_type,
                subscription_tier=subscription_tier
            )
            session.add(feedback)
            await session.commit()
            await session.refresh(feedback)
            logger.info(f"📝 AI Feedback saved: {feedback_type} for tender {tender_number}")
            return feedback.id

    async def get_recent_ai_mistakes(
        self,
        filter_keywords: List[str] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Получает недавние ошибки AI (тендеры, которые пользователи скрыли).

        Используется для динамического промпта с примерами.

        Args:
            filter_keywords: Ключевые слова для фильтрации (опционально)
            limit: Максимум записей

        Returns:
            Список ошибок с tender_name и причиной
        """
        from database import AIFeedback as AIFeedbackModel

        async with DatabaseSession() as session:
            query = select(AIFeedbackModel).where(
                and_(
                    AIFeedbackModel.feedback_type == 'hidden',
                    AIFeedbackModel.ai_decision == True  # AI сказал релевантен, но юзер скрыл
                )
            ).order_by(AIFeedbackModel.feedback_at.desc()).limit(limit)

            result = await session.execute(query)
            feedbacks = result.scalars().all()

            return [
                {
                    'tender_name': f.tender_name,
                    'filter_keywords': f.filter_keywords,
                    'ai_reason': f.ai_reason,
                    'feedback_at': f.feedback_at.isoformat() if f.feedback_at else None
                }
                for f in feedbacks
            ]

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
        # Проверка дубликата перед INSERT
        tender_number = tender_data.get('number', '')
        if tender_number and await self.is_tender_notified(tender_number, user_id):
            logger.info(f"Tender {tender_number} already notified for user {user_id}, skipping save")
            return None

        async with DatabaseSession() as session:
            # DEBUG: Логируем что именно сохраняем
            logger.debug(f"   💾 save_notification: number={tender_number}, "
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
                    except (ValueError, TypeError):
                        # Пробуем распространенные форматы даты
                        for fmt in ['%d.%m.%Y', '%Y-%m-%d', '%d.%m.%Y %H:%M', '%Y-%m-%d %H:%M']:
                            try:
                                submission_deadline = datetime.strptime(deadline_str, fmt)
                                break
                            except ValueError:
                                continue

                # Убираем timezone если есть
                if submission_deadline and submission_deadline.tzinfo is not None:
                    submission_deadline = submission_deadline.replace(tzinfo=None)

            try:
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

            except IntegrityError:
              await session.rollback()
              logger.warning(f"   ⚠️ Дубликат уведомления (IntegrityError): tender={tender_number}, user={user_id}")
              return None

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
            return result.first() is not None

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
            return result.first() is not None

    async def mark_tender_processed(self, tender_number: str, tender_hash: str):
        """Отметить тендер как обработанный."""
        async with DatabaseSession() as session:
            # Проверяем существование
            result = await session.execute(
                select(TenderCacheModel).where(TenderCacheModel.tender_number == tender_number)
            )
            existing = result.scalars().first()

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
    # ДИАГНОСТИКА ФИЛЬТРОВ
    # ============================================

    async def get_filter_diagnostics(self, user_id: int) -> List[Dict[str, Any]]:
        """
        Диагностика фильтров пользователя.
        Показывает статус, error_count, дату последнего уведомления, кол-во уведомлений.
        """
        async with DatabaseSession() as session:
            filters_result = await session.execute(
                select(SniperFilterModel).where(SniperFilterModel.user_id == user_id)
                .order_by(SniperFilterModel.created_at.desc())
            )
            filters = filters_result.scalars().all()

            diagnostics = []
            for f in filters:
                # Количество уведомлений по этому фильтру
                notif_count = await session.scalar(
                    select(func.count(SniperNotificationModel.id)).where(
                        SniperNotificationModel.filter_id == f.id
                    )
                ) or 0

                # Дата последнего уведомления
                last_notif = await session.scalar(
                    select(func.max(SniperNotificationModel.sent_at)).where(
                        SniperNotificationModel.filter_id == f.id
                    )
                )

                # Парсим keywords
                keywords = f.keywords
                if isinstance(keywords, str):
                    try:
                        keywords = json.loads(keywords)
                    except (json.JSONDecodeError, ValueError, TypeError):
                        keywords = []

                diagnostics.append({
                    'id': f.id,
                    'name': f.name,
                    'keywords': keywords[:5] if isinstance(keywords, list) else [],
                    'is_active': f.is_active,
                    'error_count': f.error_count,
                    'created_at': f.created_at,
                    'notification_count': notif_count,
                    'last_notification_at': last_notif,
                    'has_ai_intent': bool(f.ai_intent),
                })

            return diagnostics

    # ============================================
    # ОЧИСТКА ИСТОРИИ ТЕНДЕРОВ
    # ============================================

    async def cleanup_old_notifications(self, user_id: int, days: int) -> int:
        """
        Удалить уведомления старше указанного количества дней.
        Возвращает количество удалённых записей.
        """
        from datetime import timedelta
        cutoff_date = datetime.utcnow() - timedelta(days=days)

        async with DatabaseSession() as session:
            # Считаем перед удалением
            count = await session.scalar(
                select(func.count(SniperNotificationModel.id)).where(
                    and_(
                        SniperNotificationModel.user_id == user_id,
                        SniperNotificationModel.sent_at < cutoff_date
                    )
                )
            ) or 0

            if count > 0:
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
            existing = result.scalars().first()

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
            draft = result.scalars().first()

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
            existing = result.scalars().first()

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
            sub = result.scalars().first()

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
            return result.first() is not None

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

    # ============================================
    # PAYMENT METHODS
    # ============================================

    async def update_user_subscription(
        self,
        user_id: int,
        tier: str,
        filters_limit: int,
        notifications_limit: int,
        expires_at: datetime
    ) -> bool:
        """
        Обновить подписку пользователя после оплаты.

        Args:
            user_id: ID пользователя в БД
            tier: Тип подписки (basic, premium)
            filters_limit: Лимит фильтров
            notifications_limit: Лимит уведомлений в день
            expires_at: Дата окончания подписки

        Returns:
            True если успешно
        """
        async with DatabaseSession() as session:
            user = await session.get(SniperUserModel, user_id)
            if not user:
                logger.warning(f"User not found for subscription update: {user_id}")
                return False

            user.subscription_tier = tier
            user.filters_limit = filters_limit
            user.notifications_limit = notifications_limit
            user.trial_expires_at = expires_at  # Используем это поле для хранения даты окончания

            await session.commit()
            logger.info(f"✅ User subscription updated: id={user_id}, tier={tier}, expires={expires_at}")
            return True

    async def apply_promocode(self, user_id: int, code: str) -> Dict[str, Any]:
        """
        Применить промокод для пользователя.

        Args:
            user_id: ID пользователя в БД
            code: Промокод (uppercase)

        Returns:
            Dict с результатом:
            - success: True/False
            - error: код ошибки (если success=False)
            - tier: тариф промокода
            - days: количество дней
            - expires_at: новая дата окончания подписки
        """
        from database import Promocode as PromocodeModel

        async with DatabaseSession() as session:
            # Ищем промокод
            result = await session.execute(
                select(PromocodeModel).where(PromocodeModel.code == code.upper())
            )
            promocode = result.scalars().first()

            if not promocode:
                return {'success': False, 'error': 'not_found'}

            # Проверяем активность
            if not promocode.is_active:
                return {'success': False, 'error': 'inactive'}

            # Проверяем срок действия
            if promocode.expires_at and promocode.expires_at < datetime.utcnow():
                return {'success': False, 'error': 'expired'}

            # Проверяем лимит использований
            if promocode.max_uses and promocode.current_uses >= promocode.max_uses:
                return {'success': False, 'error': 'max_uses'}

            # Получаем пользователя
            user = await session.get(SniperUserModel, user_id)
            if not user:
                return {'success': False, 'error': 'user_not_found'}

            # Рассчитываем новую дату окончания
            now = datetime.utcnow()
            if user.trial_expires_at and user.trial_expires_at > now:
                # Добавляем к текущей подписке
                new_expires = user.trial_expires_at + timedelta(days=promocode.days)
            else:
                # Начинаем с сегодня
                new_expires = now + timedelta(days=promocode.days)

            # Определяем лимиты для тарифа
            limits_map = {
                'basic': {'filters': 5, 'notifications': 100},
                'premium': {'filters': 20, 'notifications': 9999}
            }
            limits = limits_map.get(promocode.tier, {'filters': 5, 'notifications': 100})

            # Обновляем пользователя
            user.subscription_tier = promocode.tier
            user.filters_limit = limits['filters']
            user.notifications_limit = limits['notifications']
            user.trial_expires_at = new_expires

            # Увеличиваем счётчик использований промокода
            promocode.current_uses += 1

            await session.commit()

            logger.info(f"🎟 Promocode {code} applied: user_id={user_id}, tier={promocode.tier}, days={promocode.days}")

            return {
                'success': True,
                'tier': promocode.tier,
                'days': promocode.days,
                'expires_at': new_expires
            }

    async def record_payment(
        self,
        user_id: int,
        payment_id: str,
        amount: float,
        tier: str,
        status: str = 'succeeded'
    ) -> int:
        """
        Записать платёж в БД.

        Args:
            user_id: ID пользователя
            payment_id: ID платежа в YooKassa
            amount: Сумма
            tier: Тариф
            status: Статус платежа

        Returns:
            ID записи платежа
        """
        from database import Payment

        async with DatabaseSession() as session:
            payment = Payment(
                user_id=user_id,
                yookassa_payment_id=payment_id,
                amount=amount,
                tier=tier,
                status=status,
                created_at=datetime.utcnow()
            )
            session.add(payment)
            await session.flush()
            payment_record_id = payment.id
            await session.commit()
            logger.info(f"💳 Payment recorded: id={payment_record_id}, user={user_id}, amount={amount}₽")
            return payment_record_id


    # ============================================
    # SUBSCRIPTION REMINDERS
    # ============================================

    async def get_expiring_subscriptions(self, days_before: int) -> List[Dict[str, Any]]:
        """
        Получить пользователей с истекающими подписками.

        Args:
            days_before: За сколько дней до истечения (например, 3 или 1)

        Returns:
            Список пользователей с telegram_id и датой истечения
        """
        async with DatabaseSession() as session:
            now = datetime.utcnow()
            target_date = now + timedelta(days=days_before)

            # Находим пользователей, у которых подписка истекает через days_before дней
            # (в диапазоне от days_before до days_before + 1)
            start_of_day = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = target_date.replace(hour=23, minute=59, second=59, microsecond=999999)

            result = await session.execute(
                select(SniperUserModel)
                .where(
                    and_(
                        SniperUserModel.trial_expires_at >= start_of_day,
                        SniperUserModel.trial_expires_at <= end_of_day,
                        SniperUserModel.subscription_tier.in_(['trial', 'basic', 'premium']),
                        SniperUserModel.status == 'active'
                    )
                )
            )
            users = result.scalars().all()

            return [{
                'id': u.id,
                'telegram_id': u.telegram_id,
                'username': u.username,
                'subscription_tier': u.subscription_tier,
                'trial_expires_at': u.trial_expires_at,
                'days_remaining': days_before
            } for u in users]

    async def get_all_active_users(self) -> List[Dict[str, Any]]:
        """
        Получить всех активных пользователей для рассылки.

        Returns:
            Список пользователей с telegram_id
        """
        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperUserModel)
                .where(SniperUserModel.status == 'active')
            )
            users = result.scalars().all()

            return [{
                'id': u.id,
                'telegram_id': u.telegram_id,
                'username': u.username,
                'subscription_tier': u.subscription_tier,
            } for u in users]

    # ============================================
    # FEEDBACK LEARNING (Premium AI функция)
    # ============================================

    async def save_hidden_tender(
        self,
        user_id: int,
        tender_number: str,
        tender_name: str = '',
        reason: str = 'skipped'
    ) -> bool:
        """
        Сохраняет скрытый тендер для обучения ML.

        Args:
            user_id: ID пользователя
            tender_number: Номер тендера
            tender_name: Название тендера (для анализа паттернов)
            reason: Причина скрытия

        Returns:
            True если успешно сохранено
        """
        try:
            async with DatabaseSession() as session:
                hidden = HiddenTenderModel(
                    user_id=user_id,
                    tender_number=tender_number,
                    reason=reason
                )
                session.add(hidden)
                await session.commit()
                logger.debug(f"Сохранен скрытый тендер: {tender_number} для user {user_id}")
                return True
        except IntegrityError:
            # Уже скрыт
            return True
        except Exception as e:
            logger.error(f"Ошибка сохранения скрытого тендера: {e}")
            return False

    async def get_hidden_tender_numbers(self, user_id: int, limit: int = 500) -> set:
        """Возвращает множество номеров тендеров, скрытых пользователем."""
        try:
            async with DatabaseSession() as session:
                result = await session.execute(
                    select(HiddenTenderModel.tender_number)
                    .where(HiddenTenderModel.user_id == user_id)
                    .order_by(HiddenTenderModel.hidden_at.desc())
                    .limit(limit)
                )
                return set(row[0] for row in result.all())
        except Exception as e:
            logger.error(f"Ошибка получения скрытых тендеров: {e}")
            return set()

    async def unhide_tender(self, user_id: int, tender_number: str) -> bool:
        """Убирает тендер из скрытых (undo skip)."""
        try:
            async with DatabaseSession() as session:
                await session.execute(
                    delete(HiddenTenderModel).where(
                        and_(
                            HiddenTenderModel.user_id == user_id,
                            HiddenTenderModel.tender_number == tender_number
                        )
                    )
                )
                await session.execute(
                    delete(UserFeedbackModel).where(
                        and_(
                            UserFeedbackModel.user_id == user_id,
                            UserFeedbackModel.tender_number == tender_number,
                            UserFeedbackModel.feedback_type == 'hidden'
                        )
                    )
                )
                return True
        except Exception as e:
            logger.error(f"Ошибка unhide тендера: {e}")
            return False

    async def get_user_hidden_patterns(self, user_id: int, min_occurrences: int = 2) -> Dict[str, Any]:
        """
        Анализирует паттерны скрытых тендеров пользователя через user_feedback.tender_name.
        Находит часто встречающиеся слова для снижения score похожих тендеров.
        """
        try:
            async with DatabaseSession() as session:
                # Берём названия из user_feedback (там хранится tender_name)
                result = await session.execute(
                    select(UserFeedbackModel.tender_name)
                    .where(
                        and_(
                            UserFeedbackModel.user_id == user_id,
                            UserFeedbackModel.feedback_type == 'hidden',
                            UserFeedbackModel.tender_name.isnot(None)
                        )
                    )
                    .order_by(UserFeedbackModel.created_at.desc())
                    .limit(100)
                )
                names = [row[0] for row in result.all() if row[0]]

                if len(names) < 5:
                    return {'negative_keywords': [], 'negative_customers': [], 'sample_size': len(names)}

                stop_words = {
                    'для', 'нужд', 'услуги', 'услуг', 'выполнение', 'работ',
                    'поставка', 'закупка', 'оказание', 'обеспечение', 'приобретение',
                    'товар', 'товаров', 'работы', 'услуга', 'на', 'в', 'по', 'и', 'с'
                }

                word_counts: Dict[str, int] = {}
                for name in names:
                    for word in name.lower().split():
                        clean = ''.join(c for c in word if c.isalnum())
                        if len(clean) >= 4 and clean not in stop_words:
                            word_counts[clean] = word_counts.get(clean, 0) + 1

                negative_keywords = sorted(
                    [w for w, cnt in word_counts.items() if cnt >= min_occurrences],
                    key=lambda w: word_counts[w],
                    reverse=True
                )

                return {
                    'negative_keywords': negative_keywords[:20],
                    'negative_customers': [],
                    'sample_size': len(names),
                    'total_unique_words': len(word_counts)
                }

        except Exception as e:
            logger.error(f"Ошибка анализа скрытых тендеров: {e}")
            return {'negative_keywords': [], 'negative_customers': [], 'error': str(e)}

    async def is_tender_hidden(self, user_id: int, tender_number: str) -> bool:
        """Проверяет, скрыт ли тендер пользователем."""
        try:
            async with DatabaseSession() as session:
                result = await session.execute(
                    select(HiddenTenderModel)
                    .where(
                        HiddenTenderModel.user_id == user_id,
                        HiddenTenderModel.tender_number == tender_number
                    )
                )
                return result.first() is not None
        except Exception as e:
            logger.error(f"Ошибка проверки скрытого тендера: {e}")
            return False


# Глобальный singleton
    # ============================================
    # GOOGLE SHEETS ИНТЕГРАЦИЯ
    # ============================================

    async def save_google_sheets_config(
        self,
        user_id: int,
        spreadsheet_id: str,
        columns: List[str],
        sheet_name: str = 'Тендеры',
        ai_enrichment: bool = False
    ) -> int:
        """
        Сохранить конфигурацию Google Sheets (upsert).

        Returns:
            ID конфига
        """
        try:
            async with DatabaseSession() as session:
                # Проверяем существующий конфиг
                existing = await session.scalar(
                    select(GoogleSheetsConfigModel).where(
                        GoogleSheetsConfigModel.user_id == user_id
                    )
                )

                if existing:
                    existing.spreadsheet_id = spreadsheet_id
                    existing.columns = columns
                    existing.sheet_name = sheet_name
                    existing.ai_enrichment = ai_enrichment
                    existing.enabled = True
                    existing.updated_at = datetime.utcnow()
                    await session.flush()
                    return existing.id
                else:
                    config = GoogleSheetsConfigModel(
                        user_id=user_id,
                        spreadsheet_id=spreadsheet_id,
                        columns=columns,
                        sheet_name=sheet_name,
                        ai_enrichment=ai_enrichment,
                        enabled=True
                    )
                    session.add(config)
                    await session.flush()
                    return config.id

        except Exception as e:
            logger.error(f"Ошибка сохранения Google Sheets config: {e}")
            return 0

    async def get_google_sheets_config(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Получить конфигурацию Google Sheets.

        Returns:
            Dict с конфигом или None
        """
        try:
            async with DatabaseSession() as session:
                config = await session.scalar(
                    select(GoogleSheetsConfigModel).where(
                        GoogleSheetsConfigModel.user_id == user_id
                    )
                )

                if not config:
                    return None

                return {
                    'id': config.id,
                    'user_id': config.user_id,
                    'spreadsheet_id': config.spreadsheet_id,
                    'sheet_name': config.sheet_name,
                    'columns': config.columns if isinstance(config.columns, list) else [],
                    'ai_enrichment': config.ai_enrichment,
                    'enabled': config.enabled,
                    'created_at': config.created_at,
                    'updated_at': config.updated_at,
                }

        except Exception as e:
            logger.error(f"Ошибка получения Google Sheets config: {e}")
            return None

    async def update_google_sheets_columns(self, user_id: int, columns: List[str]) -> bool:
        """Обновить список колонок."""
        try:
            async with DatabaseSession() as session:
                config = await session.scalar(
                    select(GoogleSheetsConfigModel).where(
                        GoogleSheetsConfigModel.user_id == user_id
                    )
                )
                if config:
                    config.columns = columns
                    config.updated_at = datetime.utcnow()
                    return True
                return False
        except Exception as e:
            logger.error(f"Ошибка обновления Google Sheets columns: {e}")
            return False

    async def toggle_google_sheets(self, user_id: int, enabled: bool) -> bool:
        """Включить/выключить Google Sheets интеграцию."""
        try:
            async with DatabaseSession() as session:
                config = await session.scalar(
                    select(GoogleSheetsConfigModel).where(
                        GoogleSheetsConfigModel.user_id == user_id
                    )
                )
                if config:
                    config.enabled = enabled
                    config.updated_at = datetime.utcnow()
                    return True
                return False
        except Exception as e:
            logger.error(f"Ошибка toggle Google Sheets: {e}")
            return False

    async def delete_google_sheets_config(self, user_id: int) -> bool:
        """Удалить конфигурацию Google Sheets."""
        try:
            async with DatabaseSession() as session:
                await session.execute(
                    delete(GoogleSheetsConfigModel).where(
                        GoogleSheetsConfigModel.user_id == user_id
                    )
                )
                return True
        except Exception as e:
            logger.error(f"Ошибка удаления Google Sheets config: {e}")
            return False


    # ============================================
    # GOOGLE SHEETS EXPORT HELPERS
    # ============================================

    async def get_notification_by_tender_number(self, user_id: int, tender_number: str) -> Optional[Dict[str, Any]]:
        """Получает уведомление по номеру тендера для пользователя."""
        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperNotificationModel).where(
                    and_(
                        SniperNotificationModel.user_id == user_id,
                        SniperNotificationModel.tender_number == tender_number
                    )
                ).order_by(SniperNotificationModel.sent_at.desc())
            )
            notif = result.scalars().first()
            if not notif:
                return None

            return {
                'id': notif.id,
                'filter_id': notif.filter_id,
                'tender_number': notif.tender_number,
                'tender_name': notif.tender_name,
                'tender_price': notif.tender_price,
                'tender_url': notif.tender_url,
                'tender_region': notif.tender_region,
                'tender_customer': notif.tender_customer,
                'filter_name': notif.filter_name,
                'score': notif.score,
                'matched_keywords': notif.matched_keywords or [],
                'published_date': notif.published_date.strftime('%d.%m.%Y') if notif.published_date else '',
                'submission_deadline': notif.submission_deadline.strftime('%d.%m.%Y') if notif.submission_deadline else '',
                'sheets_exported': notif.sheets_exported if hasattr(notif, 'sheets_exported') else False,
                'sheets_exported_by': getattr(notif, 'sheets_exported_by', None),
            }

    # Alias for convenience
    async def get_notification_by_tender(self, user_id: int, tender_number: str) -> Optional[Dict[str, Any]]:
        return await self.get_notification_by_tender_number(user_id, tender_number)

    async def mark_notification_exported(self, notification_id: int, exported_by: int = None) -> bool:
        """Помечает уведомление как экспортированное в Google Sheets."""
        try:
            values = {
                'sheets_exported': True,
                'sheets_exported_at': datetime.utcnow(),
            }
            if exported_by is not None:
                values['sheets_exported_by'] = exported_by

            async with DatabaseSession() as session:
                await session.execute(
                    update(SniperNotificationModel).where(
                        SniperNotificationModel.id == notification_id
                    ).values(**values)
                )
                return True
        except Exception as e:
            logger.error(f"Ошибка mark_notification_exported: {e}")
            return False

    async def get_unexported_notifications(self, user_id: int, days: int = 7) -> List[Dict[str, Any]]:
        """Получает неэкспортированные уведомления за указанный период."""
        async with DatabaseSession() as session:
            since = datetime.utcnow() - timedelta(days=days)
            result = await session.execute(
                select(SniperNotificationModel).where(
                    and_(
                        SniperNotificationModel.user_id == user_id,
                        SniperNotificationModel.sent_at >= since,
                        SniperNotificationModel.sheets_exported == False
                    )
                ).order_by(SniperNotificationModel.sent_at.desc())
            )
            notifications = result.scalars().all()

            return [{
                'id': n.id,
                'tender_number': n.tender_number,
                'tender_name': n.tender_name,
                'tender_price': n.tender_price,
                'tender_url': n.tender_url,
                'tender_region': n.tender_region,
                'tender_customer': n.tender_customer,
                'filter_name': n.filter_name,
                'score': n.score,
                'published_date': n.published_date.strftime('%d.%m.%Y') if n.published_date else '',
                'submission_deadline': n.submission_deadline.strftime('%d.%m.%Y') if n.submission_deadline else '',
            } for n in notifications]


    # ============================================
    # PERSISTENT CACHE
    # ============================================

    async def cache_get(self, cache_key: str, cache_type: str) -> Optional[Dict[str, Any]]:
        """Получить значение из персистентного кэша."""
        try:
            async with DatabaseSession() as session:
                result = await session.execute(
                    select(CacheEntryModel).where(
                        CacheEntryModel.cache_key == cache_key,
                        CacheEntryModel.cache_type == cache_type,
                        CacheEntryModel.expires_at > datetime.utcnow()
                    )
                )
                entry = result.scalar_one_or_none()
                if entry:
                    return entry.value
                return None
        except Exception as e:
            logger.debug(f"Cache get error: {e}")
            return None

    async def cache_set(self, cache_key: str, cache_type: str, value: Dict[str, Any], ttl_hours: int = 24):
        """Сохранить значение в персистентный кэш."""
        try:
            async with DatabaseSession() as session:
                # Upsert: удаляем старую запись и вставляем новую
                await session.execute(
                    delete(CacheEntryModel).where(
                        CacheEntryModel.cache_key == cache_key,
                        CacheEntryModel.cache_type == cache_type,
                    )
                )
                entry = CacheEntryModel(
                    cache_key=cache_key,
                    cache_type=cache_type,
                    value=value,
                    created_at=datetime.utcnow(),
                    expires_at=datetime.utcnow() + timedelta(hours=ttl_hours),
                )
                session.add(entry)
        except Exception as e:
            logger.debug(f"Cache set error: {e}")

    async def cache_cleanup(self, cache_type: Optional[str] = None):
        """Удалить просроченные записи из кэша."""
        try:
            async with DatabaseSession() as session:
                query = delete(CacheEntryModel).where(
                    CacheEntryModel.expires_at <= datetime.utcnow()
                )
                if cache_type:
                    query = query.where(CacheEntryModel.cache_type == cache_type)
                result = await session.execute(query)
                count = result.rowcount
                if count > 0:
                    logger.info(f"🗑️ Очищено {count} записей из кэша")
        except Exception as e:
            logger.debug(f"Cache cleanup error: {e}")


_sniper_db_instance = None


async def get_sniper_db() -> TenderSniperDB:
    """Получение singleton instance sniper database."""
    global _sniper_db_instance

    if _sniper_db_instance is None:
        _sniper_db_instance = TenderSniperDB()
        await _sniper_db_instance.init_db()

    return _sniper_db_instance


__all__ = ['TenderSniperDB', 'get_sniper_db', 'serialize_for_json']

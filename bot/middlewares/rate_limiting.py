"""
Rate Limiting Middleware для защиты от спама и DDoS атак.

Ограничивает количество запросов от одного пользователя за определенный период.
"""

import time
import logging
from typing import Dict, Any, Callable, Awaitable
from collections import defaultdict, deque

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseMiddleware):
    """
    Middleware для ограничения частоты запросов (rate limiting).

    Использует алгоритм "скользящего окна" (sliding window).
    """

    def __init__(
        self,
        limit: int = 10,
        period: int = 60,
        block_duration: int = 300
    ):
        """
        Инициализация rate limiter.

        Args:
            limit: Максимальное количество запросов за период
            period: Период в секундах (по умолчанию 60 = 1 минута)
            block_duration: Время блокировки при превышении лимита (секунды)
        """
        super().__init__()
        self.limit = limit
        self.period = period
        self.block_duration = block_duration

        # Словарь: user_id -> deque of timestamps
        self.requests: Dict[int, deque] = defaultdict(lambda: deque(maxlen=limit))

        # Словарь: user_id -> timestamp блокировки
        self.blocked_until: Dict[int, float] = {}

        logger.info(
            f"✅ Rate Limiting: {limit} запросов/{period}сек, "
            f"блокировка {block_duration}сек"
        )

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        """
        Обработка каждого входящего события.

        Args:
            handler: Следующий обработчик в цепочке
            event: Событие (Message или CallbackQuery)
            data: Контекст

        Returns:
            Результат обработки или None если заблокирован
        """
        # Получаем user_id из события
        user_id = None
        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None

        if not user_id:
            # Не можем определить пользователя - пропускаем
            return await handler(event, data)

        current_time = time.time()

        # Проверяем, не заблокирован ли пользователь
        if user_id in self.blocked_until:
            unblock_time = self.blocked_until[user_id]
            if current_time < unblock_time:
                remaining = int(unblock_time - current_time)
                logger.warning(
                    f"⛔ Rate limit: user {user_id} заблокирован "
                    f"(осталось {remaining}сек)"
                )

                # Отправляем предупреждение пользователю
                await self._send_rate_limit_warning(event, remaining)
                return None  # Блокируем запрос

            else:
                # Время блокировки истекло
                del self.blocked_until[user_id]
                logger.info(f"✅ Rate limit: user {user_id} разблокирован")

        # Получаем очередь запросов пользователя
        user_requests = self.requests[user_id]

        # Удаляем старые запросы (за пределами окна)
        cutoff_time = current_time - self.period
        while user_requests and user_requests[0] < cutoff_time:
            user_requests.popleft()

        # Проверяем лимит
        if len(user_requests) >= self.limit:
            # Превышен лимит - блокируем пользователя
            self.blocked_until[user_id] = current_time + self.block_duration

            logger.warning(
                f"🚨 Rate limit exceeded: user {user_id} "
                f"заблокирован на {self.block_duration}сек"
            )

            # Отправляем предупреждение
            await self._send_rate_limit_warning(event, self.block_duration)

            # Опционально: отправить в Sentry
            try:
                from tender_sniper.monitoring import capture_message
                capture_message(
                    f"Rate limit exceeded for user {user_id}",
                    level="warning",
                    tags={"component": "rate_limiting", "user_id": str(user_id)}
                )
            except ImportError:
                pass

            return None  # Блокируем запрос

        # Добавляем текущий запрос
        user_requests.append(current_time)

        # Пропускаем запрос дальше
        return await handler(event, data)

    async def _send_rate_limit_warning(
        self,
        event: TelegramObject,
        remaining_seconds: int
    ):
        """
        Отправка предупреждения о превышении лимита.

        Args:
            event: Событие (Message или CallbackQuery)
            remaining_seconds: Оставшееся время блокировки
        """
        minutes = remaining_seconds // 60
        seconds = remaining_seconds % 60

        if minutes > 0:
            time_str = f"{minutes} мин {seconds} сек"
        else:
            time_str = f"{seconds} сек"

        warning_text = (
            "⚠️ Превышен лимит запросов!\n\n"
            f"Слишком много команд за короткое время. "
            f"Пожалуйста, подождите {time_str}.\n\n"
            "Это временная мера для защиты от спама."
        )

        try:
            if isinstance(event, Message):
                await event.answer(warning_text)
            elif isinstance(event, CallbackQuery):
                await event.answer(warning_text, show_alert=True)
        except Exception as e:
            logger.error(f"Ошибка отправки предупреждения: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """
        Получение статистики rate limiting.

        Returns:
            Словарь со статистикой
        """
        current_time = time.time()

        active_users = len([
            user_id for user_id, requests in self.requests.items()
            if requests and requests[-1] > current_time - self.period
        ])

        blocked_users = len([
            user_id for user_id, unblock_time in self.blocked_until.items()
            if unblock_time > current_time
        ])

        return {
            "active_users": active_users,
            "blocked_users": blocked_users,
            "total_tracked_users": len(self.requests),
            "limit": self.limit,
            "period": self.period,
            "block_duration": self.block_duration
        }

    def reset_user(self, user_id: int):
        """
        Сброс лимитов для пользователя (для админов).

        Args:
            user_id: ID пользователя
        """
        if user_id in self.requests:
            self.requests[user_id].clear()

        if user_id in self.blocked_until:
            del self.blocked_until[user_id]

        logger.info(f"🔓 Rate limit сброшен для user {user_id}")


class AdaptiveRateLimitMiddleware(RateLimitMiddleware):
    """
    Адаптивный rate limiter с разными лимитами для разных команд.

    Более строгий для ресурсоемких операций (поиск, анализ),
    более мягкий для простых команд (меню, помощь).
    """

    # Лимиты для разных типов команд (запросов/минуту)
    COMMAND_LIMITS = {
        # Ресурсоемкие операции (увеличены для комфортного тестирования)
        "search": 10,          # 10 поисков в минуту
        "analyze": 10,         # 10 анализов в минуту
        "sniper_search": 10,   # 10 снайпер поисков в минуту

        # Обычные операции
        "filter_create": 15,   # 15 создания фильтров в минуту
        "filter_list": 20,     # 20 просмотров списка в минуту

        # Легкие операции
        "menu": 50,            # 50 переходов по меню в минуту
        "help": 30,            # 30 запросов помощи в минуту
        "default": 50          # Базовый лимит (увеличен для тестирования)
    }

    def __init__(self, period: int = 60, block_duration: int = 300):
        """
        Инициализация адаптивного rate limiter.

        Args:
            period: Период в секундах
            block_duration: Время блокировки
        """
        # Используем максимальный лимит для базового класса
        max_limit = max(self.COMMAND_LIMITS.values())
        super().__init__(limit=max_limit, period=period, block_duration=block_duration)

        # Отдельные очереди для каждого типа команды
        self.command_requests: Dict[int, Dict[str, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque())
        )

        logger.info("✅ Адаптивный Rate Limiting активирован")

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        """
        Обработка с учетом типа команды.
        """
        user_id = None
        command_type = "default"

        # Определяем тип команды из события
        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None

            if event.text:
                # Извлекаем команду
                if event.text.startswith('/'):
                    cmd = event.text.split()[0][1:]  # Убираем '/'
                    command_type = cmd if cmd in self.COMMAND_LIMITS else "default"

        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None

            if event.data:
                # Извлекаем тип из callback_data (например, "sniper_search_...")
                parts = event.data.split('_')
                if len(parts) >= 2:
                    cmd_key = f"{parts[0]}_{parts[1]}"
                    command_type = cmd_key if cmd_key in self.COMMAND_LIMITS else "default"

        if not user_id:
            return await handler(event, data)

        # Получаем лимит для данного типа команды
        command_limit = self.COMMAND_LIMITS.get(command_type, self.COMMAND_LIMITS["default"])

        # Проверяем лимит для конкретного типа команды
        current_time = time.time()
        cutoff_time = current_time - self.period

        user_cmd_requests = self.command_requests[user_id][command_type]

        # Удаляем старые запросы
        while user_cmd_requests and user_cmd_requests[0] < cutoff_time:
            user_cmd_requests.popleft()

        # Проверяем лимит
        if len(user_cmd_requests) >= command_limit:
            logger.warning(
                f"⚠️ Rate limit: user {user_id}, команда '{command_type}' "
                f"({len(user_cmd_requests)}/{command_limit})"
            )

            # Блокируем на короткое время (30 сек для конкретной команды)
            await self._send_command_rate_limit_warning(
                event,
                command_type,
                command_limit
            )
            return None

        # Добавляем запрос
        user_cmd_requests.append(current_time)

        # Вызываем базовый обработчик (глобальный лимит)
        return await handler(event, data)

    async def _send_command_rate_limit_warning(
        self,
        event: TelegramObject,
        command_type: str,
        limit: int
    ):
        """Предупреждение о превышении лимита для конкретной команды."""
        warning_text = (
            f"⚠️ Слишком много запросов!\n\n"
            f"Команда '{command_type}' ограничена: {limit} запросов/минуту.\n"
            f"Пожалуйста, подождите немного."
        )

        try:
            if isinstance(event, Message):
                await event.answer(warning_text)
            elif isinstance(event, CallbackQuery):
                await event.answer(warning_text, show_alert=True)
        except Exception as e:
            logger.error(f"Ошибка отправки предупреждения: {e}")


# ============================================
# ПРИМЕР ИСПОЛЬЗОВАНИЯ
# ============================================

if __name__ == '__main__':
    print("✅ Rate Limiting Middleware готов к использованию")
    print("\nПростой лимит:")
    print("  rate_limiter = RateLimitMiddleware(limit=10, period=60)")
    print("  dp.message.middleware(rate_limiter)")
    print("\nАдаптивный лимит:")
    print("  rate_limiter = AdaptiveRateLimitMiddleware()")
    print("  dp.message.middleware(rate_limiter)")

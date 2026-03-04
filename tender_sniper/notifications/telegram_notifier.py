"""
Telegram Notification Service для Tender Sniper.

Отправляет уведомления пользователям о новых подходящих тендерах.
"""

import asyncio
import sys
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.utils import safe_callback_data

# Импортируем форматтер карточки
try:
    from bot.formatters.tender_card import format_tender_card
    _USE_CARD_FORMATTER = True
except ImportError:
    _USE_CARD_FORMATTER = False

# Fallback: AI генератор названий (используется если форматтер недоступен)
try:
    from tender_sniper.ai_name_generator import generate_tender_name
except ImportError:
    def generate_tender_name(name, *args, **kwargs):
        return name[:80] + '...' if len(name) > 80 else name

logger = logging.getLogger(__name__)


class _TelegramRateLimiter:
    """
    Token-bucket rate limiter для Telegram Bot API.

    Telegram лимиты:
    - Глобальный: 30 сообщений/секунду
    - На чат:     1 сообщение/секунду (рекомендуется)

    Мы используем 25/с глобально и 1.1 с между сообщениями в один чат
    для надёжного запаса.
    """
    GLOBAL_RATE = 25        # токенов/сек (Telegram лимит 30, берём с запасом)
    PER_CHAT_INTERVAL = 1.1  # минимум секунд между сообщениями в один чат

    def __init__(self):
        self._tokens = float(self.GLOBAL_RATE)
        self._last_refill = time.monotonic()
        self._per_chat_last: Dict[int, float] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, chat_id: int):
        """Ожидает разрешения перед отправкой сообщения в chat_id."""
        async with self._lock:
            now = time.monotonic()

            # Пополняем глобальный bucket
            elapsed = now - self._last_refill
            self._tokens = min(float(self.GLOBAL_RATE), self._tokens + elapsed * self.GLOBAL_RATE)
            self._last_refill = now

            # Ждём глобальный токен
            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self.GLOBAL_RATE
                await asyncio.sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0

            # Ждём per-chat лимит
            last_send = self._per_chat_last.get(chat_id, 0.0)
            chat_wait = self.PER_CHAT_INTERVAL - (time.monotonic() - last_send)
            if chat_wait > 0:
                await asyncio.sleep(chat_wait)
            self._per_chat_last[chat_id] = time.monotonic()


# Singleton rate limiter — единый для всего процесса
_rate_limiter = _TelegramRateLimiter()


class TelegramNotifier:
    """
    Сервис уведомлений в Telegram для Tender Sniper.

    Особенности:
    - Красивое форматирование сообщений
    - Inline кнопки для действий
    - Обработка ошибок (бот заблокирован, чат не найден)
    - Квоты на уведомления
    - Приоритизация уведомлений
    """

    def __init__(self, bot_token: str):
        """
        Инициализация Telegram Notifier.

        Args:
            bot_token: Telegram Bot Token
        """
        self.bot = Bot(token=bot_token)

        self.stats = {
            'notifications_sent': 0,
            'notifications_failed': 0,
            'users_blocked_bot': 0,
            'quota_exceeded': 0
        }

        # Chat IDs, заблокировавшие бота (обнаруживается при TelegramForbiddenError)
        self.blocked_chat_ids: set = set()

    async def send_tender_notification(
        self,
        telegram_id: int,
        tender: Dict[str, Any],
        match_info: Dict[str, Any],
        filter_name: str,
        is_auto_notification: bool = False,
        subscription_tier: str = 'trial'
    ) -> bool:
        """
        Отправка уведомления о новом тендере.

        Args:
            telegram_id: Telegram ID пользователя
            tender: Данные тендера
            match_info: Информация о совпадении (score, matched_keywords)
            filter_name: Название фильтра
            is_auto_notification: True если уведомление из автомониторинга
            subscription_tier: Тариф пользователя (для AI функций)

        Returns:
            True если успешно отправлено, False иначе
        """
        try:
            # Форматируем сообщение и клавиатуру
            if _USE_CARD_FORMATTER:
                message, keyboard = format_tender_card(
                    tender=tender,
                    match_info=match_info,
                    filter_name=filter_name,
                    subscription_tier=subscription_tier,
                    is_auto_notification=is_auto_notification,
                )
            else:
                message = self._format_tender_message(tender, match_info, filter_name, subscription_tier)
                keyboard = self._create_tender_keyboard(tender, is_auto_notification, subscription_tier)

            # Rate limiting перед отправкой (25 msg/s глобально, 1 msg/s на чат)
            await _rate_limiter.acquire(telegram_id)
            await self.bot.send_message(
                chat_id=telegram_id,
                text=message,
                reply_markup=keyboard,
                parse_mode='HTML',
                disable_web_page_preview=True
            )

            self.stats['notifications_sent'] += 1
            logger.info(f"✅ Уведомление отправлено пользователю {telegram_id}")
            return True

        except TelegramForbiddenError:
            # Пользователь заблокировал бота
            self.stats['users_blocked_bot'] += 1
            self.blocked_chat_ids.add(telegram_id)
            logger.warning(f"⛔ Пользователь {telegram_id} заблокировал бота")
            return False

        except TelegramBadRequest as e:
            # Неверный chat_id или другая ошибка
            self.stats['notifications_failed'] += 1
            logger.error(f"❌ Ошибка отправки пользователю {telegram_id}: {e}")
            return False

        except Exception as e:
            self.stats['notifications_failed'] += 1
            logger.error(f"❌ Неожиданная ошибка при отправке уведомления: {e}", exc_info=True)
            return False

    def _format_tender_message(
        self,
        tender: Dict[str, Any],
        match_info: Dict[str, Any],
        filter_name: str,
        subscription_tier: str = 'trial'
    ) -> str:
        """
        Форматирование сообщения о тендере.

        Args:
            tender: Данные тендера
            match_info: Информация о совпадении
            filter_name: Название фильтра
            subscription_tier: Тариф пользователя (для AI функций)

        Returns:
            Отформатированное сообщение
        """
        score = match_info.get('score', 0)
        matched_keywords = match_info.get('matched_keywords', [])

        # Определяем эмодзи по score
        if score >= 80:
            score_emoji = "🔥"
        elif score >= 60:
            score_emoji = "✨"
        else:
            score_emoji = "📌"

        # Форматируем цену
        price = tender.get('price')
        if price:
            try:
                price_str = f"{float(price):,.0f} ₽".replace(',', ' ')
            except (ValueError, TypeError):
                price_str = str(price)
        else:
            price_str = "Не указана"

        # Генерируем короткое AI-название (или используем оригинальное)
        original_name = tender.get('name', 'Без названия')
        name = generate_tender_name(
            original_name,
            tender_data=tender,
            max_length=90
        )

        # Форматируем deadline + дни до дедлайна
        deadline = tender.get('submission_deadline')
        deadline_str = None
        days_left = None
        if deadline:
            try:
                deadline_dt = None
                if isinstance(deadline, str):
                    for fmt in ['%d.%m.%Y %H:%M', '%d.%m.%Y', '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S']:
                        try:
                            deadline_dt = datetime.strptime(deadline.split('+')[0].split('Z')[0], fmt)
                            break
                        except ValueError:
                            continue
                elif isinstance(deadline, datetime):
                    deadline_dt = deadline

                if deadline_dt:
                    deadline_str = deadline_dt.strftime('%d.%m.%Y')
                    days_left = (deadline_dt - datetime.now()).days
                else:
                    deadline_str = str(deadline)[:10]
            except Exception:
                pass

        # Регион и заказчик
        region = tender.get('customer_region', tender.get('region', ''))
        customer = tender.get('customer', tender.get('customer_name', ''))
        if len(customer) > 45:
            customer = customer[:42] + '...'

        # Ключевые слова (только реально совпавшие)
        kw_list = [kw for kw in matched_keywords if isinstance(kw, str) and len(kw) > 1][:5]

        # ─── Строим сообщение ───

        # Заголовок: эмодзи + название
        message = f"{score_emoji} <b>{name}</b>\n"

        # Вторая строка: цена + дедлайн в одну строку
        line2_parts = [f"💰 {price_str}"]
        if deadline_str:
            if days_left is not None and days_left >= 0:
                urgency = "‼️" if days_left <= 3 else ("⚡" if days_left <= 7 else "⏰")
                line2_parts.append(f"{urgency} до {deadline_str} ({days_left} дн.)")
            else:
                line2_parts.append(f"⏰ до {deadline_str}")
        message += "  ·  ".join(line2_parts) + "\n"

        # Место и заказчик
        if region and customer:
            message += f"📍 {region}  ·  🏢 {customer}\n"
        elif region:
            message += f"📍 {region}\n"
        elif customer:
            message += f"🏢 {customer}\n"

        # Фильтр и ключевые слова
        message += f"🎯 {filter_name}"
        if kw_list:
            message += f"  ·  <i>{', '.join(kw_list)}</i>"
        message += "\n"

        # AI строка
        ai_confidence = match_info.get('ai_confidence')
        ai_reason = match_info.get('ai_reason', '')
        if ai_confidence is not None and ai_confidence >= 40:
            if ai_reason and len(ai_reason) > 55:
                ai_reason = ai_reason[:52] + '...'
            message += f"🤖 {ai_confidence}%"
            if ai_reason:
                message += f" — {ai_reason}"
            message += "\n"

        # Красные флаги
        red_flags = match_info.get('red_flags', [])
        if red_flags:
            message += "🚩 " + " · ".join(red_flags[:2]) + "\n"

        # Номер тендера
        tender_number = tender.get('number')
        if tender_number:
            message += f"\n<code>№ {tender_number}</code>"

        return message.strip()

    def _create_tender_keyboard(
        self,
        tender: Dict[str, Any],
        is_auto_notification: bool = False,
        subscription_tier: str = 'trial'
    ) -> InlineKeyboardMarkup:
        """
        Создание inline клавиатуры для тендера.

        Args:
            tender: Данные тендера
            is_auto_notification: True если уведомление из автомониторинга
            subscription_tier: Тариф пользователя (для AI функций)

        Returns:
            Inline клавиатура
        """
        buttons = []
        tender_number = tender.get('number')

        # Кнопка просмотра на zakupki.gov.ru
        tender_url = tender.get('url', '')
        if tender_url:
            if not tender_url.startswith('http'):
                tender_url = f"https://zakupki.gov.ru{tender_url}"

            buttons.append([
                InlineKeyboardButton(
                    text="📄 Открыть на zakupki.gov.ru",
                    url=tender_url
                )
            ])

        # Кнопки действий
        if tender_number:
            buttons.append([
                InlineKeyboardButton(
                    text="✅ Интересно",
                    callback_data=safe_callback_data("interested", tender_number)
                ),
                InlineKeyboardButton(
                    text="📊 В таблицу",
                    callback_data=safe_callback_data("sheets", tender_number)
                ),
                InlineKeyboardButton(
                    text="❌ Пропустить",
                    callback_data=safe_callback_data("skip", tender_number)
                )
            ])

            # AI кнопки для Basic и Premium
            if subscription_tier in ('basic', 'premium'):
                buttons.append([
                    InlineKeyboardButton(
                        text="📝 AI-резюме",
                        callback_data=safe_callback_data("ai_summary", tender_number)
                    ),
                    InlineKeyboardButton(
                        text="📄 Анализ докум.",
                        callback_data=safe_callback_data("analyze_docs", tender_number)
                    )
                ])
            else:
                buttons.append([
                    InlineKeyboardButton(
                        text="⭐ AI-функции (Basic+)",
                        callback_data="show_premium_ai"
                    )
                ])

        return InlineKeyboardMarkup(inline_keyboard=buttons)

    async def send_batch_notifications(
        self,
        notifications: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """
        Пакетная отправка уведомлений.

        Args:
            notifications: Список уведомлений
                [{'telegram_id': int, 'tender': dict, 'match_info': dict, 'filter_name': str}, ...]

        Returns:
            Статистика отправки {'sent': int, 'failed': int}
        """
        logger.info(f"\n📤 Отправка {len(notifications)} уведомлений...")

        sent = 0
        failed = 0

        for notif in notifications:
            success = await self.send_tender_notification(
                telegram_id=notif['telegram_id'],
                tender=notif['tender'],
                match_info=notif['match_info'],
                filter_name=notif['filter_name']
            )

            if success:
                sent += 1
            else:
                failed += 1

            # Небольшая задержка между сообщениями (антиспам)
            await asyncio.sleep(0.05)

        logger.info(f"✅ Отправлено: {sent}, ❌ Ошибок: {failed}")

        return {'sent': sent, 'failed': failed}

    async def send_quota_exceeded_notification(
        self,
        telegram_id: int,
        current_limit: int,
        upgrade_plan: str = 'basic'
    ):
        """
        Уведомление о превышении квоты.

        Args:
            telegram_id: Telegram ID пользователя
            current_limit: Текущий лимит
            upgrade_plan: Рекомендуемый план для upgrade
        """
        try:
            message = f"""
⚠️ <b>Достигнут лимит уведомлений</b>

Вы получили максимальное количество уведомлений сегодня: <b>{current_limit}</b>

Для получения большего количества уведомлений рассмотрите возможность upgrade тарифа:

• <b>Базовый</b> - 50 уведомлений/день
• <b>Премиум</b> - безлимитные уведомления

Мониторинг продолжится завтра автоматически.
"""

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬆️ Улучшить тариф", callback_data="upgrade_plan")],
                [InlineKeyboardButton(text="📊 Моя статистика", callback_data="my_stats")]
            ])

            await _rate_limiter.acquire(telegram_id)
            await self.bot.send_message(
                chat_id=telegram_id,
                text=message,
                reply_markup=keyboard,
                parse_mode='HTML'
            )

            self.stats['quota_exceeded'] += 1

        except Exception as e:
            logger.error(f"❌ Ошибка отправки уведомления о квоте: {e}")

    async def send_monitoring_error_notification(
        self,
        telegram_id: int,
        filter_name: str,
        error_type: str,
        error_count: int
    ):
        """
        Отправка уведомления об ошибках автомониторинга.

        Args:
            telegram_id: Telegram ID пользователя
            filter_name: Название фильтра
            error_type: Тип ошибки (RSS, Прокси)
            error_count: Количество последовательных ошибок
        """
        try:
            message = f"""
⚠️ <b>Проблема с автомониторингом</b>

<b>Фильтр:</b> {filter_name}
<b>Проблема:</b> {error_type}
<b>Попыток подряд:</b> {error_count}

Не удается получить новые тендеры для этого фильтра.

<b>Возможные причины:</b>
{"• Проблемы с прокси-сервером" if error_type == "Прокси" else "• zakupki.gov.ru временно недоступен"}
• Временные технические проблемы

<b>Что делать:</b>
Мы продолжим попытки автоматически. Если проблема сохраняется более 24 часов, свяжитесь с поддержкой.
            """.strip()

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📊 Мои фильтры", callback_data="my_filters")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ])

            await _rate_limiter.acquire(telegram_id)
            await self.bot.send_message(
                chat_id=telegram_id,
                text=message,
                reply_markup=keyboard,
                parse_mode='HTML'
            )

            self.stats['monitoring_errors'] = self.stats.get('monitoring_errors', 0) + 1

        except Exception as e:
            logger.error(f"❌ Ошибка отправки уведомления об ошибке мониторинга: {e}")

    async def send_system_notification(
        self,
        telegram_id: int,
        message: str,
        keyboard: Optional[InlineKeyboardMarkup] = None
    ):
        """
        Отправка системного уведомления.

        Args:
            telegram_id: Telegram ID
            message: Текст сообщения
            keyboard: Опциональная клавиатура
        """
        try:
            await _rate_limiter.acquire(telegram_id)
            await self.bot.send_message(
                chat_id=telegram_id,
                text=message,
                reply_markup=keyboard,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"❌ Ошибка отправки системного уведомления: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Получение статистики уведомлений."""
        return self.stats.copy()

    async def close(self):
        """Закрытие сессии бота."""
        await self.bot.session.close()


# ============================================
# ПРИМЕР ИСПОЛЬЗОВАНИЯ
# ============================================

async def example_usage():
    """Пример использования TelegramNotifier."""
    import os
    from dotenv import load_dotenv

    load_dotenv()

    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        print("❌ TELEGRAM_BOT_TOKEN не найден в .env")
        return

    # Создаем notifier
    notifier = TelegramNotifier(bot_token)

    # Пример тендера
    tender = {
        'number': '0123456789',
        'name': 'Поставка компьютерного оборудования для нужд учреждения',
        'price': 2500000,
        'region': 'Москва',
        'customer_name': 'ООО "Тестовая компания"',
        'published_datetime': datetime.now().isoformat(),
        'url': '/epz/order/notice/ea44/view/common-info.html?regNumber=0123456789'
    }

    # Информация о совпадении
    match_info = {
        'score': 85,
        'matched_keywords': ['компьютер', 'оборудование']
    }

    # Отправляем уведомление (замените на реальный telegram_id)
    # success = await notifier.send_tender_notification(
    #     telegram_id=123456789,
    #     tender=tender,
    #     match_info=match_info,
    #     filter_name='IT оборудование'
    # )

    # print(f"Уведомление отправлено: {success}")
    # print(f"Статистика: {notifier.get_stats()}")

    await notifier.close()


if __name__ == '__main__':
    asyncio.run(example_usage())

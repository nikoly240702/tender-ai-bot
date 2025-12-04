"""
Telegram Notification Service для Tender Sniper.

Отправляет уведомления пользователям о новых подходящих тендерах.
"""

import asyncio
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

logger = logging.getLogger(__name__)


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

    async def send_tender_notification(
        self,
        telegram_id: int,
        tender: Dict[str, Any],
        match_info: Dict[str, Any],
        filter_name: str,
        is_auto_notification: bool = False
    ) -> bool:
        """
        Отправка уведомления о новом тендере.

        Args:
            telegram_id: Telegram ID пользователя
            tender: Данные тендера
            match_info: Информация о совпадении (score, matched_keywords)
            filter_name: Название фильтра
            is_auto_notification: True если уведомление из автомониторинга

        Returns:
            True если успешно отправлено, False иначе
        """
        try:
            # Форматируем сообщение
            message = self._format_tender_message(tender, match_info, filter_name)

            # Создаем кнопки
            keyboard = self._create_tender_keyboard(tender, is_auto_notification)

            # Отправляем уведомление
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
        filter_name: str
    ) -> str:
        """
        Форматирование сообщения о тендере.

        Args:
            tender: Данные тендера
            match_info: Информация о совпадении
            filter_name: Название фильтра

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
            price_str = f"{price:,.0f} ₽".replace(',', ' ')
        else:
            price_str = "Не указана"

        # Форматируем дату публикации
        published = tender.get('published_datetime')
        if published:
            try:
                if isinstance(published, str):
                    pub_dt = datetime.fromisoformat(published.replace('Z', '+00:00'))
                else:
                    pub_dt = published
                pub_str = pub_dt.strftime('%d.%m.%Y %H:%M')
            except:
                pub_str = str(published)[:16]
        else:
            pub_str = "Неизвестна"

        # Форматируем название (обрезаем если слишком длинное)
        name = tender.get('name', 'Без названия')
        if len(name) > 200:
            name = name[:197] + '...'

        # Формируем сообщение
        message = f"""
{score_emoji} <b>Новый тендер!</b>

<b>Название:</b> {name}

<b>📊 Релевантность:</b> {score}/100
<b>🎯 Фильтр:</b> {filter_name}

<b>💰 Цена:</b> {price_str}
<b>📅 Опубликован:</b> {pub_str}
<b>📍 Регион:</b> {tender.get('region', 'Не указан')}
<b>🏢 Заказчик:</b> {tender.get('customer_name', 'Не указан')[:100]}

<b>🔑 Совпадения:</b> {', '.join(matched_keywords[:5]) if matched_keywords else 'Базовый фильтр'}
"""

        # Добавляем номер тендера
        tender_number = tender.get('number')
        if tender_number:
            message += f"\n<b>№</b> {tender_number}"

        return message.strip()

    def _create_tender_keyboard(self, tender: Dict[str, Any], is_auto_notification: bool = False) -> InlineKeyboardMarkup:
        """
        Создание inline клавиатуры для тендера.

        Args:
            tender: Данные тендера
            is_auto_notification: True если уведомление из автомониторинга

        Returns:
            Inline клавиатура
        """
        buttons = []

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

        # Кнопка анализа (ТОЛЬКО для ручного поиска, не для автомониторинга)
        tender_number = tender.get('number')
        if tender_number and not is_auto_notification:
            buttons.append([
                InlineKeyboardButton(
                    text="🤖 Анализировать с AI",
                    callback_data=f"analyze_{tender_number}"
                )
            ])

        # Кнопки действий
        buttons.append([
            InlineKeyboardButton(
                text="✅ Интересно",
                callback_data=f"interested_{tender_number}"
            ),
            InlineKeyboardButton(
                text="❌ Пропустить",
                callback_data=f"skip_{tender_number}"
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

            await self.bot.send_message(
                chat_id=telegram_id,
                text=message,
                reply_markup=keyboard,
                parse_mode='HTML'
            )

            self.stats['quota_exceeded'] += 1

        except Exception as e:
            logger.error(f"❌ Ошибка отправки уведомления о квоте: {e}")

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

"""
Модуль для форматирования Telegram уведомлений о тендерах.

Создает красивые inline-уведомления с кнопками действий.
"""

from datetime import datetime
from typing import Dict, Any, Tuple, Optional
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import logging

# Импортируем AI генератор названий
try:
    from tender_sniper.ai_name_generator import generate_tender_name
except ImportError:
    # Fallback если модуль недоступен
    def generate_tender_name(name, *args, **kwargs):
        return name[:80] + '...' if len(name) > 80 else name

logger = logging.getLogger(__name__)


def format_tender_notification(tender_data: Dict[str, Any]) -> Tuple[str, InlineKeyboardMarkup]:
    """
    Форматирует краткое уведомление о тендере.

    Args:
        tender_data: dict с данными тендера

    Returns:
        tuple: (message_text, inline_keyboard)
    """

    # Определяем эмодзи для score
    score = tender_data.get('score', 0)
    if score >= 70:
        score_emoji = "🟢"
        score_text = "ВЫСОКИЙ"
    elif score >= 40:
        score_emoji = "🟡"
        score_text = "СРЕДНИЙ"
    else:
        score_emoji = "🔴"
        score_text = "НИЗКИЙ"

    # Форматируем сумму
    amount = tender_data.get('tender_price') or tender_data.get('price', 0)
    if amount:
        amount_str = f"{amount:,.0f}".replace(',', ' ')
    else:
        amount_str = "Не указана"

    # Рассчитываем оставшееся время (если есть данные)
    deadline_str = tender_data.get('deadline') or "Не указан"
    time_left_str = ""

    # Если есть published_date, можем рассчитать время
    published_date = tender_data.get('published_date')
    if published_date and isinstance(published_date, datetime):
        # Предполагаем, что у тендера есть дедлайн через ~14 дней (стандартный срок)
        # TODO: В будущем добавить реальный deadline_date в БД
        days_ago = (datetime.now() - published_date).days
        estimated_days_left = max(14 - days_ago, 0)

        if estimated_days_left < 1:
            time_left_str = f"⚠️ Менее суток"
        elif estimated_days_left == 1:
            time_left_str = f"⚠️ 1 день"
        else:
            time_left_str = f"{estimated_days_left} дней"

    # Получаем и генерируем короткое AI-название
    original_name = tender_data.get('tender_name') or tender_data.get('name', 'Без названия')
    tender_name = generate_tender_name(original_name, tender_data=tender_data, max_length=80)
    tender_number = tender_data.get('tender_number') or tender_data.get('number', 'N/A')

    # Формируем текст сообщения
    message = f"""🎯 <b>НОВЫЙ ТЕНДЕР [Score: {score}/100]</b> {score_emoji}

📋 <b>{tender_name}</b>
№{tender_number}

💰 НМЦК: {amount_str} ₽"""

    # Добавляем регион и время
    region = tender_data.get('tender_region') or tender_data.get('region')
    if region:
        message += f"\n📍 {region}"

    if time_left_str:
        message += f" | ⏰ Осталось: {time_left_str}"
    elif deadline_str != "Не указан":
        message += f"\n⏱ Подача до: {deadline_str}"

    # Добавляем matched keywords если есть
    matched_keywords = tender_data.get('matched_keywords', [])
    if matched_keywords and len(matched_keywords) > 0:
        # Показываем первые 3 ключевых слова
        keywords_str = ", ".join(matched_keywords[:3])
        if len(matched_keywords) > 3:
            keywords_str += f" +{len(matched_keywords) - 3}"
        message += f"\n\n🔑 <b>Ключевые слова:</b> {keywords_str}"

    # Добавляем фильтр, по которому найден тендер
    filter_name = tender_data.get('filter_name')
    if filter_name:
        message += f"\n📂 <b>Фильтр:</b> {filter_name}"

    # Создаем inline клавиатуру
    # Используем tender_number как ID
    tender_id = tender_number

    keyboard = [
        [
            InlineKeyboardButton(text="📊 Детали", callback_data=f"tender_details_{tender_id}"),
            InlineKeyboardButton(text="⭐ В избранное", callback_data=f"tender_favorite_{tender_id}")
        ],
        [
            InlineKeyboardButton(text="🔔 Напомнить", callback_data=f"tender_remind_{tender_id}"),
            InlineKeyboardButton(text="👎 Скрыть", callback_data=f"tender_hide_{tender_id}")
        ]
    ]

    # Добавляем ссылку на zakupki.gov.ru если есть
    tender_url = tender_data.get('tender_url') or tender_data.get('url')
    if tender_url:
        if not tender_url.startswith('http'):
            tender_url = f"https://zakupki.gov.ru{tender_url}"
        keyboard.append([
            InlineKeyboardButton(text="🔗 Открыть на zakupki.gov.ru", url=tender_url)
        ])

    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    return message, reply_markup


def format_detailed_tender_info(tender_data: Dict[str, Any]) -> str:
    """
    Форматирует детальную информацию о тендере.

    Args:
        tender_data: dict с данными тендера

    Returns:
        str: Детальное сообщение
    """
    original_name = tender_data.get('tender_name') or tender_data.get('name', 'Без названия')
    tender_name = generate_tender_name(original_name, tender_data=tender_data, max_length=100)
    tender_number = tender_data.get('tender_number') or tender_data.get('number', 'N/A')
    amount = tender_data.get('tender_price') or tender_data.get('price', 0)

    if amount:
        amount_str = f"{amount:,.0f}".replace(',', ' ')
    else:
        amount_str = "Не указана"

    message = f"""📊 <b>ДЕТАЛЬНАЯ ИНФОРМАЦИЯ</b>

<b>{tender_name}</b>
№{tender_number}

═══════════════════════
💰 <b>ФИНАНСЫ</b>
═══════════════════════
• НМЦК: {amount_str} ₽"""

    # Добавляем регион
    region = tender_data.get('tender_region') or tender_data.get('region')
    if region:
        message += f"\n\n═══════════════════════\n📍 <b>РЕГИОН</b>\n═══════════════════════\n• {region}"

    # Добавляем заказчика
    customer = tender_data.get('tender_customer') or tender_data.get('customer_name')
    if customer:
        message += f"\n\n═══════════════════════\n🏢 <b>ЗАКАЗЧИК</b>\n═══════════════════════\n• {customer}"

    # Добавляем дату публикации
    published_date = tender_data.get('published_date')
    if published_date:
        if isinstance(published_date, datetime):
            published_str = published_date.strftime('%d.%m.%Y %H:%M')
        else:
            published_str = str(published_date)[:16]
        message += f"\n\n═══════════════════════\n📅 <b>ДАТЫ</b>\n═══════════════════════\n• Публикация: {published_str}"

    # Добавляем matched keywords
    matched_keywords = tender_data.get('matched_keywords', [])
    if matched_keywords:
        keywords_str = "\n".join([f"  • {kw}" for kw in matched_keywords])
        message += f"\n\n═══════════════════════\n🔑 <b>СОВПАДЕНИЯ</b>\n═══════════════════════\n{keywords_str}"

    # Добавляем score
    score = tender_data.get('score', 0)
    message += f"\n\n═══════════════════════\n🎯 <b>ОЦЕНКА</b>\n═══════════════════════\n• Score: {score}/100"

    if score >= 70:
        recommendation = "✅ Рекомендуется участвовать"
    elif score >= 40:
        recommendation = "⚠️ Рассмотреть возможность участия"
    else:
        recommendation = "❌ Низкая релевантность"

    message += f"\n• {recommendation}"

    return message


def format_reminder_options() -> InlineKeyboardMarkup:
    """
    Создает клавиатуру с опциями напоминаний.

    Returns:
        InlineKeyboardMarkup: Клавиатура с кнопками
    """
    keyboard = [
        [
            InlineKeyboardButton(text="За 1 день", callback_data="reminder_1d"),
            InlineKeyboardButton(text="За 3 дня", callback_data="reminder_3d")
        ],
        [
            InlineKeyboardButton(text="За 7 дней", callback_data="reminder_7d"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="reminder_cancel")
        ]
    ]

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def format_favorites_list(favorites: list, username: str = "Пользователь") -> str:
    """
    Форматирует список избранных тендеров.

    Args:
        favorites: Список избранных тендеров
        username: Имя пользователя

    Returns:
        str: Сообщение со списком
    """
    if not favorites:
        return "⭐ У вас пока нет избранных тендеров"

    message = f"⭐ <b>ИЗБРАННЫЕ ТЕНДЕРЫ</b>\n\n"

    for i, tender in enumerate(favorites[:10], 1):
        tender_name = tender.get('tender_name', 'Без названия')[:50]
        tender_number = tender.get('tender_number', 'N/A')
        price = tender.get('tender_price', 0)

        if price:
            price_str = f"{price:,.0f}".replace(',', ' ')
        else:
            price_str = "Не указана"

        message += f"{i}. <b>{tender_name}...</b>\n"
        message += f"   №{tender_number} | 💰 {price_str} ₽\n\n"

    if len(favorites) > 10:
        message += f"\n... и еще {len(favorites) - 10} тендеров"

    return message


def format_stats(stats: Dict[str, Any]) -> str:
    """
    Форматирует статистику пользователя.

    Args:
        stats: Словарь со статистикой

    Returns:
        str: Сообщение со статистикой
    """
    message = f"""📊 <b>ВАША СТАТИСТИКА</b>

⭐ Избранных тендеров: {stats.get('favorites_count', 0)}
👎 Скрытых тендеров: {stats.get('hidden_count', 0)}
🔔 Активных напоминаний: {stats.get('reminders_count', 0)}

📈 За последний месяц:
  • Получено уведомлений: {stats.get('notifications_count', 0)}
  • Активных фильтров: {stats.get('active_filters', 0)}
"""

    # Добавляем информацию о подписке
    subscription_tier = stats.get('subscription_tier', 'trial')
    if subscription_tier == 'trial':
        message += "\n💳 Тариф: Пробный (20 уведомлений/день)"
    elif subscription_tier == 'basic':
        message += "\n💳 Тариф: Базовый (50 уведомлений/день)"
    else:
        message += "\n💳 Тариф: Премиум (безлимит)"

    return message


# Экспортируем все функции
__all__ = [
    'format_tender_notification',
    'format_detailed_tender_info',
    'format_reminder_options',
    'format_favorites_list',
    'format_stats'
]

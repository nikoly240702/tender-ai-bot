"""
Форматтер карточки тендера.

Единая точка форматирования сообщений о тендерах.
Используется в уведомлениях, поиске и других местах.
"""

from datetime import datetime
from typing import Dict, Any, Optional, Tuple

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def format_tender_card(
    tender: Dict[str, Any],
    match_info: Dict[str, Any],
    filter_name: str,
    subscription_tier: str = 'trial',
    is_auto_notification: bool = False,
) -> Tuple[str, InlineKeyboardMarkup]:
    """
    Форматирует карточку тендера для Telegram.

    Args:
        tender: Данные тендера (number, name, price, url, region, customer, submission_deadline, ...)
        match_info: Информация о совпадении + AI-анализ (score, matched_keywords, ai_confidence,
                    ai_reason, ai_summary, ai_key_requirements, ai_risks, ai_recommendation, ...)
        filter_name: Название фильтра, который поймал этот тендер
        subscription_tier: Тариф пользователя (влияет на кнопки AI-функций)
        is_auto_notification: True если это уведомление из автомониторинга

    Returns:
        (text, keyboard) — готовый текст и клавиатура для bot.send_message
    """
    text = _build_text(tender, match_info, filter_name)
    keyboard = _build_keyboard(tender, subscription_tier, is_auto_notification)
    return text, keyboard


def _build_text(
    tender: Dict[str, Any],
    match_info: Dict[str, Any],
    filter_name: str,
) -> str:
    score = match_info.get('score', 0)
    matched_keywords = match_info.get('matched_keywords', [])

    # Эмодзи по score
    if score >= 80:
        score_emoji = "🔥"
    elif score >= 60:
        score_emoji = "✨"
    else:
        score_emoji = "📌"

    # Название (ai_simple_name уже может быть подставлено в tender['name'] до вызова)
    name = tender.get('name', 'Без названия')

    # Цена
    price = tender.get('price')
    if price:
        try:
            price_str = f"{float(price):,.0f} ₽".replace(',', ' ')
        except (ValueError, TypeError):
            price_str = str(price)
    else:
        price_str = "Не указана"

    # Дедлайн
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

    # Совпавшие ключевые слова (только строки длиннее 1 символа)
    kw_list = [kw for kw in matched_keywords if isinstance(kw, str) and len(kw) > 1][:5]

    # AI-анализ
    ai_confidence = match_info.get('ai_confidence')
    ai_reason = match_info.get('ai_reason', '')
    ai_summary = match_info.get('ai_summary', '')
    ai_recommendation = match_info.get('ai_recommendation', '')
    ai_key_requirements = match_info.get('ai_key_requirements', [])
    ai_risks = match_info.get('ai_risks', [])
    red_flags = match_info.get('red_flags', [])

    # ─── Строим текст ───

    # Заголовок
    parts = [f"{score_emoji} <b>{name}</b>"]

    # Цена + дедлайн
    line2 = [f"💰 {price_str}"]
    if deadline_str:
        if days_left is not None and days_left >= 0:
            urgency = "‼️" if days_left <= 3 else ("⚡" if days_left <= 7 else "⏰")
            line2.append(f"{urgency} до {deadline_str} ({days_left} дн.)")
        else:
            line2.append(f"⏰ до {deadline_str}")
    parts.append("  ·  ".join(line2))

    # Место и заказчик
    if region and customer:
        parts.append(f"📍 {region}  ·  🏢 {customer}")
    elif region:
        parts.append(f"📍 {region}")
    elif customer:
        parts.append(f"🏢 {customer}")

    # AI-строка: рекомендация + confidence + summary/reason
    if ai_confidence is not None and ai_confidence >= 40:
        ai_line = "🤖 "
        if ai_recommendation:
            ai_line += f"{ai_recommendation} "
        ai_line += f"({ai_confidence}%)"
        display_text = ai_summary or ai_reason
        if display_text:
            if len(display_text) > 80:
                display_text = display_text[:77] + '...'
            ai_line += f" — {display_text}"
        parts.append(ai_line)

    # Ключевые требования
    if ai_key_requirements:
        reqs = "\n".join(f"  • {r}" for r in ai_key_requirements)
        parts.append(f"✅ <i>Требования:</i>\n{reqs}")

    # Риски
    if ai_risks:
        risks_text = "\n".join(f"  • {r}" for r in ai_risks)
        parts.append(f"⚠️ <i>Риски:</i>\n{risks_text}")

    # Красные флаги (из SmartMatcher)
    if red_flags:
        parts.append("🚩 " + " · ".join(red_flags[:2]))

    # Фильтр и ключевые слова
    filter_line = f"🎯 {filter_name}"
    if kw_list:
        filter_line += f"  ·  <i>{', '.join(kw_list)}</i>"
    parts.append(filter_line)

    # Номер тендера
    tender_number = tender.get('number')
    if tender_number:
        parts.append(f"\n<code>№ {tender_number}</code>")

    return "\n".join(parts)


def _build_keyboard(
    tender: Dict[str, Any],
    subscription_tier: str = 'trial',
    is_auto_notification: bool = False,
) -> InlineKeyboardMarkup:
    # Импортируем здесь чтобы избежать circular imports
    from bot.utils import safe_callback_data

    buttons = []
    tender_number = tender.get('number')

    # Ссылка на zakupki.gov.ru
    tender_url = tender.get('url', '')
    if tender_url:
        if not tender_url.startswith('http'):
            tender_url = f"https://zakupki.gov.ru{tender_url}"
        buttons.append([
            InlineKeyboardButton(text="📄 Открыть на zakupki.gov.ru", url=tender_url)
        ])

    # Действия
    if tender_number:
        buttons.append([
            InlineKeyboardButton(
                text="✅ Интересно",
                callback_data=safe_callback_data("interested", tender_number)
            ),
            InlineKeyboardButton(
                text="❌ Пропустить",
                callback_data=safe_callback_data("skip", tender_number)
            ),
        ])
        buttons.append([
            InlineKeyboardButton(
                text="📊 В таблицу",
                callback_data=safe_callback_data("sheets", tender_number)
            ),
            InlineKeyboardButton(
                text="🔗 В Б24",
                callback_data=safe_callback_data("bitrix", tender_number)
            ),
            InlineKeyboardButton(
                text="🤖 В Б24 + AI",
                callback_data=safe_callback_data("bitrix_ai", tender_number)
            ),
        ])

        # AI-кнопки
        if subscription_tier in ('basic', 'premium'):
            buttons.append([
                InlineKeyboardButton(
                    text="📝 AI-резюме",
                    callback_data=safe_callback_data("ai_summary", tender_number)
                ),
                InlineKeyboardButton(
                    text="📄 Анализ докум.",
                    callback_data=safe_callback_data("analyze_docs", tender_number)
                ),
            ])
        else:
            buttons.append([
                InlineKeyboardButton(
                    text="⭐ AI-функции (Basic+)",
                    callback_data="show_premium_ai"
                )
            ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

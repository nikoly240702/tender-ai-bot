"""
Интеграция с Битрикс24.

Функции:
- Кнопка «В Битрикс24» на карточке тендера → создаёт лид/сделку через webhook
- /bitrix24 — команда для настройки webhook URL
- Поддержка deep link: t.me/BOT?start=analyze_{tender_number}
"""

import logging
import asyncio
from typing import Optional, Dict, Any

import aiohttp
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from tender_sniper.database import get_sniper_db

logger = logging.getLogger(__name__)

router = Router(name="bitrix24")


# ============================================
# FSM для настройки webhook
# ============================================

class Bitrix24States(StatesGroup):
    waiting_for_webhook = State()


# ============================================
# ОТПРАВКА В БИТРИКС24
# ============================================

async def post_to_bitrix24(
    webhook_url: str,
    tender_number: str,
    tender_name: str,
    tender_price: Optional[float],
    tender_url: str,
    tender_region: str,
    filter_name: str,
    ai_summary: str = '',
    ai_recommendation: str = '',
) -> bool:
    """
    Отправляет тендер в Битрикс24 через webhook (REST API crm.lead.add).

    Returns:
        True если успешно, False при ошибке.
    """
    price_str = f"{tender_price:,.0f}".replace(',', ' ') if tender_price else ''
    ai_comment = ''
    if ai_recommendation:
        ai_comment += f"[{ai_recommendation}] "
    if ai_summary:
        ai_comment += ai_summary

    comment = (
        f"Тендер из TenderSniper\n"
        f"№ {tender_number}\n"
        f"Регион: {tender_region}\n"
        f"Фильтр: {filter_name}\n"
    )
    if ai_comment:
        comment += f"\nAI: {ai_comment}"
    comment += f"\n\nСсылка: {tender_url}"

    payload = {
        'fields': {
            'TITLE': tender_name[:255],
            'SOURCE_ID': 'WEB',
            'SOURCE_DESCRIPTION': 'TenderSniper Bot',
            'COMMENTS': comment,
            'OPPORTUNITY': tender_price or 0,
            'CURRENCY_ID': 'RUB',
        }
    }

    # Битрикс24 webhook для crm.lead.add
    if not webhook_url.endswith('/'):
        webhook_url += '/'
    endpoint = webhook_url + 'crm.lead.add.json'

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.post(endpoint, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return bool(data.get('result'))
                else:
                    body = await resp.text()
                    logger.warning(f"Bitrix24 webhook error {resp.status}: {body[:200]}")
                    return False
    except asyncio.TimeoutError:
        logger.warning("Bitrix24 webhook timeout")
        return False
    except Exception as e:
        logger.error(f"Bitrix24 post error: {e}")
        return False


# ============================================
# КНОПКА «В БИТРИКС24»
# ============================================

@router.callback_query(F.data.startswith("bitrix_"))
async def handle_bitrix_export(callback: CallbackQuery):
    """Экспорт тендера в Битрикс24."""
    tender_number = callback.data[len("bitrix_"):]
    telegram_id = callback.from_user.id

    await callback.answer("Отправляю в Битрикс24...")

    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(telegram_id)
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        user_data = user.get('data') or {}
        webhook_url = user_data.get('bitrix24_webhook_url', '')

        if not webhook_url:
            await callback.answer(
                "Битрикс24 не настроен. Используйте /bitrix24 для настройки.",
                show_alert=True
            )
            return

        user_id = user['id']

        # Получаем данные тендера
        notification = await db.get_notification_by_tender_number(user_id, tender_number)
        if not notification:
            notification = await db.find_notification_by_tender_number(tender_number)
        if not notification:
            await callback.answer("Тендер не найден в истории", show_alert=True)
            return

        # Берём AI-поля из сохранённого match_info
        mi = notification.get('match_info') or {}
        ai_summary = mi.get('ai_summary', '')
        ai_recommendation = mi.get('ai_recommendation', '')

        tender_url = notification.get('tender_url') or \
            f"https://zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html?regNumber={tender_number}"

        ok = await post_to_bitrix24(
            webhook_url=webhook_url,
            tender_number=tender_number,
            tender_name=notification.get('tender_name', ''),
            tender_price=notification.get('tender_price'),
            tender_url=tender_url,
            tender_region=notification.get('tender_region', ''),
            filter_name=notification.get('filter_name', ''),
            ai_summary=ai_summary,
            ai_recommendation=ai_recommendation,
        )

        if ok:
            await callback.answer("✅ Добавлено в Битрикс24!", show_alert=True)
        else:
            await callback.answer(
                "❌ Не удалось отправить в Битрикс24. Проверьте webhook URL.",
                show_alert=True
            )

    except Exception as e:
        logger.error(f"Bitrix24 export error: {e}", exc_info=True)
        await callback.answer("Ошибка отправки в Битрикс24", show_alert=True)


# ============================================
# НАСТРОЙКА /bitrix24
# ============================================

@router.message(Command("bitrix24"))
async def cmd_bitrix24(message: Message, state: FSMContext):
    """Настройка интеграции с Битрикс24."""
    telegram_id = message.from_user.id

    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(telegram_id)
        if not user:
            await message.answer("❌ Пользователь не найден")
            return

        user_data = user.get('data') or {}
        current_webhook = user_data.get('bitrix24_webhook_url', '')

        status = f"✅ Настроен: <code>{current_webhook[:60]}…</code>" if current_webhook else "❌ Не настроен"

        await message.answer(
            f"🔗 <b>Интеграция с Битрикс24</b>\n\n"
            f"Статус: {status}\n\n"
            f"Для настройки отправьте URL входящего webhook из Битрикс24.\n\n"
            f"<b>Как получить webhook:</b>\n"
            f"1. Откройте ваш Битрикс24\n"
            f"2. Приложения → Входящие webhooks\n"
            f"3. Добавьте webhook с правом на <i>CRM (leads, crm.lead.*)</i>\n"
            f"4. Скопируйте URL и отправьте сюда\n\n"
            f"Пример: <code>https://yourcompany.bitrix24.ru/rest/1/abc123/</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="sniper_menu")],
            ])
        )
        await state.set_state(Bitrix24States.waiting_for_webhook)

    except Exception as e:
        logger.error(f"cmd_bitrix24 error: {e}")
        await message.answer("❌ Произошла ошибка")


@router.message(Bitrix24States.waiting_for_webhook)
async def process_webhook_url(message: Message, state: FSMContext):
    """Сохраняет webhook URL Битрикс24."""
    url = (message.text or '').strip()

    if not url.startswith('https://') or 'bitrix24' not in url:
        await message.answer(
            "❌ Некорректный URL. Должен начинаться с https:// и содержать bitrix24.\n\n"
            "Пример: <code>https://yourcompany.bitrix24.ru/rest/1/abc123/</code>",
            parse_mode="HTML"
        )
        return

    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(message.from_user.id)
        if not user:
            await message.answer("❌ Пользователь не найден")
            await state.clear()
            return

        user_data = user.get('data') or {}
        user_data['bitrix24_webhook_url'] = url
        await db.update_user_json_data(user['id'], user_data)

        await state.clear()
        await message.answer(
            f"✅ <b>Битрикс24 подключён!</b>\n\n"
            f"Теперь кнопка «🔗 В Битрикс24» на карточках тендеров будет "
            f"создавать лиды в вашем Битрикс24.\n\n"
            f"<b>Deep link для анализа:</b>\n"
            f"Чтобы запустить AI-анализ тендера прямо из Битрикс24, "
            f"добавляйте ссылку вида:\n"
            f"<code>https://t.me/TenderAI111_bot?start=analyze_НОМЕР_ТЕНДЕРА</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Меню", callback_data="sniper_menu")]
            ])
        )
    except Exception as e:
        logger.error(f"process_webhook_url error: {e}")
        await state.clear()
        await message.answer("❌ Произошла ошибка при сохранении")

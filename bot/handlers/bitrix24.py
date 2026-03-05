"""
Интеграция с Битрикс24.

Функции:
- Кнопка «В Битрикс24» на карточке тендера → создаёт сделку через crm.deal.add
- /bitrix24 — настройка webhook URL с валидацией подключения
- Дедупликация: повторный клик → alert «Уже в Б24 (сделка #ID)»
- Поддержка deep link: t.me/BOT?start=analyze_{tender_number}
"""

import logging
import asyncio
from datetime import datetime, date
from typing import Optional, Tuple

import aiohttp
from aiogram import Router, F
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
# ВАЛИДАЦИЯ И СОЗДАНИЕ СДЕЛКИ
# ============================================

async def validate_bitrix24_webhook(webhook_url: str) -> Tuple[bool, str]:
    """
    Проверяет webhook через crm.deal.fields.

    Returns:
        (True, "OK") или (False, "сообщение об ошибке")
    """
    if not webhook_url.endswith('/'):
        webhook_url += '/'
    endpoint = webhook_url + 'crm.deal.fields.json'
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            async with session.get(endpoint) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('result'):
                        return True, "OK"
                    return False, f"Неожиданный ответ: {str(data)[:100]}"
                else:
                    body = await resp.text()
                    return False, f"HTTP {resp.status}: {body[:150]}"
    except asyncio.TimeoutError:
        return False, "Timeout — сервер Битрикс24 не отвечает"
    except Exception as e:
        return False, str(e)[:200]


# Этапы воронки в Битрикс24
STAGE_NEW = 'NEW'           # Новые процедуры (без AI-анализа)
STAGE_AI = 'UC_OZCYR2'     # Новые процедуры с AI
STAGE_LOSE = 'LOSE'        # Не берем в работу (истёкший дедлайн)


def _is_deadline_expired(submission_deadline_str: str) -> bool:
    """Возвращает True если срок подачи заявки уже прошёл."""
    if not submission_deadline_str:
        return False
    for fmt in ('%d.%m.%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(submission_deadline_str[:10], fmt).date() < date.today()
        except ValueError:
            continue
    return False


async def update_bitrix24_deal_stage(webhook_url: str, deal_id: str, stage_id: str) -> bool:
    """
    Перемещает сделку на указанный этап через crm.deal.update.

    Returns:
        True если успешно.
    """
    if not webhook_url.endswith('/'):
        webhook_url += '/'
    endpoint = webhook_url + 'crm.deal.update.json'
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.post(endpoint, json={
                'id': deal_id,
                'fields': {'STAGE_ID': stage_id},
            }) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return bool(data.get('result'))
                logger.warning(f"update_stage HTTP {resp.status}")
                return False
    except Exception as e:
        logger.error(f"update_bitrix24_deal_stage error: {e}")
        return False



# Маппинг закона → ID перечисления в Битрикс24
_LAW_ENUM: dict = {
    '44-фз': 45, '44фз': 45,
    '223-фз': 47, '223фз': 47,
    '615-пп': 49, '615пп': 49,
    '44-фз еп': 51, 'еп': 51,
}

# Маппинг AI-рекомендации → ID перечисления в Битрикс24
_AI_REC_ENUM: dict = {
    'взять': 53, 'рекоменд': 53, 'да': 53, 'yes': 53, 'высок': 53,
    'не брать': 57, 'отказ': 57, 'нет': 57, 'no': 57, 'низк': 57,
}


def _map_law_enum(law_type: str) -> Optional[int]:
    if not law_type:
        return None
    key = law_type.lower().strip()
    for pattern, enum_id in _LAW_ENUM.items():
        if pattern in key:
            return enum_id
    return None


def _map_ai_rec_enum(ai_recommendation: str) -> int:
    """Возвращает ID перечисления Б24: 53=Взять, 55=Требует анализа, 57=Не брать."""
    if not ai_recommendation:
        return 55  # Требует анализа по умолчанию
    text = ai_recommendation.lower()
    # Сначала проверяем "не брать" до "брать"
    if 'не брать' in text or 'отказ' in text or text in ('нет', 'no', 'низк'):
        return 57
    for pattern, enum_id in _AI_REC_ENUM.items():
        if pattern in text:
            return enum_id
    return 55


async def create_bitrix24_deal(
    webhook_url: str,
    tender_number: str,
    tender_name: str,
    tender_price: Optional[float],
    tender_url: str,
    tender_region: str,
    tender_customer: str,
    filter_name: str,
    submission_deadline: str = '',
    ai_summary: str = '',
    ai_recommendation: str = '',
    law_type: str = '',
    stage_id: str = STAGE_NEW,
) -> Optional[int]:
    """
    Создаёт сделку в Битрикс24 через crm.deal.add.

    Returns:
        deal_id (int) или None при ошибке.
    """
    # CLOSEDATE: конвертируем DD.MM.YYYY → YYYY-MM-DD
    closedate = ''
    if submission_deadline:
        for fmt_in, fmt_out in [('%d.%m.%Y', '%Y-%m-%d'), ('%Y-%m-%d', '%Y-%m-%d')]:
            try:
                closedate = datetime.strptime(submission_deadline[:10], fmt_in).strftime(fmt_out)
                break
            except ValueError:
                continue

    fields: dict = {
        'TITLE': (tender_name[:255] if tender_name else f'Тендер № {tender_number}'),
        'OPPORTUNITY': tender_price or 0,
        'CURRENCY_ID': 'RUB',
        'SOURCE_ID': 'WEB',
        'SOURCE_DESCRIPTION': 'TenderSniper Bot',
        'STAGE_ID': stage_id,
        # Кастомные поля карточки
        'UF_CRM_TENDER_NUMBER': tender_number,
        'UF_CRM_TENDER_CUSTOMER': tender_customer or '',
        'UF_CRM_TENDER_REGION': tender_region or '',
        'UF_CRM_TENDER_FILTER': filter_name or '',
        'UF_CRM_AI_SUMMARY': ai_summary or '',
        'UF_CRM_AI_RECOMMENDATION': _map_ai_rec_enum(ai_recommendation),
    }
    if tender_url:
        fields['UF_CRM_TENDER_URL'] = tender_url
    if closedate:
        fields['CLOSEDATE'] = closedate
    law_enum = _map_law_enum(law_type)
    if law_enum:
        fields['UF_CRM_TENDER_LAW'] = law_enum

    if not webhook_url.endswith('/'):
        webhook_url += '/'
    endpoint = webhook_url + 'crm.deal.add.json'

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.post(endpoint, json={'fields': fields}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get('result')
                    if result and isinstance(result, int):
                        return result
                    logger.warning(f"Bitrix24 deal.add unexpected result: {data}")
                    return None
                else:
                    body = await resp.text()
                    logger.warning(f"Bitrix24 deal.add HTTP {resp.status}: {body[:200]}")
                    return None
    except asyncio.TimeoutError:
        logger.warning("create_bitrix24_deal timeout")
        return None
    except Exception as e:
        logger.error(f"create_bitrix24_deal error: {e}")
        return None


# ============================================
# КНОПКА «В БИТРИКС24» — специфичные обработчики раньше wildcard
# ============================================

@router.callback_query(F.data == "bitrix_test_connection")
async def handle_bitrix_test(callback: CallbackQuery):
    """Тест подключения к Битрикс24."""
    await callback.answer("Проверяю подключение...")
    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(callback.from_user.id)
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        webhook_url = (user.get('data') or {}).get('bitrix24_webhook_url', '')
        if not webhook_url:
            await callback.answer("Битрикс24 не настроен", show_alert=True)
            return

        ok, msg = await validate_bitrix24_webhook(webhook_url)
        if ok:
            await callback.answer("✅ Подключение работает!", show_alert=True)
        else:
            await callback.answer(f"❌ Ошибка подключения:\n{msg}", show_alert=True)
    except Exception as e:
        logger.error(f"bitrix_test_connection error: {e}")
        await callback.answer("Ошибка при проверке", show_alert=True)


@router.callback_query(F.data == "bitrix_disconnect")
async def handle_bitrix_disconnect(callback: CallbackQuery):
    """Отключение Битрикс24."""
    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(callback.from_user.id)
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        user_data = user.get('data') or {}
        user_data.pop('bitrix24_webhook_url', None)
        await db.update_user_json_data(user['id'], user_data)

        await callback.answer("✅ Битрикс24 отключён", show_alert=True)
        await callback.message.edit_text(
            "🔗 <b>Интеграция с Битрикс24</b>\n\nСтатус: ❌ Отключена",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Меню", callback_data="sniper_menu")]
            ])
        )
    except Exception as e:
        logger.error(f"bitrix_disconnect error: {e}")
        await callback.answer("Ошибка при отключении", show_alert=True)


@router.callback_query(F.data.startswith("bitrix_ai_"))
async def handle_bitrix_ai_export(callback: CallbackQuery):
    """
    Экспорт тендера в Битрикс24 на этап «Новые процедуры с AI».

    Если сделка уже существует — перемещает её на AI-этап.
    Если нет — создаёт новую сделку сразу на AI-этапе.
    """
    tender_number = callback.data[len("bitrix_ai_"):]
    telegram_id = callback.from_user.id

    await callback.answer("Отправляю в Битрикс24 (AI-этап)...")

    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(telegram_id)
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        webhook_url = (user.get('data') or {}).get('bitrix24_webhook_url', '')
        if not webhook_url:
            await callback.answer(
                "Битрикс24 не настроен. Используйте /bitrix24 для настройки.",
                show_alert=True
            )
            return

        user_id = user['id']
        notification = await db.get_notification_by_tender_number(user_id, tender_number)
        if not notification:
            notification = await db.find_notification_by_tender_number(tender_number)
        if not notification:
            await callback.answer("Тендер не найден в истории", show_alert=True)
            return

        submission_deadline = notification.get('submission_deadline', '')
        if _is_deadline_expired(submission_deadline):
            stage_id = STAGE_LOSE
        else:
            stage_id = STAGE_AI

        # Если уже в Битрикс24 — перемещаем на AI-этап
        if notification.get('bitrix24_exported') and notification.get('bitrix24_deal_id'):
            deal_id = notification['bitrix24_deal_id']
            ok = await update_bitrix24_deal_stage(webhook_url, deal_id, stage_id)
            if ok:
                label = "Новые процедуры с AI" if stage_id == STAGE_AI else "Не берем в работу"
                await callback.answer(
                    f"✅ Сделка #{deal_id} перемещена: «{label}»",
                    show_alert=True
                )
            else:
                await callback.answer("❌ Не удалось переместить сделку", show_alert=True)
            return

        # Создаём новую сделку на AI-этапе
        mi = notification.get('match_info') or {}
        tender_url = notification.get('tender_url') or (
            f"https://zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html"
            f"?regNumber={tender_number}"
        )

        deal_id = await create_bitrix24_deal(
            webhook_url=webhook_url,
            tender_number=tender_number,
            tender_name=notification.get('tender_name', ''),
            tender_price=notification.get('tender_price'),
            tender_url=tender_url,
            tender_region=notification.get('tender_region', ''),
            tender_customer=notification.get('tender_customer', ''),
            filter_name=notification.get('filter_name', ''),
            submission_deadline=submission_deadline,
            ai_summary=mi.get('ai_summary', ''),
            ai_recommendation=mi.get('ai_recommendation', ''),
            stage_id=stage_id,
        )

        if deal_id:
            await db.mark_notification_bitrix_exported(notification['id'], deal_id)
            await _replace_bitrix_button(callback, tender_number, deal_id, prefix="bitrix_ai")
            label = "Новые процедуры с AI" if stage_id == STAGE_AI else "Не берем в работу"
            await callback.answer(f"✅ Сделка #{deal_id} → «{label}»!", show_alert=True)
        else:
            await callback.answer(
                "❌ Не удалось создать сделку. Проверьте webhook URL (/bitrix24).",
                show_alert=True
            )

    except Exception as e:
        logger.error(f"handle_bitrix_ai_export error: {e}", exc_info=True)
        await callback.answer("Ошибка отправки в Битрикс24", show_alert=True)


@router.callback_query(F.data.startswith("bitrix_"))
async def handle_bitrix_export(callback: CallbackQuery):
    """Экспорт тендера в Битрикс24 на этап «Новые процедуры»."""
    data = callback.data

    # Уже экспортировано — показываем deal ID
    if data.startswith("bitrix_done_"):
        tender_number = data[len("bitrix_done_"):]
        try:
            db = await get_sniper_db()
            user = await db.get_user_by_telegram_id(callback.from_user.id)
            if user:
                notif = await db.get_notification_by_tender_number(user['id'], tender_number)
                deal_id = notif.get('bitrix24_deal_id') if notif else None
                if deal_id:
                    await callback.answer(f"✅ Уже в Битрикс24 (сделка #{deal_id})", show_alert=True)
                    return
        except Exception:
            pass
        await callback.answer("✅ Тендер уже добавлен в Битрикс24", show_alert=True)
        return

    # Новый экспорт
    tender_number = data[len("bitrix_"):]
    telegram_id = callback.from_user.id

    await callback.answer("Отправляю в Битрикс24...")

    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(telegram_id)
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        webhook_url = (user.get('data') or {}).get('bitrix24_webhook_url', '')
        if not webhook_url:
            await callback.answer(
                "Битрикс24 не настроен. Используйте /bitrix24 для настройки.",
                show_alert=True
            )
            return

        user_id = user['id']

        # Загружаем данные тендера
        notification = await db.get_notification_by_tender_number(user_id, tender_number)
        if not notification:
            notification = await db.find_notification_by_tender_number(tender_number)
        if not notification:
            await callback.answer("Тендер не найден в истории", show_alert=True)
            return

        # Дедупликация
        if notification.get('bitrix24_exported') and notification.get('bitrix24_deal_id'):
            deal_id = notification['bitrix24_deal_id']
            await callback.answer(f"✅ Уже в Битрикс24 (сделка #{deal_id})", show_alert=True)
            return

        mi = notification.get('match_info') or {}
        submission_deadline = notification.get('submission_deadline', '')

        # Если дедлайн истёк — сразу в «Не берем в работу»
        if _is_deadline_expired(submission_deadline):
            stage_id = STAGE_LOSE
        else:
            stage_id = STAGE_NEW

        tender_url = notification.get('tender_url') or (
            f"https://zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html"
            f"?regNumber={tender_number}"
        )

        deal_id = await create_bitrix24_deal(
            webhook_url=webhook_url,
            tender_number=tender_number,
            tender_name=notification.get('tender_name', ''),
            tender_price=notification.get('tender_price'),
            tender_url=tender_url,
            tender_region=notification.get('tender_region', ''),
            tender_customer=notification.get('tender_customer', ''),
            filter_name=notification.get('filter_name', ''),
            submission_deadline=submission_deadline,
            ai_summary=mi.get('ai_summary', ''),
            ai_recommendation=mi.get('ai_recommendation', ''),
            stage_id=stage_id,
        )

        if deal_id:
            await db.mark_notification_bitrix_exported(notification['id'], deal_id)
            await _replace_bitrix_button(callback, tender_number, deal_id)
            if stage_id == STAGE_LOSE:
                await callback.answer(
                    f"⚠️ Сделка #{deal_id} → «Не берем в работу» (дедлайн истёк)",
                    show_alert=True
                )
            else:
                await callback.answer(f"✅ Сделка #{deal_id} создана в Битрикс24!", show_alert=True)
        else:
            await callback.answer(
                "❌ Не удалось создать сделку. Проверьте webhook URL (/bitrix24).",
                show_alert=True
            )

    except Exception as e:
        logger.error(f"handle_bitrix_export error: {e}", exc_info=True)
        await callback.answer("Ошибка отправки в Битрикс24", show_alert=True)


async def _replace_bitrix_button(
    callback: CallbackQuery,
    tender_number: str,
    deal_id: int,
    prefix: str = "bitrix",
):
    """
    Заменяет кнопки «🔗 В Битрикс24» и «🤖 В Б24 + AI» на «✅ В Б24 (#deal_id)».
    Заменяет обе кнопки, если они есть, чтобы не было дублирования.
    """
    try:
        from bot.utils import safe_callback_data
        markup = callback.message.reply_markup
        if not markup:
            return

        # Все callback_data которые должны быть заменены
        old_cds = {
            safe_callback_data("bitrix", tender_number),
            safe_callback_data("bitrix_ai", tender_number),
        }
        new_cd = safe_callback_data("bitrix_done", tender_number)

        new_rows = []
        done_added = False
        for row in markup.inline_keyboard:
            new_row = []
            for btn in row:
                if btn.callback_data and btn.callback_data in old_cds:
                    if not done_added:
                        new_row.append(InlineKeyboardButton(
                            text=f"✅ В Б24 (#{deal_id})",
                            callback_data=new_cd,
                        ))
                        done_added = True
                    # Вторую кнопку Б24 пропускаем (схлопываем в одну)
                else:
                    new_row.append(btn)
            if new_row:
                new_rows.append(new_row)

        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(inline_keyboard=new_rows)
        )
    except Exception as e:
        logger.debug(f"_replace_bitrix_button: {e}")


# ============================================
# НАСТРОЙКА /bitrix24
# ============================================

@router.message(Command("bitrix24"))
async def cmd_bitrix24(message: Message, state: FSMContext):
    """Настройка / просмотр статуса интеграции с Битрикс24."""
    telegram_id = message.from_user.id

    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(telegram_id)
        if not user:
            await message.answer("❌ Пользователь не найден")
            return

        current_webhook = (user.get('data') or {}).get('bitrix24_webhook_url', '')

        if current_webhook:
            short_url = current_webhook[:55] + ('…' if len(current_webhook) > 55 else '')
            await message.answer(
                f"🔗 <b>Битрикс24 подключён</b>\n\n"
                f"Webhook: <code>{short_url}</code>\n\n"
                f"Кнопка «🔗 В Битрикс24» на карточках тендеров автоматически "
                f"создаёт сделки в вашем Битрикс24.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🔍 Тест подключения",
                            callback_data="bitrix_test_connection"
                        ),
                        InlineKeyboardButton(
                            text="🔌 Отключить",
                            callback_data="bitrix_disconnect"
                        ),
                    ],
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="sniper_menu")],
                ])
            )
        else:
            await message.answer(
                f"🔗 <b>Интеграция с Битрикс24</b>\n\n"
                f"Статус: ❌ Не настроена\n\n"
                f"Для настройки отправьте URL входящего webhook из Битрикс24.\n\n"
                f"<b>Как получить webhook:</b>\n"
                f"1. Откройте ваш Битрикс24\n"
                f"2. Приложения → Входящие webhooks\n"
                f"3. Добавьте webhook с правами на <i>CRM (crm.deal.*)</i>\n"
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
    """Сохраняет и валидирует webhook URL Битрикс24."""
    url = (message.text or '').strip()

    if not url.startswith('https://') or 'bitrix24' not in url:
        await message.answer(
            "❌ Некорректный URL. Должен начинаться с https:// и содержать bitrix24.\n\n"
            "Пример: <code>https://yourcompany.bitrix24.ru/rest/1/abc123/</code>",
            parse_mode="HTML"
        )
        return

    # Валидируем webhook
    await message.answer("⏳ Проверяю подключение к Битрикс24...")
    ok, err_msg = await validate_bitrix24_webhook(url)
    if not ok:
        await message.answer(
            f"❌ Не удалось подключиться к Битрикс24:\n<code>{err_msg}</code>\n\n"
            f"Проверьте URL и права webhook (нужны права: CRM → crm.deal.*).",
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
            f"создавать сделки в вашем Битрикс24.\n\n"
            f"<b>Deep link для анализа из Битрикс24:</b>\n"
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

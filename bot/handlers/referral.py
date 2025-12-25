"""
Referral Program Handler.

Реферальная программа:
- Генерация уникальной реферальной ссылки
- Обработка регистрации по реферальной ссылке
- Начисление бонуса +7 дней за каждого приглашённого
"""

import logging
import hashlib
from datetime import datetime, timedelta

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, CommandObject, CommandStart

from sqlalchemy import select, update
from database import SniperUser, Referral, DatabaseSession

logger = logging.getLogger(__name__)
router = Router(name="referral")

# Бот username (будет установлен при инициализации)
BOT_USERNAME = "TenderSniperBot"

# Бонус за реферала (дней)
REFERRAL_BONUS_DAYS = 7


def generate_referral_code(telegram_id: int) -> str:
    """Генерирует уникальный реферальный код."""
    hash_input = f"{telegram_id}_{datetime.now().timestamp()}"
    return hashlib.md5(hash_input.encode()).hexdigest()[:8].upper()


async def get_or_create_referral_code(telegram_id: int) -> str:
    """Получает или создаёт реферальный код для пользователя."""
    async with DatabaseSession() as session:
        user = await session.scalar(
            select(SniperUser).where(SniperUser.telegram_id == telegram_id)
        )

        if not user:
            return None

        if user.referral_code:
            return user.referral_code

        # Генерируем новый код
        new_code = generate_referral_code(telegram_id)

        await session.execute(
            update(SniperUser)
            .where(SniperUser.id == user.id)
            .values(referral_code=new_code)
        )

        return new_code


async def process_referral(new_user_telegram_id: int, referral_code: str, bot: Bot) -> bool:
    """
    Обрабатывает регистрацию по реферальной ссылке.

    Returns:
        True если реферал успешно обработан
    """
    async with DatabaseSession() as session:
        # Находим реферера по коду
        referrer = await session.scalar(
            select(SniperUser).where(SniperUser.referral_code == referral_code)
        )

        if not referrer:
            logger.warning(f"Referral code not found: {referral_code}")
            return False

        # Проверяем, что пользователь не приглашает сам себя
        if referrer.telegram_id == new_user_telegram_id:
            logger.warning(f"User {new_user_telegram_id} tried to use own referral code")
            return False

        # Проверяем, что новый пользователь ещё не был зарегистрирован
        new_user = await session.scalar(
            select(SniperUser).where(SniperUser.telegram_id == new_user_telegram_id)
        )

        if new_user and new_user.referred_by:
            logger.info(f"User {new_user_telegram_id} already has referrer")
            return False

        # Если пользователь уже существует, обновляем referred_by
        if new_user:
            await session.execute(
                update(SniperUser)
                .where(SniperUser.id == new_user.id)
                .values(referred_by=referrer.id)
            )
            new_user_id = new_user.id
        else:
            # Пользователь будет создан при онбординге
            # Сохраняем referrer_id в сессии для использования позже
            return False  # Будет обработано при создании пользователя

        # Создаём запись о реферале
        referral = Referral(
            referrer_id=referrer.id,
            referred_id=new_user_id,
            bonus_given=True,
            bonus_days=REFERRAL_BONUS_DAYS
        )
        session.add(referral)

        # Начисляем бонус рефереру
        new_bonus = (referrer.referral_bonus_days or 0) + REFERRAL_BONUS_DAYS

        # Если у реферера есть триал, продлеваем его
        if referrer.trial_expires_at:
            new_expires = referrer.trial_expires_at + timedelta(days=REFERRAL_BONUS_DAYS)
            await session.execute(
                update(SniperUser)
                .where(SniperUser.id == referrer.id)
                .values(
                    referral_bonus_days=new_bonus,
                    trial_expires_at=new_expires
                )
            )
        else:
            await session.execute(
                update(SniperUser)
                .where(SniperUser.id == referrer.id)
                .values(referral_bonus_days=new_bonus)
            )

        logger.info(f"Referral processed: {referrer.telegram_id} gets +{REFERRAL_BONUS_DAYS} days for {new_user_telegram_id}")

        # Уведомляем реферера
        try:
            await bot.send_message(
                referrer.telegram_id,
                f"🎉 <b>Новый реферал!</b>\n\n"
                f"По вашей ссылке зарегистрировался новый пользователь.\n\n"
                f"🎁 Вы получили <b>+{REFERRAL_BONUS_DAYS} дней</b> к подписке!\n"
                f"Всего бонусных дней: <b>{new_bonus}</b>",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to notify referrer {referrer.telegram_id}: {e}")

        return True


# ============================================
# HANDLERS
# ============================================

@router.callback_query(F.data == "get_referral_link")
async def callback_get_referral_link(callback: CallbackQuery):
    """Показать реферальную ссылку."""
    await callback.answer()

    code = await get_or_create_referral_code(callback.from_user.id)

    if not code:
        await callback.message.answer(
            "❌ Ошибка: пользователь не найден. Используйте /start"
        )
        return

    link = f"https://t.me/{BOT_USERNAME}?start=ref_{code}"

    # Получаем статистику рефералов
    async with DatabaseSession() as session:
        user = await session.scalar(
            select(SniperUser).where(SniperUser.telegram_id == callback.from_user.id)
        )

        referrals_count = await session.scalar(
            select(Referral).where(Referral.referrer_id == user.id)
        ) or 0

        total_bonus = user.referral_bonus_days or 0

    text = (
        f"🎁 <b>Реферальная программа</b>\n\n"
        f"Приглашайте друзей и получайте <b>+{REFERRAL_BONUS_DAYS} дней</b> "
        f"подписки за каждого!\n\n"
        f"📎 <b>Ваша ссылка:</b>\n"
        f"<code>{link}</code>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"• Приглашено: {referrals_count}\n"
        f"• Бонусных дней: {total_bonus}\n\n"
        f"<i>Нажмите на ссылку, чтобы скопировать</i>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Поделиться", switch_inline_query=f"Присоединяйся к Tender Sniper! {link}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="sniper_menu")]
    ])

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "referral_stats")
async def callback_referral_stats(callback: CallbackQuery):
    """Статистика рефералов."""
    await callback.answer()

    async with DatabaseSession() as session:
        user = await session.scalar(
            select(SniperUser).where(SniperUser.telegram_id == callback.from_user.id)
        )

        if not user:
            await callback.message.answer("❌ Пользователь не найден")
            return

        # Получаем список рефералов
        result = await session.execute(
            select(Referral, SniperUser.username, SniperUser.created_at)
            .join(SniperUser, Referral.referred_id == SniperUser.id)
            .where(Referral.referrer_id == user.id)
            .order_by(Referral.created_at.desc())
            .limit(10)
        )
        referrals = result.all()

        total_bonus = user.referral_bonus_days or 0

    if referrals:
        referrals_list = "\n".join([
            f"• @{r[1] or 'anonymous'} ({r[2].strftime('%d.%m.%Y') if r[2] else 'N/A'})"
            for r in referrals
        ])
        text = (
            f"📊 <b>Ваши рефералы</b>\n\n"
            f"Всего приглашено: {len(referrals)}\n"
            f"Бонусных дней: {total_bonus}\n\n"
            f"<b>Последние:</b>\n{referrals_list}"
        )
    else:
        text = (
            f"📊 <b>Ваши рефералы</b>\n\n"
            f"Пока нет приглашённых друзей.\n\n"
            f"Используйте реферальную ссылку, чтобы получить "
            f"+{REFERRAL_BONUS_DAYS} дней за каждого!"
        )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Моя ссылка", callback_data="get_referral_link")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="sniper_menu")]
    ])

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

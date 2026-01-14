"""
Referral Program Handler.

Реферальная программа:
- +1 день Premium за регистрацию по реферальной ссылке
- +7 дней Premium когда реферал оплачивает подписку
"""

import logging
import hashlib
import os
from datetime import datetime, timedelta

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from sqlalchemy import select, update, func
from database import SniperUser, Referral, DatabaseSession

logger = logging.getLogger(__name__)
router = Router(name="referral")

# Бонусы реферальной программы
REFERRAL_REGISTRATION_BONUS_DAYS = 1   # За регистрацию по ссылке
REFERRAL_PAYMENT_BONUS_DAYS = 7        # За оплату реферала


def generate_referral_code(telegram_id: int) -> str:
    """Генерирует уникальный реферальный код."""
    hash_input = f"{telegram_id}_tender_sniper_ref"
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


async def process_referral_registration(new_user_telegram_id: int, referral_code: str, bot: Bot = None) -> bool:
    """
    Обрабатывает регистрацию по реферальной ссылке.
    Начисляет +1 день Premium рефереру.

    Args:
        new_user_telegram_id: Telegram ID нового пользователя
        referral_code: Реферальный код
        bot: Bot instance для отправки уведомления

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

        # Получаем нового пользователя
        new_user = await session.scalar(
            select(SniperUser).where(SniperUser.telegram_id == new_user_telegram_id)
        )

        if not new_user:
            logger.warning(f"New user not found: {new_user_telegram_id}")
            return False

        # Проверяем, что этот пользователь ещё не был реферером
        if new_user.referred_by:
            logger.info(f"User {new_user_telegram_id} already has referrer")
            return False

        # Проверяем, что не было уже связи в таблице referrals
        existing_referral = await session.scalar(
            select(Referral).where(Referral.referred_id == new_user.id)
        )
        if existing_referral:
            logger.info(f"Referral already exists for user {new_user_telegram_id}")
            return False

        # Обновляем referred_by у нового пользователя
        await session.execute(
            update(SniperUser)
            .where(SniperUser.id == new_user.id)
            .values(referred_by=referrer.id)
        )

        # Создаём запись о реферале (payment_bonus_given=False - ещё не оплачено)
        referral = Referral(
            referrer_id=referrer.id,
            referred_id=new_user.id,
            bonus_given=True,  # Регистрационный бонус дан
            bonus_days=REFERRAL_REGISTRATION_BONUS_DAYS,
            # payment_bonus_given будет в отдельном поле
        )
        session.add(referral)

        # Начисляем бонус рефереру (+1 день)
        new_bonus_total = (referrer.referral_bonus_days or 0) + REFERRAL_REGISTRATION_BONUS_DAYS

        # Продлеваем подписку реферера
        now = datetime.utcnow()
        if referrer.trial_expires_at and referrer.trial_expires_at > now:
            # Подписка активна - добавляем к ней
            new_expires = referrer.trial_expires_at + timedelta(days=REFERRAL_REGISTRATION_BONUS_DAYS)
        else:
            # Подписки нет или истекла - добавляем от сегодня
            new_expires = now + timedelta(days=REFERRAL_REGISTRATION_BONUS_DAYS)

        # Обновляем реферера - даём Premium
        await session.execute(
            update(SniperUser)
            .where(SniperUser.id == referrer.id)
            .values(
                referral_bonus_days=new_bonus_total,
                trial_expires_at=new_expires,
                subscription_tier='premium' if referrer.subscription_tier == 'trial' else referrer.subscription_tier,
                filters_limit=max(referrer.filters_limit or 3, 20),  # Premium лимиты
                notifications_limit=max(referrer.notifications_limit or 20, 9999)
            )
        )

        await session.commit()

        logger.info(f"✅ Referral registration bonus: {referrer.telegram_id} gets +{REFERRAL_REGISTRATION_BONUS_DAYS} day for inviting {new_user_telegram_id}")

        # Уведомляем реферера
        if bot:
            try:
                await bot.send_message(
                    referrer.telegram_id,
                    f"🎉 <b>Новый реферал!</b>\n\n"
                    f"По вашей ссылке зарегистрировался новый пользователь.\n\n"
                    f"🎁 Вы получили <b>+{REFERRAL_REGISTRATION_BONUS_DAYS} день</b> Premium!\n"
                    f"Подписка действует до: <b>{new_expires.strftime('%d.%m.%Y')}</b>\n\n"
                    f"💡 Когда этот пользователь оплатит подписку, вы получите ещё <b>+{REFERRAL_PAYMENT_BONUS_DAYS} дней</b>!",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Failed to notify referrer {referrer.telegram_id}: {e}")

        return True


async def award_referral_payment_bonus(paid_user_telegram_id: int, bot: Bot = None) -> bool:
    """
    Начисляет бонус рефереру когда реферал оплатил подписку.
    +7 дней Premium рефереру.

    Args:
        paid_user_telegram_id: Telegram ID пользователя который оплатил
        bot: Bot instance для отправки уведомления

    Returns:
        True если бонус начислен
    """
    async with DatabaseSession() as session:
        # Находим пользователя который оплатил
        paid_user = await session.scalar(
            select(SniperUser).where(SniperUser.telegram_id == paid_user_telegram_id)
        )

        if not paid_user or not paid_user.referred_by:
            # Пользователь не был приглашён по реферальной ссылке
            return False

        # Находим реферала (запись о связи)
        referral = await session.scalar(
            select(Referral).where(Referral.referred_id == paid_user.id)
        )

        if not referral:
            logger.warning(f"No referral record for user {paid_user_telegram_id}")
            return False

        # Проверяем, не был ли уже начислен бонус за оплату
        # Используем поле bonus_days > REFERRAL_REGISTRATION_BONUS_DAYS как индикатор
        if referral.bonus_days > REFERRAL_REGISTRATION_BONUS_DAYS:
            logger.info(f"Payment bonus already given for referral {referral.id}")
            return False

        # Находим реферера
        referrer = await session.scalar(
            select(SniperUser).where(SniperUser.id == paid_user.referred_by)
        )

        if not referrer:
            logger.warning(f"Referrer not found for user {paid_user_telegram_id}")
            return False

        # Обновляем запись реферала
        new_bonus_days = referral.bonus_days + REFERRAL_PAYMENT_BONUS_DAYS
        await session.execute(
            update(Referral)
            .where(Referral.id == referral.id)
            .values(bonus_days=new_bonus_days)
        )

        # Начисляем бонус рефереру (+7 дней)
        new_bonus_total = (referrer.referral_bonus_days or 0) + REFERRAL_PAYMENT_BONUS_DAYS

        # Продлеваем подписку реферера
        now = datetime.utcnow()
        if referrer.trial_expires_at and referrer.trial_expires_at > now:
            new_expires = referrer.trial_expires_at + timedelta(days=REFERRAL_PAYMENT_BONUS_DAYS)
        else:
            new_expires = now + timedelta(days=REFERRAL_PAYMENT_BONUS_DAYS)

        # Обновляем реферера
        await session.execute(
            update(SniperUser)
            .where(SniperUser.id == referrer.id)
            .values(
                referral_bonus_days=new_bonus_total,
                trial_expires_at=new_expires,
                subscription_tier='premium',
                filters_limit=20,
                notifications_limit=9999
            )
        )

        await session.commit()

        logger.info(f"✅ Referral payment bonus: {referrer.telegram_id} gets +{REFERRAL_PAYMENT_BONUS_DAYS} days because {paid_user_telegram_id} paid")

        # Уведомляем реферера
        if bot:
            try:
                await bot.send_message(
                    referrer.telegram_id,
                    f"🎉 <b>Ваш реферал оплатил подписку!</b>\n\n"
                    f"Приглашённый вами пользователь оформил платную подписку.\n\n"
                    f"🎁 Вы получили <b>+{REFERRAL_PAYMENT_BONUS_DAYS} дней</b> Premium!\n"
                    f"Подписка действует до: <b>{new_expires.strftime('%d.%m.%Y')}</b>\n\n"
                    f"Всего бонусных дней: <b>{new_bonus_total}</b>",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Failed to notify referrer {referrer.telegram_id}: {e}")

        return True


async def get_bot_username() -> str:
    """Получает username бота."""
    return os.getenv('BOT_USERNAME', 'TenderSniperBot')


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

    bot_username = await get_bot_username()
    link = f"https://t.me/{bot_username}?start=ref_{code}"

    # Получаем статистику рефералов
    async with DatabaseSession() as session:
        user = await session.scalar(
            select(SniperUser).where(SniperUser.telegram_id == callback.from_user.id)
        )

        # Считаем количество рефералов
        referrals_count = await session.scalar(
            select(func.count(Referral.id)).where(Referral.referrer_id == user.id)
        ) or 0

        total_bonus = user.referral_bonus_days or 0

    text = (
        f"🎁 <b>Реферальная программа</b>\n\n"
        f"Приглашайте друзей и получайте бонусы!\n\n"
        f"<b>Награды:</b>\n"
        f"• За регистрацию друга: <b>+{REFERRAL_REGISTRATION_BONUS_DAYS} день</b> Premium\n"
        f"• Когда друг оплатит: <b>+{REFERRAL_PAYMENT_BONUS_DAYS} дней</b> Premium\n\n"
        f"📎 <b>Ваша ссылка:</b>\n"
        f"<code>{link}</code>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"• Приглашено друзей: {referrals_count}\n"
        f"• Бонусных дней получено: {total_bonus}\n\n"
        f"<i>Нажмите на ссылку, чтобы скопировать</i>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Поделиться", switch_inline_query=f"Присоединяйся к Tender Sniper! {link}")],
        [InlineKeyboardButton(text="📊 Мои рефералы", callback_data="referral_stats")],
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

        # Получаем список рефералов с их статусом оплаты
        result = await session.execute(
            select(Referral, SniperUser)
            .join(SniperUser, Referral.referred_id == SniperUser.id)
            .where(Referral.referrer_id == user.id)
            .order_by(Referral.created_at.desc())
            .limit(10)
        )
        referrals = result.all()

        total_bonus = user.referral_bonus_days or 0
        total_count = await session.scalar(
            select(func.count(Referral.id)).where(Referral.referrer_id == user.id)
        ) or 0

    if referrals:
        referrals_list = []
        for ref, referred_user in referrals:
            username = f"@{referred_user.username}" if referred_user.username else "anonymous"
            date = ref.created_at.strftime('%d.%m.%Y') if ref.created_at else 'N/A'
            paid = "💳 оплатил" if ref.bonus_days > REFERRAL_REGISTRATION_BONUS_DAYS else "⏳ не оплатил"
            referrals_list.append(f"• {username} ({date}) — {paid}")

        text = (
            f"📊 <b>Ваши рефералы</b>\n\n"
            f"Всего приглашено: <b>{total_count}</b>\n"
            f"Бонусных дней: <b>{total_bonus}</b>\n\n"
            f"<b>Последние приглашённые:</b>\n" + "\n".join(referrals_list)
        )
    else:
        text = (
            f"📊 <b>Ваши рефералы</b>\n\n"
            f"Пока нет приглашённых друзей.\n\n"
            f"Поделитесь реферальной ссылкой и получайте:\n"
            f"• <b>+{REFERRAL_REGISTRATION_BONUS_DAYS} день</b> за регистрацию друга\n"
            f"• <b>+{REFERRAL_PAYMENT_BONUS_DAYS} дней</b> когда друг оплатит"
        )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Моя ссылка", callback_data="get_referral_link")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="sniper_menu")]
    ])

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

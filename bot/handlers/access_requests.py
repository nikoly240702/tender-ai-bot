"""
Обработчики для системы запросов на доступ к боту.
"""

from aiogram import Router, F
from aiogram.types import CallbackQuery
from bot.config import BotConfig
from bot.database.access_manager import AccessManager
import logging

logger = logging.getLogger(__name__)
router = Router()

# Инициализируем менеджер доступа
access_manager = AccessManager()


@router.callback_query(F.data.startswith("access_approve_"))
async def approve_access_request(callback: CallbackQuery):
    """
    Обрабатывает одобрение запроса на доступ.
    """
    # Проверяем что это админ
    if callback.from_user.id != BotConfig.ADMIN_USER_ID:
        await callback.answer("❌ У вас нет прав для этого действия", show_alert=True)
        return

    # Получаем user_id из callback_data
    user_id = int(callback.data.replace("access_approve_", ""))

    try:
        # Добавляем пользователя в список разрешенных
        access_manager.add_user(
            user_id=user_id,
            added_by=callback.from_user.id,
            notes="Одобрено через запрос доступа"
        )

        # Обновляем сообщение с запросом
        await callback.message.edit_text(
            f"{callback.message.text}\n\n✅ <b>Доступ одобрен</b>",
            parse_mode="HTML"
        )

        # Отправляем уведомление пользователю
        try:
            bot = callback.bot
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "✅ <b>Доступ одобрен!</b>\n\n"
                    "Ваш запрос на доступ к боту был одобрен.\n"
                    "Теперь вы можете пользоваться всеми функциями бота.\n\n"
                    "Используйте /start для начала работы."
                ),
                parse_mode="HTML"
            )
            logger.info(f"✅ Доступ одобрен для пользователя {user_id}")
        except Exception as e:
            logger.warning(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

        await callback.answer("✅ Доступ одобрен")

    except Exception as e:
        logger.error(f"Ошибка при одобрении доступа для {user_id}: {e}")
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@router.callback_query(F.data.startswith("access_reject_"))
async def reject_access_request(callback: CallbackQuery):
    """
    Обрабатывает отклонение запроса на доступ.
    """
    # Проверяем что это админ
    if callback.from_user.id != BotConfig.ADMIN_USER_ID:
        await callback.answer("❌ У вас нет прав для этого действия", show_alert=True)
        return

    # Получаем user_id из callback_data
    user_id = int(callback.data.replace("access_reject_", ""))

    try:
        # Обновляем сообщение с запросом
        await callback.message.edit_text(
            f"{callback.message.text}\n\n❌ <b>Доступ отклонен</b>",
            parse_mode="HTML"
        )

        # Отправляем уведомление пользователю
        try:
            bot = callback.bot
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "❌ <b>Доступ отклонен</b>\n\n"
                    "К сожалению, ваш запрос на доступ к боту был отклонен.\n"
                    "Если у вас есть вопросы, обратитесь к администратору."
                ),
                parse_mode="HTML"
            )
            logger.info(f"❌ Доступ отклонен для пользователя {user_id}")
        except Exception as e:
            logger.warning(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

        await callback.answer("❌ Доступ отклонен")

    except Exception as e:
        logger.error(f"Ошибка при отклонении доступа для {user_id}: {e}")
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)

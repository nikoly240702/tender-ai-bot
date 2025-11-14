"""
Обработчики админ-панели для управления доступом пользователей.
"""

import sys
from pathlib import Path

# Добавляем путь к корневой директории
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command

from bot.config import BotConfig
from bot.database.access_manager import AccessManager
from bot.keyboards import get_admin_panel_keyboard, get_user_management_keyboard
import logging

logger = logging.getLogger(__name__)
router = Router()

# Инициализируем менеджер доступа
access_manager = AccessManager()


def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором."""
    return BotConfig.ADMIN_USER_ID and user_id == BotConfig.ADMIN_USER_ID


class AddUserStates(StatesGroup):
    """Состояния для процесса добавления пользователя."""
    waiting_for_user_id = State()


class RemoveUserStates(StatesGroup):
    """Состояния для процесса удаления пользователя."""
    waiting_for_user_id = State()


@router.message(Command("admin"))
async def admin_panel(message: Message):
    """
    Открывает админ-панель.
    Доступна только администратору.
    """
    if not is_admin(message.from_user.id):
        await message.answer(
            "❌ У вас нет доступа к админ-панели.\n\n"
            f"Ваш User ID: `{message.from_user.id}`",
            parse_mode="Markdown"
        )
        return

    # Получаем статистику
    users_count = access_manager.get_users_count()
    access_mode = "Приватный" if BotConfig.ALLOWED_USERS is not None else "Открытый"

    await message.answer(
        f"🔐 <b>Админ-панель</b>\n\n"
        f"Режим доступа: <b>{access_mode}</b>\n"
        f"Пользователей с доступом: <b>{users_count}</b>\n\n"
        f"Выберите действие:",
        reply_markup=get_admin_panel_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "admin_list_users")
async def list_users(callback: CallbackQuery):
    """Показывает список пользователей с доступом."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    await callback.answer()

    users = access_manager.get_all_users()

    if not users:
        await callback.message.answer(
            "📋 <b>Список пользователей пуст</b>\n\n"
            "Пользователи с доступом не найдены.",
            parse_mode="HTML"
        )
        return

    # Формируем список пользователей
    text = "👥 <b>Пользователи с доступом:</b>\n\n"

    for i, user in enumerate(users, 1):
        user_id = user['user_id']
        username = f"@{user['username']}" if user['username'] else "нет username"
        first_name = user['first_name'] or ""
        last_name = user['last_name'] or ""
        full_name = f"{first_name} {last_name}".strip() or "Имя не указано"
        added_at = user['added_at'][:10]  # Только дата

        text += (
            f"{i}. <b>{full_name}</b> ({username})\n"
            f"   ID: <code>{user_id}</code>\n"
            f"   Добавлен: {added_at}\n\n"
        )

    # Разбиваем на несколько сообщений, если слишком длинное
    if len(text) > 4000:
        chunks = []
        current_chunk = "👥 <b>Пользователи с доступом:</b>\n\n"

        for i, user in enumerate(users, 1):
            user_text = (
                f"{i}. <b>{user['first_name'] or ''} {user['last_name'] or ''}</b> "
                f"(@{user['username'] or 'нет'})\n"
                f"   ID: <code>{user['user_id']}</code>\n"
                f"   Добавлен: {user['added_at'][:10]}\n\n"
            )

            if len(current_chunk) + len(user_text) > 4000:
                chunks.append(current_chunk)
                current_chunk = user_text
            else:
                current_chunk += user_text

        chunks.append(current_chunk)

        for chunk in chunks:
            await callback.message.answer(chunk, parse_mode="HTML")
    else:
        await callback.message.answer(text, parse_mode="HTML")


@router.callback_query(F.data == "admin_add_user")
async def start_add_user(callback: CallbackQuery, state: FSMContext):
    """Начинает процесс добавления пользователя."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    await callback.answer()

    await state.set_state(AddUserStates.waiting_for_user_id)

    await callback.message.answer(
        "➕ <b>Добавление пользователя</b>\n\n"
        "Отправьте Telegram User ID пользователя, которому нужно дать доступ.\n\n"
        "Пользователь может узнать свой ID, написав боту (бот покажет ID в сообщении об отказе в доступе).\n\n"
        "Отправьте /cancel для отмены.",
        parse_mode="HTML"
    )


@router.message(AddUserStates.waiting_for_user_id)
async def process_add_user(message: Message, state: FSMContext):
    """Обрабатывает добавление пользователя."""
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    # Проверяем команду отмены
    if message.text and message.text.startswith('/cancel'):
        await state.clear()
        await message.answer("❌ Добавление пользователя отменено.")
        return

    # Пытаемся извлечь User ID
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer(
            "❌ Неверный формат User ID. Отправьте числовой ID.\n\n"
            "Пример: 123456789"
        )
        return

    # Проверяем, не добавлен ли уже
    if access_manager.is_user_allowed(user_id):
        await message.answer(
            f"⚠️ Пользователь <code>{user_id}</code> уже имеет доступ.",
            parse_mode="HTML"
        )
        await state.clear()
        return

    # Добавляем пользователя
    success = access_manager.add_user(
        user_id=user_id,
        added_by=message.from_user.id,
        notes="Добавлен администратором через панель"
    )

    if success:
        await message.answer(
            f"✅ Пользователь <code>{user_id}</code> успешно добавлен!\n\n"
            f"Теперь он имеет доступ к боту.",
            parse_mode="HTML"
        )
        logger.info(f"Администратор {message.from_user.id} добавил пользователя {user_id}")
    else:
        await message.answer(
            f"❌ Не удалось добавить пользователя <code>{user_id}</code>.\n\n"
            f"Возможно, произошла ошибка базы данных.",
            parse_mode="HTML"
        )

    await state.clear()


@router.callback_query(F.data == "admin_remove_user")
async def start_remove_user(callback: CallbackQuery, state: FSMContext):
    """Начинает процесс удаления пользователя."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    await callback.answer()

    await state.set_state(RemoveUserStates.waiting_for_user_id)

    await callback.message.answer(
        "➖ <b>Удаление пользователя</b>\n\n"
        "Отправьте Telegram User ID пользователя, которому нужно ограничить доступ.\n\n"
        "Отправьте /cancel для отмены.",
        parse_mode="HTML"
    )


@router.message(RemoveUserStates.waiting_for_user_id)
async def process_remove_user(message: Message, state: FSMContext):
    """Обрабатывает удаление пользователя."""
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    # Проверяем команду отмены
    if message.text and message.text.startswith('/cancel'):
        await state.clear()
        await message.answer("❌ Удаление пользователя отменено.")
        return

    # Пытаемся извлечь User ID
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer(
            "❌ Неверный формат User ID. Отправьте числовой ID.\n\n"
            "Пример: 123456789"
        )
        return

    # Проверяем, не пытается ли админ удалить самого себя
    if user_id == message.from_user.id:
        await message.answer(
            "⚠️ Вы не можете удалить самого себя из списка доступа!",
            parse_mode="HTML"
        )
        await state.clear()
        return

    # Получаем информацию о пользователе
    user_info = access_manager.get_user_info(user_id)

    if not user_info:
        await message.answer(
            f"⚠️ Пользователь <code>{user_id}</code> не найден в списке доступа.",
            parse_mode="HTML"
        )
        await state.clear()
        return

    # Удаляем пользователя
    success = access_manager.remove_user(user_id)

    if success:
        username = f"@{user_info['username']}" if user_info['username'] else "нет username"
        await message.answer(
            f"✅ Пользователь <code>{user_id}</code> ({username}) успешно удален!\n\n"
            f"Доступ к боту ограничен.",
            parse_mode="HTML"
        )
        logger.info(f"Администратор {message.from_user.id} удалил пользователя {user_id}")
    else:
        await message.answer(
            f"❌ Не удалось удалить пользователя <code>{user_id}</code>.\n\n"
            f"Возможно, произошла ошибка базы данных.",
            parse_mode="HTML"
        )

    await state.clear()


@router.callback_query(F.data == "admin_back")
async def back_to_admin_panel(callback: CallbackQuery, state: FSMContext):
    """Возвращает в главное меню админ-панели."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    await callback.answer()
    await state.clear()

    # Получаем статистику
    users_count = access_manager.get_users_count()
    access_mode = "Приватный" if BotConfig.ALLOWED_USERS is not None else "Открытый"

    await callback.message.edit_text(
        f"🔐 <b>Админ-панель</b>\n\n"
        f"Режим доступа: <b>{access_mode}</b>\n"
        f"Пользователей с доступом: <b>{users_count}</b>\n\n"
        f"Выберите действие:",
        reply_markup=get_admin_panel_keyboard(),
        parse_mode="HTML"
    )


@router.message(Command("myid"))
async def show_my_id(message: Message):
    """
    Показывает User ID отправителя.
    Доступна всем пользователям.
    """
    await message.answer(
        f"🆔 Ваш Telegram User ID: <code>{message.from_user.id}</code>\n\n"
        f"Username: @{message.from_user.username or 'не указан'}\n"
        f"Имя: {message.from_user.first_name or 'не указано'}",
        parse_mode="HTML"
    )

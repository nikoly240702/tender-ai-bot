"""
Обработчик событий групповых чатов.

- Бот добавлен в группу → создаём SniperUser(is_group=True)
- Бот удалён из группы → деактивируем запись
- Утилита is_group_admin для проверки прав
"""

import logging
from aiogram import Router, F
from aiogram.types import ChatMemberUpdated
from aiogram.filters import ChatMemberUpdatedFilter, IS_NOT_MEMBER, IS_MEMBER, ADMINISTRATOR, CREATOR

logger = logging.getLogger(__name__)

router = Router(name="group_chat")


async def is_group_admin(bot, chat_id: int, user_id: int) -> bool:
    """
    Проверяет, является ли пользователь администратором или создателем группы.

    Args:
        bot: экземпляр Bot
        chat_id: ID группового чата
        user_id: Telegram ID пользователя

    Returns:
        True если admin/creator
    """
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ('administrator', 'creator')
    except Exception as e:
        logger.warning(f"Не удалось проверить статус пользователя {user_id} в чате {chat_id}: {e}")
        return False


@router.my_chat_member(
    ChatMemberUpdatedFilter(
        member_status_changed=IS_NOT_MEMBER >> (IS_MEMBER | ADMINISTRATOR | CREATOR)
    )
)
async def bot_added_to_group(event: ChatMemberUpdated):
    """Бот добавлен в группу — создаём запись SniperUser."""
    chat = event.chat
    from_user = event.from_user

    # Только для групп и супергрупп
    if chat.type not in ('group', 'supergroup'):
        return

    logger.info(f"🏢 Бот добавлен в группу '{chat.title}' (id={chat.id}) пользователем {from_user.id}")

    try:
        from database import DatabaseSession, SniperUser
        from sqlalchemy import select

        async with DatabaseSession() as session:
            # Проверяем, есть ли уже запись для этой группы
            existing = await session.scalar(
                select(SniperUser).where(SniperUser.telegram_id == chat.id)
            )

            if existing:
                # Реактивация
                existing.status = 'active'
                existing.is_group = True
                existing.group_admin_id = from_user.id
                existing.first_name = chat.title
                logger.info(f"♻️ Реактивирована группа '{chat.title}' (id={chat.id})")
            else:
                # Создаём новую запись
                group_user = SniperUser(
                    telegram_id=chat.id,
                    username=None,
                    first_name=chat.title,
                    last_name=None,
                    status='active',
                    is_group=True,
                    group_admin_id=from_user.id,
                    subscription_tier='trial',
                    filters_limit=3,
                    notifications_limit=20,
                )
                session.add(group_user)
                logger.info(f"✅ Создана запись для группы '{chat.title}' (id={chat.id})")

            await session.commit()

        # Отправляем welcome-сообщение
        admin_name = from_user.full_name or f"@{from_user.username}" or "Админ"
        await event.answer(
            f"👋 <b>Tender Sniper подключен к группе!</b>\n\n"
            f"Админ: {admin_name}\n\n"
            f"<b>Что умеет бот в группе:</b>\n"
            f"• 🎯 Автомониторинг тендеров по фильтрам\n"
            f"• 📊 Общая Google Sheets таблица\n"
            f"• 🔍 Поиск тендеров\n\n"
            f"<b>Управление:</b>\n"
            f"Создавать фильтры и менять настройки может только администратор группы.\n\n"
            f"Начните с /sniper для создания первого фильтра.",
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка при добавлении бота в группу {chat.id}: {e}", exc_info=True)


@router.my_chat_member(
    ChatMemberUpdatedFilter(
        member_status_changed=(IS_MEMBER | ADMINISTRATOR | CREATOR) >> IS_NOT_MEMBER
    )
)
async def bot_removed_from_group(event: ChatMemberUpdated):
    """Бот удалён из группы — деактивируем запись."""
    chat = event.chat

    if chat.type not in ('group', 'supergroup'):
        return

    logger.info(f"🚫 Бот удалён из группы '{chat.title}' (id={chat.id})")

    try:
        from database import DatabaseSession, SniperUser
        from sqlalchemy import select

        async with DatabaseSession() as session:
            group_user = await session.scalar(
                select(SniperUser).where(SniperUser.telegram_id == chat.id)
            )

            if group_user:
                group_user.status = 'inactive'
                await session.commit()
                logger.info(f"✅ Группа '{chat.title}' деактивирована")

    except Exception as e:
        logger.error(f"Ошибка при удалении бота из группы {chat.id}: {e}", exc_info=True)

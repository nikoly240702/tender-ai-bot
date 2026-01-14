#!/usr/bin/env python3
"""
Скрипт миграции пользователей с free на trial (7 дней).
Также отправляет рассылку всем пользователям.
"""

import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / '.env')

import aiohttp
from sqlalchemy import select, update

from database import DatabaseSession, SniperUser, BroadcastMessage, UserEvent


BROADCAST_MESSAGE = """📢 <b>Важное обновление Tender Sniper!</b>

Уважаемые пользователи!

Мы активно развиваем проект и добавляем новые функции. Для дальнейшего развития бот переходит на модель с ограниченным бесплатным периодом.

⏳ <b>Ваш бесплатный период: 7 дней</b>

После окончания бесплатного периода потребуется платная подписка для продолжения работы.

━━━━━━━━━━━━━━━━━━━━━

💡 <b>Как получить бесплатные дни:</b>

🎁 <b>Реферальная программа</b>
Приглашайте друзей по вашей реферальной ссылке и получайте <b>+7 дней</b> за каждого нового пользователя!
Ссылку можно получить в разделе «Подписка» → «Реферальная программа»

💬 <b>Помощь проекту</b>
За конструктивный фидбэк, идеи по улучшению или помощь в доработке бота мы также дарим бесплатную подписку.
Пишите разработчику: @nikolai_chizhik

━━━━━━━━━━━━━━━━━━━━━

Спасибо, что вы с нами! 🙏

<i>Нажмите кнопку ниже, чтобы обновить меню бота.</i>"""


async def migrate_free_users():
    """Переводим всех free пользователей на trial с 7 днями."""
    print("=" * 60)
    print("🔄 МИГРАЦИЯ FREE -> TRIAL (7 дней)")
    print("=" * 60)

    now = datetime.utcnow()
    expires_at = now + timedelta(days=7)

    async with DatabaseSession() as session:
        # Считаем сколько пользователей будет обновлено
        result = await session.execute(
            select(SniperUser).where(SniperUser.subscription_tier == 'free')
        )
        free_users = result.scalars().all()
        print(f"📊 Найдено free пользователей: {len(free_users)}")

        if not free_users:
            print("✅ Нет пользователей для миграции")
            return 0

        # Обновляем всех free -> trial
        await session.execute(
            update(SniperUser)
            .where(SniperUser.subscription_tier == 'free')
            .values(
                subscription_tier='trial',
                trial_expires_at=expires_at,
                filters_limit=3,
                notifications_limit=20
            )
        )
        await session.commit()

        print(f"✅ Обновлено пользователей: {len(free_users)}")
        print(f"📅 Дата окончания trial: {expires_at.strftime('%d.%m.%Y %H:%M')}")

        return len(free_users)


def get_reply_keyboard():
    """Возвращает постоянную клавиатуру бота."""
    return {
        "keyboard": [
            [{"text": "🏠 Главное меню"}, {"text": "⏸️ Пауза мониторинга"}],
            [{"text": "🎯 Tender Sniper"}, {"text": "📊 Мои фильтры"}],
            [{"text": "📊 Все мои тендеры"}],
            [{"text": "⭐ Избранное"}, {"text": "📈 Статистика"}]
        ],
        "resize_keyboard": True,
        "persistent": True
    }


async def send_broadcast_with_keyboard():
    """Отправляем рассылку всем пользователям с автообновлением клавиатуры."""
    print("\n" + "=" * 60)
    print("📢 РАССЫЛКА С АВТООБНОВЛЕНИЕМ КЛАВИАТУРЫ")
    print("=" * 60)

    bot_token = os.getenv('BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        print("❌ BOT_TOKEN не найден!")
        return 0, 0

    # Создаём запись о рассылке в БД
    broadcast_id = None
    async with DatabaseSession() as session:
        broadcast = BroadcastMessage(
            message_text=BROADCAST_MESSAGE[:500],  # Обрезаем для БД
            target_tier='all',
            sent_at=datetime.utcnow(),
            total_recipients=0,
            successful=0,
            failed=0,
            created_by='migration_script'
        )
        session.add(broadcast)
        await session.commit()
        await session.refresh(broadcast)
        broadcast_id = broadcast.id
        print(f"📝 Создана запись рассылки ID: {broadcast_id}")

    async with DatabaseSession() as session:
        result = await session.execute(
            select(SniperUser.id, SniperUser.telegram_id, SniperUser.username)
            .where(SniperUser.status == 'active')
        )
        users = result.all()

    total = len(users)
    print(f"📊 Всего активных пользователей: {total}")

    success = 0
    failed = 0

    # Собираем события для batch insert
    events_to_insert = []

    async with aiohttp.ClientSession() as http_session:
        for i, (user_id, telegram_id, username) in enumerate(users, 1):
            try:
                url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

                # Сначала отправляем сообщение с ReplyKeyboard (обновит клавиатуру автоматически)
                payload_keyboard = {
                    "chat_id": telegram_id,
                    "text": BROADCAST_MESSAGE,
                    "parse_mode": "HTML",
                    "reply_markup": get_reply_keyboard()
                }

                async with http_session.post(url, json=payload_keyboard, timeout=10) as resp:
                    if resp.status == 200:
                        # Теперь отправляем inline кнопки отдельным сообщением
                        payload_inline = {
                            "chat_id": telegram_id,
                            "text": "👇 <b>Быстрые действия:</b>",
                            "parse_mode": "HTML",
                            "reply_markup": {
                                "inline_keyboard": [
                                    [{"text": "📦 Тарифы и подписка", "callback_data": "sniper_subscription"}],
                                    [{"text": "🎁 Реферальная программа", "callback_data": "referral_menu"}]
                                ]
                            }
                        }
                        async with http_session.post(url, json=payload_inline, timeout=10) as resp2:
                            pass  # Игнорируем ошибки второго сообщения

                        success += 1

                        # Записываем событие доставки
                        events_to_insert.append(UserEvent(
                            user_id=user_id,
                            telegram_id=telegram_id,
                            event_type='broadcast_delivered',
                            broadcast_id=broadcast_id,
                            created_at=datetime.utcnow()
                        ))
                    else:
                        failed += 1
                        resp_data = await resp.json()
                        error_desc = resp_data.get('description', 'Unknown error')

                        # Записываем событие ошибки
                        events_to_insert.append(UserEvent(
                            user_id=user_id,
                            telegram_id=telegram_id,
                            event_type='broadcast_failed',
                            broadcast_id=broadcast_id,
                            event_data={'error': error_desc},
                            created_at=datetime.utcnow()
                        ))

                        # Если пользователь заблокировал бота - обновляем статус
                        if 'blocked' in error_desc.lower() or 'deactivated' in error_desc.lower():
                            async with DatabaseSession() as session:
                                await session.execute(
                                    update(SniperUser)
                                    .where(SniperUser.id == user_id)
                                    .values(status='blocked')
                                )
                                await session.commit()
                        else:
                            print(f"  ⚠️ {username or telegram_id}: {error_desc}")

                # Прогресс каждые 10 пользователей
                if i % 10 == 0:
                    print(f"  📤 Прогресс: {i}/{total} (✅ {success} / ❌ {failed})")

                await asyncio.sleep(0.05)  # Rate limiting

            except Exception as e:
                failed += 1
                print(f"  ❌ Ошибка для {username or telegram_id}: {e}")

    # Сохраняем все события в БД
    print(f"\n💾 Сохранение {len(events_to_insert)} событий в БД...")
    async with DatabaseSession() as session:
        session.add_all(events_to_insert)
        await session.commit()

    # Обновляем статистику рассылки
    async with DatabaseSession() as session:
        await session.execute(
            update(BroadcastMessage)
            .where(BroadcastMessage.id == broadcast_id)
            .values(
                total_recipients=total,
                successful=success,
                failed=failed
            )
        )
        await session.commit()

    print(f"\n✅ Рассылка завершена!")
    print(f"   Успешно: {success}")
    print(f"   Ошибок: {failed}")

    return success, failed


async def main():
    print("\n" + "=" * 60)
    print("🚀 ЗАПУСК МИГРАЦИИ И РАССЫЛКИ")
    print("=" * 60 + "\n")

    # 1. Миграция
    migrated = await migrate_free_users()

    # 2. Рассылка
    success, failed = await send_broadcast_with_keyboard()

    print("\n" + "=" * 60)
    print("📊 ИТОГИ")
    print("=" * 60)
    print(f"  Мигрировано free -> trial: {migrated}")
    print(f"  Рассылка успешно: {success}")
    print(f"  Рассылка ошибок: {failed}")
    print("=" * 60 + "\n")


if __name__ == '__main__':
    asyncio.run(main())

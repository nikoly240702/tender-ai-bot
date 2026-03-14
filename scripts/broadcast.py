"""
Универсальный скрипт для рассылок с возможностью удаления.

Использование:
    # Отправить всем, сохранить ID сообщений в файл:
    python scripts/broadcast.py --send --message "Текст сообщения" --save sent_ids.json

    # Удалить ранее отправленные сообщения:
    python scripts/broadcast.py --delete --load sent_ids.json

    # Тест на конкретном пользователе:
    python scripts/broadcast.py --send --message "Текст" --test-user 298437198
"""

import asyncio
import sys
import os
import argparse
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest, TelegramNotFound


async def get_all_users() -> list[dict]:
    from database import init_database, DatabaseSession
    from sqlalchemy import text
    await init_database()
    async with DatabaseSession() as session:
        result = await session.execute(text(
            "SELECT telegram_id, username, first_name FROM sniper_users "
            "WHERE status IS NULL OR status != 'banned'"
        ))
        return [{'telegram_id': r[0], 'username': r[1], 'first_name': r[2]} for r in result if r[0]]


async def do_send(bot: Bot, user_ids: list[int], message: str) -> dict:
    """Отправляет сообщение и возвращает {telegram_id: message_id}."""
    sent = {}
    success, blocked, failed = 0, 0, 0

    for uid in user_ids:
        try:
            msg = await bot.send_message(chat_id=uid, text=message, parse_mode='HTML')
            sent[str(uid)] = msg.message_id
            print(f"  ✅ {uid}")
            success += 1
        except TelegramForbiddenError:
            print(f"  ⚠️  {uid} — заблокировал бота")
            blocked += 1
        except Exception as e:
            print(f"  ❌ {uid} — {e}")
            failed += 1
        await asyncio.sleep(0.05)

    print(f"\n📊 Итого: ✅ {success} отправлено, ⚠️ {blocked} заблокировали, ❌ {failed} ошибок")
    return sent


async def do_delete(bot: Bot, sent: dict):
    """Удаляет сообщения по словарю {telegram_id: message_id}."""
    success, failed = 0, 0

    for uid_str, msg_id in sent.items():
        uid = int(uid_str)
        try:
            await bot.delete_message(chat_id=uid, message_id=msg_id)
            print(f"  🗑  {uid} — удалено (msg_id={msg_id})")
            success += 1
        except TelegramNotFound:
            print(f"  ⚠️  {uid} — сообщение уже удалено или не найдено")
            failed += 1
        except Exception as e:
            print(f"  ❌ {uid} — {e}")
            failed += 1
        await asyncio.sleep(0.05)

    print(f"\n📊 Удалено: ✅ {success}, ❌ {failed}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--send', action='store_true', help='Отправить сообщение')
    parser.add_argument('--delete', action='store_true', help='Удалить ранее отправленные сообщения')
    parser.add_argument('--message', type=str, help='Текст сообщения (HTML)')
    parser.add_argument('--message-file', type=str, help='Файл с текстом сообщения')
    parser.add_argument('--save', type=str, default='broadcast_sent.json', help='Файл для сохранения message_id')
    parser.add_argument('--load', type=str, default='broadcast_sent.json', help='Файл с message_id для удаления')
    parser.add_argument('--test-user', type=int, help='Отправить только этому пользователю')
    args = parser.parse_args()

    bot_token = os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('BOT_TOKEN')
    bot = Bot(token=bot_token)

    try:
        if args.send:
            # Получаем текст
            if args.message_file:
                text_msg = Path(args.message_file).read_text()
            elif args.message:
                text_msg = args.message
            else:
                print("❌ Укажите --message или --message-file")
                return

            if args.test_user:
                user_ids = [args.test_user]
            else:
                print("📋 Получаем список пользователей...")
                users = await get_all_users()
                user_ids = [u['telegram_id'] for u in users]
                print(f"   Найдено {len(user_ids)} пользователей\n")

            sent = await do_send(bot, user_ids, text_msg)

            # Сохраняем message_id
            save_path = args.save
            save_data = {
                'sent_at': datetime.now().isoformat(),
                'message_preview': text_msg[:100],
                'messages': sent
            }
            Path(save_path).write_text(json.dumps(save_data, ensure_ascii=False, indent=2))
            print(f"\n💾 ID сообщений сохранены в {save_path}")

        elif args.delete:
            load_path = args.load
            if not Path(load_path).exists():
                print(f"❌ Файл {load_path} не найден")
                return

            data = json.loads(Path(load_path).read_text())
            sent = data.get('messages', {})
            print(f"🗑  Удаляем {len(sent)} сообщений (отправлены {data.get('sent_at', '?')[:16]})...")
            print(f"   Текст: {data.get('message_preview', '?')}\n")

            await do_delete(bot, sent)

            # Переименовываем файл чтобы не удалить повторно
            done_path = load_path.replace('.json', '_deleted.json')
            Path(load_path).rename(done_path)
            print(f"\n💾 Лог перемещён в {done_path}")

        else:
            parser.print_help()

    finally:
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(main())

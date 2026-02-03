#!/usr/bin/env python3
"""
Скрипт для генерации AI intent для существующих фильтров.

Запуск:
    python scripts/generate_ai_intents.py

Требования:
    - DATABASE_URL в переменных окружения
    - OPENAI_API_KEY в переменных окружения
"""

import asyncio
import os
import sys
from pathlib import Path

# Добавляем корень проекта в path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()


async def main():
    print("🚀 Запуск генерации AI intent для существующих фильтров...")

    # Проверяем переменные окружения
    database_url = os.getenv('DATABASE_URL')
    openai_key = os.getenv('OPENAI_API_KEY')

    if not database_url:
        print("❌ DATABASE_URL не установлен")
        sys.exit(1)

    if not openai_key:
        print("⚠️ OPENAI_API_KEY не установлен, генерация будет использовать fallback")

    # Инициализируем БД
    from tender_sniper.database.sqlalchemy_adapter import TenderSniperDB

    db = TenderSniperDB()

    # Запускаем генерацию
    from tender_sniper.jobs.generate_intents_job import generate_intents_for_existing_filters

    stats = await generate_intents_for_existing_filters(
        db_adapter=db,
        batch_size=5,  # Небольшие батчи для rate limit
        delay_between_batches=2.0  # 2 секунды между батчами
    )

    print(f"\n📊 Результат:")
    print(f"   Обработано: {stats.get('processed', 0)}")
    print(f"   Успешно: {stats.get('success', 0)}")
    print(f"   Ошибок: {stats.get('errors', 0)}")


if __name__ == '__main__':
    asyncio.run(main())

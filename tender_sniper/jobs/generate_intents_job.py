"""
Background job для генерации AI intent для существующих фильтров.

Запускается один раз после миграции, затем по расписанию для новых фильтров.
"""

import asyncio
import logging
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)


async def generate_intents_for_existing_filters(
    db_adapter,
    batch_size: int = 10,
    delay_between_batches: float = 1.0
) -> dict:
    """
    Генерирует AI intent для всех фильтров, у которых его нет.

    Args:
        db_adapter: Адаптер базы данных (SQLAlchemyAdapter)
        batch_size: Размер батча для обработки
        delay_between_batches: Задержка между батчами (секунды)

    Returns:
        Статистика: {'processed': int, 'success': int, 'errors': int}
    """
    from tender_sniper.ai_relevance_checker import get_relevance_checker

    checker = get_relevance_checker()

    stats = {
        'processed': 0,
        'success': 0,
        'errors': 0,
        'started_at': datetime.now().isoformat()
    }

    logger.info("🚀 Запуск генерации AI intent для существующих фильтров...")

    try:
        # Получаем все фильтры без ai_intent
        filters_without_intent = await db_adapter.get_filters_without_intent(limit=1000)

        if not filters_without_intent:
            logger.info("✅ Все фильтры уже имеют AI intent")
            return stats

        logger.info(f"📋 Найдено фильтров без intent: {len(filters_without_intent)}")

        # Обрабатываем батчами
        for i in range(0, len(filters_without_intent), batch_size):
            batch = filters_without_intent[i:i + batch_size]

            for filter_data in batch:
                try:
                    filter_id = filter_data['id']
                    filter_name = filter_data.get('name', 'Без названия')
                    keywords = filter_data.get('keywords', [])
                    exclude_keywords = filter_data.get('exclude_keywords', [])

                    if not keywords:
                        logger.warning(f"   ⚠️ Фильтр {filter_id} без ключевых слов, пропускаем")
                        stats['errors'] += 1
                        continue

                    # Генерируем intent
                    intent = await checker.generate_filter_intent(
                        filter_name=filter_name,
                        keywords=keywords,
                        exclude_keywords=exclude_keywords
                    )

                    # Сохраняем в БД
                    await db_adapter.update_filter_intent(filter_id, intent)

                    stats['success'] += 1
                    logger.info(f"   ✅ [{stats['success']}/{len(filters_without_intent)}] "
                               f"Фильтр '{filter_name}' (ID: {filter_id})")

                except Exception as e:
                    stats['errors'] += 1
                    logger.error(f"   ❌ Ошибка для фильтра {filter_data.get('id')}: {e}")

                stats['processed'] += 1

            # Задержка между батчами
            if i + batch_size < len(filters_without_intent):
                await asyncio.sleep(delay_between_batches)

    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)

    stats['finished_at'] = datetime.now().isoformat()
    logger.info(f"✅ Генерация завершена: {stats}")

    return stats


async def generate_intent_for_filter(
    db_adapter,
    filter_id: int
) -> Optional[str]:
    """
    Генерирует AI intent для конкретного фильтра.

    Вызывается при создании/обновлении фильтра.

    Args:
        db_adapter: Адаптер базы данных
        filter_id: ID фильтра

    Returns:
        Сгенерированный intent или None при ошибке
    """
    from tender_sniper.ai_relevance_checker import get_relevance_checker

    checker = get_relevance_checker()

    try:
        # Получаем данные фильтра
        filter_data = await db_adapter.get_filter_by_id(filter_id)

        if not filter_data:
            logger.error(f"❌ Фильтр {filter_id} не найден")
            return None

        filter_name = filter_data.get('name', 'Без названия')
        keywords = filter_data.get('keywords', [])
        exclude_keywords = filter_data.get('exclude_keywords', [])

        if not keywords:
            logger.warning(f"⚠️ Фильтр {filter_id} без ключевых слов")
            return None

        # Генерируем intent
        intent = await checker.generate_filter_intent(
            filter_name=filter_name,
            keywords=keywords,
            exclude_keywords=exclude_keywords
        )

        # Сохраняем в БД
        await db_adapter.update_filter_intent(filter_id, intent)

        logger.info(f"✅ Intent сгенерирован для фильтра '{filter_name}' (ID: {filter_id})")
        return intent

    except Exception as e:
        logger.error(f"❌ Ошибка генерации intent для фильтра {filter_id}: {e}")
        return None


# CLI для ручного запуска
if __name__ == '__main__':
    import sys
    sys.path.insert(0, '/Users/nikolaichizhik/Desktop/tender-ai-bot-fresh')

    async def main():
        from tender_sniper.database.sqlalchemy_adapter import SQLAlchemyAdapter
        import os

        database_url = os.getenv('DATABASE_URL')
        if not database_url:
            print("❌ DATABASE_URL не установлен")
            return

        adapter = SQLAlchemyAdapter(database_url)
        await adapter.init()

        stats = await generate_intents_for_existing_filters(adapter)
        print(f"\n📊 Результат: {stats}")

        await adapter.close()

    asyncio.run(main())

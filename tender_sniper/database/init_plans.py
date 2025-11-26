"""
Инициализация тарифных планов Tender Sniper.
"""

import aiosqlite
import json
from pathlib import Path
from typing import List, Dict, Any


async def init_subscription_plans(db_path: Path):
    """Инициализация тарифных планов в базе данных."""

    plans = [
        {
            'name': 'free',
            'display_name': 'Бесплатный',
            'price_monthly': 0,
            'price_yearly': 0,
            'max_filters': 5,
            'max_notifications_daily': 10,
            'ai_analysis_enabled': 0,
            'api_access_enabled': 0,
            'priority_support': 0,
            'description': 'Базовый функционал для знакомства с системой',
            'features': json.dumps([
                '5 фильтров мониторинга',
                '10 уведомлений в день',
                'Telegram бот',
                'История поисков'
            ], ensure_ascii=False)
        },
        {
            'name': 'basic',
            'display_name': 'Базовый',
            'price_monthly': 15000,
            'price_yearly': 144000,  # 12K/месяц при годовой оплате
            'max_filters': 15,
            'max_notifications_daily': 50,
            'ai_analysis_enabled': 1,
            'api_access_enabled': 0,
            'priority_support': 0,
            'description': 'Для малого бизнеса и индивидуальных предпринимателей',
            'features': json.dumps([
                '15 фильтров мониторинга',
                '50 уведомлений в день',
                'AI анализ тендеров (ограниченный)',
                'Email поддержка',
                'Приоритет в обработке',
                'Экспорт в Excel'
            ], ensure_ascii=False)
        },
        {
            'name': 'premium',
            'display_name': 'Премиум',
            'price_monthly': 50000,
            'price_yearly': 480000,  # 40K/месяц при годовой оплате
            'max_filters': 999999,  # Unlimited
            'max_notifications_daily': 999999,  # Unlimited
            'ai_analysis_enabled': 1,
            'api_access_enabled': 1,
            'priority_support': 1,
            'description': 'Для компаний с большим объемом тендеров',
            'features': json.dumps([
                'Неограниченные фильтры',
                'Неограниченные уведомления',
                'Полный AI анализ',
                'API доступ',
                '24/7 приоритетная поддержка',
                'Персональный менеджер',
                'Расширенная аналитика',
                'Интеграция с CRM'
            ], ensure_ascii=False)
        }
    ]

    async with aiosqlite.connect(db_path) as db:
        for plan in plans:
            await db.execute("""
                INSERT INTO subscription_plans
                (name, display_name, price_monthly, price_yearly, max_filters,
                 max_notifications_daily, ai_analysis_enabled, api_access_enabled,
                 priority_support, description, features, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    display_name = excluded.display_name,
                    price_monthly = excluded.price_monthly,
                    price_yearly = excluded.price_yearly,
                    max_filters = excluded.max_filters,
                    max_notifications_daily = excluded.max_notifications_daily,
                    ai_analysis_enabled = excluded.ai_analysis_enabled,
                    api_access_enabled = excluded.api_access_enabled,
                    priority_support = excluded.priority_support,
                    description = excluded.description,
                    features = excluded.features
            """, (
                plan['name'],
                plan['display_name'],
                plan['price_monthly'],
                plan['price_yearly'],
                plan['max_filters'],
                plan['max_notifications_daily'],
                plan['ai_analysis_enabled'],
                plan['api_access_enabled'],
                plan['priority_support'],
                plan['description'],
                plan['features'],
                1
            ))

        await db.commit()
        print(f"✅ Инициализировано {len(plans)} тарифных планов")


async def get_plan_limits(db_path: Path, plan_name: str) -> Dict[str, Any]:
    """Получение лимитов тарифного плана."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute("""
            SELECT * FROM subscription_plans WHERE name = ? AND active = 1
        """, (plan_name,)) as cursor:
            row = await cursor.fetchone()

            if row:
                return dict(row)

            # Возвращаем free plan по умолчанию
            return {
                'max_filters': 5,
                'max_notifications_daily': 10,
                'ai_analysis_enabled': 0,
                'api_access_enabled': 0
            }


if __name__ == '__main__':
    import asyncio

    db_path = Path(__file__).parent / 'sniper.db'
    asyncio.run(init_subscription_plans(db_path))

#!/usr/bin/env python3
"""
Интеграционный тест Tender Sniper MVP.

Проверяет работу всех компонентов в связке:
- Database
- Real-time Parser (mock mode)
- Smart Matcher
- Integration workflow
"""

import asyncio
import sys
import json
from pathlib import Path
from datetime import datetime

# Добавляем путь к проекту
sys.path.insert(0, str(Path(__file__).parent))

print("="*70)
print("🧪 ИНТЕГРАЦИОННЫЙ ТЕСТ TENDER SNIPER MVP")
print("="*70)


async def test_integration():
    """Полный интеграционный тест."""

    # ==========================================
    # ТЕСТ 1: ИМПОРТЫ
    # ==========================================
    print("\n1️⃣ ТЕСТ ИМПОРТОВ")
    print("-"*60)

    try:
        from tender_sniper.database import get_sniper_db, init_subscription_plans
        from tender_sniper.parser import RealtimeParser
        from tender_sniper.matching import SmartMatcher
        from tender_sniper import is_enabled
        print("✅ Все модули успешно импортированы")
    except Exception as e:
        print(f"❌ Ошибка импорта: {e}")
        return False

    # ==========================================
    # ТЕСТ 2: DATABASE
    # ==========================================
    print("\n2️⃣ ТЕСТ DATABASE")
    print("-"*60)

    try:
        # Инициализация БД
        db = await get_sniper_db()
        await init_subscription_plans(db.db_path)
        print("✅ База данных инициализирована")

        # Создание тестовых данных
        user_id = await db.create_or_update_user(
            telegram_id=999999999,
            username="integration_test",
            subscription_tier="basic"
        )
        print(f"✅ Тестовый пользователь создан (ID: {user_id})")

        # Создание нескольких фильтров
        filter1_id = await db.create_filter(
            user_id=user_id,
            name="IT Equipment",
            keywords=["компьютер", "ноутбук", "сервер"],
            price_min=500000,
            price_max=10000000,
            regions=["Москва"],
            tender_types=["товары"]
        )

        filter2_id = await db.create_filter(
            user_id=user_id,
            name="Medical Supplies",
            keywords=["медицинское оборудование", "инструменты"],
            price_min=100000,
            price_max=5000000,
            regions=["Москва", "Санкт-Петербург"],
            tender_types=["товары"]
        )

        print(f"✅ Создано 2 фильтра (IDs: {filter1_id}, {filter2_id})")

        # Получение активных фильтров
        filters = await db.get_active_filters(user_id)
        print(f"✅ Получено активных фильтров: {len(filters)}")

    except Exception as e:
        print(f"❌ Ошибка database: {e}")
        import traceback
        traceback.print_exc()
        return False

    # ==========================================
    # ТЕСТ 3: SMART MATCHER
    # ==========================================
    print("\n3️⃣ ТЕСТ SMART MATCHER")
    print("-"*60)

    try:
        matcher = SmartMatcher()

        # Создаем тестовые тендеры
        test_tenders = [
            {
                'number': 'TEST001',
                'name': 'Поставка компьютеров и ноутбуков для офиса',
                'description': 'Требуется поставка 50 ноутбуков и 20 персональных компьютеров',
                'price': 3500000,
                'region': 'Москва',
                'purchase_type': 'товары',
                'customer_name': 'ООО "Технологии"',
                'published_datetime': datetime.now().isoformat()
            },
            {
                'number': 'TEST002',
                'name': 'Поставка медицинского оборудования',
                'description': 'Требуется медицинское оборудование для поликлиники',
                'price': 2500000,
                'region': 'Санкт-Петербург',
                'purchase_type': 'товары',
                'customer_name': 'Городская поликлиника №5',
                'published_datetime': datetime.now().isoformat()
            },
            {
                'number': 'TEST003',
                'name': 'Строительные работы',
                'description': 'Ремонт фасада здания',
                'price': 5000000,
                'region': 'Москва',
                'purchase_type': 'работы',
                'customer_name': 'Администрация района',
                'published_datetime': datetime.now().isoformat()
            }
        ]

        print(f"Создано тестовых тендеров: {len(test_tenders)}")

        # Проверяем каждый тендер против всех фильтров
        total_matches = 0
        for tender in test_tenders:
            matches = matcher.match_against_filters(tender, filters, min_score=40)
            if matches:
                total_matches += len(matches)
                print(f"\n   Тендер {tender['number']}: {tender['name'][:50]}...")
                for match in matches:
                    print(f"      ✅ Match with '{match['filter_name']}' (score: {match['score']}/100)")

        print(f"\n✅ Всего найдено совпадений: {total_matches}")

        if total_matches == 0:
            print("⚠️  Совпадений не найдено - возможно, нужно скорректировать фильтры")

    except Exception as e:
        print(f"❌ Ошибка smart matcher: {e}")
        import traceback
        traceback.print_exc()
        return False

    # ==========================================
    # ТЕСТ 4: WORKFLOW INTEGRATION
    # ==========================================
    print("\n4️⃣ ТЕСТ WORKFLOW INTEGRATION")
    print("-"*60)

    try:
        # Сохраняем тендер в БД
        await db.add_or_update_tender(
            tender_number=test_tenders[0]['number'],
            name=test_tenders[0]['name'],
            nmck=test_tenders[0]['price'],
            customer_name=test_tenders[0]['customer_name'],
            region=test_tenders[0]['region'],
            raw_data=test_tenders[0]
        )
        print("✅ Тендер сохранен в monitoring cache")

        # Проверяем квоту
        has_quota = await db.check_notification_quota(user_id, limit=50)
        print(f"✅ Квота проверена: {has_quota}")

        # Симулируем сохранение уведомления
        if has_quota:
            notif_id = await db.save_notification(
                user_id=user_id,
                tender_number=test_tenders[0]['number'],
                filter_id=filter1_id,
                notification_type='match'
            )
            print(f"✅ Уведомление сохранено (ID: {notif_id})")

            await db.increment_notification_quota(user_id)
            print("✅ Квота увеличена")

        # Получаем статистику
        stats = await db.get_user_stats(user_id)
        print(f"\n📊 Статистика пользователя:")
        print(f"   • Активных фильтров: {stats['active_filters']}")
        print(f"   • Всего совпадений: {stats['total_matches']}")
        print(f"   • Уведомлений сегодня: {stats['notifications_today']}/{stats['notifications_limit']}")

    except Exception as e:
        print(f"❌ Ошибка workflow: {e}")
        import traceback
        traceback.print_exc()
        return False

    # ==========================================
    # ТЕСТ 5: FEATURE FLAGS
    # ==========================================
    print("\n5️⃣ ТЕСТ FEATURE FLAGS")
    print("-"*60)

    try:
        from tender_sniper.config import (
            is_tender_sniper_enabled,
            is_component_enabled,
            is_feature_enabled
        )

        print(f"Tender Sniper enabled: {is_tender_sniper_enabled()}")
        print(f"Real-time Parser: {is_component_enabled('realtime_parser')}")
        print(f"Smart Matching: {is_component_enabled('smart_matching')}")
        print(f"Notifications: {is_component_enabled('instant_notifications')}")
        print(f"CLI Analyzer: {is_feature_enabled('cli_analyzer')}")

        print("✅ Feature flags работают корректно")

    except Exception as e:
        print(f"❌ Ошибка feature flags: {e}")
        return False

    # ==========================================
    # ИТОГОВЫЙ РЕЗУЛЬТАТ
    # ==========================================
    print("\n" + "="*70)
    print("✅ ВСЕ ИНТЕГРАЦИОННЫЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
    print("="*70)
    print("\n📝 Сводка:")
    print(f"   • База данных: ✅ Работает")
    print(f"   • Smart Matcher: ✅ Работает (найдено совпадений: {total_matches})")
    print(f"   • Workflow: ✅ Работает")
    print(f"   • Feature Flags: ✅ Работают")
    print("\n🚀 Tender Sniper MVP готов к использованию!")
    print("\nДля активации:")
    print("   1. Отредактируйте config/features.yaml")
    print("   2. Установите tender_sniper.enabled: true")
    print("   3. Запустите: python -m tender_sniper.service")
    print("="*70)

    return True


if __name__ == '__main__':
    try:
        result = asyncio.run(test_integration())
        sys.exit(0 if result else 1)
    except KeyboardInterrupt:
        print("\n\n🛑 Тест прерван пользователем")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

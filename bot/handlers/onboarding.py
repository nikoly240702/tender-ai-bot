"""
Улучшенный онбординг для новых пользователей Tender Sniper.

Включает:
- Быстрые шаблоны фильтров по нишам
- Тестовое уведомление после создания фильтра
- Follow-up сообщения (День 1, День 3)
- Статистика "Сэкономлено времени"
"""

import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from tender_sniper.database import get_sniper_db

logger = logging.getLogger(__name__)
router = Router()


# ============================================
# БЫСТРЫЕ ШАБЛОНЫ ФИЛЬТРОВ
# ============================================

FILTER_TEMPLATES = {
    "it": {
        "name": "IT и компьютеры",
        "emoji": "💻",
        "description": "Компьютерная техника, ПО, IT-услуги",
        "keywords": ["компьютер", "ноутбук", "сервер", "программное обеспечение", "IT", "информационные технологии"],
        "price_min": 100000,
        "price_max": 10000000,
    },
    "construction": {
        "name": "Строительство",
        "emoji": "🏗️",
        "description": "Строительные работы, материалы, ремонт",
        "keywords": ["строительство", "ремонт", "строительные работы", "капитальный ремонт", "реконструкция"],
        "price_min": 500000,
        "price_max": 50000000,
    },
    "office": {
        "name": "Канцелярия",
        "emoji": "📎",
        "description": "Канцтовары, бумага, офисные принадлежности",
        "keywords": ["канцелярские товары", "бумага", "канцтовары", "офисные принадлежности"],
        "price_min": 50000,
        "price_max": 2000000,
    },
    "food": {
        "name": "Продукты питания",
        "emoji": "🍎",
        "description": "Продовольствие, питание, кейтеринг",
        "keywords": ["продукты питания", "продовольствие", "питание", "пищевые продукты"],
        "price_min": 100000,
        "price_max": 5000000,
    },
    "cleaning": {
        "name": "Клининг",
        "emoji": "🧹",
        "description": "Уборка, клининговые услуги",
        "keywords": ["уборка", "клининг", "клининговые услуги", "содержание помещений"],
        "price_min": 100000,
        "price_max": 5000000,
    },
    "security": {
        "name": "Охрана",
        "emoji": "🔒",
        "description": "Охранные услуги, безопасность",
        "keywords": ["охрана", "охранные услуги", "безопасность", "пропускной режим"],
        "price_min": 200000,
        "price_max": 10000000,
    },
    "medical": {
        "name": "Медицина",
        "emoji": "🏥",
        "description": "Медоборудование, медикаменты, медуслуги",
        "keywords": ["медицинское оборудование", "медикаменты", "лекарственные средства", "медицинские изделия"],
        "price_min": 100000,
        "price_max": 20000000,
    },
    "furniture": {
        "name": "Мебель",
        "emoji": "🪑",
        "description": "Офисная и специальная мебель",
        "keywords": ["мебель", "офисная мебель", "мебель для школ", "учебная мебель"],
        "price_min": 100000,
        "price_max": 5000000,
    },
}

# Пример тендера для тестового уведомления
SAMPLE_TENDER = {
    "number": "0373100012324000015",
    "name": "Поставка компьютерного оборудования для нужд учреждения",
    "customer": "ГБОУ Школа №1234",
    "price": 2850000,
    "region": "Москва",
    "deadline": (datetime.now() + timedelta(days=7)).strftime("%d.%m.%Y"),
    "law_type": "44-ФЗ",
    "relevance_score": 87,
}


class OnboardingStates(StatesGroup):
    """Состояния онбординга."""
    welcome = State()
    select_template = State()
    confirm_template = State()
    completed = State()


# ============================================
# KEYBOARDS
# ============================================

def get_welcome_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура приветствия."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Быстрый старт (2 мин)", callback_data="onboarding_quickstart")],
        [InlineKeyboardButton(text="🎯 Создать фильтр вручную", callback_data="onboarding_manual")],
        [InlineKeyboardButton(text="📖 Подробнее о боте", callback_data="onboarding_about")],
    ])


def get_templates_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора шаблона."""
    builder = InlineKeyboardBuilder()

    # Добавляем шаблоны по 2 в ряд
    templates_list = list(FILTER_TEMPLATES.items())
    for i in range(0, len(templates_list), 2):
        row = []
        for key, template in templates_list[i:i+2]:
            row.append(InlineKeyboardButton(
                text=f"{template['emoji']} {template['name']}",
                callback_data=f"template_{key}"
            ))
        builder.row(*row)

    builder.row(InlineKeyboardButton(text="🎯 Своя ниша", callback_data="onboarding_manual"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="onboarding_back"))

    return builder.as_markup()


def get_confirm_template_keyboard(template_key: str) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения шаблона."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Создать фильтр", callback_data=f"confirm_template_{template_key}")],
        [InlineKeyboardButton(text="✏️ Изменить параметры", callback_data=f"edit_template_{template_key}")],
        [InlineKeyboardButton(text="◀️ Выбрать другую нишу", callback_data="onboarding_quickstart")],
    ])


# ============================================
# HANDLERS
# ============================================

async def start_onboarding(message: Message, state: FSMContext):
    """Запуск онбординга для нового пользователя."""
    logger.info(f"🎯 Запуск онбординга для пользователя {message.from_user.id}")

    await state.set_state(OnboardingStates.welcome)

    text = """
👋 <b>Добро пожаловать в Tender Sniper!</b>

Я помогу вам находить тендеры на zakupki.gov.ru автоматически.

<b>Как это работает:</b>
1️⃣ Вы создаёте фильтр с критериями
2️⃣ Бот мониторит 15,000+ тендеров ежедневно
3️⃣ Получаете уведомления о подходящих

🎁 <b>У вас 7 дней бесплатного доступа!</b>

Выберите как начать:
"""

    await message.answer(text, reply_markup=get_welcome_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "onboarding_quickstart")
async def callback_quickstart(callback: CallbackQuery, state: FSMContext):
    """Быстрый старт - выбор ниши."""
    await callback.answer()
    await state.set_state(OnboardingStates.select_template)

    text = """
🚀 <b>Быстрый старт</b>

Выберите вашу нишу, и я создам готовый фильтр с оптимальными настройками:

<i>Вы сможете изменить параметры позже</i>
"""

    await callback.message.edit_text(text, reply_markup=get_templates_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "onboarding_back")
async def callback_back_to_welcome(callback: CallbackQuery, state: FSMContext):
    """Возврат к приветствию."""
    await callback.answer()
    await state.set_state(OnboardingStates.welcome)

    text = """
👋 <b>Добро пожаловать в Tender Sniper!</b>

Я помогу вам находить тендеры на zakupki.gov.ru автоматически.

<b>Как это работает:</b>
1️⃣ Вы создаёте фильтр с критериями
2️⃣ Бот мониторит 15,000+ тендеров ежедневно
3️⃣ Получаете уведомления о подходящих

🎁 <b>У вас 7 дней бесплатного доступа!</b>

Выберите как начать:
"""

    await callback.message.edit_text(text, reply_markup=get_welcome_keyboard(), parse_mode="HTML")


@router.callback_query(F.data.startswith("template_"))
async def callback_select_template(callback: CallbackQuery, state: FSMContext):
    """Выбор шаблона - показать детали."""
    await callback.answer()

    template_key = callback.data.replace("template_", "")
    template = FILTER_TEMPLATES.get(template_key)

    if not template:
        await callback.message.answer("❌ Шаблон не найден")
        return

    await state.update_data(selected_template=template_key)
    await state.set_state(OnboardingStates.confirm_template)

    # Форматируем цены
    price_min = f"{template['price_min']:,}".replace(",", " ")
    price_max = f"{template['price_max']:,}".replace(",", " ")
    keywords_str = ", ".join(template['keywords'][:5])

    text = f"""
{template['emoji']} <b>{template['name']}</b>

{template['description']}

<b>Настройки фильтра:</b>
🔑 Ключевые слова: <i>{keywords_str}...</i>
💰 Бюджет: {price_min} — {price_max} ₽
📍 Регионы: Вся Россия

<b>Что будет дальше:</b>
• Создам фильтр с этими параметрами
• Покажу пример уведомления о тендере
• Бот начнёт мониторинг автоматически
"""

    await callback.message.edit_text(
        text,
        reply_markup=get_confirm_template_keyboard(template_key),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("confirm_template_"))
async def callback_confirm_template(callback: CallbackQuery, state: FSMContext):
    """Подтверждение и создание фильтра из шаблона."""
    await callback.answer("⏳ Создаю фильтр...")

    template_key = callback.data.replace("confirm_template_", "")
    template = FILTER_TEMPLATES.get(template_key)

    if not template:
        await callback.message.answer("❌ Шаблон не найден")
        return

    db = await get_sniper_db()

    # Получаем или создаём пользователя
    user = await db.get_user_by_telegram_id(callback.from_user.id)
    if not user:
        user = await db.create_or_update_user(
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
            last_name=callback.from_user.last_name
        )
        user = await db.get_user_by_telegram_id(callback.from_user.id)

    # Создаём фильтр из шаблона
    filter_id = await db.create_filter(
        user_id=user['id'],
        name=f"{template['emoji']} {template['name']}",
        keywords=template['keywords'],
        price_min=template['price_min'],
        price_max=template['price_max'],
        is_active=True
    )

    # 🤖 Генерируем AI intent для семантической проверки
    try:
        from tender_sniper.ai_relevance_checker import generate_intent
        ai_intent = await generate_intent(
            filter_name=f"{template['emoji']} {template['name']}",
            keywords=template['keywords'],
            exclude_keywords=[]
        )
        if ai_intent:
            await db.update_filter_intent(filter_id, ai_intent)
            logger.info(f"AI intent generated for onboarding filter {filter_id}")
    except Exception as e:
        logger.warning(f"Failed to generate AI intent for onboarding filter {filter_id}: {e}")

    await state.clear()

    # Показываем успех
    text = f"""
✅ <b>Фильтр создан!</b>

{template['emoji']} <b>{template['name']}</b>
ID фильтра: #{filter_id}

🤖 Бот уже начал мониторинг!
Вы получите уведомление, как только появится подходящий тендер.

<b>Вот как будет выглядеть уведомление:</b>
"""

    await callback.message.edit_text(text, parse_mode="HTML")

    # Отправляем тестовое уведомление
    await asyncio.sleep(1)
    await send_sample_tender_notification(callback.message, template)

    # Сохраняем дату создания первого фильтра для follow-up
    await db.update_user_data(user['id'], {
        'first_filter_created_at': datetime.now().isoformat(),
        'onboarding_completed': True
    })

    logger.info(f"✅ Фильтр #{filter_id} создан для пользователя {callback.from_user.id} из шаблона {template_key}")


async def send_sample_tender_notification(message: Message, template: Dict[str, Any]):
    """Отправить тестовое уведомление о тендере."""

    # Генерируем пример на основе шаблона
    sample_name = f"Поставка: {template['description'].lower()}"
    sample_price = (template['price_min'] + template['price_max']) // 2
    price_formatted = f"{sample_price:,}".replace(",", " ")

    text = f"""
🎯 <b>ПРИМЕР УВЕДОМЛЕНИЯ</b>

📋 <b>{sample_name}</b>

💰 Цена: <b>{price_formatted} ₽</b>
🏢 Заказчик: ГБОУ Школа №1234
📍 Регион: Москва
📅 Подача до: {(datetime.now() + timedelta(days=7)).strftime("%d.%m.%Y")}
📊 Релевантность: <b>87%</b>

<i>Это пример. Реальные уведомления придут, когда появятся подходящие тендеры.</i>
"""

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Перейти в Tender Sniper", callback_data="sniper_menu")],
        [InlineKeyboardButton(text="➕ Создать ещё фильтр", callback_data="sniper_new_filter")],
    ])

    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "onboarding_manual")
async def callback_manual_filter(callback: CallbackQuery, state: FSMContext):
    """Переход к ручному созданию фильтра."""
    await callback.answer()
    await state.clear()

    # Создаём пользователя если ещё нет
    db = await get_sniper_db()
    user = await db.get_user_by_telegram_id(callback.from_user.id)
    if not user:
        await db.create_or_update_user(
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
            last_name=callback.from_user.last_name
        )

    # Переходим к визарду создания фильтра
    from bot.handlers.sniper import show_sniper_menu
    await show_sniper_menu(callback)


@router.callback_query(F.data == "onboarding_about")
async def callback_about(callback: CallbackQuery, state: FSMContext):
    """Подробнее о боте."""
    await callback.answer()

    text = """
📖 <b>О Tender Sniper</b>

<b>Tender Sniper</b> — это AI-бот для автоматического мониторинга тендеров на zakupki.gov.ru

<b>🎯 Что делает бот:</b>
• Мониторит 15,000+ новых тендеров ежедневно
• Фильтрует по вашим критериям (ключевые слова, бюджет, регионы)
• Присылает уведомления в Telegram мгновенно
• Использует AI для оценки релевантности (0-100%)

<b>💡 Преимущества:</b>
• Экономия 2-4 часа в день на ручном поиске
• Не пропустите ни одного подходящего тендера
• До 20 фильтров на Premium тарифе
• Цена в 6 раз ниже конкурентов

<b>📊 Тарифы:</b>
• Trial: 7 дней бесплатно (3 фильтра)
• Basic: 1 490₽/мес (5 фильтров)
• Premium: 2 990₽/мес (20 фильтров, безлимит)

<b>🚀 Начните прямо сейчас!</b>
"""

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Начать", callback_data="onboarding_quickstart")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="onboarding_back")],
    ])

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("edit_template_"))
async def callback_edit_template(callback: CallbackQuery, state: FSMContext):
    """Редактирование шаблона перед созданием."""
    await callback.answer()

    # Перенаправляем в визард с предзаполненными данными
    template_key = callback.data.replace("edit_template_", "")
    template = FILTER_TEMPLATES.get(template_key)

    if not template:
        await callback.message.answer("❌ Шаблон не найден")
        return

    # Сохраняем шаблон в state для визарда
    await state.update_data(
        template_data={
            'name': template['name'],
            'keywords': template['keywords'],
            'price_min': template['price_min'],
            'price_max': template['price_max'],
        }
    )

    # Переходим в визард создания фильтра
    await state.clear()
    from bot.handlers.sniper_wizard_new import start_extended_wizard_from_template
    await start_extended_wizard_from_template(callback, template)


# ============================================
# FOLLOW-UP MESSAGES
# ============================================

async def send_day1_followup(bot: Bot, telegram_id: int, stats: Dict[str, Any]):
    """Сообщение на День 1 после создания фильтра."""

    tenders_found = stats.get('tenders_found', 0)
    notifications_sent = stats.get('notifications_sent', 0)

    if notifications_sent > 0:
        text = f"""
📊 <b>День 1 с Tender Sniper</b>

За первый день мы нашли для вас:
• 🎯 Тендеров по вашим фильтрам: <b>{notifications_sent}</b>
• 💰 На общую сумму: <b>~{tenders_found * 500000:,} ₽</b>

⏱ Вы сэкономили примерно <b>2 часа</b> на ручном поиске!

<i>Настройте дополнительные фильтры, чтобы находить ещё больше тендеров.</i>
"""
    else:
        text = """
📊 <b>День 1 с Tender Sniper</b>

Пока не было тендеров по вашим критериям.

💡 <b>Рекомендации:</b>
• Расширьте ключевые слова
• Увеличьте бюджетный диапазон
• Добавьте больше регионов

Бот продолжает мониторинг 24/7!
"""

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Мои фильтры", callback_data="sniper_my_filters")],
        [InlineKeyboardButton(text="➕ Добавить фильтр", callback_data="sniper_new_filter")],
    ])

    try:
        await bot.send_message(telegram_id, text, reply_markup=keyboard, parse_mode="HTML")
        logger.info(f"📧 Day 1 follow-up sent to {telegram_id}")
    except Exception as e:
        logger.error(f"Failed to send day 1 follow-up to {telegram_id}: {e}")


async def send_day3_followup(bot: Bot, telegram_id: int, stats: Dict[str, Any]):
    """Сообщение на День 3 после создания фильтра."""

    total_notifications = stats.get('total_notifications', 0)
    hours_saved = max(6, total_notifications * 0.5)  # ~30 мин на тендер

    text = f"""
🎉 <b>3 дня с Tender Sniper!</b>

<b>Ваша статистика:</b>
• 📬 Уведомлений получено: <b>{total_notifications}</b>
• ⏱ Сэкономлено времени: <b>~{hours_saved:.0f} часов</b>
• 💰 Потенциальная ценность: <b>~{total_notifications * 2}% зарплаты тендерного менеджера</b>

📈 Это эквивалентно <b>{hours_saved / 8:.1f} рабочих дней</b>!

<i>Осталось {11} дней пробного периода. Успейте оценить все возможности!</i>
"""

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Моя статистика", callback_data="sniper_stats")],
        [InlineKeyboardButton(text="⭐ Оформить подписку", callback_data="subscription_tiers")],
    ])

    try:
        await bot.send_message(telegram_id, text, reply_markup=keyboard, parse_mode="HTML")
        logger.info(f"📧 Day 3 follow-up sent to {telegram_id}")
    except Exception as e:
        logger.error(f"Failed to send day 3 follow-up to {telegram_id}: {e}")


# ============================================
# HELPER FUNCTIONS
# ============================================

async def is_first_time_user(user_id: int) -> bool:
    """Проверка, впервые ли пользователь запустил бота."""
    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(user_id)

        if not user:
            return True

        # Проверяем, проходил ли пользователь онбординг
        user_data = user.get('data', {}) or {}
        if user_data.get('onboarding_completed'):
            return False

        # Также проверяем, есть ли у него фильтры
        filters = await db.get_user_filters(user['id'])
        return len(filters) == 0

    except Exception as e:
        logger.error(f"Ошибка проверки первого запуска: {e}")
        return False


async def get_user_stats(user_id: int) -> Dict[str, Any]:
    """Получить статистику пользователя для follow-up."""
    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(user_id)

        if not user:
            return {}

        # Получаем количество уведомлений
        notifications_count = await db.count_user_notifications(user['id'])

        return {
            'tenders_found': notifications_count,
            'notifications_sent': notifications_count,
            'total_notifications': notifications_count,
        }

    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        return {}


# ============================================
# ЭКСПОРТ
# ============================================

__all__ = [
    "router",
    "start_onboarding",
    "is_first_time_user",
    "send_day1_followup",
    "send_day3_followup",
    "get_user_stats",
    "FILTER_TEMPLATES",
]

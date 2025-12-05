"""
Inline Search - поиск тендеров прямо из строки поиска Telegram.

Позволяет искать тендеры, не заходя в бота: @botusername компьютеры москва
"""

import logging
import json
from typing import List
from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

from tender_sniper.instant_search import InstantSearch

logger = logging.getLogger(__name__)
router = Router()


def format_tender_for_inline(tender: dict) -> str:
    """
    Форматирование тендера для inline результата.

    Args:
        tender: Данные тендера

    Returns:
        Отформатированный текст
    """
    price = tender.get('price', 0)
    price_str = f"{price:,.0f} ₽".replace(',', ' ') if price else "Не указана"

    text = (
        f"📋 **{tender.get('name', 'Без названия')[:80]}**\n\n"
        f"💰 **Цена:** {price_str}\n"
        f"📅 **Дата:** {tender.get('published_date', 'N/A')[:10]}\n"
        f"📍 **Регион:** {tender.get('region', 'N/A')}\n"
        f"🏢 **Заказчик:** {tender.get('customer', 'N/A')[:60]}\n\n"
        f"🔗 [Открыть на zakupki.gov.ru]({tender.get('url', '#')})"
    )

    return text


def create_inline_keyboard(tender_url: str) -> InlineKeyboardMarkup:
    """
    Создание inline клавиатуры для результата поиска.

    Args:
        tender_url: URL тендера

    Returns:
        Клавиатура
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Открыть на zakupki.gov.ru", url=tender_url)]
    ])


@router.inline_query()
async def inline_search_handler(inline_query: InlineQuery):
    """
    Обработчик inline поиска тендеров.

    Формат запроса:
    - @botusername компьютеры москва
    - @botusername ноутбуки 1000000-5000000 Москва
    - @botusername медоборудование
    """
    query_text = inline_query.query.strip()

    # Минимальная длина запроса - 3 символа
    if len(query_text) < 3:
        # Показываем подсказку
        results = [
            InlineQueryResultArticle(
                id="help",
                title="❓ Как искать тендеры",
                description="Введите ключевые слова (минимум 3 символа)",
                input_message_content=InputTextMessageContent(
                    message_text=(
                        "🔍 **Inline поиск тендеров**\n\n"
                        "Просто начните вводить в любом чате:\n"
                        "`@tender_sniper_bot компьютеры москва`\n\n"
                        "**Примеры:**\n"
                        "• `компьютеры`\n"
                        "• `медоборудование москва`\n"
                        "• `ремонт школ`\n\n"
                        "Результаты появятся автоматически!"
                    ),
                    parse_mode="Markdown"
                ),
                thumb_url="https://zakupki.gov.ru/epz/main/public/favicon.ico"
            )
        ]

        await inline_query.answer(
            results=results,
            cache_time=60,
            is_personal=True
        )
        return

    logger.info(f"Inline search: query='{query_text}' from user {inline_query.from_user.id}")

    try:
        # Парсим запрос (простой парсинг: всё - ключевые слова)
        keywords = query_text

        # Создаем фильтр для поиска
        filter_data = {
            'name': 'Inline Search',
            'keywords': json.dumps([keywords], ensure_ascii=False),
            'exclude_keywords': json.dumps([], ensure_ascii=False),
            'regions': json.dumps([], ensure_ascii=False),
            'tender_types': json.dumps([], ensure_ascii=False)
        }

        # Выполняем поиск
        search_engine = InstantSearch()
        results_data = await search_engine.search_by_filter(
            filter_data=filter_data,
            max_tenders=10  # Ограничиваем 10 результатами для inline
        )

        tenders = results_data.get('tenders', [])

        if not tenders:
            # Нет результатов
            results = [
                InlineQueryResultArticle(
                    id="no_results",
                    title="❌ Ничего не найдено",
                    description=f"По запросу '{query_text}' тендеры не найдены",
                    input_message_content=InputTextMessageContent(
                        message_text=(
                            f"❌ **Результаты поиска: 0**\n\n"
                            f"По запросу `{query_text}` тендеры не найдены.\n\n"
                            "💡 **Попробуйте:**\n"
                            "• Изменить ключевые слова\n"
                            "• Сделать запрос более общим\n"
                            "• Использовать синонимы"
                        ),
                        parse_mode="Markdown"
                    )
                )
            ]

            await inline_query.answer(
                results=results,
                cache_time=30,
                is_personal=True
            )
            return

        # Формируем результаты
        inline_results = []

        for idx, tender in enumerate(tenders[:10]):  # Максимум 10 результатов
            tender_url = tender.get('url', '')
            if tender_url and not tender_url.startswith('http'):
                tender_url = f"https://zakupki.gov.ru{tender_url}"

            price = tender.get('price', 0)
            price_str = f"{price:,.0f} ₽".replace(',', ' ') if price else "Не указана"

            title = tender.get('name', 'Без названия')
            if len(title) > 80:
                title = title[:77] + "..."

            description = f"💰 {price_str} | 📍 {tender.get('region', 'N/A')}"

            inline_results.append(
                InlineQueryResultArticle(
                    id=f"tender_{idx}_{tender.get('number', idx)}",
                    title=title,
                    description=description,
                    input_message_content=InputTextMessageContent(
                        message_text=format_tender_for_inline(tender),
                        parse_mode="Markdown"
                    ),
                    reply_markup=create_inline_keyboard(tender_url),
                    thumb_url="https://zakupki.gov.ru/epz/main/public/favicon.ico"
                )
            )

        logger.info(f"Inline search: found {len(inline_results)} results")

        # Отправляем результаты
        await inline_query.answer(
            results=inline_results,
            cache_time=60,  # Кешируем на 1 минуту
            is_personal=True
        )

    except Exception as e:
        logger.error(f"Ошибка inline поиска: {e}", exc_info=True)

        # Показываем ошибку пользователю
        error_results = [
            InlineQueryResultArticle(
                id="error",
                title="⚠️ Ошибка поиска",
                description="Не удалось выполнить поиск, попробуйте позже",
                input_message_content=InputTextMessageContent(
                    message_text=(
                        "⚠️ **Ошибка поиска**\n\n"
                        f"Не удалось выполнить поиск по запросу `{query_text}`.\n\n"
                        "Попробуйте позже или обратитесь к администратору."
                    ),
                    parse_mode="Markdown"
                )
            )
        ]

        await inline_query.answer(
            results=error_results,
            cache_time=10,
            is_personal=True
        )


# ============================================
# QUICK ACTIONS (встроенные в меню)
# ============================================

def get_quick_actions_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура с быстрыми действиями для главного меню.

    Returns:
        Клавиатура
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚡ Быстрый поиск", callback_data="quick_search"),
            InlineKeyboardButton(text="🔁 Повторить последний", callback_data="repeat_last_search")
        ],
        [
            InlineKeyboardButton(text="📊 Мои тендеры", callback_data="my_tenders"),
            InlineKeyboardButton(text="🎯 Активные фильтры", callback_data="active_filters")
        ]
    ])


# ============================================
# ЭКСПОРТ
# ============================================

__all__ = [
    "router",
    "get_quick_actions_keyboard"
]

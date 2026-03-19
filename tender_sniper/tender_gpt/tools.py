"""
LangGraph tools for Tender-GPT.

Wrappers over existing modules: InstantSearch, DB, AI relevance checker.
"""

import json
import logging
import asyncio
import functools
from typing import Optional
from datetime import datetime

from langchain_core.tools import tool

from tender_sniper.database import get_sniper_db

logger = logging.getLogger(__name__)


@tool
async def search_tenders(
    keywords: str,
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    region: Optional[str] = None,
    max_results: int = 5,
) -> str:
    """
    Search for government procurement tenders on zakupki.gov.ru.

    Use this tool when the user asks to find tenders by keywords, price range, or region.

    Args:
        keywords: Search keywords (Russian), e.g. "поставка компьютеров"
        price_min: Minimum contract price in rubles (optional)
        price_max: Maximum contract price in rubles (optional)
        region: Region name in Russian (optional), e.g. "Москва"
        max_results: Number of results to return (default 5, max 10)
    """
    from tender_sniper.instant_search import InstantSearch

    max_results = min(max_results, 10)

    # Build filter_data dict matching InstantSearch.search_by_filter() format
    filter_data = {
        'name': f'GPT search: {keywords}',
        'keywords': [kw.strip() for kw in keywords.split(',') if kw.strip()],
        'exclude_keywords': [],
        'price_min': price_min,
        'price_max': price_max,
        'regions': [region] if region else [],
        'tender_types': [],
        'law_type': None,
        'purchase_stage': 'submission',  # Only active tenders
        'purchase_method': None,
        'okpd2_codes': [],
        'min_deadline_days': None,
        'customer_keywords': [],
        'publication_days': None,
    }

    try:
        searcher = InstantSearch()
        result = await searcher.search_by_filter(
            filter_data=filter_data,
            max_tenders=max_results,
            use_ai_check=False,  # Skip AI relevance check for speed
        )

        matches = result.get('matches', [])
        if not matches:
            matches = result.get('tenders', [])[:max_results]

        if not matches:
            return "По вашему запросу тендеры не найдены. Попробуйте изменить ключевые слова или расширить параметры поиска."

        # Format results for LLM
        output_lines = [f"Найдено тендеров: {len(matches)}\n"]
        for i, tender in enumerate(matches[:max_results], 1):
            name = tender.get('name', tender.get('tender_name', 'Без названия'))
            number = tender.get('number', tender.get('tender_number', '—'))
            price = tender.get('price', tender.get('nmck', 0))
            customer = tender.get('customer_name', tender.get('customer', '—'))
            end_date = tender.get('end_date', tender.get('submission_deadline', '—'))
            url = tender.get('url', tender.get('tender_url', ''))

            price_str = f"{price:,.0f} руб." if price else "Не указана"

            output_lines.append(
                f"{i}. {name}\n"
                f"   Номер: {number}\n"
                f"   Цена: {price_str}\n"
                f"   Заказчик: {customer}\n"
                f"   Срок подачи: {end_date}\n"
                f"   Ссылка: {url}\n"
            )

        return "\n".join(output_lines)

    except Exception as e:
        logger.error(f"search_tenders tool error: {e}", exc_info=True)
        return f"Ошибка при поиске тендеров: {str(e)[:200]}"


@tool
async def get_tender_details(tender_number: str) -> str:
    """
    Get detailed information about a specific tender by its number.

    Use this tool when the user asks about a specific tender, mentions a tender number,
    or wants to analyze a particular procurement.

    Args:
        tender_number: The tender/purchase number (e.g. "0123456789012345678")
    """
    try:
        db = await get_sniper_db()

        # Search in notifications DB (already parsed tenders)
        from database import SniperNotification as SniperNotificationModel, DatabaseSession
        from sqlalchemy import select

        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperNotificationModel).where(
                    SniperNotificationModel.tender_number == tender_number
                ).limit(1)
            )
            notification = result.scalar_one_or_none()

        if notification:
            price_str = f"{notification.tender_price:,.0f} руб." if notification.tender_price else "Не указана"
            deadline = notification.submission_deadline.strftime('%d.%m.%Y') if notification.submission_deadline else "—"
            published = notification.published_date.strftime('%d.%m.%Y') if notification.published_date else "—"

            # Include match_info if available (has AI analysis data)
            extra = ""
            if notification.match_info:
                mi = notification.match_info if isinstance(notification.match_info, dict) else {}
                if mi.get('summary'):
                    extra += f"\nAI-анализ: {mi['summary']}"
                if mi.get('risks'):
                    extra += f"\nРиски: {', '.join(mi['risks'])}"
                if mi.get('recommendation'):
                    extra += f"\nРекомендация: {mi['recommendation']}"
                if mi.get('estimated_competition'):
                    extra += f"\nКонкуренция: {mi['estimated_competition']}"

            return (
                f"Тендер: {notification.tender_name}\n"
                f"Номер: {tender_number}\n"
                f"Цена (НМЦК): {price_str}\n"
                f"Заказчик: {notification.tender_customer or '—'}\n"
                f"Регион: {notification.tender_region or '—'}\n"
                f"Опубликован: {published}\n"
                f"Срок подачи: {deadline}\n"
                f"Ссылка: {notification.tender_url or '—'}"
                f"{extra}"
            )

        # Tender not in DB — try RSS search by number
        from tender_sniper.instant_search import InstantSearch
        searcher = InstantSearch()
        filter_data = {
            'name': f'GPT lookup: {tender_number}',
            'keywords': [tender_number],
            'exclude_keywords': [],
            'price_min': None,
            'price_max': None,
            'regions': [],
            'tender_types': [],
            'law_type': None,
            'purchase_stage': None,
            'purchase_method': None,
            'okpd2_codes': [],
            'min_deadline_days': None,
            'customer_keywords': [],
            'publication_days': None,
        }
        result = await searcher.search_by_filter(filter_data=filter_data, max_tenders=3, use_ai_check=False)
        tenders = result.get('tenders', []) + result.get('matches', [])

        for t in tenders:
            num = t.get('number', t.get('tender_number', ''))
            if tender_number in str(num):
                name = t.get('name', t.get('tender_name', 'Без названия'))
                price = t.get('price', t.get('nmck', 0))
                price_str = f"{price:,.0f} руб." if price else "Не указана"
                return (
                    f"Тендер: {name}\n"
                    f"Номер: {num}\n"
                    f"Цена (НМЦК): {price_str}\n"
                    f"Заказчик: {t.get('customer_name', t.get('customer', '—'))}\n"
                    f"Срок подачи: {t.get('end_date', t.get('submission_deadline', '—'))}\n"
                    f"Ссылка: {t.get('url', t.get('tender_url', '—'))}"
                )

        return f"Тендер с номером {tender_number} не найден в базе. Возможно, он был опубликован давно или номер указан неверно. Проверьте номер или поищите на zakupki.gov.ru."

    except Exception as e:
        logger.error(f"get_tender_details tool error: {e}", exc_info=True)
        return f"Ошибка при получении данных тендера: {str(e)[:200]}"


@tool
async def analyze_risks(
    tender_name: str,
    tender_description: str = "",
    filter_keywords: str = "",
) -> str:
    """
    Analyze risks and give a recommendation for a tender.

    Use this tool when the user asks whether they should participate in a tender,
    asks about risks, or wants an assessment.

    Args:
        tender_name: Name/title of the tender
        tender_description: Description or additional details about the tender
        filter_keywords: Comma-separated keywords describing user's business area
    """
    from tender_sniper.ai_relevance_checker import get_relevance_checker

    try:
        checker = get_relevance_checker()
        if not checker.client:
            return "AI-анализ временно недоступен (нет API ключа)."

        keywords_list = [k.strip() for k in filter_keywords.split(',') if k.strip()] if filter_keywords else ["общий анализ"]

        # Use the existing AI checker which returns structured analysis
        result = await checker.check_relevance(
            tender_name=tender_name,
            tender_description=tender_description,
            filter_intent=f"Анализ рисков тендера для бизнеса в области: {', '.join(keywords_list)}",
            filter_keywords=keywords_list,
            # Skip quota for GPT tool calls (GPT has its own quota)
            user_id=None,
            subscription_tier='premium',
        )

        # Format the analysis result
        parts = []
        if result.get('simple_name'):
            parts.append(f"Краткое название: {result['simple_name']}")
        if result.get('summary'):
            parts.append(f"Суть: {result['summary']}")

        confidence = result.get('confidence', 0)
        is_relevant = result.get('is_relevant', False)
        parts.append(f"Релевантность: {'Да' if is_relevant else 'Нет'} (уверенность: {confidence}%)")

        if result.get('reason'):
            parts.append(f"Обоснование: {result['reason']}")
        if result.get('key_requirements'):
            parts.append(f"Ключевые требования: {', '.join(result['key_requirements'])}")
        if result.get('risks'):
            parts.append(f"Риски: {', '.join(result['risks'])}")
        if result.get('estimated_competition'):
            parts.append(f"Конкуренция: {result['estimated_competition']}")
        if result.get('recommendation'):
            parts.append(f"Рекомендация: {result['recommendation']}")

        return "\n".join(parts) if parts else "Не удалось выполнить анализ."

    except Exception as e:
        logger.error(f"analyze_risks tool error: {e}", exc_info=True)
        return f"Ошибка при анализе рисков: {str(e)[:200]}"

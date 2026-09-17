"""Сборщик общего пула тендеров.

Забирает выдачу zakupki.gov.ru за период целиком — без привязки к фильтрам
и ключевым словам — и складывает в таблицу tender_pool. Матчинг идёт потом
локально, по сохранённым строкам.

Зачем так. Сейчас запрос к площадке привязан к паре (фильтр × ключевое
слово): 39 фильтров по ~25 ключевиков — около 2000 запросов за цикл, и
фильтры с пересекающимися словами качают одно и то же по многу раз. Замер
17.09.2026: цикл 45 минут, ~18 000 запросов в сутки, площадка режет доступ.

Здесь объём обращений определяется только числом публикаций на площадке и
НЕ зависит ни от числа фильтров, ни от числа пользователей: ~6200 извещений
в сутки в рабочем ценовом диапазоне при 200 карточках на страницу — это
около 30 страниц на полный суточный обход, либо 1-2 страницы на проход раз
в 15 минут.
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import DatabaseSession, TenderPool

logger = logging.getLogger(__name__)

# Больше 200 карточек площадка на страницу не отдаёт, что бы ни просили.
RECORDS_PER_PAGE = '_500'
CARDS_PER_PAGE = 200

# Потолок страниц за один проход — защита от бесконечной пагинации, если
# площадка начнёт отдавать одно и то же. 40 страниц ≈ 8000 извещений, с
# запасом перекрывает суточный объём.
MAX_PAGES_PER_SWEEP = 40


def _parse_ru_date(value: Optional[str]) -> Optional[datetime]:
    """'24.09.2026' или '24.09.2026 10:30' -> datetime. Мусор -> None."""
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.strptime(text, '%d.%m.%Y %H:%M')
    except ValueError:
        pass
    try:
        return datetime.strptime(text[:10], '%d.%m.%Y')
    except ValueError:
        return None


def _row_from_tender(t: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Карточка выдачи -> строка пула. Без номера строка бесполезна."""
    number = (t.get('number') or '').strip()
    if not number:
        return None
    return {
        'tender_number': number[:40],
        'name': t.get('name'),
        'customer': t.get('customer'),
        'price': t.get('price'),
        'url': t.get('url'),
        'published_at': _parse_ru_date(t.get('published')),
        'submission_deadline': _parse_ru_date(t.get('submission_deadline')),
        'source': 'eis',
        'fetched_at': datetime.utcnow(),
    }


async def _upsert(rows: List[Dict[str, Any]]) -> int:
    """Кладёт строки в пул, возвращает число НОВЫХ.

    Повторно встреченный тендер обновляет поля, но НЕ сбрасывает matched_at:
    иначе площадка, помечающая извещение как «обновлено», заставляла бы нас
    слать повторное уведомление по тому же тендеру.
    """
    if not rows:
        return 0

    from sqlalchemy import select

    numbers = [r['tender_number'] for r in rows]
    async with DatabaseSession() as session:
        # Какие уже были — считаем ДО записи: после upsert отличить вставку
        # от обновления можно только трюками вроде xmax, а так это очевидно.
        existing = set((await session.scalars(
            select(TenderPool.tender_number).where(
                TenderPool.tender_number.in_(numbers))
        )).all())

        stmt = pg_insert(TenderPool).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=['tender_number'],
            set_={
                'name': stmt.excluded.name,
                'customer': stmt.excluded.customer,
                'price': stmt.excluded.price,
                'submission_deadline': stmt.excluded.submission_deadline,
                'updated_at': datetime.utcnow(),
            },
        )
        await session.execute(stmt)
        await session.commit()

    return len(set(numbers) - existing)


async def sweep_once(days_back: int = 1,
                     max_pages: int = MAX_PAGES_PER_SWEEP) -> Dict[str, Any]:
    """Один проход сборщика.

    days_back=1 — сегодня и вчера: публикация может появиться в выдаче с
    задержкой, а повторная встреча тендера ничего не стоит (upsert).
    """
    from src.parsers.zakupki_rss_parser import ZakupkiRSSParser
    import asyncio

    date_from = (datetime.now() - timedelta(days=days_back)).strftime('%d.%m.%Y')
    parser = ZakupkiRSSParser()
    loop = asyncio.get_event_loop()

    total_seen = 0
    total_new = 0
    pages_done = 0

    for page in range(1, max_pages + 1):
        try:
            tenders = await loop.run_in_executor(
                None,
                lambda p=page: parser.search_tenders_html(
                    keywords=None,
                    date_from=date_from,
                    max_results=CARDS_PER_PAGE,
                    records_per_page=RECORDS_PER_PAGE,
                    page_number=p,
                ),
            )
        except Exception as e:
            logger.error(f"Пул: страница {page} не загрузилась: {e}")
            break

        if not tenders:
            # Пусто на первой странице — источник недоступен (или за период
            # правда ничего). Дальше листать смысла нет.
            break

        rows = [r for r in (_row_from_tender(t) for t in tenders) if r]
        total_new += await _upsert(rows)
        total_seen += len(rows)
        pages_done = page

        # Неполная страница — выдача кончилась.
        if len(tenders) < CARDS_PER_PAGE:
            break

    logger.info(f"Пул: страниц {pages_done}, тендеров {total_seen}, новых {total_new}")
    return {'pages': pages_done, 'seen': total_seen, 'new': total_new,
            'date_from': date_from}

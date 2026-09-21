"""Локальный матчинг общего пула.

Вторая половина замены «каждый фильтр качает площадку сам». Сборщик
(pool_sweep) складывает выдачу в tender_pool, а здесь сохранённые строки
прогоняются через фильтры в памяти — без единого обращения к площадке.

Функция намеренно ничего не отправляет и не трогает уведомления: логика
дедупа и рассылки уже существует в tender_sniper/service.py и
tender_sniper/jobs/mos_portal_poll.py, и третья её копия неизбежно
разъедется с ними. Здесь только сопоставление, которое можно проверить
отдельно и сравнить с тем, что находит нынешний путь.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update

from database import DatabaseSession, TenderPool
from tender_sniper.matching import SmartMatcher

logger = logging.getLogger(__name__)

# Порог тот же, что у Портала поставщиков: здесь, как и там, score сырой
# (только по названию, без описания и без AI-надбавки), поэтому порог
# service.py (35, рассчитанный на composite score) отсёк бы живые совпадения.
POOL_MIN_SCORE = 20


def pool_row_to_tender(row: TenderPool) -> Dict[str, Any]:
    """Строка пула -> словарь в том виде, который ждёт SmartMatcher.

    Форма повторяет mos_portal_mapper.ks_dto_to_tender, чтобы матчер везде
    получал одинаковый вход.
    """
    return {
        'number': row.tender_number,
        'name': row.name or '',
        # Источник с сайта описания не отдаёт, интеграционный —
        # отдаёт (собирается из позиций закупки). Пустая строка
        # для первого, реальное описание для второго.
        'description': getattr(row, 'description', '') or '',
        'price': row.price,
        'region': row.region or '',
        'customer_name': row.customer or '',
        'published_date': row.published_at,
        'submission_deadline': row.submission_deadline,
        'url': row.url,
    }


async def match_pool(limit: int = 500, dry_run: bool = False,
                     filters: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Прогоняет необработанные строки пула через активные фильтры.

    dry_run=True не проставляет matched_at — строки останутся в очереди.
    Нужен, чтобы сравнить выдачу пула с нынешним путём, ничего не меняя.
    """
    from tender_sniper.database import get_sniper_db

    db = await get_sniper_db()
    if filters is None:
        filters = await db.get_all_active_filters()
    if not filters:
        return {'checked': 0, 'matches': [], 'filters': 0}

    async with DatabaseSession() as session:
        rows = (await session.scalars(
            select(TenderPool)
            .where(TenderPool.matched_at.is_(None))
            .order_by(TenderPool.fetched_at.desc())
            .limit(limit)
        )).all()

        matcher = SmartMatcher()
        matches: List[Dict[str, Any]] = []
        checked_numbers: List[str] = []

        for row in rows:
            tender = pool_row_to_tender(row)
            checked_numbers.append(row.tender_number)
            for filter_data in filters:
                try:
                    match = matcher.match_tender(tender, filter_data)
                except Exception as e:
                    # Один кривой фильтр не должен ронять весь проход.
                    logger.debug(f"Пул: фильтр {filter_data.get('id')} упал на "
                                 f"{row.tender_number}: {e}")
                    continue
                if not match or match.get('score', 0) < POOL_MIN_SCORE:
                    continue
                matches.append({
                    'tender_number': row.tender_number,
                    'name': row.name,
                    'price': row.price,
                    'submission_deadline': row.submission_deadline,
                    'filter_id': filter_data['id'],
                    'filter_name': filter_data['name'],
                    'user_id': filter_data['user_id'],
                    'score': match['score'],
                })

        if checked_numbers and not dry_run:
            await session.execute(
                update(TenderPool)
                .where(TenderPool.tender_number.in_(checked_numbers))
                .values(matched_at=datetime.utcnow())
            )
            await session.commit()

    logger.info(f"Пул: проверено {len(checked_numbers)} тендеров × {len(filters)} фильтров, "
                f"совпадений {len(matches)}")
    return {'checked': len(checked_numbers), 'matches': matches, 'filters': len(filters)}

"""Данные для раздела «Аналитика ниш» в кабинете.

Читает готовую витрину eis.niche_metrics и досчитывает индекс
привлекательности (веса в конфиге, см. tender_sniper/niche/metrics.py).
Никаких обращений к ЕИС на лету: страница должна открываться за
доли секунды, а расчёт по сырым таблицам занимает секунды.

Раздел показывает исторические данные по рынку целиком, а не по
компании пользователя, поэтому запросы здесь НЕ company-scoped — в
отличие от остального кабинета. Данные публичные: это сведения о
госзакупках из ЕИС.
"""
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from database import DatabaseSession
from tender_sniper.niche.metrics import IndexConfig, compute_index, rank
from tender_sniper.sources.eis_regions import KLADR_TO_REGION

logger = logging.getLogger(__name__)

PRICE_BUCKETS = ["0-500k", "500k-1m", "1m-3m", "3m-5m"]
BUCKET_LABELS = {
    "0-500k": "до 500 тыс",
    "500k-1m": "500 тыс – 1 млн",
    "1m-3m": "1 – 3 млн",
    "3m-5m": "3 – 5 млн",
}

SLICES_SQL = """
SELECT okpd2_level, okpd2, region, price_bucket, procedures_count,
       median_bids, share_single_bid, share_zero_bid, median_drop, p90_drop,
       median_nmck, unique_winners, known_winners, comparable_prices,
       undefined_volume_count, winner_inns, customer_inns, monthly_trend,
       first_seen, last_seen
FROM eis.niche_metrics
WHERE okpd2_level = CAST(:level AS int)
  AND (CAST(:region AS text) IS NULL OR region = CAST(:region AS text))
  AND (CAST(:bucket AS text) IS NULL OR price_bucket = CAST(:bucket AS text))
  AND procedures_count >= CAST(:min_count AS int)
"""

AVAILABILITY_SQL = """
SELECT count(*) AS slices,
       min(first_seen) AS data_from,
       max(last_seen) AS data_to,
       sum(procedures_count) AS procedures,
       sum(known_winners) AS known_winners
FROM eis.niche_metrics WHERE okpd2_level = 4
"""


async def _fetch(session, sql: str, params: Dict[str, Any]) -> List[Dict]:
    rows = (await session.execute(text(sql), params)).mappings().all()
    return [dict(r) for r in rows]


def _humanise(row: Dict) -> Dict:
    """Добавляет поля, которые нужны шаблону, и честно помечает то, что
    не измерено. Пустое значение и «ноль» — разные вещи: без пометки
    пользователь прочитает «снижение 0%» как «цену не роняют», хотя на
    деле сравнивать было не с чем."""
    row = dict(row)
    row["price_bucket_label"] = BUCKET_LABELS.get(row.get("price_bucket"), row.get("price_bucket"))
    row["region_name"] = KLADR_TO_REGION.get(row.get("region") or "", row.get("region"))
    row["drop_known"] = bool(row.get("comparable_prices"))
    row["hhi_known"] = bool(row.get("known_winners"))
    # Индекс вычисляется по тем же данным, но пользователю важно видеть,
    # на чём он основан: без победителей «открытость» не измерена и даёт
    # полные баллы, то есть индекс завышен.
    row["confidence"] = (
        "полная" if row["drop_known"] and row["hhi_known"]
        else "без концентрации" if row["drop_known"]
        else "мало данных")
    return row


async def list_niches(level: int = 4, region: Optional[str] = None,
                      bucket: Optional[str] = None, min_count: int = 5,
                      limit: int = 200) -> Dict[str, Any]:
    """Рейтинг ниш. Возвращает и сам список, и сведения о полноте данных."""
    config = IndexConfig(min_procedures=min_count)
    async with DatabaseSession() as session:
        rows = await _fetch(session, SLICES_SQL, {
            "level": level, "region": region, "bucket": bucket,
            "min_count": min_count})
        availability = (await _fetch(session, AVAILABILITY_SQL, {})) or [{}]

    ranked, sparse = rank(rows, config)
    return {
        "niches": [_humanise(r) for r in ranked[:limit]],
        "sparse_count": len(sparse),
        "total": len(rows),
        "availability": availability[0],
        "level": level,
        "region": region,
        "bucket": bucket,
        "min_count": min_count,
    }


DETAIL_SQL = SLICES_SQL + " AND okpd2 = CAST(:okpd2 AS text)"

RECENT_SQL = """
SELECT pr.purchase_number, pr.customer_name, pr.nmck, pr.published_at,
       p.bids_submitted, p.winner_price, pr.quantity_undefined
FROM eis.procedure pr
LEFT JOIN eis.protocol p ON p.purchase_number = pr.purchase_number
WHERE pr.okpd2_primary LIKE CAST(:okpd2 AS text) || '%'
  AND (CAST(:region AS text) IS NULL OR pr.customer_region_code = CAST(:region AS text))
ORDER BY pr.published_at DESC NULLS LAST
LIMIT 50
"""

TOP_CUSTOMERS_SQL = """
SELECT pr.customer_inn, max(pr.customer_name) AS customer_name,
       count(*) AS purchases, sum(pr.nmck) AS total_nmck
FROM eis.procedure pr
WHERE pr.okpd2_primary LIKE CAST(:okpd2 AS text) || '%'
  AND (CAST(:region AS text) IS NULL OR pr.customer_region_code = CAST(:region AS text))
  AND pr.customer_inn IS NOT NULL
GROUP BY pr.customer_inn ORDER BY count(*) DESC LIMIT 10
"""

TOP_WINNERS_SQL = """
SELECT c.supplier_inn, max(c.supplier_name) AS supplier_name,
       count(*) AS wins, sum(c.contract_price) AS total_price
FROM eis.contract c
JOIN eis.procedure pr ON pr.purchase_number = c.purchase_number
WHERE pr.okpd2_primary LIKE CAST(:okpd2 AS text) || '%'
  AND (CAST(:region AS text) IS NULL OR pr.customer_region_code = CAST(:region AS text))
  AND c.supplier_inn IS NOT NULL
GROUP BY c.supplier_inn ORDER BY count(*) DESC LIMIT 10
"""


async def niche_detail(okpd2: str, level: int = 4, region: Optional[str] = None,
                       bucket: Optional[str] = None) -> Dict[str, Any]:
    """Детализация ниши: срезы по корзинам, заказчики, победители,
    последние процедуры со ссылками на карточки ЕИС."""
    params = {"level": level, "region": region, "bucket": bucket,
              "min_count": 1, "okpd2": okpd2}
    async with DatabaseSession() as session:
        slices = await _fetch(session, DETAIL_SQL, params)
        recent = await _fetch(session, RECENT_SQL,
                              {"okpd2": okpd2, "region": region})
        customers = await _fetch(session, TOP_CUSTOMERS_SQL,
                                 {"okpd2": okpd2, "region": region})
        winners = await _fetch(session, TOP_WINNERS_SQL,
                               {"okpd2": okpd2, "region": region})

    enriched = [_humanise(compute_index(s)) for s in slices]
    enriched.sort(key=lambda r: r.get("procedures_count") or 0, reverse=True)
    return {
        "okpd2": okpd2,
        "level": level,
        "region": region,
        "slices": enriched,
        "recent": recent,
        "customers": customers,
        "winners": winners,
    }

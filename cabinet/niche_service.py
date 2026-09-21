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


# ---------------------------------------------------------------------------
# Блок «Конкуренция в нише» для карточки тендера
# ---------------------------------------------------------------------------

# Срез ищем по той же тройке, что и в рейтинге: категория, регион,
# ценовая корзина. Корзина считается здесь же из НМЦК самой закупки,
# чтобы не зависеть от того, попала ли она в загрузку.
COMPETITION_SQL = """
WITH t AS (
    SELECT okpd2_primary, customer_region_code, nmck,
           CASE
               WHEN nmck <  500000 THEN '0-500k'
               WHEN nmck < 1000000 THEN '500k-1m'
               WHEN nmck < 3000000 THEN '1m-3m'
               ELSE                     '3m-5m'
           END AS bucket
    FROM eis.procedure WHERE purchase_number = :number
)
SELECT t.nmck, t.okpd2_primary, t.customer_region_code, t.bucket,
       m.procedures_count, m.median_bids, m.share_single_bid, m.share_zero_bid,
       m.median_drop, m.p90_drop, m.comparable_prices, m.known_winners,
       m.winner_inns
FROM t
LEFT JOIN eis.niche_metrics m
       ON m.okpd2_level = 4
      AND m.okpd2 = eis.okpd2_prefix(t.okpd2_primary, 4)
      AND m.region = t.customer_region_code
      AND m.price_bucket = t.bucket
"""

WINNER_NAMES_SQL = """
SELECT supplier_inn, max(supplier_name) AS name
FROM eis.contract WHERE supplier_inn = ANY(:inns) GROUP BY supplier_inn
"""

# Ниже этого числа процедур блок не показывает цифр. Медиана по трём
# закупкам — не статистика, а совпадение, и выдавать её за ориентир
# хуже, чем честно написать «мало данных».
MIN_PROCEDURES_FOR_ADVICE = 5

# Доля побед одного поставщика, после которой ниша считается занятой.
CAPTURED_SHARE = 0.5


def expected_winner_price(nmck: Optional[float], median_drop: Optional[float],
                          p90_drop: Optional[float]) -> Optional[Dict[str, float]]:
    """Ожидаемая цена победителя: НМЦК за вычетом типичного снижения.

    Самое полезное число блока — сразу видно, укладывается ли
    закупочная цена, ещё до подготовки заявки.

    Две границы, а не одна: медиана показывает обычный исход, 90-й
    перцентиль — насколько жёстко бывает. С одной границей типичное
    снижение легко принять за худший случай и отказаться от закупки,
    которая на деле проходит.
    """
    if nmck is None or median_drop is None:
        return None
    result = {"typical": round(float(nmck) * (1 - float(median_drop)), 2)}
    if p90_drop is not None:
        result["tough"] = round(float(nmck) * (1 - float(p90_drop)), 2)
    else:
        result["tough"] = None
    return result


def captured_by(winner_inns: Optional[List[str]]) -> Optional[Dict[str, Any]]:
    """Поставщик, забравший больше половины закупок ниши.

    Мало участников — ещё не свободное поле. Если все победы у одного,
    это не «никто не приходит», а «приходить бесполезно», и пользователь
    должен увидеть предупреждение, а не высокий индекс.
    """
    values = [i for i in (winner_inns or []) if i]
    if not values:
        return None
    from collections import Counter
    inn, wins = Counter(values).most_common(1)[0]
    if wins / len(values) < CAPTURED_SHARE:
        return None
    return {"inn": inn, "wins": wins, "total": len(values)}


async def competition_for_tender(purchase_number: str) -> Dict[str, Any]:
    """Что известно о нише этой закупки.

    Самое полезное здесь — ожидаемая цена победителя: НМЦК за вычетом
    типичного для ниши снижения. Она сразу показывает, укладывается ли
    закупочная цена, ещё до того как считать заявку.

    Всё берётся из витрины. Обращений к ЕИС в момент открытия карточки
    нет: страница должна открываться мгновенно, а живой запрос к сервису
    занимает секунды и может не ответить вовсе.
    """
    async with DatabaseSession() as session:
        rows = await _fetch(session, COMPETITION_SQL, {"number": purchase_number})
        if not rows:
            return {"known": False,
                    "reason": "закупки нет в исторических данных"}
        row = rows[0]

        if not row.get("procedures_count"):
            return {"known": False,
                    "reason": "по этой категории и региону истории пока нет",
                    "okpd2": row.get("okpd2_primary")}

        captured = captured_by(row.get("winner_inns"))
        if captured:
            names = await _fetch(session, WINNER_NAMES_SQL,
                                 {"inns": [captured["inn"]]})
            captured["name"] = (names[0]["name"] if names else None) or captured["inn"]

    count = row["procedures_count"]
    enough = count >= MIN_PROCEDURES_FOR_ADVICE
    nmck = float(row["nmck"]) if row.get("nmck") is not None else None
    drop = row.get("median_drop")
    p90 = row.get("p90_drop")

    expected = expected_winner_price(nmck, drop, p90) if enough else None

    return {
        "known": True,
        "enough_data": enough,
        "okpd2": row.get("okpd2_primary"),
        "region": row.get("customer_region_code"),
        "region_name": KLADR_TO_REGION.get(row.get("customer_region_code") or ""),
        "price_bucket": row.get("bucket"),
        "procedures_count": count,
        "median_bids": row.get("median_bids"),
        "share_single_bid": row.get("share_single_bid"),
        "share_zero_bid": row.get("share_zero_bid"),
        "median_drop": drop,
        "p90_drop": p90,
        "drop_known": bool(row.get("comparable_prices")),
        "nmck": nmck,
        "expected_winner_price": expected,
        "captured_by": captured,
    }


# ---------------------------------------------------------------------------
# Аудит собственных фильтров
# ---------------------------------------------------------------------------

# Какие категории фактически ловит фильтр — берём не из его ключевых
# слов, а из того, что он уже находил: настоящие совпадения из
# sniper_notifications, связанные с процедурами по реестровому номеру.
# Ключевые слова говорят о намерении, история — о результате, и
# расходятся они регулярно.
FILTER_AUDIT_SQL = """
WITH hits AS (
    SELECT n.filter_id, n.filter_name,
           eis.okpd2_prefix(pr.okpd2_primary, 4) AS okpd2,
           pr.customer_region_code AS region, pr.nmck,
           -- Корзина считается здесь же: витрина хранит строку на каждую
           -- ценовую корзину, и соединение без неё размножает категорию
           -- на три строки с разными медианами — читается как разные
           -- ниши, хотя это одна.
           CASE
               WHEN pr.nmck <  500000 THEN '0-500k'
               WHEN pr.nmck < 1000000 THEN '500k-1m'
               WHEN pr.nmck < 3000000 THEN '1m-3m'
               ELSE                       '3m-5m'
           END AS bucket
    FROM sniper_notifications n
    JOIN eis.procedure pr ON pr.purchase_number = n.tender_number
    WHERE n.sent_at > now() - make_interval(days => CAST(:days AS int))
      AND (CAST(:user_id AS bigint) IS NULL OR n.user_id = CAST(:user_id AS bigint))
      AND pr.okpd2_primary IS NOT NULL
      AND pr.nmck IS NOT NULL
),
per_category AS (
    SELECT filter_id, max(filter_name) AS filter_name, okpd2, region, bucket,
           count(*) AS hits, avg(nmck) AS avg_nmck
    FROM hits GROUP BY filter_id, okpd2, region, bucket
)
SELECT c.filter_id, c.filter_name, c.okpd2, c.region, c.bucket,
       c.hits, c.avg_nmck,
       m.procedures_count, m.median_bids, m.share_single_bid, m.median_drop,
       m.comparable_prices, m.known_winners, m.winner_inns
FROM per_category c
LEFT JOIN eis.niche_metrics m
       ON m.okpd2_level = 4 AND m.okpd2 = c.okpd2
      AND m.region = c.region AND m.price_bucket = c.bucket
ORDER BY c.hits DESC
"""

TOTAL_HITS_SQL = """
SELECT count(*) AS notifications,
       count(*) FILTER (
           WHERE EXISTS (SELECT 1 FROM eis.procedure p
                         WHERE p.purchase_number = n.tender_number)) AS covered
FROM sniper_notifications n
WHERE n.sent_at > now() - make_interval(days => CAST(:days AS int))
  AND (CAST(:user_id AS bigint) IS NULL OR n.user_id = CAST(:user_id AS bigint))
"""

# Медиана заявок, выше которой категорию считаем перегретой: при четырёх
# и более участниках снижение по замерам переваливает за 30%, и маржа
# съедается.
CROWDED_BIDS = 4


def verdict_for(median_bids: Optional[float], median_drop: Optional[float],
                procedures_count: Optional[int]) -> str:
    """Короткий вывод по категории.

    Формулировки осторожные: это подсказка, куда смотреть, а не
    рекомендация отключать фильтр. Данных по одному региону мало, и
    ошибиться здесь дороже, чем промолчать.
    """
    if not procedures_count:
        return "нет истории"
    if median_bids is None:
        return "результаты неизвестны"
    if median_bids >= CROWDED_BIDS:
        return "людно"
    if median_drop is not None and median_drop >= 0.20:
        return "цену роняют"
    if median_bids <= 1:
        return "свободно"
    return "умеренно"


async def audit_filters(user_id: Optional[int] = None,
                        days: int = 60) -> Dict[str, Any]:
    """Что на самом деле ловят фильтры и насколько эти ниши тесные."""
    params = {"user_id": user_id, "days": days}
    async with DatabaseSession() as session:
        rows = await _fetch(session, FILTER_AUDIT_SQL, params)
        totals = (await _fetch(session, TOTAL_HITS_SQL, params)) or [{}]

    by_filter: Dict[Any, Dict[str, Any]] = {}
    for row in rows:
        key = row["filter_id"]
        entry = by_filter.setdefault(key, {
            "filter_id": key,
            "filter_name": row["filter_name"],
            "hits": 0,
            "categories": [],
        })
        entry["hits"] += row["hits"]
        entry["categories"].append({
            "okpd2": row["okpd2"],
            "region": row["region"],
            "price_bucket": row.get("bucket"),
            "price_bucket_label": BUCKET_LABELS.get(row.get("bucket"), row.get("bucket")),
            "region_name": KLADR_TO_REGION.get(row["region"] or "", row["region"]),
            "hits": row["hits"],
            "avg_nmck": float(row["avg_nmck"]) if row.get("avg_nmck") else None,
            "procedures_count": row.get("procedures_count"),
            "median_bids": row.get("median_bids"),
            "share_single_bid": row.get("share_single_bid"),
            "median_drop": row.get("median_drop"),
            "drop_known": bool(row.get("comparable_prices")),
            "verdict": verdict_for(row.get("median_bids"), row.get("median_drop"),
                                   row.get("procedures_count")),
        })

    filters = sorted(by_filter.values(), key=lambda f: f["hits"], reverse=True)
    total = totals[0] or {}
    return {
        "filters": filters,
        "days": days,
        # Доля уведомлений, по которым вообще есть история. Без неё
        # пользователь решит, что фильтр ловит мало, хотя на деле просто
        # не загружен его регион.
        "notifications": total.get("notifications") or 0,
        "covered": total.get("covered") or 0,
    }

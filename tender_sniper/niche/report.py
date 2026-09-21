"""Отчёт по нишам: пересчёт витрины, рейтинг, выгрузка в CSV.

Запуск:
  python -m tender_sniper.niche.report --refresh --top 30 --csv /tmp/niches.csv
  python -m tender_sniper.niche.report --level 4 --region 77 --min 10

`--refresh` пересчитывает eis.niche_metrics. Пересчёт идёт
CONCURRENTLY, чтобы не блокировать чтение; на пустой витрине это
невозможно (PostgreSQL требует хотя бы одного успешного обычного
REFRESH), поэтому первый раз делается обычный.
"""
import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from sqlalchemy import text

from database import DatabaseSession
from tender_sniper.niche.metrics import DEFAULT_CONFIG, IndexConfig, rank, write_csv

logger = logging.getLogger("niche.report")

SELECT_SLICES = """
SELECT okpd2_level, okpd2, region, price_bucket, procedures_count,
       median_bids, share_single_bid, share_zero_bid, median_drop, p90_drop,
       median_nmck, unique_winners, known_winners, winner_inns, customer_inns,
       monthly_trend, first_seen, last_seen
FROM eis.niche_metrics
-- Приведения обязательны: без них asyncpg не может вывести тип
-- параметра в «$1 IS NULL» и отвечает AmbiguousParameterError.
WHERE (CAST(:level AS int) IS NULL OR okpd2_level = CAST(:level AS int))
  AND (CAST(:region AS text) IS NULL OR region = CAST(:region AS text))
  AND (CAST(:bucket AS text) IS NULL OR price_bucket = CAST(:bucket AS text))
"""


async def refresh_view(session) -> None:
    try:
        await session.execute(
            text("REFRESH MATERIALIZED VIEW CONCURRENTLY eis.niche_metrics"))
        await session.commit()
        logger.info("витрина пересчитана (CONCURRENTLY)")
    except Exception as exc:  # noqa: BLE001
        # Первый пересчёт после создания обязан быть обычным — иначе
        # PostgreSQL отвечает «materialized view has not been populated».
        await session.rollback()
        logger.info("обычный пересчёт (%s)", str(exc)[:80])
        await session.execute(text("REFRESH MATERIALIZED VIEW eis.niche_metrics"))
        await session.commit()
        logger.info("витрина пересчитана")


async def load_slices(session, level, region, bucket):
    rows = (await session.execute(text(SELECT_SLICES), {
        "level": level, "region": region, "bucket": bucket})).mappings().all()
    return [dict(r) for r in rows]


def print_table(rows, limit: int) -> None:
    header = ("ОКПД2", "рег", "корзина", "проц", "заявок", "1 заявка",
              "паден.", "HHI", "индекс")
    print("%-10s %-4s %-9s %5s %7s %9s %8s %6s %7s" % header)
    print("-" * 76)
    for row in rows[:limit]:
        print("%-10s %-4s %-9s %5d %7s %9s %8s %6s %7.1f" % (
            (row.get("okpd2") or "")[:10],
            row.get("region") or "",
            row.get("price_bucket") or "",
            row.get("procedures_count") or 0,
            "%.1f" % row["median_bids"] if row.get("median_bids") is not None else "—",
            "%.0f%%" % (row["share_single_bid"] * 100) if row.get("share_single_bid") is not None else "—",
            "%.1f%%" % (row["median_drop"] * 100) if row.get("median_drop") is not None else "—",
            "%.2f" % row["winner_hhi"] if row.get("winner_hhi") is not None else "—",
            row["index"]))


async def run(args) -> None:
    config = IndexConfig()
    async with DatabaseSession() as session:
        if args.refresh:
            await refresh_view(session)
        rows = await load_slices(session, args.level, args.region, args.bucket)

    if args.min is not None:
        config = IndexConfig(min_procedures=args.min)
    ranked, sparse = rank(rows, config)

    logger.info("срезов всего %d, в рейтинге %d, мало данных %d",
                len(rows), len(ranked), len(sparse))
    if not ranked:
        logger.warning("нечего ранжировать: нет срезов с %d+ процедурами",
                       config.min_procedures)
    else:
        print_table(ranked, args.top)

    # Kill-criterion этапа по ТЗ: нужны ниши с индексом выше 60 при
    # заметном числе процедур. Если их нет — гипотеза о свободных нишах
    # в нашем коридоре не подтвердилась, и строить интерфейс рано.
    strong = [r for r in ranked
              if r["index"] > 60 and (r.get("procedures_count") or 0) > 20]
    logger.info("ниш с индексом > 60 и более 20 процедур: %d", len(strong))

    if args.csv:
        write_csv(ranked, args.csv)
        logger.info("выгружено в %s строк: %d", args.csv, len(ranked))


def main() -> None:
    parser = argparse.ArgumentParser(description="Рейтинг ниш по индексу привлекательности")
    parser.add_argument("--refresh", action="store_true", help="пересчитать витрину")
    parser.add_argument("--level", type=int, choices=(2, 4, 6), default=4,
                        help="уровень ОКПД2 (по умолчанию 4)")
    parser.add_argument("--region", help="код региона, например 77")
    parser.add_argument("--bucket", help="ценовая корзина: 0-500k, 500k-1m, 1m-3m, 3m-5m")
    parser.add_argument("--min", type=int, help="минимум процедур в срезе")
    parser.add_argument("--top", type=int, default=30, help="сколько строк показать")
    parser.add_argument("--csv", help="выгрузить рейтинг в CSV")
    args = parser.parse_args()

    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s",
                        level=logging.INFO)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()

"""Загрузка исторических данных ЕИС в схему `eis`.

Запуск:
  python -m tender_sniper.niche.ingest --region ЦФО --from 2026-08 --to 2026-09
  python -m tender_sniper.niche.ingest --region 77 --from 2026-09 --to 2026-09 --dry

Что делает: за каждый день периода и каждый регион запрашивает у
интеграционного сервиса ЕИС архивы извещений, протоколов и контрактов,
разбирает их и складывает в eis.procedure / eis.protocol / eis.contract.

Три требования из ТЗ, каждое отражено в коде:

**Идемпотентность.** Повторный запуск не плодит дублей: запись пишется
через ON CONFLICT DO UPDATE, а уже загруженный архив пропускается по
`eis.ingest_log`. Ключ журнала — логический адрес выгрузки
(подсистема/тип/регион/дата), а не URL: URL одноразовый, с тикетом, и
при повторном запросе тех же данных будет другим. Хеш содержимого
сверяется отдельно — если ЕИС перевыпустил срез, архив перезагружается.

**Фильтрация на лету.** В базу не попадает то, что мы заведомо не
анализируем: НМЦК выше потолка и регионы вне списка. Отбрасываем до
записи, а не после.

**Устойчивость.** Сетевые сбои — ретраи с экспоненциальной задержкой.
Ошибка на одном дне не останавливает период: день помечается в журнале
как сбойный и работа идёт дальше, иначе одна сетевая икота посреди
годовой выгрузки обнуляла бы часы работы.
"""
import argparse
import asyncio
import datetime as _dt
import hashlib
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from sqlalchemy import select

from database import DatabaseSession
from tender_sniper.niche import storage
from tender_sniper.sources import eis_integration as eis

logger = logging.getLogger("niche.ingest")

# Потолок НМЦК: в ТЗ интерес до 3 млн ₽, собираем до 5 млн, чтобы был
# запас на анализ соседних корзин.
NMCK_CEILING = 5_000_000

# Коды КЛАДР Центрального федерального округа — приоритетная география.
CFO_REGIONS = ["31", "32", "33", "36", "37", "40", "44", "46", "48", "50",
               "57", "62", "67", "68", "69", "71", "76", "77"]

REGION_GROUPS = {"ЦФО": CFO_REGIONS, "CFO": CFO_REGIONS}

# Что грузим: (подсистема, тип документа, как разбирать).
#
# ВСЕ типы извещений и протоколов, а не только электронный аукцион.
# Первая версия брала один epNotificationEF2020, и это оставляло за
# бортом запрос котировок, электронный запрос и открытый конкурс — 39%
# потока по замеру 21.09.2026. Метрики ниш при этом считались на двух
# третях рынка: «ниша с одним участником» могла оказаться нишей, где
# остальные пришли через запрос котировок.
SOURCES = tuple(
    [(eis.SUBSYSTEM_NOTICES, t, "notice") for t in eis.NOTICE_TYPES]
    + [(eis.SUBSYSTEM_NOTICES, t, "protocol") for t in eis.PROTOCOL_TYPES]
    + [(eis.SUBSYSTEM_CONTRACTS, eis.DOC_CONTRACT, "contract")]
)

RETRIES = 4
RETRY_BASE_DELAY = 3.0


@dataclass
class Stats:
    archives: int = 0
    skipped_archives: int = 0
    rows_loaded: int = 0
    rows_filtered: int = 0
    parse_errors: int = 0
    failed_days: List[str] = field(default_factory=list)

    def merge(self, other: "Stats") -> None:
        self.archives += other.archives
        self.skipped_archives += other.skipped_archives
        self.rows_loaded += other.rows_loaded
        self.rows_filtered += other.rows_filtered
        self.parse_errors += other.parse_errors
        self.failed_days.extend(other.failed_days)


def resolve_regions(value: str) -> List[str]:
    """«ЦФО» или список кодов через запятую."""
    key = value.strip().upper()
    if key in REGION_GROUPS:
        return list(REGION_GROUPS[key])
    codes = [c.strip() for c in value.split(",") if c.strip()]
    bad = [c for c in codes if not (len(c) == 2 and c.isdigit())]
    if bad:
        raise ValueError("неизвестные коды регионов: %s" % ", ".join(bad))
    return codes


def month_days(start: _dt.date, end: _dt.date) -> Iterable[_dt.date]:
    day = start
    while day <= end:
        yield day
        day += _dt.timedelta(days=1)


def parse_month(value: str, *, last_day: bool = False) -> _dt.date:
    """«2026-09» → первое или последнее число месяца. Принимаем и полную
    дату — удобно, когда нужен один день."""
    parts = value.split("-")
    if len(parts) == 3:
        return _dt.date.fromisoformat(value)
    year, month = int(parts[0]), int(parts[1])
    if not last_day:
        return _dt.date(year, month, 1)
    return (_dt.date(year + month // 12, month % 12 + 1, 1) - _dt.timedelta(days=1))


def archive_key(subsystem: str, doc_type: str, region: str, day: _dt.date,
                index: int) -> str:
    return f"{subsystem}/{doc_type}/{region}/{day.isoformat()}#{index}"


def keep_procedure(row: Dict) -> bool:
    """Фильтр на лету: дорогие процедуры в базу не кладём."""
    nmck = row.get("nmck")
    return nmck is None or nmck <= NMCK_CEILING


async def _with_retries(func, *args, what: str = ""):
    """Ретраи с экспоненциальной задержкой. Сервис ЕИС отвечает не сразу
    и под нагрузкой рвёт соединения — без повторов длинная выгрузка не
    доходит до конца."""
    delay = RETRY_BASE_DELAY
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            return await asyncio.to_thread(func, *args)
        except Exception as exc:  # noqa: BLE001 — любой сетевой сбой
            last = exc
            if attempt == RETRIES:
                break
            logger.warning("   %s: попытка %d/%d не удалась (%s), ждём %.0f с",
                           what, attempt, RETRIES, str(exc)[:90], delay)
            await asyncio.sleep(delay)
            delay *= 2
    raise last


async def _already_loaded(session, key: str, digest: str,
                          force: bool = False) -> bool:
    if session is None or force:   # --dry: базы может ещё не быть
        return False
    row = (await session.execute(
        select(storage.ingest_log.c.sha256, storage.ingest_log.c.status)
        .where(storage.ingest_log.c.archive_path == key))).first()
    return bool(row and row.status == "ok" and row.sha256 == digest)


async def _record_failure(session, key: str, region: str, day: _dt.date,
                          exc: Exception) -> None:
    """Отметка о сбойной выгрузке. Сама по себе упасть не должна — иначе
    потеряем и обработку, и запись о потере."""
    if session is None:
        return
    try:
        await session.rollback()
        await session.execute(storage.ingest_log.delete().where(
            storage.ingest_log.c.archive_path == key))
        await session.execute(storage.ingest_log.insert().values(
            archive_path=key, region=region, period=day, sha256=None,
            rows_loaded=0, rows_skipped=0, status="failed",
            error=str(exc)[:500], loaded_at=_dt.datetime.now(_dt.timezone.utc)))
        await session.commit()
    except Exception as log_exc:  # noqa: BLE001
        logger.debug("   не удалось записать сбой %s: %s", key, str(log_exc)[:100])


async def ingest_archive(session, client, *, subsystem: str, doc_type: str,
                         kind: str, region: str, day: _dt.date, url: str,
                         index: int, dry: bool, force: bool = False) -> Stats:
    stats = Stats()
    key = archive_key(subsystem, doc_type, region, day, index)

    blob = await _with_retries(client.download, url, what=f"скачивание {key}")
    digest = hashlib.sha256(blob).hexdigest()
    if await _already_loaded(session, key, digest, force):
        stats.skipped_archives = 1
        return stats

    rows: List[Dict] = []
    okpd2_names: Dict[str, str] = {}
    for doc_kind, xml in eis.iter_documents(blob):
        try:
            if kind == "notice":
                root = eis.parse_xml(xml)
                row = storage.parse_notice(root, region_code=region, source=key)
                if row and not keep_procedure(row):
                    stats.rows_filtered += 1
                    continue
            elif kind == "protocol":
                row = storage.protocol_to_row(
                    eis.parse_protocol_final(xml), source=key)
            else:
                row = storage.contract_to_row(eis.parse_contract(xml), source=key)
        except Exception as exc:  # noqa: BLE001 — один битый документ не должен ронять архив
            stats.parse_errors += 1
            logger.debug("   не разобран %s из %s: %s", doc_kind, key, str(exc)[:120])
            continue
        if row:
            # Названия категорий едут отдельным справочником, а не
            # колонкой процедуры: они повторяются в каждом документе
            # и в строке были бы чистым дублированием.
            names = row.pop("_okpd2_names", None)
            if names:
                okpd2_names.update(names)
            rows.append(row)

    table = {"notice": storage.procedure, "protocol": storage.protocol,
             "contract": storage.contract}[kind]
    rows = storage.dedupe_by_key(table, rows)

    if rows and not dry:
        try:
            await session.execute(storage.upsert(table, rows))
            if okpd2_names:
                await session.execute(storage.upsert(
                    storage.okpd2_dict, storage.okpd2_dict_rows(okpd2_names)))
            await session.execute(storage.ingest_log.delete().where(
                storage.ingest_log.c.archive_path == key))
            await session.execute(storage.ingest_log.insert().values(
                archive_path=key, region=region, period=day, sha256=digest,
                rows_loaded=len(rows), rows_skipped=stats.rows_filtered,
                status="ok", loaded_at=_dt.datetime.now(_dt.timezone.utc)))
            await session.commit()
        except Exception:
            # Без отката сессия остаётся в сорванной транзакции, и КАЖДЫЙ
            # следующий архив падает с InFailedSQLTransactionError — одна
            # ошибка забирала с собой весь день.
            await session.rollback()
            raise

    stats.archives = 1
    stats.rows_loaded = len(rows)
    return stats


async def ingest_day(session, client, region: str, day: _dt.date,
                     dry: bool, force: bool = False) -> Stats:
    stats = Stats()
    for subsystem, doc_type, kind in SOURCES:
        try:
            urls = await _with_retries(
                client.request_archives, subsystem, doc_type, region, day,
                what=f"запрос {doc_type} {region} {day}")
        except Exception as exc:  # noqa: BLE001
            logger.error("   %s %s %s: запрос не удался — %s",
                         doc_type, region, day, str(exc)[:120])
            stats.failed_days.append(f"{region}/{day}/{doc_type}")
            continue

        for index, url in enumerate(urls):
            try:
                stats.merge(await ingest_archive(
                    session, client, subsystem=subsystem, doc_type=doc_type,
                    kind=kind, region=region, day=day, url=url, index=index,
                    dry=dry, force=force))
            except Exception as exc:  # noqa: BLE001 — день не должен ронять период
                logger.error("   архив %s#%d за %s: %s", doc_type, index, day,
                             str(exc)[:120])
                stats.failed_days.append(f"{region}/{day}/{doc_type}#{index}")
                # Помечаем в журнале: иначе сбойная выгрузка неотличима от
                # никогда не запрошенной, и догрузить её потом нечем.
                await _record_failure(session, archive_key(
                    subsystem, doc_type, region, day, index), region, day, exc)
    return stats


async def run(regions: List[str], start: _dt.date, end: _dt.date,
              dry: bool, force: bool = False) -> Stats:
    client = eis.EisIntegrationClient()
    if not client.token:
        raise SystemExit("не задан %s" % eis.TOKEN_ENV_VAR)

    total = Stats()

    async def sweep(session):
        for region in regions:
            for day in month_days(start, end):
                day_stats = await ingest_day(session, client, region, day,
                                             dry, force)
                total.merge(day_stats)
                logger.info("регион %s %s: архивов %d (пропущено %d), строк %d, "
                            "отфильтровано %d", region, day, day_stats.archives,
                            day_stats.skipped_archives, day_stats.rows_loaded,
                            day_stats.rows_filtered)

    # В сухом прогоне в базу не ходим вовсе — он нужен в том числе
    # чтобы проверить разбор до того, как применена миграция.
    if dry:
        await sweep(None)
    else:
        async with DatabaseSession() as session:
            await sweep(session)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Загрузка исторических данных ЕИС для аналитики ниш")
    parser.add_argument("--region", default="ЦФО",
                        help="ЦФО либо коды КЛАДР через запятую (77,50)")
    parser.add_argument("--from", dest="date_from", required=True,
                        help="начало периода, YYYY-MM или YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", required=True,
                        help="конец периода, YYYY-MM или YYYY-MM-DD")
    parser.add_argument("--dry", action="store_true",
                        help="разобрать и посчитать, но ничего не писать")
    parser.add_argument("--force", action="store_true",
                        help="перезагрузить, игнорируя журнал: нужно после\n                              изменения разбора, иначе старые строки\n                              останутся без новых полей")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(message)s",
        level=logging.DEBUG if args.verbose else logging.INFO)

    regions = resolve_regions(args.region)
    start = parse_month(args.date_from)
    end = parse_month(args.date_to, last_day=True)
    if start > end:
        raise SystemExit("начало периода позже конца")

    logger.info("регионов %d, период %s — %s%s",
                len(regions), start, end, ", РЕЖИМ DRY" if args.dry else "")
    stats = asyncio.run(run(regions, start, end, args.dry, args.force))

    logger.info("ИТОГО: архивов %d, пропущено уже загруженных %d, строк %d, "
                "отфильтровано по цене %d, документов не разобрано %d",
                stats.archives, stats.skipped_archives, stats.rows_loaded,
                stats.rows_filtered, stats.parse_errors)
    if stats.failed_days:
        logger.warning("сбойных выгрузок %d, первые: %s",
                       len(stats.failed_days), ", ".join(stats.failed_days[:10]))


if __name__ == "__main__":
    main()

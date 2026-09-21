"""Сборщик общего пула через интеграционный сервис ЕИС.

Замена pool_sweep, который брал выдачу с сайта. Складывает в ту же
таблицу tender_pool, матчинг остаётся прежний (pool_match).

**Зачем менять источник.** Сайт ЕИС блокирует хостинговые адреса: замер
20.09.2026 через прокси — 0 успешных запросов из 9 к zakupki.gov.ru
против 5 из 5 к int.zakupki.gov.ru. Из-за этого поток уведомлений
держится около половины нормы, и покупка новых прокси проблему не
решила — свежие адреса из других подсетей и городов отбиваются так же.
Интеграционный сервис не блокируется вовсе.

**Что это меняет по существу.**

1. Объём обращений перестаёт зависеть от числа фильтров и ключевиков.
   Было ~18 000 запросов в сутки (фильтры × ключевики), стало
   регионы × типы извещений × 24 часа.
2. Регион приходит из самого запроса. Именно его отсутствие блокировало
   запуск общего пула: без региона матчинг давал 3% совпадений против
   23%, а в выдаче сайта региона нет.
3. Название закупки приходит отдельным полем `purchaseObjectInfo` —
   это предмет, а не способ проведения. Эвристики resolve_tender_name
   здесь не нужны.

**Чем платим — и это главное решение.** Задержка 3-4 часа вместо двух
минут. Сервис отдаёт документы часовыми срезами, но отказывается
отдавать свежие: требуется, чтобы запрашиваемый час отставал от
текущего более чем на два. Замер 21.09.2026: в 13:14 час 12 отвергнут,
час 11 отдан.

То есть это НЕ замена мониторингу «почти в реальном времени», а другой
режим работы: гарантированный полный охват с задержкой в полсмены
против быстрого, но рваного потока через блокируемый сайт. Для закупок
со сроком подачи в неделю-две разница несущественна, для коротких
процедур — существенна.
"""
import asyncio
import datetime as _dt
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import DatabaseSession, TenderPool
from tender_sniper.sources import eis_integration as eis
from tender_sniper.sources.eis_regions import region_name

logger = logging.getLogger(__name__)

# Часовой пояс запроса. Сервис принимает смещение числом, не «+03:00»:
# тип timeZoneDifferenceType это [+\-]?\d{1,3}.
TIME_ZONE_OFFSET = "3"

# Сервис не отдаёт свежие часы: «ожидается, что будет указан час,
# который отстаёт от текущего времени более чем на 2 часа». Проверено
# замером — в 13:14 час 12 отвергается, час 11 отдаётся. Поэтому идём от
# трёх часов назад, и минимальная задержка уведомления составляет 3-4
# часа, а не час, как можно было предположить по наличию oneHourInfo.
MIN_HOURS_LAG = 3

# Сколько часов забирать за проход, начиная с MIN_HOURS_LAG. Два, чтобы
# на границе часа ничего не потерялось и чтобы пережить один пропущенный
# запуск.
HOURS_BACK = 2

# Параллельность запросов. Единица, а не больше: замер 21.09.2026 при
# трёх потоках дал 16 ошибок из 24 — «Unable to connect to proxy»,
# то есть в параллель упирается не сервис ЕИС, а единственный рабочий
# прокси, через который мы к нему ходим. Поднимать имеет смысл только
# вместе с числом прокси; переопределяется через окружение.
CONCURRENCY = int(os.getenv("EIS_SWEEP_CONCURRENCY", "1"))

# Минимальный интервал между запросами. Ограничение накладывает не
# сервис ЕИС, а единственный рабочий прокси, через который мы к нему
# ходим. Замер 21.09.2026 на одинаковых 712 запросах:
#   0.94 запроса/с  ->   1 ошибка
#   2.5  запроса/с  -> 340 ошибок «Max retries exceeded»
# Отказы быстрые, поэтому без выдержки проход «ускоряется» ровно за
# счёт того, что перестаёт работать. Поднимать темп можно только
# вместе с числом прокси.
MIN_REQUEST_INTERVAL = float(os.getenv("EIS_SWEEP_INTERVAL", "1.0"))


@dataclass
class SweepStats:
    requests: int = 0
    archives: int = 0
    documents: int = 0
    saved: int = 0
    errors: int = 0
    regions: int = 0

    def merge(self, other: "SweepStats") -> None:
        for name in ("requests", "archives", "documents", "saved", "errors"):
            setattr(self, name, getattr(self, name) + getattr(other, name))


def _to_datetime(value: Optional[str]) -> Optional[_dt.datetime]:
    """Дата-время ЕИС без часового пояса.

    tender_pool хранит naive-даты, а сервис отдаёт со смещением. Приводим
    к московскому времени и снимаем tzinfo: смешивать в одной колонке
    aware и naive нельзя, PostgreSQL на сравнении таких значений падает.
    """
    if not value:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    moscow = _dt.timezone(_dt.timedelta(hours=int(TIME_ZONE_OFFSET)))
    return parsed.astimezone(moscow).replace(tzinfo=None)


def notice_to_pool_row(card: Dict, region: str) -> Optional[Dict]:
    if not card or not card.get("tender_number"):
        return None
    return {
        "tender_number": card["tender_number"][:40],
        "name": card.get("name"),
        "description": card.get("description"),
        "customer": card.get("customer"),
        "price": card.get("price"),
        "region": region,
        "law": "44",
        "procedure_type": (card.get("procedure_type") or "")[:255] or None,
        "status": "Подача заявок",
        "url": (card.get("url") or "")[:500] or None,
        "published_at": _to_datetime(card.get("published_at")),
        "submission_deadline": _to_datetime(card.get("submission_deadline")),
        "source": "eis_integration",
    }


async def _save(rows: List[Dict]) -> int:
    """Пишет в пул, не трогая matched_at.

    Сбрасывать matched_at при обновлении нельзя: ЕИС переиздаёт извещения
    с правками, и тендер уходил бы на повторный матчинг, а оттуда —
    вторым уведомлением о той же закупке.
    """
    if not rows:
        return 0
    unique = {r["tender_number"]: r for r in rows}
    async with DatabaseSession() as session:
        statement = pg_insert(TenderPool).values(list(unique.values()))
        await session.execute(statement.on_conflict_do_update(
            index_elements=["tender_number"],
            set_={
                "name": statement.excluded.name,
                "description": statement.excluded.description,
                "customer": statement.excluded.customer,
                "price": statement.excluded.price,
                "region": statement.excluded.region,
                "procedure_type": statement.excluded.procedure_type,
                "url": statement.excluded.url,
                "submission_deadline": statement.excluded.submission_deadline,
                "updated_at": _dt.datetime.utcnow(),
            }))
        await session.commit()
    return len(unique)


class _Pacer:
    """Выдерживает минимальный интервал между запросами.

    Сон намеренно происходит ДО захвата семафора и без удержания
    блокировки: держать лок через await — способ превратить
    ограничение темпа в полную сериализацию с непредсказуемой
    задержкой.
    """

    def __init__(self, interval: float):
        self.interval = interval
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.interval
        if delay:
            await asyncio.sleep(delay)


async def sweep_region_hour(client, region_code: str, day: _dt.date, hour: int,
                            semaphore: asyncio.Semaphore,
                            pacer: "_Pacer") -> SweepStats:
    stats = SweepStats()
    name = region_name(region_code)
    if not name:
        logger.debug("код региона %s не в справочнике, пропускаем", region_code)
        return stats

    rows: List[Dict] = []
    for doc_type in eis.NOTICE_TYPES:
        await pacer.wait()
        async with semaphore:
            try:
                urls = await asyncio.to_thread(
                    client.request_archives_hourly, eis.SUBSYSTEM_NOTICES,
                    doc_type, region_code, day, hour, TIME_ZONE_OFFSET)
                stats.requests += 1
            except Exception as exc:  # noqa: BLE001 — регион не должен ронять проход
                stats.errors += 1
                logger.warning("   %s %s %s ч%d: %s", region_code, doc_type,
                               day, hour, str(exc)[:110])
                continue

            for url in urls:
                try:
                    blob = await asyncio.to_thread(client.download, url)
                except Exception as exc:  # noqa: BLE001
                    stats.errors += 1
                    logger.warning("   архив %s: %s", doc_type, str(exc)[:110])
                    continue
                stats.archives += 1
                for _, xml in eis.iter_documents(blob):
                    stats.documents += 1
                    try:
                        row = notice_to_pool_row(eis.parse_notice_card(xml), name)
                    except Exception as exc:  # noqa: BLE001
                        stats.errors += 1
                        logger.debug("   документ не разобран: %s", str(exc)[:90])
                        continue
                    if row:
                        rows.append(row)

    stats.saved = await _save(rows)
    return stats


async def sweep(region_codes: Sequence[str], hours_back: int = HOURS_BACK,
                now: Optional[_dt.datetime] = None) -> SweepStats:
    """Проход по регионам за последние часы."""
    client = eis.EisIntegrationClient()
    if not client.token:
        logger.error("не задан %s — сбор пула через ЕИС невозможен",
                     eis.TOKEN_ENV_VAR)
        return SweepStats()

    moscow = _dt.timezone(_dt.timedelta(hours=int(TIME_ZONE_OFFSET)))
    now = now or _dt.datetime.now(moscow)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    pacer = _Pacer(MIN_REQUEST_INTERVAL)

    total = SweepStats(regions=len(region_codes))
    for offset in range(hours_back):
        moment = now - _dt.timedelta(hours=MIN_HOURS_LAG + offset)
        results = await asyncio.gather(*[
            sweep_region_hour(client, code, moment.date(), moment.hour,
                              semaphore, pacer)
            for code in region_codes], return_exceptions=True)
        for result in results:
            if isinstance(result, SweepStats):
                total.merge(result)
            else:
                total.errors += 1
                logger.error("   регион упал: %s", str(result)[:120])

    logger.info("пул ЕИС: регионов %d, запросов %d, архивов %d, документов %d, "
                "сохранено %d, ошибок %d", total.regions, total.requests,
                total.archives, total.documents, total.saved, total.errors)
    return total

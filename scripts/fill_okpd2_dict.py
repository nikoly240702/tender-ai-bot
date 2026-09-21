"""Наполнение справочника категорий ОКПД2 из выгрузок ЕИС.

Отдельного классификатора у нас нет, но названия категорий лежат рядом
с кодами в самих документах: в извещениях как OKPDName, в контрактах —
как name внутри узла OKPD2. Этот скрипт проходит по архивам и собирает
пары код→название, ничего больше не трогая.

Нужен потому, что загрузчик начал сохранять названия только сейчас, а
данные за март–сентябрь уже лежат без них. Для новых загрузок скрипт не
требуется — ingest пополняет справочник сам.

Названия повторяются в каждом документе, поэтому недели выгрузки хватает
на подавляющее большинство встречающихся кодов: гнать весь период
незачем.

Запуск:
  python -m scripts.fill_okpd2_dict --region 77 --from 2026-09-08 --to 2026-09-14
"""
import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from database import DatabaseSession
from tender_sniper.niche import storage
from tender_sniper.niche.ingest import month_days, parse_month, resolve_regions
from tender_sniper.sources import eis_integration as eis

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger("fill_okpd2")


def names_from_notice(xml: bytes) -> dict:
    row = storage.parse_notice(eis.parse_xml(xml), region_code="00", source="dict")
    return (row or {}).get("_okpd2_names") or {}


def names_from_contract(xml: bytes) -> dict:
    """У контракта код и название лежат в том же узле OKPD2, но под
    другими именами тегов, чем в извещении."""
    root = eis.parse_xml(xml)
    local = lambda tag: tag.split("}")[-1]  # noqa: E731
    found = {}
    for node in root.iter():
        if local(node.tag) != "OKPD2":
            continue
        code = name = None
        for child in node:
            text = (child.text or "").strip()
            if not text:
                continue
            if local(child.tag) in ("code", "OKPDCode"):
                code = text
            elif local(child.tag) in ("name", "OKPDName"):
                name = text
        if code and name:
            found.setdefault(code, name)
    return found


async def run(regions, start, end) -> None:
    client = eis.EisIntegrationClient()
    if not client.token:
        raise SystemExit("не задан %s" % eis.TOKEN_ENV_VAR)

    collected = {}
    sources = ([(eis.SUBSYSTEM_NOTICES, t, names_from_notice) for t in eis.NOTICE_TYPES]
               + [(eis.SUBSYSTEM_CONTRACTS, eis.DOC_CONTRACT, names_from_contract)])

    for region in regions:
        for day in month_days(start, end):
            for subsystem, doc_type, extract in sources:
                try:
                    urls = await asyncio.to_thread(
                        client.request_archives, subsystem, doc_type, region, day)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("   %s %s %s: %s", doc_type, region, day,
                                   str(exc)[:110])
                    continue
                for url in urls:
                    try:
                        blob = await asyncio.to_thread(client.download, url)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("   архив: %s", str(exc)[:110])
                        continue
                    for _, xml in eis.iter_documents(blob):
                        try:
                            collected.update(extract(xml))
                        except Exception:  # noqa: BLE001 — битый документ не важен
                            continue
            logger.info("регион %s %s: кодов накоплено %d", region, day, len(collected))

    if not collected:
        logger.warning("ничего не собрано")
        return

    rows = storage.okpd2_dict_rows(collected)
    async with DatabaseSession() as session:
        await session.execute(storage.upsert(storage.okpd2_dict, rows))
        await session.commit()
    logger.info("в справочник записано кодов: %d", len(rows))


def main() -> None:
    parser = argparse.ArgumentParser(description="Наполнить eis.okpd2_dict")
    parser.add_argument("--region", default="77")
    parser.add_argument("--from", dest="date_from", required=True)
    parser.add_argument("--to", dest="date_to", required=True)
    args = parser.parse_args()
    asyncio.run(run(resolve_regions(args.region),
                    parse_month(args.date_from),
                    parse_month(args.date_to, last_day=True)))


if __name__ == "__main__":
    main()

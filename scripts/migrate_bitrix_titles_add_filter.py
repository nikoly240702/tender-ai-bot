#!/usr/bin/env python3
"""
Одноразовая миграция: добавляет префикс [Фильтр] в TITLE существующих
сделок Bitrix24, чтобы на канбане сразу было видно, по какому фильтру
пришло совпадение.

Имя фильтра берётся из поля UF_CRM_TENDER_FILTER самой сделки; если оно
пустое — ищется в БД по номеру тендера (SniperNotification.filter_name).
Идемпотентно: сделки, чей TITLE уже начинается с «[», пропускаются.

Запуск:
    DATABASE_URL=postgresql+asyncpg://... \
    BITRIX24_WEBHOOK=https://b24-xxx.bitrix24.ru/rest/1/TOKEN/ \
    python3 scripts/migrate_bitrix_titles_add_filter.py

    # dry-run (без изменений):
    DRY_RUN=1 ... python3 scripts/migrate_bitrix_titles_add_filter.py
"""

import asyncio
import logging
import os
import sys

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.handlers.bitrix24 import build_deal_title

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

WEBHOOK_URL = os.getenv('BITRIX24_WEBHOOK', 'https://b24-gc3ju5.bitrix24.ru/rest/1/il3soo8fwc7108wz/')
DATABASE_URL = os.getenv('DATABASE_URL', '')
DRY_RUN = os.getenv('DRY_RUN', '0') == '1'
# Локально в цепочке сертификатов бывает self-signed (корпоративный MITM-прокси).
# BITRIX_SSL_VERIFY=0 отключает проверку только для этого one-off скрипта.
SSL_VERIFY = os.getenv('BITRIX_SSL_VERIFY', '1') != '0'


def _ssl_param():
    """None → обычная проверка; False → проверка отключена (aiohttp)."""
    return None if SSL_VERIFY else False


def _normalize_db_url(url: str) -> str:
    if url.startswith('postgres://'):
        return url.replace('postgres://', 'postgresql+asyncpg://', 1)
    if url.startswith('postgresql://') and '+asyncpg' not in url:
        return url.replace('postgresql://', 'postgresql+asyncpg://', 1)
    return url


async def fetch_all_deals(webhook_url: str) -> list[dict]:
    """crm.deal.list с пагинацией: ID, TITLE, номер тендера, фильтр."""
    if not webhook_url.endswith('/'):
        webhook_url += '/'
    deals: list[dict] = []
    start = 0
    select = ['ID', 'TITLE', 'UF_CRM_TENDER_NUMBER', 'UF_CRM_TENDER_FILTER']
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        while True:
            payload = {'select': select, 'start': start, 'order': {'ID': 'ASC'}}
            data = None
            for attempt in range(4):
                try:
                    async with session.post(
                        webhook_url + 'crm.deal.list.json', json=payload, ssl=_ssl_param()
                    ) as resp:
                        if resp.status != 200:
                            body = await resp.text()
                            logger.warning(f"crm.deal.list HTTP {resp.status}: {body[:150]}")
                        else:
                            data = await resp.json()
                            break
                except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                    logger.warning(f"crm.deal.list start={start} retry {attempt + 1}/4: {e}")
                await asyncio.sleep(1.0 + attempt)
            if data is None:
                logger.error(f"crm.deal.list start={start} — не удалось после ретраев, стоп")
                break
            batch = data.get('result') or []
            deals.extend(batch)
            nxt = data.get('next')
            if not nxt:
                break
            start = nxt
    return deals


async def load_db_filter_map(database_url: str) -> dict[str, str]:
    """{tender_number: filter_name} из последних уведомлений — фолбэк."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(_normalize_db_url(database_url), echo=False)
    out: dict[str, str] = {}
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(text(
                "SELECT DISTINCT ON (tender_number) tender_number, filter_name "
                "FROM sniper_notifications "
                "WHERE filter_name IS NOT NULL AND filter_name <> '' "
                "ORDER BY tender_number, sent_at DESC"
            ))
            for tender_number, filter_name in rows:
                if tender_number:
                    out[str(tender_number)] = filter_name
    finally:
        await engine.dispose()
    return out


async def update_title(webhook_url: str, deal_id: str, title: str) -> bool:
    if not webhook_url.endswith('/'):
        webhook_url += '/'
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                async with session.post(
                    webhook_url + 'crm.deal.update.json',
                    json={'id': deal_id, 'fields': {'TITLE': title}},
                    ssl=_ssl_param(),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return bool(data.get('result'))
                    body = await resp.text()
                    logger.warning(f"deal {deal_id} HTTP {resp.status}: {body[:120]}")
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.warning(f"update_title {deal_id} retry {attempt + 1}/3: {e}")
        await asyncio.sleep(1.0 + attempt)
    logger.error(f"update_title {deal_id} — не удалось после ретраев")
    return False


async def main() -> None:
    logger.info(f"Режим: {'DRY RUN' if DRY_RUN else 'LIVE'}")
    logger.info(f"Webhook: {WEBHOOK_URL[:60]}...")

    deals = await fetch_all_deals(WEBHOOK_URL)
    logger.info(f"Сделок в Bitrix: {len(deals)}")
    if not deals:
        return

    db_map: dict[str, str] = {}
    if DATABASE_URL:
        db_map = await load_db_filter_map(DATABASE_URL)
        logger.info(f"Фильтров из БД (фолбэк): {len(db_map)}")
    else:
        logger.warning("DATABASE_URL не задан — фолбэк по БД отключён")

    updated = skipped_prefixed = skipped_no_filter = failed = 0

    for deal in deals:
        deal_id = str(deal.get('ID'))
        title = (deal.get('TITLE') or '').strip()
        number = str(deal.get('UF_CRM_TENDER_NUMBER') or '').strip()
        filter_name = (deal.get('UF_CRM_TENDER_FILTER') or '').strip()

        if title.startswith('['):
            skipped_prefixed += 1
            continue

        if not filter_name and number:
            filter_name = db_map.get(number, '')

        if not filter_name:
            skipped_no_filter += 1
            continue

        new_title = build_deal_title(filter_name, title, number)
        if new_title == title:
            skipped_prefixed += 1
            continue

        logger.info(f"#{deal_id}: '{title[:50]}' → '{new_title[:60]}'")

        if DRY_RUN:
            updated += 1
            continue

        if await update_title(WEBHOOK_URL, deal_id, new_title):
            updated += 1
        else:
            failed += 1
        await asyncio.sleep(0.3)

    logger.info('=' * 50)
    logger.info(
        f"Итог: ✅ {updated} обновлено, ⏭ {skipped_prefixed} уже с префиксом, "
        f"❓ {skipped_no_filter} без фильтра, ❌ {failed} ошибок"
    )


if __name__ == '__main__':
    asyncio.run(main())

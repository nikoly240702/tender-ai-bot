#!/usr/bin/env python3
"""
Одноразовый скрипт: обновляет существующие сделки в Битрикс24 —
заполняет кастомные поля (UF_CRM_*) данными из БД и очищает COMMENTS.

Запуск:
    DATABASE_URL=postgresql+asyncpg://... \
    BITRIX24_WEBHOOK=https://b24-xxx.bitrix24.ru/rest/1/TOKEN/ \
    python3 scripts/update_bitrix24_deals.py

    # dry-run (без изменений):
    DRY_RUN=1 ... python3 scripts/update_bitrix24_deals.py
"""

import asyncio
import os
import sys
import logging
from datetime import datetime

import aiohttp

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

WEBHOOK_URL = os.getenv('BITRIX24_WEBHOOK', 'https://b24-gc3ju5.bitrix24.ru/rest/1/il3soo8fwc7108wz/')
DATABASE_URL = os.getenv('DATABASE_URL', '')
DRY_RUN = os.getenv('DRY_RUN', '0') == '1'

_AI_REC_ENUM: dict = {
    'не брать': 57, 'отказ': 57,
    'взять': 53, 'рекоменд': 53, 'да': 53, 'высок': 53,
}


def _map_ai_rec_enum(ai_recommendation: str) -> int:
    if not ai_recommendation:
        return 55
    text = ai_recommendation.lower()
    for pattern, enum_id in _AI_REC_ENUM.items():
        if pattern in text:
            return enum_id
    return 55


def _normalize_db_url(url: str) -> str:
    if url.startswith('postgres://'):
        return url.replace('postgres://', 'postgresql+asyncpg://', 1)
    if url.startswith('postgresql://') and '+asyncpg' not in url:
        return url.replace('postgresql://', 'postgresql+asyncpg://', 1)
    return url


async def fetch_exported_notifications(database_url: str):
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import text

    engine = create_async_engine(_normalize_db_url(database_url), echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        result = await session.execute(text("""
            SELECT
                id, tender_number, tender_name, tender_price, tender_url,
                tender_region, tender_customer, filter_name,
                submission_deadline, match_info, bitrix24_deal_id
            FROM sniper_notifications
            WHERE bitrix24_exported = true
              AND bitrix24_deal_id IS NOT NULL
            ORDER BY id ASC
        """))
        rows = result.mappings().all()

    await engine.dispose()
    return [dict(r) for r in rows]


async def update_deal(webhook_url: str, row: dict) -> bool:
    deal_id = row['bitrix24_deal_id']
    mi = row.get('match_info') or {}
    ai_summary = mi.get('ai_summary', '')
    ai_recommendation = mi.get('ai_recommendation', '')

    tender_url = row.get('tender_url') or (
        f"https://zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html"
        f"?regNumber={row['tender_number']}"
    )

    if not webhook_url.endswith('/'):
        webhook_url += '/'

    fields = {
        'UF_CRM_TENDER_NUMBER': row['tender_number'],
        'UF_CRM_TENDER_CUSTOMER': row.get('tender_customer') or '',
        'UF_CRM_TENDER_REGION': row.get('tender_region') or '',
        'UF_CRM_TENDER_FILTER': row.get('filter_name') or '',
        'UF_CRM_AI_SUMMARY': ai_summary or '',
        'UF_CRM_AI_RECOMMENDATION': _map_ai_rec_enum(ai_recommendation),
        'UF_CRM_TENDER_URL': tender_url,
        'COMMENTS': '',  # очищаем старый комментарий
    }

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
            async with session.post(
                webhook_url + 'crm.deal.update.json',
                json={'id': deal_id, 'fields': fields}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return bool(data.get('result'))
                body = await resp.text()
                logger.warning(f"HTTP {resp.status}: {body[:150]}")
    except Exception as e:
        logger.error(f"update_deal error for {deal_id}: {e}")

    return False


async def main():
    if not DATABASE_URL:
        logger.error("DATABASE_URL не задан")
        sys.exit(1)

    logger.info(f"Режим: {'DRY RUN' if DRY_RUN else 'LIVE'}")
    logger.info(f"Webhook: {WEBHOOK_URL[:60]}...")

    rows = await fetch_exported_notifications(DATABASE_URL)
    logger.info(f"Найдено сделок для обновления: {len(rows)}")

    if not rows:
        logger.info("Нечего обновлять.")
        return

    ok_count = 0
    fail_count = 0

    for i, row in enumerate(rows, 1):
        logger.info(f"[{i}/{len(rows)}] deal_id={row['bitrix24_deal_id']} | {row['tender_number']}")

        if DRY_RUN:
            ok_count += 1
            continue

        ok = await update_deal(WEBHOOK_URL, row)
        if ok:
            ok_count += 1
        else:
            fail_count += 1
            logger.warning(f"  ❌ Не удалось обновить сделку {row['bitrix24_deal_id']}")

        await asyncio.sleep(0.3)

    logger.info(f"\n{'=' * 50}")
    logger.info(f"Итог: ✅ {ok_count} обновлено, ❌ {fail_count} ошибок")


if __name__ == '__main__':
    asyncio.run(main())

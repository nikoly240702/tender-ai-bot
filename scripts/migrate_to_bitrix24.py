#!/usr/bin/env python3
"""
Одноразовый скрипт переноса тендеров из БД в Битрикс24.

Переносит уведомления с дедлайном от 05.03.2026 и позже,
которые ещё не экспортированы в Битрикс24.

Запуск:
    DATABASE_URL=postgresql+asyncpg://... \
    BITRIX24_WEBHOOK=https://b24-xxx.bitrix24.ru/rest/1/TOKEN/ \
    python3 scripts/migrate_to_bitrix24.py

    # dry-run (без создания сделок):
    DRY_RUN=1 ... python3 scripts/migrate_to_bitrix24.py
"""

import asyncio
import os
import sys
import logging
from datetime import datetime

import aiohttp

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# ─── Настройки ─────────────────────────────────────────────────
DEADLINE_FROM = datetime(2026, 3, 5)         # дедлайн от этой даты
STAGE_ID_DEFAULT = 'NEW'                     # Новые процедуры
STAGE_ID_LOSE = 'LOSE'                       # Не берем в работу (истёк дедлайн)
WEBHOOK_URL = os.getenv('BITRIX24_WEBHOOK', 'https://b24-gc3ju5.bitrix24.ru/rest/1/il3soo8fwc7108wz/')
DATABASE_URL = os.getenv('DATABASE_URL', '')
DRY_RUN = os.getenv('DRY_RUN', '0') == '1'
# ───────────────────────────────────────────────────────────────


async def fetch_notifications(database_url: str):
    """Читает уведомления из БД."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import select, and_, text

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        result = await session.execute(text("""
            SELECT
                id,
                tender_number,
                tender_name,
                tender_price,
                tender_url,
                tender_region,
                tender_customer,
                filter_name,
                submission_deadline,
                match_info,
                bitrix24_exported,
                bitrix24_deal_id
            FROM sniper_notifications
            WHERE submission_deadline >= :from_date
              AND (bitrix24_exported = false OR bitrix24_exported IS NULL)
            ORDER BY submission_deadline ASC
        """), {'from_date': DEADLINE_FROM})
        rows = result.mappings().all()

    await engine.dispose()
    return [dict(r) for r in rows]


async def mark_exported(database_url: str, notification_id: int, deal_id: int):
    """Помечает уведомление как экспортированное."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import text

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        await session.execute(text("""
            UPDATE sniper_notifications
            SET bitrix24_exported = true,
                bitrix24_exported_at = NOW(),
                bitrix24_deal_id = :deal_id
            WHERE id = :id
        """), {'deal_id': str(deal_id), 'id': notification_id})
        await session.commit()

    await engine.dispose()


def _is_expired(deadline) -> bool:
    if not deadline:
        return False
    if isinstance(deadline, datetime):
        return deadline < datetime.utcnow()
    return False


async def create_deal(webhook_url: str, row: dict) -> int | None:
    """Создаёт сделку в Битрикс24, возвращает deal_id или None."""
    deadline_dt = row.get('submission_deadline')
    closedate = ''
    if isinstance(deadline_dt, datetime):
        closedate = deadline_dt.strftime('%Y-%m-%d')

    stage_id = STAGE_ID_LOSE if _is_expired(deadline_dt) else STAGE_ID_DEFAULT

    # AI-поля из match_info
    mi = row.get('match_info') or {}
    ai_summary = mi.get('ai_summary', '')
    ai_recommendation = mi.get('ai_recommendation', '')

    tender_url = row.get('tender_url') or (
        f"https://zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html"
        f"?regNumber={row['tender_number']}"
    )

    comment_lines = [
        'Тендер из TenderSniper (перенос из таблицы)',
        f"№ {row['tender_number']}",
    ]
    if row.get('tender_customer'):
        comment_lines.append(f"Заказчик: {row['tender_customer']}")
    if row.get('tender_region'):
        comment_lines.append(f"Регион: {row['tender_region']}")
    if row.get('filter_name'):
        comment_lines.append(f"Фильтр: {row['filter_name']}")
    if ai_recommendation or ai_summary:
        ai_text = f"[{ai_recommendation}] {ai_summary}".strip(' []') if ai_recommendation else ai_summary
        comment_lines.extend(['', f"AI: {ai_text}"])
    comment_lines.extend(['', f"Ссылка: {tender_url}"])

    name = row.get('tender_name') or f"Тендер № {row['tender_number']}"

    fields = {
        'TITLE': name[:255],
        'OPPORTUNITY': row.get('tender_price') or 0,
        'CURRENCY_ID': 'RUB',
        'SOURCE_ID': 'WEB',
        'SOURCE_DESCRIPTION': 'TenderSniper Bot',
        'COMMENTS': '\n'.join(comment_lines),
        'STAGE_ID': stage_id,
    }
    if closedate:
        fields['CLOSEDATE'] = closedate

    if not webhook_url.endswith('/'):
        webhook_url += '/'
    endpoint = webhook_url + 'crm.deal.add.json'

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
            async with session.post(endpoint, json={'fields': fields}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get('result')
                    if isinstance(result, int):
                        return result
                    logger.warning(f"Unexpected result: {data}")
                else:
                    body = await resp.text()
                    logger.warning(f"HTTP {resp.status}: {body[:200]}")
    except Exception as e:
        logger.error(f"create_deal error: {e}")

    return None


async def main():
    if not WEBHOOK_URL:
        logger.error("BITRIX24_WEBHOOK не задан")
        sys.exit(1)

    if not DATABASE_URL:
        logger.error("DATABASE_URL не задан")
        sys.exit(1)

    logger.info(f"Режим: {'DRY RUN (без создания сделок)' if DRY_RUN else 'LIVE'}")
    logger.info(f"Дедлайн от: {DEADLINE_FROM.strftime('%d.%m.%Y')}")
    logger.info(f"Webhook: {WEBHOOK_URL[:60]}...")

    logger.info("Загружаю уведомления из БД...")
    rows = await fetch_notifications(DATABASE_URL)
    logger.info(f"Найдено тендеров для переноса: {len(rows)}")

    if not rows:
        logger.info("Нечего переносить.")
        return

    ok_count = 0
    fail_count = 0

    for i, row in enumerate(rows, 1):
        deadline_dt = row.get('submission_deadline')
        deadline_str = deadline_dt.strftime('%d.%m.%Y') if isinstance(deadline_dt, datetime) else '—'
        name = (row.get('tender_name') or '')[:60]

        logger.info(f"[{i}/{len(rows)}] {row['tender_number']} | {deadline_str} | {name}")

        if DRY_RUN:
            ok_count += 1
            continue

        deal_id = await create_deal(WEBHOOK_URL, row)
        if deal_id:
            await mark_exported(DATABASE_URL, row['id'], deal_id)
            logger.info(f"  ✅ Сделка #{deal_id} создана")
            ok_count += 1
        else:
            logger.warning(f"  ❌ Не удалось создать сделку")
            fail_count += 1

        # Пауза чтобы не перегружать Bitrix24 API
        await asyncio.sleep(0.5)

    logger.info(f"\n{'=' * 50}")
    logger.info(f"Итог: ✅ {ok_count} сделок создано, ❌ {fail_count} ошибок")
    if DRY_RUN:
        logger.info("(DRY RUN — сделки не создавались)")


if __name__ == '__main__':
    asyncio.run(main())

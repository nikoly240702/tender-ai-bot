#!/usr/bin/env python3
"""
One-shot cleanup: move stuck Bitrix24 deals to LOSE stage.

Iterates all deals on initial stages (NEW, UC_OZCYR2) directly via Bitrix24 API,
finds those with CLOSEDATE in the past, moves them to LOSE. Bypasses the DB
entirely so it works regardless of missing submission_deadline or missing
per-user webhook_url (the two bugs that let these deals pile up).

Usage:
    BITRIX24_WEBHOOK=https://b24-xxx.bitrix24.ru/rest/1/TOKEN/ \
    python3 scripts/fix_stuck_bitrix24_deals.py [--dry-run]
"""
import argparse
import asyncio
import logging
import os
import ssl
import sys
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
)
logger = logging.getLogger(__name__)


DEFAULT_WEBHOOK = 'https://b24-gc3ju5.bitrix24.ru/rest/1/il3soo8fwc7108wz/'
INITIAL_STAGES = ('NEW', 'UC_OZCYR2')
LOSE_STAGE = 'LOSE'
PAGE_SIZE = 50  # Bitrix24 default


def _normalize_webhook(url: str) -> str:
    return url if url.endswith('/') else url + '/'


def _parse_closedate(value: str) -> Optional[datetime]:
    """Parse Bitrix24 CLOSEDATE string (ISO 8601 with tz) → naive datetime in UTC."""
    if not value:
        return None
    try:
        # e.g. "2026-03-27T03:00:00+03:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except ValueError:
        return None


async def _list_deals_on_stage(
    session: aiohttp.ClientSession,
    webhook: str,
    stage: str,
) -> List[Dict[str, Any]]:
    """Fetch all deals currently on the given stage (paginated)."""
    all_deals: List[Dict[str, Any]] = []
    start = 0
    while True:
        payload = {
            'filter': {'STAGE_ID': stage},
            'select': ['ID', 'STAGE_ID', 'CLOSEDATE', 'TITLE', 'UF_CRM_TENDER_NUMBER'],
            'start': start,
        }
        async with session.post(webhook + 'crm.deal.list', json=payload) as resp:
            data = await resp.json()
            result = data.get('result') or []
            all_deals.extend(result)
            next_start = data.get('next')
            if next_start is None:
                break
            start = next_start
            await asyncio.sleep(0.2)
    return all_deals


async def _move_to_lose(
    session: aiohttp.ClientSession,
    webhook: str,
    deal_id: str,
) -> bool:
    payload = {'id': deal_id, 'fields': {'STAGE_ID': LOSE_STAGE}}
    async with session.post(webhook + 'crm.deal.update', json=payload) as resp:
        data = await resp.json()
        return bool(data.get('result'))


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='Do not modify deals, just report')
    args = parser.parse_args()

    webhook = _normalize_webhook(os.getenv('BITRIX24_WEBHOOK', DEFAULT_WEBHOOK))
    logger.info(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    logger.info(f"Webhook: {webhook[:60]}...")

    now = datetime.utcnow()
    logger.info(f"Now (UTC): {now.isoformat(timespec='seconds')}")

    # Some macOS local environments have issues with the Bitrix24 cert chain;
    # allow opting out of verification via INSECURE_SSL=1. In prod (Railway)
    # this env var is unset and verification is on.
    if os.getenv('INSECURE_SSL') == '1':
        ssl_ctx = ssl._create_unverified_context()
    else:
        ssl_ctx = ssl.create_default_context()
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)

    async with aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        # 1. Collect all deals on initial stages
        all_candidates: List[Dict[str, Any]] = []
        for stage in INITIAL_STAGES:
            deals = await _list_deals_on_stage(session, webhook, stage)
            logger.info(f"Stage {stage}: {len(deals)} deals")
            all_candidates.extend(deals)

        # 2. Filter to expired ones
        to_move: List[Dict[str, Any]] = []
        for deal in all_candidates:
            closedate = _parse_closedate(deal.get('CLOSEDATE', ''))
            if closedate is None:
                continue
            if closedate < now:
                to_move.append(deal)

        logger.info(f"Total expired candidates on initial stages: {len(to_move)}")

        if not to_move:
            logger.info("Nothing to move. Exiting.")
            return

        # Show a few examples
        for d in to_move[:5]:
            logger.info(
                f"  - ID={d['ID']} stage={d['STAGE_ID']} "
                f"closedate={d.get('CLOSEDATE', '')[:10]} "
                f"tender={d.get('UF_CRM_TENDER_NUMBER', '-')}"
            )
        if len(to_move) > 5:
            logger.info(f"  ... and {len(to_move) - 5} more")

        if args.dry_run:
            logger.info("DRY RUN — not modifying any deals.")
            return

        # 3. Move each to LOSE
        moved = 0
        failed = 0
        for i, deal in enumerate(to_move, 1):
            deal_id = deal['ID']
            try:
                ok = await _move_to_lose(session, webhook, deal_id)
                if ok:
                    moved += 1
                    if moved % 20 == 0:
                        logger.info(f"Progress: {moved}/{len(to_move)} moved")
                else:
                    failed += 1
                    logger.warning(f"  ❌ Failed to move deal {deal_id}")
            except Exception as e:
                failed += 1
                logger.warning(f"  ❌ Error moving deal {deal_id}: {e}")
            await asyncio.sleep(0.2)  # rate limit

        logger.info(f"\n{'=' * 50}")
        logger.info(f"Result: ✅ {moved} moved to LOSE, ❌ {failed} failed")


if __name__ == '__main__':
    asyncio.run(main())

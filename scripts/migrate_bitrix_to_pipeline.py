"""One-shot миграция сделок из Bitrix24 в pipeline_cards.

Использование:
    python -m scripts.migrate_bitrix_to_pipeline --company-id 1 [--dry-run] [--limit 1000]

Нужен env BITRIX_WEBHOOK_URL (тот же что у бота). Скрипт идемпотентен —
повторный запуск пропускает уже импортированные карточки (UNIQUE constraint
по (company_id, tender_number)).
"""
import argparse
import asyncio
import logging
import os
import re
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import aiohttp
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database import (
    DatabaseSession, Company,
    PipelineCard, PipelineCardHistory, TenderCache,
)

logger = logging.getLogger(__name__)


STAGE_MAP = {
    'NEW': 'FOUND',
    'UC_OZCYR2': 'IN_WORK',  # AI-стадия Николая
    'LOSE': 'RESULT',
}


async def fetch_deals_page(webhook_url: str, start: int = 0):
    async with aiohttp.ClientSession() as session:
        async with session.get(f'{webhook_url}crm.deal.list',
                               params={'start': start}) as resp:
            return await resp.json()


def _extract_tender_number(text: str) -> str:
    """Ищет 19-20значное число (regNumber тендера)."""
    if not text:
        return ''
    m = re.search(r'\b\d{19,20}\b', text)
    return m.group() if m else ''


async def migrate(company_id: int, dry_run: bool = False, limit: int = 1000) -> bool:
    webhook = os.environ.get('BITRIX_WEBHOOK_URL') or os.environ.get('BITRIX24_WEBHOOK_URL')
    if not webhook:
        logger.error('BITRIX_WEBHOOK_URL not set')
        return False
    if not webhook.endswith('/'):
        webhook += '/'

    imported = skipped = errors = 0
    start = 0
    processed = 0

    while processed < limit:
        try:
            data = await fetch_deals_page(webhook, start)
        except Exception as e:
            logger.error(f'Bitrix API error: {e}', exc_info=True)
            return False

        deals = data.get('result', [])
        if not deals:
            break

        for deal in deals:
            processed += 1
            try:
                tender_number = (
                    deal.get('UF_CRM_TENDER_NUMBER')
                    or _extract_tender_number(
                        (deal.get('TITLE') or '') + ' ' + (deal.get('COMMENTS') or '')
                    )
                )
                if not tender_number:
                    skipped += 1
                    continue

                async with DatabaseSession() as session:
                    cache = await session.scalar(
                        select(TenderCache).where(TenderCache.tender_number == tender_number)
                    )
                    if not cache:
                        logger.warning(f'Tender {tender_number} not in cache — skip')
                        skipped += 1
                        continue

                    company = await session.get(Company, company_id)
                    if not company:
                        logger.error(f'Company {company_id} not found')
                        return False

                    bitrix_stage = deal.get('STAGE_ID', 'NEW')
                    new_stage = STAGE_MAP.get(bitrix_stage, 'IN_WORK')
                    result = 'lost' if bitrix_stage == 'LOSE' else None

                    if dry_run:
                        logger.info(f'DRY: {tender_number} ({bitrix_stage}) -> {new_stage}')
                        imported += 1
                        continue

                    sale_price = float(cache.price) if getattr(cache, 'price', None) else None
                    card = PipelineCard(
                        company_id=company_id,
                        tender_number=tender_number,
                        stage=new_stage,
                        assignee_user_id=company.owner_user_id,
                        source='bitrix_import',
                        result=result,
                        sale_price=Decimal(str(sale_price)) if sale_price else None,
                        ai_summary=deal.get('UF_CRM_AI_SUMMARY'),
                        ai_recommendation=(deal.get('UF_CRM_AI_RECOMMENDATION') or '')[:40] or None,
                        data={
                            'name': getattr(cache, 'name', None),
                            'customer': getattr(cache, 'customer', None),
                            'region': getattr(cache, 'region', None),
                            'price_max': sale_price,
                            'deadline': cache.deadline.isoformat() if getattr(cache, 'deadline', None) else None,
                            'url': f'https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber={tender_number}',
                            'bitrix_deal_id': deal.get('ID'),
                            'bitrix_original_stage': bitrix_stage,
                            'bitrix_opportunity': deal.get('OPPORTUNITY'),
                        },
                        created_by=company.owner_user_id,
                    )
                    session.add(card)
                    await session.flush()
                    history = PipelineCardHistory(
                        card_id=card.id, user_id=company.owner_user_id,
                        action='imported_from_bitrix',
                        payload={
                            'bitrix_deal_id': deal.get('ID'),
                            'original_stage': bitrix_stage,
                        },
                    )
                    session.add(history)
                    try:
                        await session.commit()
                        imported += 1
                    except IntegrityError:
                        await session.rollback()
                        logger.warning(f'Tender {tender_number} already in pipeline')
                        skipped += 1
            except Exception as e:
                logger.error(f'Error processing deal {deal.get("ID", "?")}: {e}', exc_info=True)
                errors += 1

        if 'next' in data:
            start = data['next']
        else:
            break

    print(f'Imported: {imported}, Skipped: {skipped}, Errors: {errors}')
    return errors == 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--company-id', type=int, required=True)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--limit', type=int, default=1000)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    success = asyncio.run(migrate(args.company_id, args.dry_run, args.limit))
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()

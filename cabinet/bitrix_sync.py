"""Синхронизация Pipeline ↔ Bitrix24.

При создании карточки в pipeline → автоматически создаём deal в Bitrix24
(если у владельца команды настроен webhook). При смене стадии в pipeline →
обновляем STAGE_ID в Bitrix. При проигрыше/выигрыше → закрываем сделку.

Также: импорт всех сделок Bitrix → pipeline_cards (используется кнопкой
«Импорт из Bitrix24» в UI).

Принципиально неблокирующее поведение: ошибки Bitrix не должны валить
работу с карточкой. Все вызовы — best-effort, логируем и идём дальше.
"""

from __future__ import annotations

import asyncio
import logging
import re
from decimal import Decimal
from typing import Any, Dict, List, Optional

import aiohttp
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified

from database import (
    DatabaseSession, Company, SniperUser,
    PipelineCard, PipelineCardHistory, TenderCache,
)

logger = logging.getLogger(__name__)


# Pipeline stage → Bitrix STAGE_ID. См. bot/handlers/bitrix24.py
# Маппинг под воронку Николая (UC_OZCYR2 = AI-стадия). Безопасно для других
# порталов: при ошибке update просто логируем.
_STAGE_MAP = {
    'FOUND': 'NEW',
    'IN_WORK': 'UC_OZCYR2',
    'REJECTED': 'LOSE',
}
# Финальные стадии — отдельно, через set_card_result
_RESULT_STAGE_MAP = {
    'won': 'WON',
    'lost': 'LOSE',
}


# ============================================
# Webhook lookup
# ============================================

async def _get_company_webhook(company_id: int) -> Optional[str]:
    """Возвращает webhook владельца команды (или None если не настроен).

    Кабинет хранит webhook в data владельца компании — единая точка для всей команды.
    """
    async with DatabaseSession() as session:
        company = await session.get(Company, company_id)
        if not company:
            return None
        owner = await session.get(SniperUser, company.owner_user_id)
        if not owner:
            return None
        data = owner.data or {}
        webhook = data.get('bitrix24_webhook_url') or data.get('bitrix24_webhook') or ''
        enabled = bool(data.get('bitrix24_enabled', True))
        if not webhook or not enabled:
            return None
        return webhook.strip()


# ============================================
# Push: pipeline event → Bitrix
# ============================================

async def _store_deal_id_on_card(card_id: int, deal_id: int, *,
                                 original_stage: Optional[str] = None) -> None:
    async with DatabaseSession() as session:
        card = await session.get(PipelineCard, card_id)
        if not card:
            return
        data = dict(card.data or {})
        data['bitrix_deal_id'] = deal_id
        if original_stage:
            data['bitrix_stage'] = original_stage
        card.data = data
        flag_modified(card, 'data')
        await session.commit()


async def push_card_created(card_id: int) -> Optional[int]:
    """Создаёт сделку в Bitrix для карточки. Возвращает deal_id или None.

    Best-effort. Ошибки логируются, не пробрасываются.
    """
    try:
        async with DatabaseSession() as session:
            card = await session.get(PipelineCard, card_id)
            if not card:
                return None
            company_id = card.company_id
            data = dict(card.data or {})
            tender_number = card.tender_number
            tender_name = data.get('name') or f'Тендер {tender_number}'
            customer = data.get('customer') or ''
            region = data.get('region') or ''
            tender_url = data.get('url') or ''
            deadline = data.get('deadline') or ''
            filter_name = data.get('filter_name') or ''
            price = data.get('price_max')
            if data.get('bitrix_deal_id'):
                logger.info(f'[bitrix] card {card_id} already has deal {data["bitrix_deal_id"]}')
                return data['bitrix_deal_id']

        webhook = await _get_company_webhook(company_id)
        if not webhook:
            return None

        from bot.handlers.bitrix24 import (
            BITRIX24_FULL_ACCESS_USERS,
            create_bitrix24_deal,
            create_simple_bitrix24_deal,
        )
        # Используем full-version если company.owner есть в списке
        async with DatabaseSession() as session:
            company = await session.get(Company, company_id)
            owner_user_id = company.owner_user_id if company else None

        if owner_user_id in BITRIX24_FULL_ACCESS_USERS:
            deal_id = await create_bitrix24_deal(
                webhook_url=webhook,
                tender_number=tender_number,
                tender_name=tender_name,
                tender_price=float(price) if price else None,
                tender_url=tender_url,
                tender_region=region,
                tender_customer=customer,
                filter_name=filter_name,
                submission_deadline=deadline,
            )
        else:
            deal_id = await create_simple_bitrix24_deal(
                webhook_url=webhook,
                tender_number=tender_number,
                tender_name=tender_name,
                tender_price=float(price) if price else None,
                tender_url=tender_url,
                tender_customer=customer,
                tender_region=region,
                submission_deadline=deadline,
            )

        if deal_id:
            await _store_deal_id_on_card(card_id, int(deal_id), original_stage='NEW')
            logger.info(f'[bitrix] card {card_id} → deal #{deal_id} created')
            return int(deal_id)
        logger.warning(f'[bitrix] failed to create deal for card {card_id}')
        return None
    except Exception as e:
        logger.error(f'[bitrix] push_card_created error card={card_id}: {e}', exc_info=True)
        return None


async def push_stage_changed(card_id: int, new_stage: str) -> None:
    """Обновить STAGE_ID сделки в Bitrix при смене стадии в pipeline."""
    bitrix_stage = _STAGE_MAP.get(new_stage)
    if not bitrix_stage:
        return  # промежуточные стадии (RFQ/QUOTED/SUBMITTED) не маппим
    try:
        async with DatabaseSession() as session:
            card = await session.get(PipelineCard, card_id)
            if not card:
                return
            data = card.data or {}
            deal_id = data.get('bitrix_deal_id')
            company_id = card.company_id
        if not deal_id:
            return
        webhook = await _get_company_webhook(company_id)
        if not webhook:
            return
        from bot.handlers.bitrix24 import update_bitrix24_deal_stage
        ok = await update_bitrix24_deal_stage(webhook, str(deal_id), bitrix_stage)
        logger.info(f'[bitrix] card {card_id} → deal {deal_id} stage={bitrix_stage} ok={ok}')
    except Exception as e:
        logger.error(f'[bitrix] push_stage_changed error card={card_id}: {e}', exc_info=True)


async def push_result_set(card_id: int, result: str) -> None:
    """Перевести сделку в WON/LOSE при выставлении результата."""
    bitrix_stage = _RESULT_STAGE_MAP.get(result)
    if not bitrix_stage:
        return
    try:
        async with DatabaseSession() as session:
            card = await session.get(PipelineCard, card_id)
            if not card:
                return
            data = card.data or {}
            deal_id = data.get('bitrix_deal_id')
            company_id = card.company_id
        if not deal_id:
            return
        webhook = await _get_company_webhook(company_id)
        if not webhook:
            return
        from bot.handlers.bitrix24 import update_bitrix24_deal_stage
        ok = await update_bitrix24_deal_stage(webhook, str(deal_id), bitrix_stage)
        logger.info(f'[bitrix] card {card_id} → deal {deal_id} result={result} ok={ok}')
    except Exception as e:
        logger.error(f'[bitrix] push_result_set error card={card_id}: {e}', exc_info=True)


def fire_and_forget(coro) -> None:
    """Запустить корутину как background task с защитой от warn про unawaited."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(coro)

    def _log_exc(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            logger.error(f'[bitrix] background task failed: {exc}')

    task.add_done_callback(_log_exc)


# ============================================
# Pull: import existing deals → pipeline
# ============================================

# Bitrix STAGE_ID → pipeline stage (для импорта)
_IMPORT_STAGE_MAP = {
    'NEW': 'FOUND',
    'UC_OZCYR2': 'IN_WORK',
    'PREPARATION': 'IN_WORK',
    'PREPAYMENT_INVOICE': 'RFQ',
    'EXECUTING': 'SUBMITTED',
    'FINAL_INVOICE': 'SUBMITTED',
    'WON': 'RESULT',
    'LOSE': 'RESULT',
}


def _extract_tender_number(text: str) -> str:
    if not text:
        return ''
    m = re.search(r'\b\d{19,20}\b', text)
    return m.group() if m else ''


def _extract_tender_number_from_deal(deal: Dict[str, Any]) -> str:
    """Ищет regNumber в нескольких местах сделки: явное UF-поле,
    URL (regNumber=...), TITLE, COMMENTS и любые UF_CRM_* строковые значения.
    Поля у разных порталов могут называться по-разному, поэтому делаем
    максимально широкий поиск.
    """
    direct = (deal.get('UF_CRM_TENDER_NUMBER')
              or deal.get('UF_CRM_TENDER_NUM')
              or deal.get('UF_CRM_NUMBER')
              or deal.get('UF_CRM_REG_NUMBER'))
    if direct:
        s = str(direct).strip()
        if re.fullmatch(r'\d{19,20}', s):
            return s

    # regNumber=... в URL-полях
    for key, value in deal.items():
        if not value or not isinstance(value, str):
            continue
        m = re.search(r'regNumber=(\d{19,20})', value)
        if m:
            return m.group(1)

    # 19-20значное число в TITLE/COMMENTS
    for key in ('TITLE', 'COMMENTS', 'SOURCE_DESCRIPTION'):
        v = deal.get(key)
        if isinstance(v, str):
            m = re.search(r'\b\d{19,20}\b', v)
            if m:
                return m.group()

    # последний шанс — скан всех строковых значений
    for value in deal.values():
        if isinstance(value, str):
            m = re.search(r'\b\d{19,20}\b', value)
            if m:
                return m.group()
    return ''


async def _fetch_all_deals(webhook: str) -> List[Dict[str, Any]]:
    """Берёт все сделки через crm.deal.list с пагинацией."""
    if not webhook.endswith('/'):
        webhook += '/'
    deals: List[Dict[str, Any]] = []
    start = 0
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        for _ in range(200):  # safety: до 100K сделок
            try:
                async with session.get(f'{webhook}crm.deal.list',
                                       params={'start': start}) as resp:
                    data = await resp.json()
            except Exception as e:
                logger.error(f'[bitrix-import] fetch error: {e}')
                break
            page = data.get('result') or []
            if not page:
                break
            deals.extend(page)
            if 'next' not in data:
                break
            start = data['next']
    return deals


async def import_deals_to_pipeline(company_id: int) -> Dict[str, int]:
    """Тянет все сделки из Bitrix24 и создаёт карточки. Идемпотентно —
    повторный запуск пропускает уже импортированные (UNIQUE на tender_number).
    Возвращает {imported, skipped, errors, total}.
    """
    logger.info(f'[bitrix-import] START company_id={company_id}')
    webhook = await _get_company_webhook(company_id)
    if not webhook:
        logger.warning(f'[bitrix-import] no webhook for company {company_id}')
        return {'imported': 0, 'skipped': 0, 'errors': 0, 'total': 0,
                'error': 'Webhook Bitrix24 не настроен. Проверьте Настройки → Интеграции.'}
    logger.info(f'[bitrix-import] webhook ok, host={webhook[:50]}...')

    deals = await _fetch_all_deals(webhook)
    logger.info(f'[bitrix-import] fetched {len(deals)} deals for company {company_id}')
    if deals:
        sample = deals[0]
        uf_keys = [k for k in sample.keys() if k.startswith('UF_')]
        logger.info(
            f'[bitrix-import] sample deal #{sample.get("ID")}: '
            f'TITLE={(sample.get("TITLE") or "")[:60]}, '
            f'STAGE_ID={sample.get("STAGE_ID")}, '
            f'UF_keys={uf_keys}'
        )
        # Покажу значения UF-полей на первой сделке — увидим где лежит номер
        for k in uf_keys[:15]:
            v = sample.get(k)
            if v:
                logger.info(f'[bitrix-import]   {k}={str(v)[:120]}')
    if not deals:
        return {'imported': 0, 'skipped': 0, 'errors': 0, 'total': 0,
                'error': 'Bitrix24 не вернул ни одной сделки. Проверьте права у webhook (нужен CRM).'}

    imported = skipped = errors = 0

    async with DatabaseSession() as session:
        company = await session.get(Company, company_id)
        if not company:
            return {'imported': 0, 'skipped': 0, 'errors': 0, 'total': len(deals),
                    'error': 'company not found'}
        owner_user_id = company.owner_user_id

    no_number_samples: List[str] = []
    for deal in deals:
        try:
            tender_number = _extract_tender_number_from_deal(deal)
            if not tender_number:
                # Без номера тендера не можем сделать UNIQUE-ключ
                if len(no_number_samples) < 3:
                    no_number_samples.append(
                        f'#{deal.get("ID")} TITLE={(deal.get("TITLE") or "")[:60]}'
                    )
                skipped += 1
                continue

            bitrix_stage = deal.get('STAGE_ID', 'NEW')
            new_stage = _IMPORT_STAGE_MAP.get(bitrix_stage, 'IN_WORK')
            result = None
            if bitrix_stage == 'LOSE':
                result = 'lost'
            elif bitrix_stage == 'WON':
                result = 'won'

            # Берём мету сначала из TenderCache, фолбэк — из самого Bitrix-deal
            async with DatabaseSession() as session:
                cache = await session.scalar(
                    select(TenderCache).where(TenderCache.tender_number == tender_number)
                )
                exists = await session.scalar(
                    select(PipelineCard).where(
                        PipelineCard.company_id == company_id,
                        PipelineCard.tender_number == tender_number,
                    )
                )
                if exists:
                    # Уже в пайплайне — обновим только bitrix_deal_id если ещё нет
                    data = dict(exists.data or {})
                    if not data.get('bitrix_deal_id'):
                        data['bitrix_deal_id'] = deal.get('ID')
                        data['bitrix_stage'] = bitrix_stage
                        exists.data = data
                        flag_modified(exists, 'data')
                        await session.commit()
                    skipped += 1
                    continue

                # Собираем data
                opportunity = deal.get('OPPORTUNITY')
                try:
                    sale_price = float(opportunity) if opportunity else None
                except (TypeError, ValueError):
                    sale_price = None
                if cache:
                    cache_price = float(cache.price) if getattr(cache, 'price', None) else None
                else:
                    cache_price = None
                final_price = cache_price or sale_price

                deadline_raw = deal.get('CLOSEDATE') or ''
                if cache and getattr(cache, 'deadline', None):
                    deadline_str = cache.deadline.isoformat()
                else:
                    deadline_str = (deadline_raw or '')[:10] or None

                card_data = {
                    'name': (getattr(cache, 'name', None) if cache else None)
                            or (deal.get('TITLE') or '')[:255],
                    'customer': (getattr(cache, 'customer', None) if cache else None)
                                or deal.get('UF_CRM_TENDER_CUSTOMER') or '',
                    'region': (getattr(cache, 'region', None) if cache else None)
                              or deal.get('UF_CRM_TENDER_REGION') or '',
                    'price_max': final_price,
                    'deadline': deadline_str,
                    'url': deal.get('UF_CRM_TENDER_URL')
                           or f'https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber={tender_number}',
                    'bitrix_deal_id': deal.get('ID'),
                    'bitrix_stage': bitrix_stage,
                    'bitrix_opportunity': opportunity,
                }

                card = PipelineCard(
                    company_id=company_id,
                    tender_number=tender_number,
                    stage=new_stage,
                    assignee_user_id=owner_user_id,
                    source='bitrix_import',
                    result=result,
                    sale_price=Decimal(str(final_price)) if final_price else None,
                    ai_summary=deal.get('UF_CRM_AI_SUMMARY'),
                    ai_recommendation=(deal.get('UF_CRM_AI_RECOMMENDATION') or '')[:40] or None,
                    data=card_data,
                    created_by=owner_user_id,
                )
                session.add(card)
                await session.flush()
                history = PipelineCardHistory(
                    card_id=card.id, user_id=owner_user_id,
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
                    skipped += 1
        except Exception as e:
            logger.error(f'[bitrix-import] deal {deal.get("ID","?")}: {e}', exc_info=True)
            errors += 1

    logger.info(f'[bitrix-import] done company={company_id} '
                f'imported={imported} skipped={skipped} errors={errors}')
    if no_number_samples:
        logger.warning(
            f'[bitrix-import] no tender_number samples: {no_number_samples}'
        )
    return {'imported': imported, 'skipped': skipped, 'errors': errors,
            'total': len(deals)}

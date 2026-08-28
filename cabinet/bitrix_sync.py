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
import hmac
import logging
import re
import secrets
from datetime import datetime
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


async def get_or_create_inbound_secret(company_id: int) -> Optional[str]:
    """Секрет для проверки входящих событий Bitrix (в URL исходящего вебхука).
    Генерируется один раз при первом обращении, дальше стабилен.
    """
    async with DatabaseSession() as session:
        company = await session.get(Company, company_id)
        if not company:
            return None
        owner = await session.get(SniperUser, company.owner_user_id)
        if not owner:
            return None
        data = dict(owner.data or {})
        secret = data.get('bitrix_inbound_secret')
        if secret:
            return secret
        secret = secrets.token_urlsafe(24)
        data['bitrix_inbound_secret'] = secret
        owner.data = data
        flag_modified(owner, 'data')
        await session.commit()
        return secret


async def rotate_inbound_secret(company_id: int) -> Optional[str]:
    """Перегенерировать секрет (например, при подозрении на утечку)."""
    async with DatabaseSession() as session:
        company = await session.get(Company, company_id)
        if not company:
            return None
        owner = await session.get(SniperUser, company.owner_user_id)
        if not owner:
            return None
        data = dict(owner.data or {})
        secret = secrets.token_urlsafe(24)
        data['bitrix_inbound_secret'] = secret
        owner.data = data
        flag_modified(owner, 'data')
        await session.commit()
        return secret


async def verify_inbound_secret(company_id: int, secret: str) -> bool:
    if not secret:
        return False
    async with DatabaseSession() as session:
        company = await session.get(Company, company_id)
        if not company:
            return False
        owner = await session.get(SniperUser, company.owner_user_id)
        if not owner:
            return False
        stored = (owner.data or {}).get('bitrix_inbound_secret')
        return bool(stored) and hmac.compare_digest(stored, secret)


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
                filter_name=filter_name,
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


async def push_assignee_changed(card_id: int, assignee_user_id: int) -> None:
    """Обновить ASSIGNED_BY_ID сделки в Bitrix когда кто-то берёт карточку."""
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
            user = await session.get(SniperUser, assignee_user_id)
            if not user:
                return
            user_data = user.data or {}
            bitrix_id = user_data.get('bitrix_user_id')
        if not bitrix_id:
            logger.debug(f'[bitrix] user {assignee_user_id} has no bitrix_user_id mapping')
            return
        webhook = await _get_company_webhook(company_id)
        if not webhook:
            return
        from bot.handlers.bitrix24 import update_bitrix24_deal_assignee
        ok = await update_bitrix24_deal_assignee(webhook, str(deal_id), int(bitrix_id))
        logger.info(f'[bitrix] card {card_id} → deal {deal_id} assignee={bitrix_id} ok={ok}')
    except Exception as e:
        logger.error(f'[bitrix] push_assignee_changed error card={card_id}: {e}', exc_info=True)


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


_TRACKED_DEAL_FIELDS = [
    'TITLE', 'OPPORTUNITY', 'UF_CRM_TENDER_CUSTOMER',
    'UF_CRM_TENDER_REGION', 'CLOSEDATE', 'ASSIGNED_BY_ID', 'STAGE_ID',
]


def _snapshot_from_deal(deal: Dict[str, Any]) -> Dict[str, Any]:
    """Срез отслеживаемых полей сделки — для diff при следующем событии."""
    return {f: deal.get(f) for f in _TRACKED_DEAL_FIELDS}


def _build_card_data_from_deal(
    deal: Dict[str, Any], cache: Optional[TenderCache], tender_number: str,
) -> Dict[str, Any]:
    """Собирает card.data из сделки Bitrix (+ фолбэк на TenderCache).
    Вынесено из import_deals_to_pipeline без изменения поведения.
    """
    opportunity = deal.get('OPPORTUNITY')
    try:
        sale_price = float(opportunity) if opportunity else None
    except (TypeError, ValueError):
        sale_price = None
    cache_price = float(cache.price) if (cache and getattr(cache, 'price', None)) else None
    final_price = cache_price or sale_price

    deadline_raw = deal.get('CLOSEDATE') or ''
    if cache and getattr(cache, 'deadline', None):
        deadline_str = cache.deadline.isoformat()
    else:
        deadline_str = (deadline_raw or '')[:10] or None

    return {
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
        'bitrix_stage': deal.get('STAGE_ID', 'NEW'),
        'bitrix_opportunity': opportunity,
        'bitrix_snapshot': _snapshot_from_deal(deal),
    }


async def _create_card_from_deal(
    session, company_id: int, owner_user_id: int, tender_number: str,
    deal: Dict[str, Any], cache: Optional[TenderCache], source: str,
    history_action: str,
) -> PipelineCard:
    """Создаёт PipelineCard + запись истории из сделки Bitrix. Не коммитит —
    вызывающий код решает, когда коммитить (нужно для try/except IntegrityError
    в bulk-импорте).
    """
    bitrix_stage = deal.get('STAGE_ID', 'NEW')
    new_stage = _IMPORT_STAGE_MAP.get(bitrix_stage, 'IN_WORK')
    result = None
    if bitrix_stage == 'LOSE':
        result = 'lost'
    elif bitrix_stage == 'WON':
        result = 'won'

    card_data = _build_card_data_from_deal(deal, cache, tender_number)
    final_price = card_data.get('price_max')

    card = PipelineCard(
        company_id=company_id,
        tender_number=tender_number,
        stage=new_stage,
        assignee_user_id=owner_user_id,
        source=source,
        result=result,
        sale_price=Decimal(str(final_price)) if final_price else None,
        ai_summary=deal.get('UF_CRM_AI_SUMMARY'),
        ai_recommendation=(deal.get('UF_CRM_AI_RECOMMENDATION') or '')[:40] or None,
        data=card_data,
        created_by=owner_user_id,
    )
    session.add(card)
    await session.flush()
    session.add(PipelineCardHistory(
        card_id=card.id, user_id=owner_user_id,
        action=history_action,
        payload={'bitrix_deal_id': deal.get('ID'), 'original_stage': bitrix_stage},
    ))
    return card


async def _diff_and_log_field_changes(
    session, card: PipelineCard, deal: Dict[str, Any], owner_user_id: int,
) -> None:
    """Сравнивает отслеживаемые поля со снимком в card.data['bitrix_snapshot'],
    пишет по одной записи истории на каждое изменённое поле (кроме STAGE_ID —
    им занимается _pull_stage_update, и ASSIGNED_BY_ID — им _sync_assignee_from_deal).
    Обновляет снимок в конце независимо от того, было ли изменение.
    """
    data = dict(card.data or {})
    old_snapshot = data.get('bitrix_snapshot') or {}
    new_snapshot = _snapshot_from_deal(deal)

    for field in _TRACKED_DEAL_FIELDS:
        if field in ('STAGE_ID', 'ASSIGNED_BY_ID'):
            continue
        if field not in old_snapshot:
            continue  # первое наблюдение поля — не считаем изменением
        old_val = old_snapshot.get(field)
        new_val = new_snapshot.get(field)
        if old_val == new_val:
            continue
        session.add(PipelineCardHistory(
            card_id=card.id, user_id=owner_user_id,
            action='bitrix_field_changed',
            payload={'field': field, 'old': old_val, 'new': new_val},
        ))

    data['bitrix_snapshot'] = new_snapshot
    card.data = data
    flag_modified(card, 'data')


async def _sync_assignee_from_deal(
    session, company_id: int, card: PipelineCard, deal: Dict[str, Any], owner_user_id: int,
) -> None:
    """Отражает смену ответственного в Bitrix. Если новый ASSIGNED_BY_ID
    сопоставляется с sniper_users этой компании (по data['bitrix_user_id']) —
    обновляет assignee_user_id. Если нет — оставляет assignee_user_id как есть
    (FK-целостность) и только сохраняет отображаемое имя для UI.
    """
    from database import CompanyMember

    if 'ASSIGNED_BY_ID' not in deal:
        return  # malformed/partial deal dict — do not treat as "assignee cleared"

    new_assigned = deal.get('ASSIGNED_BY_ID')
    data = dict(card.data or {})
    old_snapshot = data.get('bitrix_snapshot') or {}
    old_assigned = old_snapshot.get('ASSIGNED_BY_ID')

    # Bitrix's API may return numeric fields as strings; str()-normalize both
    # sides so a str/int mismatch never silently defeats the comparison.
    def _s(v):
        return str(v) if v is not None else None

    if 'ASSIGNED_BY_ID' not in old_snapshot or _s(old_assigned) == _s(new_assigned):
        return

    matched_user_id: Optional[int] = None
    members = (await session.execute(
        select(CompanyMember).where(CompanyMember.company_id == company_id)
    )).scalars().all()
    candidate_ids = {m.user_id for m in members}
    owner = await session.get(SniperUser, owner_user_id)
    if owner:
        candidate_ids.add(owner.id)
    for user_id in candidate_ids:
        user = await session.get(SniperUser, user_id)
        if user and _s((user.data or {}).get('bitrix_user_id')) == _s(new_assigned):
            matched_user_id = user.id
            break

    if matched_user_id:
        card.assignee_user_id = matched_user_id
        data.pop('bitrix_assigned_name', None)
    else:
        display_name = deal.get('ASSIGNED_BY_NAME') or f'Bitrix user #{new_assigned}'
        data['bitrix_assigned_name'] = display_name

    session.add(PipelineCardHistory(
        card_id=card.id, user_id=owner_user_id,
        action='bitrix_field_changed',
        payload={'field': 'ASSIGNED_BY_ID', 'old': old_assigned, 'new': new_assigned},
    ))
    card.data = data
    flag_modified(card, 'data')


async def _fetch_all_deals(webhook: str) -> List[Dict[str, Any]]:
    """Берёт все сделки через crm.deal.list с пагинацией.

    Передаём select[]=*&select[]=UF_* — иначе Bitrix возвращает только
    базовые поля без кастомных UF_*.
    """
    if not webhook.endswith('/'):
        webhook += '/'
    deals: List[Dict[str, Any]] = []
    start = 0
    base_params = [('select[]', '*'), ('select[]', 'UF_*')]
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        for _ in range(200):  # safety: до 100K сделок
            params = base_params + [('start', str(start))]
            try:
                async with session.get(f'{webhook}crm.deal.list',
                                       params=params) as resp:
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

                card = await _create_card_from_deal(
                    session, company_id, owner_user_id, tender_number,
                    deal, cache, source='bitrix_import',
                    history_action='imported_from_bitrix',
                )
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


# ============================================
# Pull: периодическое отслеживание изменений в Bitrix
# ============================================

# Bitrix STAGE_ID → (pipeline stage, result). Только эти стадии вытягиваем
# обратно — для остальных промежуточных шагов (RFQ/QUOTED/SUBMITTED) Bitrix
# не источник правды.
#
# Внимание: REJECTED → LOSE при push, но при pull мы трактуем LOSE как
# result=lost. Если карточка уже REJECTED — стадию не трогаем (см. логику ниже).
_PULL_STAGE_MAP: Dict[str, Dict[str, Any]] = {
    'NEW': {'stage': 'FOUND', 'result': None},
    'UC_OZCYR2': {'stage': 'IN_WORK', 'result': None},
    'WON': {'stage': 'RESULT', 'result': 'won'},
    'LOSE': {'stage': 'RESULT', 'result': 'lost'},
}

# Stage ordering for anti-rollback logic: pipeline is source of truth
# for forward progress. If pipeline card is at a later stage than what
# Bitrix reports, we keep the pipeline stage.
_STAGE_ORDER = {
    'FOUND': 0, 'IN_WORK': 1, 'RFQ': 2, 'QUOTED': 3,
    'SUBMITTED': 4, 'RESULT': 5, 'REJECTED': 5,
}


def _pull_stage_update(card: PipelineCard, stage_id: str) -> Optional[Dict[str, Any]]:
    """Anti-rollback обновление стадии/результата карточки по стадии из Bitrix.
    Мутирует card на месте если нужно применить изменение; возвращает payload
    для записи в историю, или None если применять нечего.
    """
    mapping = _PULL_STAGE_MAP.get(stage_id)
    if not mapping:
        return None  # промежуточная стадия — не трогаем

    target_stage = mapping['stage']
    target_result = mapping['result']

    current_idx = _STAGE_ORDER.get(card.stage, 0)
    target_idx = _STAGE_ORDER.get(target_stage, 0)
    if target_idx < current_idx:
        return None

    if card.stage == 'REJECTED' and stage_id == 'LOSE':
        return None

    stage_changed = card.stage != target_stage
    result_changed = (target_result is not None and card.result != target_result)
    if not (stage_changed or result_changed):
        return None

    old_stage, old_result = card.stage, card.result
    card.stage = target_stage
    if target_result is not None:
        card.result = target_result
    return {
        'from_stage': old_stage, 'to_stage': target_stage,
        'from_result': old_result, 'to_result': target_result,
        'bitrix_stage_id': stage_id,
    }


async def _find_card_by_bitrix_deal_id(session, company_id: int, deal_id: str) -> Optional[PipelineCard]:
    """Ищет карточку компании по data['bitrix_deal_id'] сравнением в Python —
    портируемо между SQLite (тесты) и Postgres (прод), без JSON-операторов, и
    без риска подстрокового ложняка (`900` не совпадёт с `9000`).
    """
    rows = await session.execute(
        select(PipelineCard).where(PipelineCard.company_id == company_id)
    )
    deal_id_str = str(deal_id)
    for card in rows.scalars().all():
        if str((card.data or {}).get('bitrix_deal_id', '')) == deal_id_str:
            return card
    return None


async def _get_last_sync_at(company_id: int) -> Optional[str]:
    async with DatabaseSession() as session:
        company = await session.get(Company, company_id)
        if not company:
            return None
        owner = await session.get(SniperUser, company.owner_user_id)
        if not owner:
            return None
        return (owner.data or {}).get('last_bitrix_sync_at')


async def _set_last_sync_at(company_id: int, iso_str: str) -> None:
    async with DatabaseSession() as session:
        company = await session.get(Company, company_id)
        if not company:
            return
        owner = await session.get(SniperUser, company.owner_user_id)
        if not owner:
            return
        data = dict(owner.data or {})
        data['last_bitrix_sync_at'] = iso_str
        owner.data = data
        flag_modified(owner, 'data')
        await session.commit()


async def _fetch_modified_deals(webhook: str, since_iso: Optional[str]) -> List[Dict[str, Any]]:
    """Сделки изменённые после since_iso (или все, если None).

    Bitrix формат для DATE_MODIFY: YYYY-MM-DDTHH:MM:SS+TZ. ISO от datetime.utcnow
    Bitrix принимает.
    """
    if not webhook.endswith('/'):
        webhook += '/'
    deals: List[Dict[str, Any]] = []
    start = 0
    base_params: List[tuple] = [('select[]', '*'), ('select[]', 'UF_*')]
    if since_iso:
        base_params.append(('filter[>DATE_MODIFY]', since_iso))
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        for _ in range(200):
            params = base_params + [('start', str(start))]
            try:
                async with session.get(f'{webhook}crm.deal.list', params=params) as resp:
                    data = await resp.json()
            except Exception as e:
                logger.error(f'[bitrix-pull] fetch error: {e}')
                break
            page = data.get('result') or []
            if not page:
                break
            deals.extend(page)
            if 'next' not in data:
                break
            start = data['next']
    return deals


async def pull_changes_from_bitrix(company_id: int) -> Dict[str, int]:
    """Опрашивает Bitrix на изменения и обновляет стадии карточек pipeline.

    Идемпотентно. Возвращает {checked, updated, errors, since}.
    Карточка обновляется только если у неё есть bitrix_deal_id и текущая
    стадия в Bitrix отличается от того, что у нас.
    """
    from datetime import datetime as _dt
    logger.info(f'[bitrix-pull] START company={company_id}')
    webhook = await _get_company_webhook(company_id)
    if not webhook:
        return {'checked': 0, 'updated': 0, 'errors': 0,
                'error': 'webhook не настроен'}

    # Owner используется как «системный» актёр для записи в history,
    # т.к. user_id NOT NULL.
    async with DatabaseSession() as session:
        company = await session.get(Company, company_id)
        owner_user_id = company.owner_user_id if company else None
    if not owner_user_id:
        return {'checked': 0, 'updated': 0, 'errors': 0,
                'error': 'company not found'}

    since = await _get_last_sync_at(company_id)
    # Отметка, на которую сдвинем после успешной синки. Снимаем её ДО запроса
    # чтобы не пропустить deals изменённые во время выполнения.
    next_since = _dt.utcnow().replace(microsecond=0).isoformat()
    deals = await _fetch_modified_deals(webhook, since)
    logger.info(f'[bitrix-pull] since={since} → fetched {len(deals)} modified deals')

    updated = errors = 0
    for deal in deals:
        try:
            deal_id = deal.get('ID')
            if not deal_id:
                continue
            stage_id = deal.get('STAGE_ID')

            async with DatabaseSession() as session:
                card = await _find_card_by_bitrix_deal_id(session, company_id, deal_id)
                if not card:
                    continue

                update_payload = _pull_stage_update(card, stage_id)
                if update_payload is None:
                    continue

                card.updated_at = _dt.utcnow()
                update_payload['bitrix_deal_id'] = deal_id
                history = PipelineCardHistory(
                    card_id=card.id, user_id=owner_user_id,
                    action='bitrix_pull',
                    payload=update_payload,
                )
                session.add(history)
                await session.commit()
                updated += 1
        except Exception as e:
            logger.error(f'[bitrix-pull] deal {deal.get("ID","?")}: {e}', exc_info=True)
            errors += 1

    await _set_last_sync_at(company_id, next_since)
    logger.info(f'[bitrix-pull] done company={company_id} '
                f'checked={len(deals)} updated={updated} errors={errors}')
    return {'checked': len(deals), 'updated': updated, 'errors': errors,
            'since': since, 'next_since': next_since}


async def pull_changes_for_all_companies() -> Dict[str, Any]:
    """Опрашивает все команды у которых настроен Bitrix-webhook."""
    async with DatabaseSession() as session:
        rows = await session.execute(select(Company))
        companies = list(rows.scalars().all())

    total_updated = 0
    total_checked = 0
    company_count = 0
    for c in companies:
        webhook = await _get_company_webhook(c.id)
        if not webhook:
            continue
        company_count += 1
        try:
            result = await pull_changes_from_bitrix(c.id)
            total_checked += result.get('checked', 0)
            total_updated += result.get('updated', 0)
        except Exception as e:
            logger.error(f'[bitrix-pull-all] company={c.id}: {e}', exc_info=True)
    return {'companies': company_count, 'checked': total_checked, 'updated': total_updated}


# ============================================
# Inbound: Bitrix webhook event → pipeline
# ============================================

_HANDLED_EVENTS = {'ONCRMDEALADD', 'ONCRMDEALUPDATE', 'ONCRMDEALMOVETOCATEGORY', 'ONCRMDEALDELETE'}


async def _handle_deal_deleted(company_id: int, deal_id: str, owner_user_id: int) -> None:
    async with DatabaseSession() as session:
        card = await _find_card_by_bitrix_deal_id(session, company_id, deal_id)
        if not card or card.archived_at:
            return
        card.archived_at = datetime.utcnow()
        session.add(PipelineCardHistory(
            card_id=card.id, user_id=owner_user_id,
            action='bitrix_deal_deleted',
            payload={'bitrix_deal_id': deal_id},
        ))
        await session.commit()


async def handle_deal_event(company_id: int, event: str, deal_id: str) -> None:
    """Обрабатывает одно событие исходящего вебхука Bitrix для сделки.
    Best-effort: любая ошибка логируется и не пробрасывается дальше.
    """
    event = (event or '').upper()
    if event not in _HANDLED_EVENTS:
        return
    try:
        webhook = await _get_company_webhook(company_id)
        if not webhook:
            return

        async with DatabaseSession() as session:
            company = await session.get(Company, company_id)
            owner_user_id = company.owner_user_id if company else None
        if not owner_user_id:
            return

        if event == 'ONCRMDEALDELETE':
            await _handle_deal_deleted(company_id, deal_id, owner_user_id)
            return

        from bot.handlers.bitrix24 import get_bitrix24_deal
        deal = await get_bitrix24_deal(webhook, deal_id)
        if not deal:
            logger.warning(f'[bitrix-event] deal {deal_id} not found via API (event={event})')
            return

        tender_number = _extract_tender_number_from_deal(deal)

        async with DatabaseSession() as session:
            card = await _find_card_by_bitrix_deal_id(session, company_id, deal_id)
            if not card:
                if not tender_number:
                    logger.warning(f'[bitrix-event] deal {deal_id} has no tender number, skip create')
                    return

                # A PipelineCard may already exist for this tender number
                # (created some other way — the tender-monitoring feed, or a
                # Bitrix push that failed before the deal_id got stored) but
                # without a bitrix_deal_id yet. Backfill the link onto it
                # instead of attempting a doomed duplicate create that would
                # just hit the uq_pipeline_company_tender constraint every
                # time this event repeats, permanently losing the link.
                existing = await session.scalar(
                    select(PipelineCard).where(
                        PipelineCard.company_id == company_id,
                        PipelineCard.tender_number == tender_number,
                    )
                )
                if existing:
                    existing_data = dict(existing.data or {})
                    if not existing_data.get('bitrix_deal_id'):
                        existing_data['bitrix_deal_id'] = deal_id
                        existing_data['bitrix_stage'] = deal.get('STAGE_ID', 'NEW')
                        existing_data['bitrix_snapshot'] = _snapshot_from_deal(deal)
                        existing.data = existing_data
                        flag_modified(existing, 'data')
                        await session.commit()
                        logger.info(
                            f'[bitrix-event] backfilled bitrix_deal_id={deal_id} onto '
                            f'existing card {existing.id} (tender {tender_number})'
                        )
                    return

                cache = await session.scalar(
                    select(TenderCache).where(TenderCache.tender_number == tender_number)
                )
                try:
                    await _create_card_from_deal(
                        session, company_id, owner_user_id, tender_number,
                        deal, cache, source='bitrix_event',
                        history_action='card_created_from_bitrix',
                    )
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                return

            stage_id = deal.get('STAGE_ID')
            update_payload = _pull_stage_update(card, stage_id) if stage_id else None
            if update_payload:
                update_payload['bitrix_deal_id'] = deal_id
                session.add(PipelineCardHistory(
                    card_id=card.id, user_id=owner_user_id,
                    action='bitrix_pull', payload=update_payload,
                ))
            elif stage_id and stage_id not in _PULL_STAGE_MAP:
                # Genuinely unmapped Bitrix stage (not just anti-rollback
                # declining a mapped one) — Pipeline's own stage vocabulary
                # doesn't cover it, but the move should still be visible.
                old_stage_id = ((card.data or {}).get('bitrix_snapshot') or {}).get('STAGE_ID')
                if old_stage_id != stage_id:
                    session.add(PipelineCardHistory(
                        card_id=card.id, user_id=owner_user_id,
                        action='bitrix_field_changed',
                        payload={'field': 'STAGE_ID', 'old': old_stage_id, 'new': stage_id},
                    ))

            await _diff_and_log_field_changes(session, card, deal, owner_user_id)
            await _sync_assignee_from_deal(session, company_id, card, deal, owner_user_id)
            card.updated_at = datetime.utcnow()
            await session.commit()
        logger.info(f'[bitrix-event] company={company_id} event={event} deal={deal_id} processed')
    except Exception as e:
        logger.error(f'[bitrix-event] error company={company_id} event={event} deal={deal_id}: {e}', exc_info=True)


# ============================================
# Comments: periodic poll (no push event exists for this in Bitrix)
# ============================================

async def sync_comments_for_company(company_id: int) -> Dict[str, int]:
    """Подтягивает новые комментарии ленты Bitrix для всех активных карточек
    компании, у которых есть привязка к сделке. Возвращает {checked, added}.
    """
    webhook = await _get_company_webhook(company_id)
    if not webhook:
        return {'checked': 0, 'added': 0}

    async with DatabaseSession() as session:
        rows = await session.execute(
            select(PipelineCard).where(
                PipelineCard.company_id == company_id,
                PipelineCard.archived_at.is_(None),
            )
        )
        cards = [c for c in rows.scalars().all() if (c.data or {}).get('bitrix_deal_id')]

    if not cards:
        return {'checked': 0, 'added': 0}

    deal_since = {
        str(c.data['bitrix_deal_id']): int(c.data.get('bitrix_last_comment_id') or 0)
        for c in cards
    }
    # "Never synced" (key absent) must NOT be treated the same as "synced
    # before, cursor happens to be 0" (key present with value 0) — the former
    # would otherwise dump a deal's entire historical comment log into
    # PipelineCardHistory on its very first sync. These cards still get
    # fetched (since_id=0, so we learn the current max id) but their
    # comments are only used to seed the cursor, never logged as history.
    first_observation = {
        str(c.data['bitrix_deal_id'])
        for c in cards
        if 'bitrix_last_comment_id' not in (c.data or {})
    }

    from bot.handlers.bitrix24 import batch_list_deal_comments
    try:
        comments_by_deal = await batch_list_deal_comments(webhook, deal_since)
    except Exception as e:
        logger.error(f'[bitrix-comments] company={company_id} batch error: {e}', exc_info=True)
        return {'checked': len(cards), 'added': 0}

    added = 0
    async with DatabaseSession() as session:
        company = await session.get(Company, company_id)
        owner_user_id = company.owner_user_id if company else None
        for card_stub in cards:
            deal_id = str(card_stub.data['bitrix_deal_id'])
            new_comments = comments_by_deal.get(deal_id) or []
            is_first_observation = deal_id in first_observation
            if not new_comments and not is_first_observation:
                continue
            card = await session.get(PipelineCard, card_stub.id)
            data = dict(card.data or {})
            max_id = int(data.get('bitrix_last_comment_id') or 0)
            for comment in new_comments:
                if not is_first_observation:
                    session.add(PipelineCardHistory(
                        card_id=card.id, user_id=owner_user_id,
                        action='bitrix_comment',
                        payload={
                            'author': comment.get('AUTHOR_ID'),
                            'text': comment.get('COMMENT'),
                            'created': comment.get('CREATED'),
                        },
                    ))
                    added += 1
                try:
                    max_id = max(max_id, int(comment.get('ID')))
                except (TypeError, ValueError):
                    pass
            data['bitrix_last_comment_id'] = max_id
            card.data = data
            flag_modified(card, 'data')
        await session.commit()

    return {'checked': len(cards), 'added': added}

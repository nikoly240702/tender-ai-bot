"""Доменная логика Pipeline: создание карточек, смена стадий, история, файлы.

Все функции принимают и возвращают plain dicts.
"""

import logging
import os
import re
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional, List, Dict

from sqlalchemy import select, update, func

from database import (
    DatabaseSession, SniperUser, Company,
    PipelineCard, PipelineCardHistory, PipelineCardNote,
    PipelineCardFile, PipelineCardChecklist, PipelineCardRelation,
    TenderCache,
)

logger = logging.getLogger(__name__)


# Stages
STAGE_FOUND = 'FOUND'
STAGE_IN_WORK = 'IN_WORK'
STAGE_RFQ = 'RFQ'
STAGE_QUOTED = 'QUOTED'
STAGE_SUBMITTED = 'SUBMITTED'
STAGE_RESULT = 'RESULT'

ALL_STAGES = [STAGE_FOUND, STAGE_IN_WORK, STAGE_RFQ, STAGE_QUOTED, STAGE_SUBMITTED, STAGE_RESULT]

STAGE_LABELS = {
    'FOUND': 'Найденные',
    'IN_WORK': 'Взято в работу',
    'RFQ': 'Запрос предложений',
    'QUOTED': 'Получено КП',
    'SUBMITTED': 'Участвуем',
    'RESULT': 'Результат',
}

# Source
SOURCE_FEED = 'feed'
SOURCE_MANUAL = 'manual'
SOURCE_BITRIX_IMPORT = 'bitrix_import'

# Result
RESULT_WON = 'won'
RESULT_LOST = 'lost'

# Files
UPLOAD_ROOT = Path(os.environ.get('UPLOAD_ROOT', '/app/uploads'))
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
TEAM_FILE_QUOTA = 1024 * 1024 * 1024  # 1 GB

# Archive
ARCHIVE_AGE_DAYS = 90


def _decimal_to_float(value) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _card_dict(card: PipelineCard) -> Dict:
    return {
        'id': card.id,
        'company_id': card.company_id,
        'tender_number': card.tender_number,
        'stage': card.stage,
        'assignee_user_id': card.assignee_user_id,
        'filter_id': card.filter_id,
        'source': card.source,
        'result': card.result,
        'purchase_price': _decimal_to_float(card.purchase_price),
        'sale_price': _decimal_to_float(card.sale_price),
        'ai_summary': card.ai_summary,
        'ai_recommendation': card.ai_recommendation,
        'ai_enriched_at': card.ai_enriched_at.isoformat() if card.ai_enriched_at else None,
        'archived_at': card.archived_at.isoformat() if card.archived_at else None,
        'data': card.data or {},
        'created_at': card.created_at.isoformat() if card.created_at else None,
        'created_by': card.created_by,
        'updated_at': card.updated_at.isoformat() if card.updated_at else None,
    }


def calc_margin(purchase: Optional[float], sale: Optional[float]) -> Optional[Dict]:
    """Возвращает {abs, pct, color} или None."""
    if purchase is None or sale is None or sale == 0:
        return None
    abs_margin = sale - purchase
    pct = (abs_margin / sale) * 100
    if pct >= 5:
        color = 'positive'
    elif pct >= 0:
        color = 'warn'
    else:
        color = 'alert'
    return {'abs': abs_margin, 'pct': pct, 'color': color}


def _safe_filename(name: str) -> str:
    name = re.sub(r'[^\w\s.\-а-яА-ЯёЁ]', '_', name, flags=re.UNICODE)
    name = name.replace('..', '_').strip()[:200]
    return name or 'file'


# ============================================
# Card CRUD + stage transitions
# ============================================

async def create_card_from_tender(
    company_id: int,
    tender_number: str,
    creator_user_id: int,
    filter_id: Optional[int] = None,
    source: str = SOURCE_FEED,
) -> Dict:
    """Создаёт карточку в стадии FOUND. Берёт мета из tender_cache.
    Возвращает {card} или {error: 'already_exists', existing_card}.
    """
    async with DatabaseSession() as session:
        existing = await session.scalar(
            select(PipelineCard).where(
                PipelineCard.company_id == company_id,
                PipelineCard.tender_number == tender_number,
            )
        )
        if existing:
            return {'error': 'already_exists', 'existing_card': _card_dict(existing)}

        cache = await session.scalar(
            select(TenderCache).where(TenderCache.tender_number == tender_number)
        )
        cache_data: Dict = {}
        if cache:
            cache_data = {
                'name': getattr(cache, 'name', None),
                'customer': getattr(cache, 'customer', None),
                'region': getattr(cache, 'region', None),
                'price_max': float(cache.price) if getattr(cache, 'price', None) is not None else None,
                'deadline': cache.deadline.isoformat() if getattr(cache, 'deadline', None) else None,
                'url': f'https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber={tender_number}',
                'law_type': getattr(cache, 'law_type', None),
            }

        sale_price_default = cache_data.get('price_max')
        card = PipelineCard(
            company_id=company_id,
            tender_number=tender_number,
            stage=STAGE_FOUND,
            assignee_user_id=creator_user_id,
            filter_id=filter_id,
            source=source,
            sale_price=Decimal(str(sale_price_default)) if sale_price_default else None,
            data=cache_data,
            created_by=creator_user_id,
        )
        session.add(card)
        await session.flush()
        history = PipelineCardHistory(
            card_id=card.id, user_id=creator_user_id, action='created',
            payload={'source': source},
        )
        session.add(history)
        await session.commit()
        return {'card': _card_dict(card)}


async def move_card_stage(card_id: int, new_stage: str, by_user_id: int) -> Dict:
    if new_stage not in ALL_STAGES:
        return {'ok': False, 'error': f'Недопустимая стадия: {new_stage}'}
    async with DatabaseSession() as session:
        card = await session.get(PipelineCard, card_id)
        if not card:
            return {'ok': False, 'error': 'Карточка не найдена'}
        old_stage = card.stage
        if old_stage == new_stage:
            return {'ok': True, 'card': _card_dict(card), 'unchanged': True}
        card.stage = new_stage
        card.updated_at = datetime.utcnow()
        history = PipelineCardHistory(
            card_id=card.id, user_id=by_user_id, action='stage_changed',
            payload={'from': old_stage, 'to': new_stage},
        )
        session.add(history)
        await session.commit()
        return {'ok': True, 'card': _card_dict(card)}


async def set_card_result(card_id: int, result: str, by_user_id: int) -> Dict:
    if result not in (RESULT_WON, RESULT_LOST):
        return {'ok': False, 'error': f'Недопустимый result: {result}'}
    async with DatabaseSession() as session:
        card = await session.get(PipelineCard, card_id)
        if not card:
            return {'ok': False, 'error': 'Карточка не найдена'}
        card.stage = STAGE_RESULT
        card.result = result
        card.updated_at = datetime.utcnow()
        history = PipelineCardHistory(
            card_id=card.id, user_id=by_user_id,
            action='won' if result == RESULT_WON else 'lost',
            payload={},
        )
        session.add(history)
        await session.commit()
        return {'ok': True, 'card': _card_dict(card)}


async def set_assignee(card_id: int, assignee_user_id: int, by_user_id: int) -> Dict:
    async with DatabaseSession() as session:
        card = await session.get(PipelineCard, card_id)
        if not card:
            return {'ok': False, 'error': 'Не найдено'}
        old = card.assignee_user_id
        card.assignee_user_id = assignee_user_id
        card.updated_at = datetime.utcnow()
        history = PipelineCardHistory(
            card_id=card_id, user_id=by_user_id, action='assigned',
            payload={'from': old, 'to': assignee_user_id},
        )
        session.add(history)
        await session.commit()
        return {'ok': True, 'card': _card_dict(card)}


async def set_prices(card_id: int, purchase_price: Optional[float],
                     sale_price: Optional[float], by_user_id: int) -> Dict:
    async with DatabaseSession() as session:
        card = await session.get(PipelineCard, card_id)
        if not card:
            return {'ok': False, 'error': 'Не найдено'}
        if purchase_price is not None:
            card.purchase_price = Decimal(str(purchase_price))
        if sale_price is not None:
            card.sale_price = Decimal(str(sale_price))
        card.updated_at = datetime.utcnow()
        history = PipelineCardHistory(
            card_id=card_id, user_id=by_user_id, action='price_set',
            payload={'purchase': purchase_price, 'sale': sale_price},
        )
        session.add(history)
        await session.commit()
        return {'ok': True, 'card': _card_dict(card)}


async def delete_card(card_id: int, by_user_id: int, is_owner: bool) -> Dict:
    if not is_owner:
        return {'ok': False, 'error': 'Только owner может удалять карточки'}
    async with DatabaseSession() as session:
        card = await session.get(PipelineCard, card_id)
        if not card:
            return {'ok': False, 'error': 'Не найдено'}
        await session.delete(card)
        await session.commit()
        return {'ok': True}


async def unarchive_card(card_id: int, company_id: int, by_user_id: int) -> Dict:
    async with DatabaseSession() as session:
        card = await session.get(PipelineCard, card_id)
        if not card or card.company_id != company_id:
            return {'ok': False, 'error': 'Не найдено'}
        card.archived_at = None
        card.updated_at = datetime.utcnow()
        await session.commit()
        return {'ok': True, 'card': _card_dict(card)}


# ============================================
# Read-only listings
# ============================================

async def list_company_cards(company_id: int, include_archived: bool = False) -> List[Dict]:
    async with DatabaseSession() as session:
        q = select(PipelineCard).where(PipelineCard.company_id == company_id)
        if not include_archived:
            q = q.where(PipelineCard.archived_at.is_(None))
        result = await session.execute(q.order_by(PipelineCard.updated_at.desc()))
        return [_card_dict(c) for c in result.scalars().all()]


async def list_archived_cards(company_id: int) -> List[Dict]:
    async with DatabaseSession() as session:
        result = await session.execute(
            select(PipelineCard).where(
                PipelineCard.company_id == company_id,
                PipelineCard.archived_at.is_not(None),
            ).order_by(PipelineCard.archived_at.desc())
        )
        return [_card_dict(c) for c in result.scalars().all()]


async def get_card(card_id: int, company_id: int) -> Optional[Dict]:
    """Возвращает карточку только если она в указанной company (RBAC)."""
    async with DatabaseSession() as session:
        card = await session.scalar(
            select(PipelineCard).where(
                PipelineCard.id == card_id,
                PipelineCard.company_id == company_id,
            )
        )
        return _card_dict(card) if card else None


# ============================================
# Notes
# ============================================

async def add_note(card_id: int, text: str, by_user_id: int) -> Dict:
    text = (text or '').strip()
    if not text:
        return {'ok': False, 'error': 'Заметка пуста'}
    async with DatabaseSession() as session:
        note = PipelineCardNote(card_id=card_id, user_id=by_user_id, text=text)
        session.add(note)
        history = PipelineCardHistory(
            card_id=card_id, user_id=by_user_id, action='note_added', payload={},
        )
        session.add(history)
        await session.commit()
        return {'ok': True, 'note': {
            'id': note.id, 'text': note.text, 'user_id': note.user_id,
            'created_at': note.created_at.isoformat() if note.created_at else None,
        }}


async def list_notes(card_id: int) -> List[Dict]:
    async with DatabaseSession() as session:
        result = await session.execute(
            select(PipelineCardNote).where(PipelineCardNote.card_id == card_id)
            .order_by(PipelineCardNote.created_at.desc())
        )
        return [{
            'id': n.id, 'text': n.text, 'user_id': n.user_id,
            'created_at': n.created_at.isoformat() if n.created_at else None,
        } for n in result.scalars().all()]


# ============================================
# History
# ============================================

async def list_history(card_id: int, limit: int = 100) -> List[Dict]:
    async with DatabaseSession() as session:
        result = await session.execute(
            select(PipelineCardHistory).where(PipelineCardHistory.card_id == card_id)
            .order_by(PipelineCardHistory.created_at.desc()).limit(limit)
        )
        return [{
            'id': h.id, 'user_id': h.user_id, 'action': h.action,
            'payload': h.payload or {},
            'created_at': h.created_at.isoformat() if h.created_at else None,
        } for h in result.scalars().all()]


# ============================================
# Files
# ============================================

async def list_files(card_id: int) -> List[Dict]:
    async with DatabaseSession() as session:
        result = await session.execute(
            select(PipelineCardFile).where(PipelineCardFile.card_id == card_id)
            .order_by(PipelineCardFile.uploaded_at.desc())
        )
        return [{
            'id': f.id, 'filename': f.filename, 'size': f.size,
            'mime_type': f.mime_type, 'uploaded_by': f.uploaded_by,
            'uploaded_at': f.uploaded_at.isoformat() if f.uploaded_at else None,
            'is_generated': f.is_generated,
        } for f in result.scalars().all()]


async def total_team_files_size(company_id: int) -> int:
    async with DatabaseSession() as session:
        result = await session.execute(
            select(func.coalesce(func.sum(PipelineCardFile.size), 0))
            .select_from(PipelineCardFile)
            .join(PipelineCard, PipelineCard.id == PipelineCardFile.card_id)
            .where(PipelineCard.company_id == company_id)
        )
        return int(result.scalar() or 0)


async def save_file(card_id: int, company_id: int, original_name: str,
                    content: bytes, mime_type: str, by_user_id: int) -> Dict:
    if len(content) > MAX_FILE_SIZE:
        return {'ok': False, 'error': f'Файл больше {MAX_FILE_SIZE // 1024 // 1024} МБ'}
    used = await total_team_files_size(company_id)
    if used + len(content) > TEAM_FILE_QUOTA:
        return {'ok': False, 'error': 'Превышена квота команды (1 GB)'}

    safe = _safe_filename(original_name)
    folder = UPLOAD_ROOT / str(company_id) / str(card_id)
    folder.mkdir(parents=True, exist_ok=True)

    async with DatabaseSession() as session:
        pf = PipelineCardFile(
            card_id=card_id, uploaded_by=by_user_id,
            filename=safe, size=len(content), mime_type=mime_type,
            path='', is_generated=False,
        )
        session.add(pf)
        await session.flush()
        target_path = folder / f'{pf.id}_{safe}'
        target_path.write_bytes(content)
        pf.path = str(target_path)
        history = PipelineCardHistory(
            card_id=card_id, user_id=by_user_id, action='file_uploaded',
            payload={'filename': safe, 'file_id': pf.id},
        )
        session.add(history)
        await session.commit()
        return {'ok': True, 'file': {
            'id': pf.id, 'filename': pf.filename, 'size': pf.size,
            'mime_type': pf.mime_type,
            'uploaded_at': pf.uploaded_at.isoformat() if pf.uploaded_at else None,
        }}


async def delete_file(file_id: int, company_id: int, by_user_id: int) -> Dict:
    async with DatabaseSession() as session:
        pf = await session.get(PipelineCardFile, file_id)
        if not pf:
            return {'ok': False, 'error': 'Не найдено'}
        card = await session.get(PipelineCard, pf.card_id)
        if not card or card.company_id != company_id:
            return {'ok': False, 'error': 'Forbidden'}
        try:
            Path(pf.path).unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f'Не удалось удалить файл {pf.path}: {e}')
        history = PipelineCardHistory(
            card_id=pf.card_id, user_id=by_user_id, action='file_deleted',
            payload={'filename': pf.filename, 'file_id': pf.id},
        )
        session.add(history)
        await session.delete(pf)
        await session.commit()
        return {'ok': True}


async def get_file_for_download(file_id: int, company_id: int) -> Optional[Dict]:
    async with DatabaseSession() as session:
        pf = await session.get(PipelineCardFile, file_id)
        if not pf:
            return None
        card = await session.get(PipelineCard, pf.card_id)
        if not card or card.company_id != company_id:
            return None
        return {'path': pf.path, 'filename': pf.filename, 'mime_type': pf.mime_type}


# ============================================
# Checklist
# ============================================

async def list_checklist(card_id: int) -> List[Dict]:
    async with DatabaseSession() as session:
        result = await session.execute(
            select(PipelineCardChecklist).where(PipelineCardChecklist.card_id == card_id)
            .order_by(PipelineCardChecklist.position, PipelineCardChecklist.created_at)
        )
        return [{
            'id': c.id, 'text': c.text, 'done': c.done, 'position': c.position,
            'created_by': c.created_by, 'done_by': c.done_by,
            'done_at': c.done_at.isoformat() if c.done_at else None,
            'created_at': c.created_at.isoformat() if c.created_at else None,
        } for c in result.scalars().all()]


async def add_checklist(card_id: int, text: str, by_user_id: int) -> Dict:
    text = (text or '').strip()
    if not text:
        return {'ok': False, 'error': 'Пустой пункт'}
    async with DatabaseSession() as session:
        # Узнаём max position
        max_pos = await session.scalar(
            select(func.coalesce(func.max(PipelineCardChecklist.position), -1))
            .where(PipelineCardChecklist.card_id == card_id)
        )
        item = PipelineCardChecklist(
            card_id=card_id, text=text[:500], position=(max_pos or 0) + 1,
            created_by=by_user_id,
        )
        session.add(item)
        history = PipelineCardHistory(
            card_id=card_id, user_id=by_user_id, action='checklist_added',
            payload={'text': text[:500]},
        )
        session.add(history)
        await session.commit()
        return {'ok': True, 'item': {
            'id': item.id, 'text': item.text, 'done': False, 'position': item.position,
        }}


async def toggle_checklist(item_id: int, done: bool, by_user_id: int) -> Dict:
    async with DatabaseSession() as session:
        item = await session.get(PipelineCardChecklist, item_id)
        if not item:
            return {'ok': False, 'error': 'Не найдено'}
        item.done = done
        if done:
            item.done_by = by_user_id
            item.done_at = datetime.utcnow()
            history = PipelineCardHistory(
                card_id=item.card_id, user_id=by_user_id, action='checklist_done',
                payload={'item_id': item.id, 'text': item.text},
            )
            session.add(history)
        else:
            item.done_by = None
            item.done_at = None
        await session.commit()
        return {'ok': True}


async def delete_checklist(item_id: int) -> Dict:
    async with DatabaseSession() as session:
        item = await session.get(PipelineCardChecklist, item_id)
        if not item:
            return {'ok': False, 'error': 'Не найдено'}
        await session.delete(item)
        await session.commit()
        return {'ok': True}


# ============================================
# Relations
# ============================================

async def add_relation(card_id: int, related_tender_number: str,
                       company_id: int, by_user_id: int,
                       kind: str = 'manual') -> Dict:
    async with DatabaseSession() as session:
        related = await session.scalar(
            select(PipelineCard).where(
                PipelineCard.company_id == company_id,
                PipelineCard.tender_number == related_tender_number,
            )
        )
        if not related:
            return {'ok': False, 'error': 'Связанный тендер не найден в pipeline'}
        if related.id == card_id:
            return {'ok': False, 'error': 'Нельзя связать карточку с собой'}
        rel = PipelineCardRelation(
            card_id=card_id, related_card_id=related.id, kind=kind,
            created_by=by_user_id,
        )
        session.add(rel)
        history = PipelineCardHistory(
            card_id=card_id, user_id=by_user_id, action='related_added',
            payload={'related_card_id': related.id},
        )
        session.add(history)
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            return {'ok': False, 'error': 'Связь уже существует'}
        return {'ok': True}


async def list_relations(card_id: int) -> List[Dict]:
    async with DatabaseSession() as session:
        result = await session.execute(
            select(PipelineCardRelation, PipelineCard).join(
                PipelineCard, PipelineCard.id == PipelineCardRelation.related_card_id
            ).where(PipelineCardRelation.card_id == card_id)
        )
        out = []
        for rel, card in result.all():
            out.append({
                'id': rel.id,
                'kind': rel.kind,
                'related_card_id': card.id,
                'related_tender_number': card.tender_number,
                'related_name': (card.data or {}).get('name'),
                'related_stage': card.stage,
            })
        return out


async def delete_relation(relation_id: int, company_id: int) -> Dict:
    async with DatabaseSession() as session:
        rel = await session.get(PipelineCardRelation, relation_id)
        if not rel:
            return {'ok': False, 'error': 'Не найдено'}
        # Проверим что card принадлежит company
        card = await session.get(PipelineCard, rel.card_id)
        if not card or card.company_id != company_id:
            return {'ok': False, 'error': 'Forbidden'}
        await session.delete(rel)
        await session.commit()
        return {'ok': True}


# ============================================
# AI enrichment
# ============================================

async def enrich_card_with_ai(card_id: int, by_user_id: int) -> Dict:
    """Запускает AI-анализ карточки. Проверяет квоту owner-а, инкрементит счётчик.
    Возвращает {ok, started: True} сразу — реальная работа в фоне.
    """
    async with DatabaseSession() as session:
        card = await session.get(PipelineCard, card_id)
        if not card:
            return {'ok': False, 'error': 'Не найдено', 'status': 404}
        company = await session.get(Company, card.company_id)
        if not company:
            return {'ok': False, 'error': 'Команда не найдена', 'status': 404}
        owner = await session.get(SniperUser, company.owner_user_id)
        if not owner:
            return {'ok': False, 'error': 'Owner не найден', 'status': 404}

        used = owner.ai_analyses_used_month or 0
        tier = owner.subscription_tier or 'trial'
        # Лимит AI анализов в месяц по тарифу. Premium/business — безлимит.
        limits = {'trial': 0, 'starter': 0, 'pro': 500, 'premium': 999999, 'business': 999999}
        limit = limits.get(tier, 0)
        if owner.has_ai_unlimited:
            limit = 999999
        if limit == 0 or used >= limit:
            return {'ok': False, 'error': 'Квота AI исчерпана', 'status': 402}

        owner.ai_analyses_used_month = used + 1
        await session.commit()

    asyncio.create_task(_do_ai_enrich(card_id, by_user_id))
    return {'ok': True, 'started': True}


async def _do_ai_enrich(card_id: int, by_user_id: int):
    """Background task: вызывает существующие AI-функции, обновляет карточку."""
    try:
        async with DatabaseSession() as session:
            card = await session.get(PipelineCard, card_id)
            if not card:
                return
            tender_data = card.data or {}

            summary = None
            recommendation = None
            try:
                from tender_sniper.ai_summarizer import summarize_tender
                summary = await summarize_tender(tender_data)
            except Exception as e:
                logger.warning(f'AI summarizer failed: {e}')
            try:
                from tender_sniper.ai_relevance_checker import check_relevance
                recommendation = await check_relevance(tender_data, {'name': 'pipeline'})
            except Exception as e:
                logger.warning(f'AI relevance checker failed: {e}')

            card.ai_summary = (summary or '')[:2000] if summary else None
            card.ai_recommendation = (recommendation or '')[:40] if recommendation else None
            card.ai_enriched_at = datetime.utcnow()
            history = PipelineCardHistory(
                card_id=card_id, user_id=by_user_id, action='ai_enriched', payload={},
            )
            session.add(history)
            await session.commit()
    except Exception as e:
        logger.error(f'AI enrich failed for card {card_id}: {e}', exc_info=True)


# ============================================
# Owner dashboard
# ============================================

async def team_dashboard(company_id: int) -> Dict:
    """Метрики команды для owner."""
    async with DatabaseSession() as session:
        total = await session.scalar(
            select(func.count()).select_from(PipelineCard)
            .where(PipelineCard.company_id == company_id, PipelineCard.archived_at.is_(None))
        )

        result = await session.execute(
            select(PipelineCard.stage, func.count()).select_from(PipelineCard)
            .where(PipelineCard.company_id == company_id, PipelineCard.archived_at.is_(None))
            .group_by(PipelineCard.stage)
        )
        by_stage = {s: c for s, c in result.all()}

        result = await session.execute(
            select(PipelineCard.assignee_user_id, func.count()).select_from(PipelineCard)
            .where(PipelineCard.company_id == company_id, PipelineCard.archived_at.is_(None))
            .group_by(PipelineCard.assignee_user_id)
        )
        per_member = {(uid or 0): c for uid, c in result.all()}

        cutoff_7 = datetime.utcnow() - timedelta(days=7)
        stale_q = await session.execute(
            select(PipelineCard).where(
                PipelineCard.company_id == company_id,
                PipelineCard.archived_at.is_(None),
                PipelineCard.updated_at < cutoff_7,
                PipelineCard.stage != STAGE_RESULT,
            ).order_by(PipelineCard.updated_at).limit(20)
        )
        stale = [_card_dict(c) for c in stale_q.scalars().all()]

        cutoff_30 = datetime.utcnow() - timedelta(days=30)
        won_count = await session.scalar(
            select(func.count()).select_from(PipelineCard)
            .where(PipelineCard.company_id == company_id,
                   PipelineCard.result == RESULT_WON,
                   PipelineCard.updated_at >= cutoff_30)
        ) or 0
        lost_count = await session.scalar(
            select(func.count()).select_from(PipelineCard)
            .where(PipelineCard.company_id == company_id,
                   PipelineCard.result == RESULT_LOST,
                   PipelineCard.updated_at >= cutoff_30)
        ) or 0
        won_sum = await session.scalar(
            select(func.coalesce(func.sum(PipelineCard.sale_price), 0)).select_from(PipelineCard)
            .where(PipelineCard.company_id == company_id,
                   PipelineCard.result == RESULT_WON,
                   PipelineCard.updated_at >= cutoff_30)
        ) or 0
        margin_sum = await session.scalar(
            select(func.coalesce(func.sum(PipelineCard.sale_price - PipelineCard.purchase_price), 0))
            .select_from(PipelineCard)
            .where(PipelineCard.company_id == company_id,
                   PipelineCard.result == RESULT_WON,
                   PipelineCard.purchase_price.is_not(None),
                   PipelineCard.updated_at >= cutoff_30)
        ) or 0

        return {
            'total_active': int(total or 0),
            'by_stage': {s: int(c) for s, c in by_stage.items()},
            'per_member': {int(uid): int(c) for uid, c in per_member.items()},
            'stale': stale,
            'last30': {
                'won': int(won_count),
                'lost': int(lost_count),
                'won_sum': float(won_sum or 0),
                'margin_sum': float(margin_sum or 0),
            },
        }


# ============================================
# Archive job
# ============================================

async def archive_old_lost_cards() -> int:
    """Архивирует lost-карточки старше ARCHIVE_AGE_DAYS. Возвращает количество."""
    cutoff = datetime.utcnow() - timedelta(days=ARCHIVE_AGE_DAYS)
    async with DatabaseSession() as session:
        result = await session.execute(
            update(PipelineCard)
            .where(
                PipelineCard.result == RESULT_LOST,
                PipelineCard.archived_at.is_(None),
                PipelineCard.updated_at < cutoff,
            )
            .values(archived_at=datetime.utcnow())
        )
        await session.commit()
        return result.rowcount or 0

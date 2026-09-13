"""Поставщики и их предложения (цены) по карточкам пайплайна.

Не путать с _modal_supplier.html ("Запрос поставщикам") — тот модал
генерирует AI-письмо для рассылки внешним поставщикам. Этот модуль —
учёт того, что поставщики УЖЕ прислали в ответ, чтобы сравнивать цены
разных поставщиков по одному тендеру.
"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database import DatabaseSession, Supplier, PipelineCardQuote, PipelineCard, PipelineCardHistory

logger = logging.getLogger(__name__)


def _supplier_dict(s: Supplier) -> Dict:
    return {
        'id': s.id,
        'company_id': s.company_id,
        'name': s.name,
        'contact': s.contact,
        'created_at': s.created_at.isoformat() if s.created_at else None,
    }


def _quote_dict(q: PipelineCardQuote, supplier_name: Optional[str] = None) -> Dict:
    return {
        'id': q.id,
        'card_id': q.card_id,
        'supplier_id': q.supplier_id,
        'supplier_name': supplier_name,
        'price': float(q.price) if q.price is not None else None,
        'notes': q.notes,
        'created_by': q.created_by,
        'created_at': q.created_at.isoformat() if q.created_at else None,
    }


async def list_suppliers(company_id: int) -> List[Dict]:
    async with DatabaseSession() as session:
        result = await session.execute(
            select(Supplier).where(Supplier.company_id == company_id).order_by(Supplier.name)
        )
        return [_supplier_dict(s) for s in result.scalars().all()]


async def create_supplier(company_id: int, name: str, contact: Optional[str],
                          by_user_id: int) -> Dict:
    name = (name or '').strip()
    if not name:
        return {'ok': False, 'error': 'Укажите название поставщика'}
    async with DatabaseSession() as session:
        supplier = Supplier(
            company_id=company_id, name=name, contact=(contact or '').strip() or None,
            created_by=by_user_id,
        )
        session.add(supplier)
        await session.commit()
        return {'ok': True, 'supplier': _supplier_dict(supplier)}


async def update_supplier(supplier_id: int, company_id: int,
                          name: Optional[str], contact: Optional[str]) -> Dict:
    async with DatabaseSession() as session:
        supplier = await session.scalar(
            select(Supplier).where(Supplier.id == supplier_id, Supplier.company_id == company_id)
        )
        if not supplier:
            return {'ok': False, 'error': 'Поставщик не найден'}
        if name is not None:
            name = name.strip()
            if not name:
                return {'ok': False, 'error': 'Название не может быть пустым'}
            supplier.name = name
        if contact is not None:
            supplier.contact = contact.strip() or None
        await session.commit()
        return {'ok': True, 'supplier': _supplier_dict(supplier)}


async def delete_supplier(supplier_id: int, company_id: int) -> Dict:
    async with DatabaseSession() as session:
        supplier = await session.scalar(
            select(Supplier).where(Supplier.id == supplier_id, Supplier.company_id == company_id)
        )
        if not supplier:
            return {'ok': False, 'error': 'Поставщик не найден'}
        await session.delete(supplier)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return {'ok': False, 'error': 'У поставщика уже есть предложения по тендерам — сначала удалите их'}
        return {'ok': True}


async def list_quotes(card_id: int, company_id: int) -> List[Dict]:
    async with DatabaseSession() as session:
        card = await session.get(PipelineCard, card_id)
        if not card or card.company_id != company_id:
            return []
        result = await session.execute(
            select(PipelineCardQuote, Supplier)
            .join(Supplier, Supplier.id == PipelineCardQuote.supplier_id)
            .where(PipelineCardQuote.card_id == card_id)
            .order_by(PipelineCardQuote.price)
        )
        return [_quote_dict(q, s.name) for q, s in result.all()]


async def add_quote(card_id: int, company_id: int, supplier_id: Optional[int],
                    new_supplier_name: Optional[str], price: float,
                    notes: Optional[str], by_user_id: int) -> Dict:
    """supplier_id — существующий поставщик; если не задан, создаём нового
    по new_supplier_name (кабинетный флоу «нет в списке — создать новый»)."""
    async with DatabaseSession() as session:
        card = await session.get(PipelineCard, card_id)
        if not card or card.company_id != company_id:
            return {'ok': False, 'error': 'Карточка не найдена'}

        if not supplier_id:
            name = (new_supplier_name or '').strip()
            if not name:
                return {'ok': False, 'error': 'Укажите поставщика'}
            supplier = Supplier(company_id=company_id, name=name, created_by=by_user_id)
            session.add(supplier)
            await session.flush()
            supplier_id = supplier.id
            supplier_name = supplier.name
        else:
            supplier = await session.scalar(
                select(Supplier).where(Supplier.id == supplier_id, Supplier.company_id == company_id)
            )
            if not supplier:
                return {'ok': False, 'error': 'Поставщик не найден'}
            supplier_name = supplier.name

        quote = PipelineCardQuote(
            card_id=card_id, supplier_id=supplier_id,
            price=Decimal(str(price)), notes=(notes or '').strip() or None,
            created_by=by_user_id,
        )
        session.add(quote)
        await session.flush()

        session.add(PipelineCardHistory(
            card_id=card_id, user_id=by_user_id, action='quote_added',
            payload={'supplier_name': supplier_name, 'price': float(quote.price)},
        ))
        await session.commit()
        return {'ok': True, 'quote': _quote_dict(quote, supplier_name)}


async def delete_quote(quote_id: int, company_id: int) -> Dict:
    async with DatabaseSession() as session:
        quote = await session.get(PipelineCardQuote, quote_id)
        if not quote:
            return {'ok': False, 'error': 'Предложение не найдено'}
        card = await session.get(PipelineCard, quote.card_id)
        if not card or card.company_id != company_id:
            return {'ok': False, 'error': 'Предложение не найдено'}
        await session.delete(quote)
        await session.commit()
        return {'ok': True}

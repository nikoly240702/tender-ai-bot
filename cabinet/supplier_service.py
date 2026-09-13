"""Поставщики, их каталог позиций и предложения (позиции закупки) по
карточкам пайплайна.

Не путать с _modal_supplier.html ("Запрос поставщикам") — тот модал
генерирует AI-письмо для рассылки внешним поставщикам. Этот модуль —
учёт того, что поставщики УЖЕ прислали в ответ: цены за единицу по
конкретным позициям, с накоплением в каталог поставщика (база знаний)
для повторного использования в новых тендерах.
"""

import logging
from decimal import Decimal
from typing import Optional, List, Dict

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

from database import (
    DatabaseSession, Supplier, SupplierProduct, PipelineCardQuote,
    PipelineCard, PipelineCardHistory,
)

logger = logging.getLogger(__name__)


def _supplier_dict(s: Supplier) -> Dict:
    return {
        'id': s.id,
        'company_id': s.company_id,
        'name': s.name,
        'contact': s.contact,
        'website': s.website,
        'contact_person': s.contact_person,
        'created_at': s.created_at.isoformat() if s.created_at else None,
    }


def _product_dict(p: SupplierProduct) -> Dict:
    return {
        'id': p.id,
        'supplier_id': p.supplier_id,
        'name': p.name,
        'unit_price': float(p.unit_price) if p.unit_price is not None else None,
        'updated_at': p.updated_at.isoformat() if p.updated_at else None,
    }


def _quote_dict(q: PipelineCardQuote, supplier_name: Optional[str] = None) -> Dict:
    unit_price = float(q.unit_price) if q.unit_price is not None else None
    quantity = float(q.quantity) if q.quantity is not None else None
    return {
        'id': q.id,
        'card_id': q.card_id,
        'supplier_id': q.supplier_id,
        'supplier_name': supplier_name,
        'supplier_product_id': q.supplier_product_id,
        'product_name': q.product_name,
        'unit_price': unit_price,
        'quantity': quantity,
        'line_total': (unit_price * quantity) if unit_price is not None and quantity is not None else None,
        'notes': q.notes,
        'created_by': q.created_by,
        'created_at': q.created_at.isoformat() if q.created_at else None,
    }


# ============================================
# Suppliers CRUD
# ============================================

async def list_suppliers(company_id: int) -> List[Dict]:
    async with DatabaseSession() as session:
        result = await session.execute(
            select(Supplier).where(Supplier.company_id == company_id).order_by(Supplier.name)
        )
        return [_supplier_dict(s) for s in result.scalars().all()]


async def create_supplier(company_id: int, name: str, contact: Optional[str],
                          website: Optional[str], contact_person: Optional[str],
                          by_user_id: int) -> Dict:
    name = (name or '').strip()
    if not name:
        return {'ok': False, 'error': 'Укажите название поставщика'}
    async with DatabaseSession() as session:
        supplier = Supplier(
            company_id=company_id, name=name,
            contact=(contact or '').strip() or None,
            website=(website or '').strip() or None,
            contact_person=(contact_person or '').strip() or None,
            created_by=by_user_id,
        )
        session.add(supplier)
        await session.commit()
        return {'ok': True, 'supplier': _supplier_dict(supplier)}


async def update_supplier(supplier_id: int, company_id: int, **fields) -> Dict:
    """fields: любые из name/contact/website/contact_person (None — не менять)."""
    async with DatabaseSession() as session:
        supplier = await session.scalar(
            select(Supplier).where(Supplier.id == supplier_id, Supplier.company_id == company_id)
        )
        if not supplier:
            return {'ok': False, 'error': 'Поставщик не найден'}
        if fields.get('name') is not None:
            name = fields['name'].strip()
            if not name:
                return {'ok': False, 'error': 'Название не может быть пустым'}
            supplier.name = name
        for attr in ('contact', 'website', 'contact_person'):
            if fields.get(attr) is not None:
                setattr(supplier, attr, fields[attr].strip() or None)
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
            return {'ok': False, 'error': 'У поставщика есть предложения по тендерам — сначала удалите их'}
        return {'ok': True}


# ============================================
# Supplier catalog (каталог позиций)
# ============================================

async def list_supplier_products(supplier_id: int, company_id: int) -> List[Dict]:
    async with DatabaseSession() as session:
        supplier = await session.get(Supplier, supplier_id)
        if not supplier or supplier.company_id != company_id:
            return []
        result = await session.execute(
            select(SupplierProduct).where(SupplierProduct.supplier_id == supplier_id)
            .order_by(SupplierProduct.name)
        )
        return [_product_dict(p) for p in result.scalars().all()]


async def add_supplier_product(supplier_id: int, company_id: int, name: str,
                               unit_price: float, by_user_id: int) -> Dict:
    name = (name or '').strip()
    if not name:
        return {'ok': False, 'error': 'Укажите название позиции'}
    async with DatabaseSession() as session:
        supplier = await session.get(Supplier, supplier_id)
        if not supplier or supplier.company_id != company_id:
            return {'ok': False, 'error': 'Поставщик не найден'}
        product = SupplierProduct(
            supplier_id=supplier_id, name=name,
            unit_price=Decimal(str(unit_price)), created_by=by_user_id,
        )
        session.add(product)
        await session.commit()
        return {'ok': True, 'product': _product_dict(product)}


async def delete_supplier_product(product_id: int, company_id: int) -> Dict:
    async with DatabaseSession() as session:
        product = await session.get(SupplierProduct, product_id)
        if not product:
            return {'ok': False, 'error': 'Позиция не найдена'}
        supplier = await session.get(Supplier, product.supplier_id)
        if not supplier or supplier.company_id != company_id:
            return {'ok': False, 'error': 'Позиция не найдена'}
        await session.delete(product)
        await session.commit()
        return {'ok': True}


async def _upsert_supplier_product(session, supplier_id: int, name: str,
                                   unit_price: Decimal, by_user_id: int) -> SupplierProduct:
    """Находит позицию каталога по имени (без учёта регистра) — обновляет
    цену, если нашлась, иначе заводит новую. Так каждая внесённая на
    карточке позиция автоматически пополняет базу знаний поставщика."""
    existing = await session.scalar(
        select(SupplierProduct).where(
            SupplierProduct.supplier_id == supplier_id,
            func.lower(SupplierProduct.name) == name.lower(),
        )
    )
    if existing:
        existing.unit_price = unit_price
        return existing
    product = SupplierProduct(
        supplier_id=supplier_id, name=name, unit_price=unit_price, created_by=by_user_id,
    )
    session.add(product)
    await session.flush()
    return product


async def _recalc_card_purchase_price(session, card_id: int) -> None:
    total = await session.scalar(
        select(func.sum(PipelineCardQuote.unit_price * PipelineCardQuote.quantity))
        .where(PipelineCardQuote.card_id == card_id)
    )
    card = await session.get(PipelineCard, card_id)
    if card:
        card.purchase_price = total


# ============================================
# Line items (позиции закупки на карточке)
# ============================================

async def list_quotes(card_id: int, company_id: int) -> List[Dict]:
    async with DatabaseSession() as session:
        card = await session.get(PipelineCard, card_id)
        if not card or card.company_id != company_id:
            return []
        result = await session.execute(
            select(PipelineCardQuote, Supplier)
            .join(Supplier, Supplier.id == PipelineCardQuote.supplier_id)
            .where(PipelineCardQuote.card_id == card_id)
            .order_by(PipelineCardQuote.id)
        )
        return [_quote_dict(q, s.name) for q, s in result.all()]


async def add_quote(card_id: int, company_id: int, supplier_id: Optional[int],
                    new_supplier_name: Optional[str],
                    product_name: str, unit_price: float, quantity: float,
                    notes: Optional[str], by_user_id: int) -> Dict:
    """supplier_id — существующий поставщик; если не задан, создаём нового
    по new_supplier_name. product_name/unit_price всегда обязательны (даже
    если выбрана существующая позиция каталога — на случай правки цены
    прямо на карточке); каталог поставщика обновляется/пополняется этим же
    именем+ценой (upsert по имени)."""
    product_name = (product_name or '').strip()
    if not product_name:
        return {'ok': False, 'error': 'Укажите наименование позиции'}
    if quantity is None or quantity <= 0:
        return {'ok': False, 'error': 'Укажите количество'}

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

        price_dec = Decimal(str(unit_price))
        catalog_product = await _upsert_supplier_product(
            session, supplier_id, product_name, price_dec, by_user_id,
        )

        quote = PipelineCardQuote(
            card_id=card_id, supplier_id=supplier_id,
            supplier_product_id=catalog_product.id, product_name=product_name,
            unit_price=price_dec, quantity=Decimal(str(quantity)),
            notes=(notes or '').strip() or None, created_by=by_user_id,
        )
        session.add(quote)
        await session.flush()

        await _recalc_card_purchase_price(session, card_id)

        session.add(PipelineCardHistory(
            card_id=card_id, user_id=by_user_id, action='quote_added',
            payload={
                'supplier_name': supplier_name, 'product_name': product_name,
                'unit_price': float(price_dec), 'quantity': float(quote.quantity),
            },
        ))
        await session.commit()
        return {'ok': True, 'quote': _quote_dict(quote, supplier_name)}


async def delete_quote(quote_id: int, company_id: int) -> Dict:
    async with DatabaseSession() as session:
        quote = await session.get(PipelineCardQuote, quote_id)
        if not quote:
            return {'ok': False, 'error': 'Позиция не найдена'}
        card = await session.get(PipelineCard, quote.card_id)
        if not card or card.company_id != company_id:
            return {'ok': False, 'error': 'Позиция не найдена'}
        card_id = quote.card_id
        await session.delete(quote)
        await session.flush()
        await _recalc_card_purchase_price(session, card_id)
        await session.commit()
        return {'ok': True}

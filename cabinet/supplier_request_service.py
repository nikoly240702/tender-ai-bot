"""Запрос поставщикам / Оценка стоимости тендера.

Две связанные фичи:
1. estimate_by_own_catalog(card_id) — AI-матчинг позиций ТЗ с own_products
   (наш каталог поставщиков) → оценка стоимости.
2. generate_clean_request(card_id) — AI чистит ТЗ от КТРУ/ГОСТ/ссылок
   на 44-ФЗ → краткое письмо для рассылки внешним поставщикам.

Используется gpt-4o-mini. Результаты не кэшируются (расход небольшой,
актуальность важнее).
"""

import json
import logging
import os
import re
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from database import DatabaseSession, OwnProduct, PipelineCard

logger = logging.getLogger(__name__)


# ============================================
# OpenAI
# ============================================

_openai_client: Optional[Any] = None


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI
        _openai_client = AsyncOpenAI(api_key=os.environ.get('OPENAI_API_KEY', ''))
    return _openai_client


# ============================================
# 1. Estimate by own catalog (СИЗ-оценка)
# ============================================

_ESTIMATE_PROMPT = """Ты помогаешь оценить стоимость закупки по тендеру.

ВХОД:
1. Текст ТЗ заказчика (характеристики и количества по позициям)
2. JSON-массив товаров из нашего каталога (наименование, размеры, параметры, цена за единицу)

ЗАДАЧА: для каждой позиции ТЗ найти ОДИН ЛУЧШИЙ matching-товар из каталога. Если подходящего нет — пометить как unmatched.

Отвечай СТРОГО валидным JSON:
{
  "matches": [
    {
      "tz_position": "<позиция как в ТЗ>",
      "tz_quantity": "<количество как в ТЗ или null>",
      "catalog_id": <id товара из каталога или null>,
      "match_confidence": "high"|"medium"|"low",
      "rationale": "<почему этот товар подходит, 1 фраза>"
    }
  ],
  "unmatched": ["<позиция ТЗ без совпадения>", ...]
}

Не выдумывай catalog_id — используй только id из переданного списка."""


async def _ai_match_catalogue(tz_text: str, catalogue: List[Dict]) -> Dict[str, Any]:
    """Запрашивает GPT матчинг ТЗ ↔ каталог."""
    if not tz_text.strip():
        return {'matches': [], 'unmatched': [], 'error': 'Пустой ТЗ'}
    if not catalogue:
        return {'matches': [], 'unmatched': [], 'error': 'Каталог пуст'}

    catalogue_brief = [
        {
            'id': c['id'],
            'name': c['name'],
            'sizes': c.get('sizes'),
            'params': c.get('params'),
            'pack': c.get('pack'),
            'price': float(c['price']) if c.get('price') is not None else None,
            'unit': c.get('price_unit'),
        }
        for c in catalogue
    ]

    user_prompt = (
        f"ТЗ ЗАКАЗЧИКА:\n{tz_text[:6000]}\n\n"
        f"НАШ КАТАЛОГ:\n{json.dumps(catalogue_brief, ensure_ascii=False, indent=1)}"
    )

    try:
        client = _get_openai_client()
        response = await client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[
                {'role': 'system', 'content': _ESTIMATE_PROMPT},
                {'role': 'user', 'content': user_prompt},
            ],
            temperature=0,
            max_tokens=2500,
        )
        content = response.choices[0].message.content.strip()
        content = re.sub(r'^```(?:json)?\s*', '', content)
        content = re.sub(r'\s*```$', '', content)
        return json.loads(content)
    except Exception as e:
        logger.error(f'AI catalog match failed: {e}', exc_info=True)
        return {'matches': [], 'unmatched': [], 'error': f'AI ошибка: {e}'}


def _qty_to_int(qty_raw: Any) -> int:
    """'500 шт' → 500, '10 пар' → 10, '5,5' → 5. На непарсимое возвращает 0."""
    if qty_raw is None:
        return 0
    if isinstance(qty_raw, (int, float)):
        return int(qty_raw)
    s = str(qty_raw)
    digits = re.sub(r'[^\d]', '', s.split('.')[0].split(',')[0])
    return int(digits) if digits else 0


async def estimate_by_own_catalog(card_id: int, company_id: int) -> Dict[str, Any]:
    """Главная функция оценки. Возвращает структуру:
    {
      ok: bool,
      tz_text_used: str,  # что мы использовали как источник ТЗ
      tz_source: str,
      catalogue_size: int,
      matches: [{tz_position, tz_quantity, qty_int, item, line_total, rationale, confidence}, ...],
      unmatched: [str, ...],
      total: float,
      currency: 'RUB'
    }
    """
    # 1. Получаем полный текст ТЗ (приоритет: card files → zakupki → fallback summary/name)
    from cabinet.tz_text_service import get_full_tz_text
    tz_result = await get_full_tz_text(card_id, company_id)
    if not tz_result.get('ok'):
        return {'ok': False, 'error': tz_result.get('error', 'Не удалось получить ТЗ')}
    tz_text = tz_result['text']
    tz_source = tz_result.get('source', 'unknown')
    tz_note = tz_result.get('note')

    # 2. Загружаем каталог
    async with DatabaseSession() as session:
        result = await session.execute(
            select(OwnProduct).where(OwnProduct.company_id == company_id)
            .order_by(OwnProduct.category, OwnProduct.id)
        )
        catalogue = []
        catalogue_by_id: Dict[int, OwnProduct] = {}
        for p in result.scalars().all():
            catalogue.append({
                'id': p.id,
                'name': p.name,
                'sizes': p.sizes,
                'params': p.params,
                'pack': p.pack,
                'price': p.price,
                'price_unit': p.price_unit,
                'price_text': p.price_text,
                'category': p.category,
            })
            catalogue_by_id[p.id] = p

    if not catalogue:
        return {
            'ok': False,
            'error': 'Свой каталог пуст. Залейте прайс через scripts/import_siz_catalogue.py.',
        }

    ai_result = await _ai_match_catalogue(tz_text, catalogue)
    if 'error' in ai_result:
        return {
            'ok': False,
            'error': ai_result['error'],
            'tz_text_used': tz_text,
        }

    matches_out: List[Dict] = []
    total = Decimal('0')
    for m in ai_result.get('matches', []):
        cat_id = m.get('catalog_id')
        product = catalogue_by_id.get(int(cat_id)) if cat_id else None
        qty_int = _qty_to_int(m.get('tz_quantity'))
        line_total = None
        if product and product.price is not None and qty_int > 0:
            line_total = product.price * qty_int
            total += line_total

        matches_out.append({
            'tz_position': m.get('tz_position', ''),
            'tz_quantity': m.get('tz_quantity'),
            'qty_int': qty_int,
            'rationale': m.get('rationale', ''),
            'confidence': m.get('match_confidence', 'low'),
            'item': {
                'id': product.id,
                'name': product.name,
                'sizes': product.sizes,
                'params': product.params,
                'pack': product.pack,
                'price': float(product.price) if product.price else None,
                'price_unit': product.price_unit,
                'price_text': product.price_text,
            } if product else None,
            'line_total': float(line_total) if line_total is not None else None,
        })

    return {
        'ok': True,
        'tz_text_used': tz_text[:500] + ('…' if len(tz_text) > 500 else ''),
        'tz_source': tz_source,
        'tz_note': tz_note,
        'tz_files_used': tz_result.get('files_used', []),
        'catalogue_size': len(catalogue),
        'matches': matches_out,
        'unmatched': ai_result.get('unmatched', []),
        'total': float(total),
        'currency': 'RUB',
    }


# ============================================
# 2. Clean ТЗ (для рассылки внешним поставщикам)
# ============================================

_CLEAN_REQUEST_PROMPT = """Ты помогаешь подготовить запрос коммерческого предложения поставщикам.

ВХОД: фрагмент технического задания заказчика из тендера на zakupki.gov.ru.
Там много мусора: КТРУ-коды, ссылки на ГОСТы, ОКПД2, цитаты из 44-ФЗ, формулы расчёта НМЦК, юридические оговорки.

ЗАДАЧА:
- Выкини всё юридическое: КТРУ, ОКПД2, ГОСТы, цитаты статей закона, обоснование НМЦК
- Сохрани характеристики товара: название, размеры, параметры, объём, материал, цвет, упаковка
- Сохрани количество (штук, пар, единиц)
- Структурируй как нумерованный список позиций
- Никаких приветствий, никакой подписи, ТОЛЬКО позиции

Формат ответа — обычный markdown-текст:
1. <Название>. <Характеристики кратко>. Кол-во: N шт.
2. ...

Ничего лишнего."""


async def generate_clean_request(card_id: int, company_id: int) -> Dict[str, Any]:
    """AI чистит ТЗ от мусора → возвращает текст для рассылки."""
    # Получаем полный текст ТЗ
    from cabinet.tz_text_service import get_full_tz_text
    tz_result = await get_full_tz_text(card_id, company_id)
    if not tz_result.get('ok'):
        return {'ok': False, 'error': tz_result.get('error', 'Не удалось получить ТЗ')}
    tz_text = tz_result['text']
    tz_source = tz_result.get('source', 'unknown')
    tz_note = tz_result.get('note')

    async with DatabaseSession() as session:
        card = await session.scalar(
            select(PipelineCard).where(
                PipelineCard.id == card_id,
                PipelineCard.company_id == company_id,
            )
        )
        if not card:
            return {'ok': False, 'error': 'Карточка не найдена'}

    if not tz_text:
        return {'ok': False, 'error': 'Нет данных по тендеру для очистки'}

    try:
        client = _get_openai_client()
        response = await client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[
                {'role': 'system', 'content': _CLEAN_REQUEST_PROMPT},
                {'role': 'user', 'content': tz_text[:8000]},
            ],
            temperature=0.2,
            max_tokens=1500,
        )
        positions = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f'AI clean request failed: {e}', exc_info=True)
        return {'ok': False, 'error': f'AI ошибка: {e}'}

    # Готовый текст письма
    tender_name = (card.data or {}).get('name') or f'Тендер {card.tender_number}'
    full_letter = (
        f"Здравствуйте!\n\n"
        f"Прошу прислать коммерческое предложение по следующим позициям:\n\n"
        f"{positions}\n\n"
        f"Заранее благодарю,\nОжидаю ответ."
    )

    return {
        'ok': True,
        'tender_name': tender_name,
        'tender_number': card.tender_number,
        'positions_text': positions,
        'letter_text': full_letter,
        'tz_source': tz_source,
        'tz_note': tz_note,
        'tz_files_used': tz_result.get('files_used', []),
    }

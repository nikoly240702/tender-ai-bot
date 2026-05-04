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

_ESTIMATE_PROMPT = """Ты помогаешь оценить стоимость закупки по реальному тендеру.

ВХОД:
1. Текст ТЗ заказчика — ОФИЦИАЛЬНЫЙ ДОКУМЕНТ из тендера. Часто содержит ДВЕ таблицы:
   - ТАБЛИЦА 1: список позиций и количеств
   - ТАБЛИЦА 2: характеристики (размер, материал, толщина, покрытие, цвет)
   ОБЯЗАТЕЛЬНО смотри ОБЕ таблицы — характеристики критичны для матчинга.
2. JSON-массив товаров из нашего каталога (id, наименование, размеры, параметры, цена за единицу).

ЗАДАЧА: извлечь из ТЗ РЕАЛЬНЫЕ позиции (которые ЯВНО упомянуты в тексте) с характеристиками и для каждой найти лучший matching-товар из каталога. При матчинге учитывай характеристики из ТАБЛИЦЫ 2 — например, перчатки по материалу/толщине/размеру должны совпасть с теми же параметрами в каталоге.

ЖЁСТКИЕ ПРАВИЛА:
1. НИКОГДА НЕ ВЫДУМЫВАЙ позиции которых нет в тексте ТЗ. Если в ТЗ нет «инфузионных систем» — ты НЕ имеешь права их добавить, даже если тендер «медицинский».
2. НИКОГДА не выдумывай характеристики. Используй ТОЛЬКО те значения, которые есть в тексте ТЗ.
3. Если в тексте ТЗ нет конкретных позиций с характеристиками (например, дали только название «Поставка медицинских изделий» без описания товаров) — верни ПУСТОЙ matches и no_data_reason.
4. catalog_id используй только из переданного списка. Не выдумывай числа.
5. tz_quantity и tz_position должны буквально присутствовать в ТЗ.
6. В rationale обязательно укажи КАКИЕ ХАРАКТЕРИСТИКИ из ТЗ совпали с характеристиками товара из каталога. Если совпадение по названию но не по характеристикам — confidence = "low".

Отвечай СТРОГО валидным JSON:
{
  "matches": [
    {
      "tz_position": "<позиция БУКВАЛЬНО как в ТЗ>",
      "tz_quantity": "<количество как в ТЗ, например '500 шт' или null>",
      "catalog_id": <id товара из переданного списка или null>,
      "match_confidence": "high"|"medium"|"low",
      "rationale": "<1 фраза почему подходит, ссылаясь на цитату из ТЗ>"
    }
  ],
  "unmatched": ["<позиция из ТЗ без совпадения с каталогом>", ...],
  "no_data_reason": "<если в ТЗ нет конкретики — объясни что именно отсутствует, иначе пустая строка>"
}"""


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


def _looks_like_real_tz(text: str) -> bool:
    """True если в тексте есть признаки реального ТЗ: характеристики, размеры, кол-ва.
    False для коротких/общих текстов где AI начнёт галлюцинировать."""
    if not text or len(text) < 500:
        return False
    text_low = text.lower()
    # Хотя бы 3 признака реального ТЗ
    indicators = [
        'характеристик', 'наименован', 'позици', 'кол-во', 'количество',
        'ед. изм', 'размер', 'материал', 'объём', 'объем', 'упаков',
        'требования', 'параметр',
    ]
    hits = sum(1 for kw in indicators if kw in text_low)
    return hits >= 3


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

    # Проверка что текст реально похож на ТЗ
    if not _looks_like_real_tz(tz_text):
        return {
            'ok': False,
            'tz_source': tz_source,
            'tz_files_used': tz_result.get('files_used', []),
            'tz_text_preview': tz_text[:300],
            'error': (
                'Текст ТЗ не содержит признаков технического задания (характеристик товаров, '
                'количеств). AI был бы вынужден выдумывать. Загрузите полную документацию '
                'тендера во вкладку «Файлы» карточки или дождитесь когда zakupki.gov.ru '
                'отдаст приложения.'
            ),
        }

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
        'tz_text_full_chars': len(tz_text),
        'tz_text_preview': tz_text[:2000],  # первые 2K символов для проверки юзером
        'tz_source': tz_source,
        'tz_note': tz_note,
        'tz_files_used': tz_result.get('files_used', []),
        'no_data_reason': ai_result.get('no_data_reason') or '',
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

ВХОД: фрагмент технического задания заказчика из тендера. Там может быть мусор: КТРУ-коды, ссылки на ГОСТы, ОКПД2, цитаты из 44-ФЗ, формулы НМЦК, юридические оговорки.

В тексте часто бывают ДВЕ таблицы:
- ТАБЛИЦА 1: список позиций с количествами
- ТАБЛИЦА 2: подробные характеристики (размер, материал, цвет, плотность, толщина, покрытие, упаковка, страна происхождения)
ОБЯЗАТЕЛЬНО просматривай ОБЕ — именно во второй таблице обычно лежат критичные характеристики.

Также характеристики могут быть в свободном тексте после фразы «Требования к», «Характеристики», «Параметры товара».

ЗАДАЧА:
- Выкини всё юридическое: КТРУ-коды, ОКПД2-коды, ГОСТы, цитаты статей закона, обоснование НМЦК, формулы расчёта цены
- Сохрани название товара
- Сохрани ВСЕ конкретные характеристики из ТЗ: размер, толщина, материал, плотность, цвет, покрытие, стерильность, упаковка
- Сохрани количество с единицами измерения

ЖЁСТКИЕ ПРАВИЛА:
1. Используй ТОЛЬКО данные которые БУКВАЛЬНО есть в тексте ТЗ.
2. НИКОГДА не выдумывай позиции, размеры, материалы, количества. Если характеристики не указаны — пиши «не указано», не придумывай.
3. Если в тексте ТЗ только название тендера без позиций — верни ровно одну строку:
   «НЕДОСТАТОЧНО ДАННЫХ: в ТЗ нет конкретных позиций с характеристиками. Загрузите полную документацию тендера в карточку.»

Формат — нумерованный список:
1. <Название товара>.
   • Размер: <значение из ТЗ или 'не указано'>
   • Материал: <значение>
   • Толщина/плотность: <значение>
   • Цвет: <значение>
   • Покрытие/стерильность: <значение>
   • Упаковка: <значение>
   • Кол-во: N <ед. изм.>

Если для товара какие-то поля не применимы или отсутствуют — пропускай эту строку (не пиши «не указано» лишний раз).

Никаких приветствий, никакой подписи."""


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

    if not _looks_like_real_tz(tz_text):
        return {
            'ok': False,
            'tz_source': tz_source,
            'tz_files_used': tz_result.get('files_used', []),
            'tz_text_preview': tz_text[:300],
            'error': (
                'Текст ТЗ не содержит признаков технического задания. '
                'Нет смысла генерировать письмо без реальных позиций. '
                'Загрузите документацию тендера во вкладку «Файлы» карточки.'
            ),
        }

    try:
        client = _get_openai_client()
        response = await client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[
                {'role': 'system', 'content': _CLEAN_REQUEST_PROMPT},
                {'role': 'user', 'content': tz_text[:80000]},  # 80K хватит для полного ТЗ
            ],
            temperature=0.2,
            max_tokens=4000,  # характеристик может быть много на позицию
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
        'tz_text_full_chars': len(tz_text),
        'tz_text_preview': tz_text[:2000],
    }

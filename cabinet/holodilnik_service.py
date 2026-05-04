"""Holodilnik.ru — автопоиск артикулов под тендер.

Async + polling. start_search создаёт asyncio task, статус в _TASKS dict.
Кэш результатов в pipeline_cards.data.suppliers.holodilnik (24 часа).

См. docs/superpowers/specs/2026-05-03-holodilnik-design.md
"""

import asyncio
import json
import logging
import os
import re
import secrets
import urllib.parse
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

import aiohttp
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from database import DatabaseSession, PipelineCard

logger = logging.getLogger(__name__)


# In-memory task registry: task_id → {status, progress, current_step, error?, results?}
# status ∈ {'pending', 'running', 'done', 'error'}
_TASKS: Dict[str, Dict[str, Any]] = {}

CACHE_TTL_HOURS = 24
SEARCH_LIMIT_PER_POSITION = 10
HOLODILNIK_BASE = 'https://www.holodilnik.ru'
HOLODILNIK_USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'
)
INTER_REQUEST_DELAY_SEC = 1.0  # rate-limit между HTTP-запросами к holodilnik


def _now() -> datetime:
    return datetime.utcnow()


# ============================================
# AI keyword rewrite
# ============================================

_openai_client: Optional[Any] = None


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI
        _openai_client = AsyncOpenAI(api_key=os.environ.get('OPENAI_API_KEY', ''))
    return _openai_client


_KEYWORD_REWRITE_PROMPT = """Ты помощник по подбору товаров для тендеров. Из строки ТЗ заказчика извлеки максимально короткий поисковый запрос (3-6 слов) для интернет-магазина бытовой техники, плюс структурированные фильтры по характеристикам.

Отвечай СТРОГО валидным JSON без markdown-обёрток в формате:
{"query": "...", "filters": {...}}

Возможные ключи в filters (используй только те которые есть в ТЗ):
- min_volume_l, max_volume_l (литры)
- energy_class (массив строк, например ["A", "A+"])
- frost ("no_frost" если NoFrost требуется)
- color
- min_capacity_kg, max_capacity_kg (для стиральных машин)
- brand_preferences (массив)

Не выдумывай характеристики. Если в ТЗ их нет — не добавляй ключ."""


async def _ai_keyword_rewrite(tz_position: str) -> Dict[str, Any]:
    """Превращает строку ТЗ в {query, filters}. Fallback на raw query при ошибке."""
    fallback = {'query': tz_position[:60], 'filters': {}}
    try:
        client = _get_openai_client()
        response = await client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[
                {'role': 'system', 'content': _KEYWORD_REWRITE_PROMPT},
                {'role': 'user', 'content': tz_position},
            ],
            temperature=0,
            max_tokens=200,
        )
        content = response.choices[0].message.content.strip()
        # Снять возможные markdown-обёртки ```json ... ```
        content = re.sub(r'^```(?:json)?\s*', '', content)
        content = re.sub(r'\s*```$', '', content)
        parsed = json.loads(content)
        query = (parsed.get('query') or '').strip() or fallback['query']
        filters_raw = parsed.get('filters')
        filters = filters_raw if isinstance(filters_raw, dict) else {}
        return {'query': query, 'filters': filters}
    except Exception as e:
        logger.warning(f'AI keyword rewrite failed for "{tz_position[:50]}...": {e}')
        return fallback


# ============================================
# HTTP fetch + HTML parsing
# ============================================

async def _fetch_holodilnik_search(query: str) -> bytes:
    """GET /search/?text=<query> с браузерным User-Agent. Возвращает raw bytes.

    ВАЖНО: holodilnik.ru ожидает URL-encoded строку в cp1251 (старый стек).
    При UTF-8 он возвращает 400 Bad Request.
    """
    # Encode в cp1251 перед URL-quoting. Для latin это no-op.
    encoded = urllib.parse.quote(query.encode('cp1251', errors='replace'))
    url = f'{HOLODILNIK_BASE}/search/?text={encoded}'
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(
        timeout=timeout,
        headers={'User-Agent': HOLODILNIK_USER_AGENT},
    ) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history,
                    status=resp.status, message=f'holodilnik HTTP {resp.status}',
                )
            return await resp.read()


def _parse_search_html(html_bytes: bytes, limit: int = 10) -> List[Dict[str, Any]]:
    """Парсит HTML страницы /search/?text=... → список карточек товаров.

    Кодировка holodilnik — windows-1251.
    """
    try:
        html = html_bytes.decode('cp1251', errors='replace')
    except Exception:
        html = html_bytes.decode('utf-8', errors='replace')

    soup = BeautifulSoup(html, 'html.parser')
    results: List[Dict[str, Any]] = []

    # Несколько вариантов селекторов — на случай разных версий HTML
    candidates = soup.select(
        '.product-tile-list__item, .product-card, .b-product, '
        '[class*="product-tile"], [class*="product-item"], '
        '[data-product-id], .item-card'
    )

    seen_urls = set()
    for card in candidates[:limit * 3]:  # с запасом
        try:
            link_el = card.select_one('a[href]')
            if not link_el:
                continue
            href = link_el.get('href', '')
            if not href:
                continue
            url = href if href.startswith('http') else f'{HOLODILNIK_BASE}{href}'
            if url in seen_urls:
                continue
            seen_urls.add(url)

            name_el = (
                card.select_one('.product-tile-list__name')
                or card.select_one('.product-card__name')
                or card.select_one('.b-product__name')
                or card.select_one('[class*="name"]')
                or link_el
            )
            name = (name_el.get_text(strip=True) if name_el else '')[:200]
            if not name:
                continue

            price_el = (
                card.select_one('.product-tile-list__price')
                or card.select_one('.product-card__price')
                or card.select_one('.b-product__price')
                or card.select_one('[class*="price"]')
            )
            price_text = price_el.get_text(strip=True) if price_el else ''
            price_digits = re.sub(r'[^\d]', '', price_text)
            price = int(price_digits) if price_digits else 0

            img_el = card.select_one('img[src], img[data-src]')
            img = ''
            if img_el:
                img = img_el.get('data-src') or img_el.get('src') or ''
                if img.startswith('//'):
                    img = 'https:' + img
                elif img.startswith('/'):
                    img = HOLODILNIK_BASE + img

            # SKU: чаще всего из URL
            sku_match = (
                re.search(r'/(\d{5,})\.html', url)
                or re.search(r'/([a-z0-9-]+)\.html$', url)
            )
            sku = sku_match.group(1) if sku_match else url[-50:]

            in_stock = not card.select_one(
                '.out-of-stock, .b-product__no-stock, [data-out-of-stock], '
                '[class*="out-of-stock"], [class*="no-stock"]'
            )

            if name and url and price > 0:
                results.append({
                    'sku': sku,
                    'name': name,
                    'price': price,
                    'url': url,
                    'img': img,
                    'in_stock': in_stock,
                })
        except Exception as e:
            logger.debug(f'Skip malformed product card: {e}')

        if len(results) >= limit:
            break

    return results


async def _search_holodilnik(query: str, limit: int = SEARCH_LIMIT_PER_POSITION) -> List[Dict]:
    """Полный цикл: fetch + parse."""
    html_bytes = await _fetch_holodilnik_search(query)
    return _parse_search_html(html_bytes, limit=limit)


def _filter_results(items: List[Dict], filters: Dict[str, Any]) -> List[Dict]:
    """Heuristic-фильтрация по AI-filters. Recall > precision на MVP."""
    if not filters:
        return items
    out = []
    frost = filters.get('frost')

    for item in items:
        name_lower = item['name'].lower()

        if frost == 'no_frost':
            no_frost_keywords = ['no frost', 'нофрост', 'но фрост', 'noffrost']
            if not any(kw in name_lower for kw in no_frost_keywords):
                continue
        out.append(item)

    return out


# ============================================
# Position extraction из ТЗ
# ============================================

async def _extract_positions(card: PipelineCard) -> List[str]:
    """Извлекает список ТЗ-позиций для поиска.

    Приоритет:
    1. card.ai_summary с bullet-list
    2. Fallback: одна "позиция" = название тендера
    """
    positions: List[str] = []

    summary = (card.ai_summary or '').strip()
    if summary:
        for line in summary.split('\n'):
            stripped = line.strip(' \t\r-*•·1234567890.)')
            if 10 <= len(stripped) <= 300:
                positions.append(stripped)

    if not positions:
        data = card.data or {}
        name = data.get('name') or f'Тендер {card.tender_number}'
        positions = [name]

    seen = set()
    unique: List[str] = []
    for p in positions:
        key = p.lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(p)
        if len(unique) >= 15:
            break
    return unique


# ============================================
# Background task orchestration
# ============================================

async def _do_search(task_id: str, card_id: int, company_id: int, by_user_id: int):
    """Background task. Обновляет _TASKS[task_id] + сохраняет results в card.data."""
    started_at = _now()
    _TASKS[task_id] = {
        'status': 'running',
        'progress': '0/?',
        'current_step': 'Загружаю карточку',
        'started_at': started_at.isoformat(),
    }

    try:
        async with DatabaseSession() as session:
            card = await session.scalar(
                select(PipelineCard).where(
                    PipelineCard.id == card_id,
                    PipelineCard.company_id == company_id,
                )
            )
            if not card:
                _TASKS[task_id] = {**_TASKS[task_id], 'status': 'error', 'error': 'Карточка не найдена'}
                return
            positions = await _extract_positions(card)

        if not positions:
            _TASKS[task_id] = {**_TASKS[task_id], 'status': 'error', 'error': 'Не удалось извлечь позиции из ТЗ'}
            return

        total = len(positions)
        _TASKS[task_id]['progress'] = f'0/{total}'

        position_results = []
        for idx, tz_text in enumerate(positions):
            _TASKS[task_id]['progress'] = f'{idx}/{total}'
            _TASKS[task_id]['current_step'] = f'Обрабатываю: {tz_text[:60]}'

            kw = await _ai_keyword_rewrite(tz_text)
            try:
                items = await _search_holodilnik(kw['query'], limit=SEARCH_LIMIT_PER_POSITION * 2)
                items = _filter_results(items, kw['filters'])[:SEARCH_LIMIT_PER_POSITION]
            except Exception as e:
                logger.warning(f'holodilnik search failed for "{kw["query"]}": {e}')
                items = []

            position_results.append({
                'tz_text': tz_text,
                'ai_query': kw['query'],
                'ai_filters': kw['filters'],
                'results': [{**it, 'selected': False} for it in items],
            })
            await asyncio.sleep(INTER_REQUEST_DELAY_SEC)

        finished_at = _now()
        suppliers_block = {
            'status': 'done',
            'started_at': started_at.isoformat(),
            'finished_at': finished_at.isoformat(),
            'expires_at': (finished_at + timedelta(hours=CACHE_TTL_HOURS)).isoformat(),
            'positions': position_results,
        }

        async with DatabaseSession() as session:
            card = await session.get(PipelineCard, card_id)
            if card:
                data = dict(card.data or {})
                suppliers = dict(data.get('suppliers') or {})
                suppliers['holodilnik'] = suppliers_block
                data['suppliers'] = suppliers
                card.data = data
                flag_modified(card, 'data')
                await session.commit()

        _TASKS[task_id] = {
            **_TASKS[task_id],
            'status': 'done',
            'progress': f'{total}/{total}',
            'current_step': 'Готово',
            'results': suppliers_block,
            'finished_at': finished_at.isoformat(),
        }
    except Exception as e:
        logger.error(f'holodilnik task {task_id} crashed: {e}', exc_info=True)
        _TASKS[task_id] = {
            **_TASKS.get(task_id, {}),
            'status': 'error',
            'error': str(e)[:200],
        }


async def start_search(card_id: int, company_id: int, by_user_id: int,
                        force: bool = False) -> Dict[str, Any]:
    """Возвращает {task_id} или {cached: True, results}."""
    async with DatabaseSession() as session:
        card = await session.scalar(
            select(PipelineCard).where(
                PipelineCard.id == card_id,
                PipelineCard.company_id == company_id,
            )
        )
        if not card:
            return {'error': 'Card not found', 'status': 404}

        if not force:
            data = card.data or {}
            cached = (data.get('suppliers') or {}).get('holodilnik')
            if cached and cached.get('expires_at'):
                try:
                    expires = datetime.fromisoformat(cached['expires_at'])
                    if expires > _now():
                        return {'cached': True, 'results': cached}
                except Exception:
                    pass

    task_id = secrets.token_urlsafe(12)
    _TASKS[task_id] = {'status': 'pending', 'progress': '0/?', 'current_step': 'В очереди'}
    asyncio.create_task(_do_search(task_id, card_id, company_id, by_user_id))
    return {'cached': False, 'task_id': task_id}


def get_status(task_id: str) -> Optional[Dict[str, Any]]:
    return _TASKS.get(task_id)


# ============================================
# Toggle selected SKU
# ============================================

async def toggle_selected(card_id: int, company_id: int, position_idx: int,
                           sku: str, selected: bool) -> Dict[str, Any]:
    async with DatabaseSession() as session:
        card = await session.scalar(
            select(PipelineCard).where(
                PipelineCard.id == card_id,
                PipelineCard.company_id == company_id,
            )
        )
        if not card:
            return {'ok': False, 'error': 'Не найдено'}

        data = dict(card.data or {})
        suppliers = dict(data.get('suppliers') or {})
        h = dict(suppliers.get('holodilnik') or {})
        positions = list(h.get('positions') or [])
        if position_idx < 0 or position_idx >= len(positions):
            return {'ok': False, 'error': 'position_idx out of range'}

        pos = dict(positions[position_idx])
        results = list(pos.get('results') or [])
        found = False
        for i, r in enumerate(results):
            if r.get('sku') == sku:
                r2 = dict(r)
                r2['selected'] = bool(selected)
                results[i] = r2
                found = True
                break
        if not found:
            return {'ok': False, 'error': 'sku not found'}

        pos['results'] = results
        positions[position_idx] = pos
        h['positions'] = positions
        suppliers['holodilnik'] = h
        data['suppliers'] = suppliers
        card.data = data
        flag_modified(card, 'data')
        await session.commit()
        return {'ok': True}

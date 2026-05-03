# Holodilnik.ru Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Кнопка «Найти на holodilnik.ru» в карточке тендера → AI-извлечение позиций ТЗ → AI-keyword → парсинг holodilnik → подмодалка с сеткой моделей и чекбоксами выбора + кнопка «📋 Артикулы».

**Architecture:** Async + polling. POST запускает background asyncio.Task, in-memory dict хранит статус. UI каждые 2 сек polls статус, рендерит результаты когда готово. Кэш в `pipeline_cards.data.suppliers.holodilnik` на 24 часа.

**Tech Stack:** aiohttp + SQLAlchemy async (как кабинет), aiohttp.ClientSession для GET к holodilnik, BeautifulSoup4 для HTML parse (уже в requirements), OpenAI GPT-4o-mini для keyword-rewrite, vanilla JS для UI.

**Spec:** `docs/superpowers/specs/2026-05-03-holodilnik-design.md`

---

## File Structure

**Создаётся:**
| Файл | Назначение |
|---|---|
| `cabinet/holodilnik_service.py` | Domain logic: search task, AI keyword, HTTP fetch, parsing |
| `cabinet/templates/_modal_holodilnik.html` | Подмодалка с loading + сеткой |
| `cabinet/static/js/pages/holodilnik.js` | UI logic: open modal, poll, render, toggle, copy SKUs |
| `tests/unit/test_holodilnik_service.py` | Unit-тесты на keyword + parsing + cache |
| `tests/unit/fixtures/holodilnik_search.html` | Реальный HTML-snapshot для парсинга |

**Изменяется:**
| Файл | Что |
|---|---|
| `cabinet/api.py` | +3 endpoint (start/status/toggle) |
| `cabinet/routes.py` | Регистрация маршрутов |
| `cabinet/templates/_modal_card.html` | include `_modal_holodilnik.html` + поведение `cm-btn-supplier` |
| `cabinet/static/css/pages/pipeline.css` | Стили подмодалки и сетки `.hl-*` |
| `cabinet/static/js/pages/pipeline.js` | Подключение нового JS-модуля |

---

## Phase 1 — Backend service

### Task 1.1: Скелет сервиса + in-memory tasks

**Files:** `cabinet/holodilnik_service.py`

- [ ] **Step 1: Создать файл с константами и пустыми функциями**

```python
"""Holodilnik.ru — автопоиск артикулов под тендер.

Async + polling: start_search создаёт asyncio task, статус в _TASKS dict.
Кэш результатов в pipeline_cards.data.suppliers.holodilnik (24 часа).

См. docs/superpowers/specs/2026-05-03-holodilnik-design.md
"""

import asyncio
import json
import logging
import re
import secrets
import urllib.parse
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

import aiohttp
from bs4 import BeautifulSoup
from sqlalchemy import select

from database import DatabaseSession, PipelineCard

logger = logging.getLogger(__name__)


# In-memory task registry: task_id → {status, progress, current_step, error?}
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
TASK_MAX_DURATION_SEC = 300  # 5 минут timeout per task
```

- [ ] **Step 2: Smoke import**

Run: `python -c "from cabinet import holodilnik_service; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add cabinet/holodilnik_service.py
git commit -m "feat(holodilnik): module skeleton with constants"
```

### Task 1.2: AI keyword-rewrite (TDD)

**Files:** `tests/unit/test_holodilnik_service.py`, `cabinet/holodilnik_service.py`

- [ ] **Step 1: Написать failing test**

`tests/unit/test_holodilnik_service.py`:
```python
import pytest
from unittest.mock import patch, AsyncMock

from cabinet.holodilnik_service import _ai_keyword_rewrite


@pytest.mark.asyncio
async def test_ai_keyword_rewrite_parses_json():
    """Возвращает {query, filters} из JSON-ответа OpenAI."""
    mock_response = type('R', (), {})()
    mock_response.choices = [type('C', (), {'message': type('M', (), {
        'content': '{"query": "холодильник 250л", "filters": {"frost": "no_frost"}}'
    })})]
    with patch('cabinet.holodilnik_service._openai_client') as cli:
        cli.chat.completions.create = AsyncMock(return_value=mock_response)
        result = await _ai_keyword_rewrite('Холодильник 250л No Frost')
    assert result == {'query': 'холодильник 250л', 'filters': {'frost': 'no_frost'}}


@pytest.mark.asyncio
async def test_ai_keyword_rewrite_falls_back_on_invalid_json():
    """При невалидном JSON-ответе fallback: query = первые 60 символов tz."""
    mock_response = type('R', (), {})()
    mock_response.choices = [type('C', (), {'message': type('M', (), {
        'content': 'извините, не понял'
    })})]
    with patch('cabinet.holodilnik_service._openai_client') as cli:
        cli.chat.completions.create = AsyncMock(return_value=mock_response)
        result = await _ai_keyword_rewrite('Стиральная машина 7кг класс A')
    assert result['query'] == 'Стиральная машина 7кг класс A'
    assert result['filters'] == {}
```

- [ ] **Step 2: Run test — должно упасть на ImportError**

Run: `pytest tests/unit/test_holodilnik_service.py::test_ai_keyword_rewrite_parses_json -v`
Expected: ImportError на `_ai_keyword_rewrite` или `_openai_client`.

- [ ] **Step 3: Реализовать функцию**

В `cabinet/holodilnik_service.py` после констант:
```python
import os
from openai import AsyncOpenAI

_openai_client: Optional[AsyncOpenAI] = None

def _get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=os.environ.get('OPENAI_API_KEY', ''))
    return _openai_client


_KEYWORD_REWRITE_PROMPT = """Ты помощник по подбору товаров для тендеров. Из строки ТЗ заказчика извлеки максимально короткий поисковый запрос (3-6 слов) для интернет-магазина бытовой техники, плюс структурированные фильтры по характеристикам.

Отвечай СТРОГО валидным JSON без markdown-обёрток в формате:
{"query": "...", "filters": {...}}

Возможные ключи в filters (используй те которые есть в ТЗ):
- min_volume_l, max_volume_l (литры)
- energy_class (массив строк, A/A+/A++/A+++)
- frost ("no_frost" если NoFrost требуется)
- color
- min_capacity_kg, max_capacity_kg (для стиральных машин)
- brand_preferences (массив)

Не выдумывай характеристики. Если в ТЗ их нет — не добавляй ключ."""


async def _ai_keyword_rewrite(tz_position: str) -> Dict[str, Any]:
    """Превращает строку ТЗ в {query, filters}. Fallback на raw query при ошибке."""
    fallback = {'query': tz_position[:60], 'filters': {}}
    try:
        client = _get_openai_client() if _openai_client is None else _openai_client
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
        query = parsed.get('query', '').strip() or fallback['query']
        filters = parsed.get('filters', {}) if isinstance(parsed.get('filters'), dict) else {}
        return {'query': query, 'filters': filters}
    except (json.JSONDecodeError, AttributeError, KeyError, Exception) as e:
        logger.warning(f'AI keyword rewrite failed for "{tz_position[:50]}...": {e}')
        return fallback
```

- [ ] **Step 4: Запустить тесты**

Run: `pytest tests/unit/test_holodilnik_service.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add cabinet/holodilnik_service.py tests/unit/test_holodilnik_service.py
git commit -m "feat(holodilnik): AI keyword rewrite with JSON parse + fallback"
```

### Task 1.3: HTTP fetch + HTML parsing (TDD)

**Files:** `tests/unit/fixtures/holodilnik_search.html`, `tests/unit/test_holodilnik_service.py`, `cabinet/holodilnik_service.py`

- [ ] **Step 1: Скачать реальный snapshot для fixture**

Run:
```bash
mkdir -p tests/unit/fixtures
curl -s -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15" \
  "https://www.holodilnik.ru/search/?text=холодильник+двухкамерный" \
  > tests/unit/fixtures/holodilnik_search.html
wc -c tests/unit/fixtures/holodilnik_search.html
```
Expected: файл размером > 50 KB.

- [ ] **Step 2: Inspect фактическую разметку**

Open `tests/unit/fixtures/holodilnik_search.html` в браузере или редакторе. Найти:
- Класс обёртки карточки товара (например `.product-tile`, `.b-product-card`, etc.)
- Селектор названия / цены / фото / ссылки
- Где SKU (часто в атрибуте `data-id` или в URL `.../1234567.html`)

Зафиксировать selectors на уровне функции `_parse_search_html`. **Внимание:** селекторы зависят от реальной разметки holodilnik — могут отличаться от примера ниже.

- [ ] **Step 3: Написать failing test для парсера**

В `tests/unit/test_holodilnik_service.py` добавить:
```python
from pathlib import Path
from cabinet.holodilnik_service import _parse_search_html


def test_parse_search_html_returns_results():
    fixture = Path('tests/unit/fixtures/holodilnik_search.html').read_bytes()
    results = _parse_search_html(fixture, limit=10)
    assert 5 <= len(results) <= 10
    first = results[0]
    assert first['name']
    assert first['url'].startswith('https://www.holodilnik.ru/')
    assert first['price'] > 0
    assert 'sku' in first
```

- [ ] **Step 4: Run — упадёт на ImportError**

Run: `pytest tests/unit/test_holodilnik_service.py::test_parse_search_html_returns_results -v`
Expected: ImportError.

- [ ] **Step 5: Реализовать парсер**

В `cabinet/holodilnik_service.py`:
```python
def _parse_search_html(html_bytes: bytes, limit: int = 10) -> List[Dict[str, Any]]:
    """Парсит HTML страницы /search/?text=... → список карточек товаров.

    Кодировка holodilnik — windows-1251. Декодим с errors=replace на случай
    кривых символов.
    """
    try:
        html = html_bytes.decode('cp1251', errors='replace')
    except Exception:
        html = html_bytes.decode('utf-8', errors='replace')

    soup = BeautifulSoup(html, 'html.parser')
    results: List[Dict[str, Any]] = []

    # ВАЖНО: следующие селекторы — пример. Подобрать под реальный HTML
    # из tests/unit/fixtures/holodilnik_search.html.
    # Распространённые паттерны: .product-card / .b-prod / .item-card
    # У holodilnik 2026 это, скорее всего, .product-tile-list__item
    candidates = soup.select('.product-tile-list__item, .product-card, .b-product')

    for card in candidates[:limit * 2]:  # берём с запасом, после фильтра выживет ~limit
        try:
            link_el = card.select_one('a[href]')
            if not link_el:
                continue
            href = link_el.get('href', '')
            url = href if href.startswith('http') else f'{HOLODILNIK_BASE}{href}'

            name_el = card.select_one('.product-tile-list__name, .product-card__name, .b-product__name')
            name = (name_el.get_text(strip=True) if name_el else link_el.get_text(strip=True))[:200]

            price_el = card.select_one('.product-tile-list__price, .product-card__price, .b-product__price')
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

            # SKU: чаще всего из URL — последний числовой сегмент или slug
            sku_match = re.search(r'/(\d{5,})\.html', url) or re.search(r'/([a-z0-9-]+)\.html$', url)
            sku = sku_match.group(1) if sku_match else url[-50:]

            in_stock = not card.select_one('.out-of-stock, .b-product__no-stock, [data-out-of-stock]')

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
```

- [ ] **Step 6: Run test — может потребоваться правка селекторов**

Run: `pytest tests/unit/test_holodilnik_service.py::test_parse_search_html_returns_results -v`

Если упало с `assert 5 <= len(results)` — посмотреть HTML, найти реальные селекторы, обновить `candidates` строку.

- [ ] **Step 7: HTTP fetch wrapper**

Добавить:
```python
async def _fetch_holodilnik_search(query: str) -> bytes:
    """GET /search/?text=<query> с браузерным User-Agent. Возвращает raw bytes
    (для последующей декодировки cp1251). Бросает aiohttp ClientError при сбое."""
    encoded = urllib.parse.quote(query)
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


async def _search_holodilnik(query: str, limit: int = SEARCH_LIMIT_PER_POSITION) -> List[Dict]:
    """Полный цикл: fetch + parse. Возвращает список найденных карточек."""
    html_bytes = await _fetch_holodilnik_search(query)
    return _parse_search_html(html_bytes, limit=limit)
```

- [ ] **Step 8: Run all tests**

Run: `pytest tests/unit/test_holodilnik_service.py -v`
Expected: 3 passed.

- [ ] **Step 9: Commit**

```bash
git add cabinet/holodilnik_service.py tests/unit/test_holodilnik_service.py tests/unit/fixtures/holodilnik_search.html
git commit -m "feat(holodilnik): HTTP fetch + HTML parser (cp1251)"
```

### Task 1.4: Filter results по AI-фильтрам (TDD)

**Files:** `tests/unit/test_holodilnik_service.py`, `cabinet/holodilnik_service.py`

- [ ] **Step 1: Написать тест**

```python
from cabinet.holodilnik_service import _filter_results


def test_filter_results_no_frost():
    items = [
        {'sku': '1', 'name': 'LG GA No Frost', 'price': 40000, 'url': '', 'img': '', 'in_stock': True},
        {'sku': '2', 'name': 'Atlant ХМ-4012', 'price': 30000, 'url': '', 'img': '', 'in_stock': True},
        {'sku': '3', 'name': 'Bosch No Frost класс A', 'price': 50000, 'url': '', 'img': '', 'in_stock': True},
    ]
    filtered = _filter_results(items, {'frost': 'no_frost'})
    assert {x['sku'] for x in filtered} == {'1', '3'}


def test_filter_results_no_filter_returns_all():
    items = [{'sku': '1', 'name': 'X', 'price': 1, 'url': '', 'img': '', 'in_stock': True}]
    assert _filter_results(items, {}) == items
```

- [ ] **Step 2: Run — fail**

Run: `pytest tests/unit/test_holodilnik_service.py -k filter_results -v`

- [ ] **Step 3: Реализовать**

```python
def _filter_results(items: List[Dict], filters: Dict[str, Any]) -> List[Dict]:
    """Простая heuristic-фильтрация результатов по AI-filters.

    Поведение: если фильтр не удаётся проверить (нет данных в карточке) —
    оставляем item (recall > precision на MVP).
    """
    if not filters:
        return items
    out = []
    frost = filters.get('frost')
    energy_classes = filters.get('energy_class') or []
    color = (filters.get('color') or '').lower()

    for item in items:
        name_lower = item['name'].lower()

        if frost == 'no_frost':
            if 'no frost' not in name_lower and 'нофрост' not in name_lower and 'но фрост' not in name_lower:
                continue
        if energy_classes:
            # Проверяем что хотя бы один из классов упомянут в названии
            if not any(f' {cls.lower()}' in f' {name_lower} ' for cls in energy_classes):
                # Нет упоминания — но не отбрасываем (карточка холодильника часто без класса в названии)
                pass
        if color:
            color_words = {'белый': 'white', 'чёрный': 'black', 'серебристый': 'silver'}
            if color in color_words.values():
                ru_words = [k for k, v in color_words.items() if v == color]
                if ru_words and ru_words[0] not in name_lower:
                    pass  # не отбрасываем, цвет редко в названии
        out.append(item)

    return out
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_holodilnik_service.py -k filter_results -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add cabinet/holodilnik_service.py tests/unit/test_holodilnik_service.py
git commit -m "feat(holodilnik): heuristic filter by AI-extracted constraints"
```

### Task 1.5: Position extraction из ТЗ карточки

**Files:** `cabinet/holodilnik_service.py`

- [ ] **Step 1: Реализовать функцию извлечения позиций**

Логика: если у карточки есть `ai_summary` (от AI document extractor), пробуем извлечь bullet-list позиций. Иначе fallback на название тендера + ключевые слова из фильтра.

```python
async def _extract_positions(card: PipelineCard) -> List[str]:
    """Возвращает список ТЗ-позиций для поиска.

    Приоритет:
    1. card.ai_summary с bullet-list — splitter по строкам начинающимся с -, *, цифрой+точкой
    2. Если ai_summary пустой — попытаться запустить ai_document_extractor
       (если включён OPENAI_API_KEY и есть тендерная документация)
    3. Fallback: одна "позиция" = название тендера

    На MVP используем (1) и (3). (2) — отдельная задача обогащения,
    запускается через кнопку 'Запустить AI-анализ' в карточке.
    """
    positions: List[str] = []

    summary = (card.ai_summary or '').strip()
    if summary:
        # Bullet-style splitting
        lines = summary.split('\n')
        for line in lines:
            stripped = line.strip(' \t\r-*•·1234567890.)')
            if 10 <= len(stripped) <= 300:
                positions.append(stripped)

    if not positions:
        # Fallback: название тендера
        data = card.data or {}
        name = data.get('name') or f'Тендер {card.tender_number}'
        positions = [name]

    # Дедупликация и лимит
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
```

- [ ] **Step 2: Smoke import**

Run: `python -c "from cabinet.holodilnik_service import _extract_positions; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add cabinet/holodilnik_service.py
git commit -m "feat(holodilnik): extract TZ positions from card.ai_summary with fallback"
```

### Task 1.6: Background search task — оркестрация

**Files:** `cabinet/holodilnik_service.py`

- [ ] **Step 1: Реализовать `_do_search` и `start_search`**

```python
def _now() -> datetime:
    return datetime.utcnow()


async def _do_search(task_id: str, card_id: int, company_id: int, by_user_id: int):
    """Background task. Обновляет _TASKS[task_id] по ходу + сохраняет result в card.data."""
    started_at = _now()
    _TASKS[task_id] = {
        'status': 'running',
        'progress': '0/?',
        'current_step': 'Загружаю карточку',
        'started_at': started_at.isoformat(),
    }

    try:
        # 1. Загружаем карточку
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

        # 2. Для каждой позиции — keyword + search + filter
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

        # 3. Сохраняем в card.data.suppliers.holodilnik
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
                from sqlalchemy.orm.attributes import flag_modified
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
        _TASKS[task_id] = {**_TASKS.get(task_id, {}), 'status': 'error', 'error': str(e)[:200]}


async def start_search(card_id: int, company_id: int, by_user_id: int,
                        force: bool = False) -> Dict[str, Any]:
    """Возвращает {task_id, cached?}. Если кэш свежий и !force — cached=True + results."""
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
                expires = datetime.fromisoformat(cached['expires_at'])
                if expires > _now():
                    return {'cached': True, 'results': cached}

    # Стартуем background task
    task_id = secrets.token_urlsafe(12)
    _TASKS[task_id] = {'status': 'pending', 'progress': '0/?', 'current_step': 'В очереди'}
    asyncio.create_task(_do_search(task_id, card_id, company_id, by_user_id))
    return {'cached': False, 'task_id': task_id}


def get_status(task_id: str) -> Optional[Dict[str, Any]]:
    return _TASKS.get(task_id)
```

- [ ] **Step 2: Smoke import**

Run: `python -c "from cabinet.holodilnik_service import start_search, get_status, _do_search; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add cabinet/holodilnik_service.py
git commit -m "feat(holodilnik): background search orchestrator with 24h cache"
```

### Task 1.7: Toggle selected SKU

**Files:** `cabinet/holodilnik_service.py`, `tests/unit/test_holodilnik_service.py`

- [ ] **Step 1: Test**

```python
@pytest.mark.asyncio
async def test_toggle_selected_updates_flag(db_session, make_company_with_card):
    card, owner = await make_company_with_card()
    # Заранее впихнём suppliers в data
    from database import DatabaseSession, PipelineCard
    from sqlalchemy.orm.attributes import flag_modified
    async with DatabaseSession() as session:
        c = await session.get(PipelineCard, card['id'])
        c.data = {
            **(c.data or {}),
            'suppliers': {'holodilnik': {
                'status': 'done',
                'positions': [{'tz_text': 'X', 'results': [
                    {'sku': 'AAA-1', 'name': 'A', 'selected': False},
                    {'sku': 'AAA-2', 'name': 'B', 'selected': False},
                ]}],
            }},
        }
        flag_modified(c, 'data')
        await session.commit()

    from cabinet.holodilnik_service import toggle_selected
    result = await toggle_selected(card['id'], card['company_id'], 0, 'AAA-1', True)
    assert result['ok'] is True

    async with DatabaseSession() as session:
        c2 = await session.get(PipelineCard, card['id'])
        positions = c2.data['suppliers']['holodilnik']['positions']
        assert positions[0]['results'][0]['selected'] is True
        assert positions[0]['results'][1]['selected'] is False
```

- [ ] **Step 2: Реализовать `toggle_selected`**

```python
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
        for i, r in enumerate(results):
            if r.get('sku') == sku:
                r2 = dict(r)
                r2['selected'] = bool(selected)
                results[i] = r2
                break
        else:
            return {'ok': False, 'error': 'sku not found'}

        pos['results'] = results
        positions[position_idx] = pos
        h['positions'] = positions
        suppliers['holodilnik'] = h
        data['suppliers'] = suppliers
        card.data = data
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(card, 'data')
        await session.commit()
        return {'ok': True}
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/unit/test_holodilnik_service.py -v`
Expected: все passed.

- [ ] **Step 4: Commit**

```bash
git add cabinet/holodilnik_service.py tests/unit/test_holodilnik_service.py
git commit -m "feat(holodilnik): toggle_selected for SKU choice persistence"
```

---

## Phase 2 — API endpoints

### Task 2.1: Endpoints в `cabinet/api.py`

**Files:** `cabinet/api.py`, `cabinet/routes.py`

- [ ] **Step 1: Добавить endpoints в конец `cabinet/api.py`**

```python
# ============================================
# HOLODILNIK SUPPLIER SEARCH API
# ============================================

from cabinet import holodilnik_service


@require_team_member
async def holodilnik_start_search(request: web.Request) -> web.Response:
    user = request['user']
    company = request['company']
    card_id = int(request.match_info['id'])

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    force = bool(body.get('force', False))

    result = await holodilnik_service.start_search(
        card_id=card_id,
        company_id=company['id'],
        by_user_id=user['user_id'],
        force=force,
    )
    if 'error' in result:
        return web.json_response(result, status=result.get('status', 400))
    if result.get('cached'):
        return web.json_response({'ok': True, 'cached': True, 'results': result['results']})
    return web.json_response({'ok': True, 'cached': False, 'task_id': result['task_id']}, status=202)


@require_team_member
async def holodilnik_get_status(request: web.Request) -> web.Response:
    task_id = request.query.get('task_id', '')
    if not task_id:
        return web.json_response({'error': 'task_id required'}, status=400)
    status = holodilnik_service.get_status(task_id)
    if not status:
        return web.json_response({'error': 'Task not found'}, status=404)
    return web.json_response(status)


@require_team_member
async def holodilnik_toggle_select(request: web.Request) -> web.Response:
    company = request['company']
    card_id = int(request.match_info['id'])
    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    position_idx = body.get('position_idx')
    sku = body.get('sku')
    selected = bool(body.get('selected', False))
    if position_idx is None or sku is None:
        return web.json_response({'error': 'position_idx and sku required'}, status=400)

    result = await holodilnik_service.toggle_selected(
        card_id, company['id'], int(position_idx), str(sku), selected,
    )
    return web.json_response(result, status=200 if result.get('ok') else 400)
```

- [ ] **Step 2: Routes в `cabinet/routes.py`**

В блок Pipeline routes добавить:
```python
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/holodilnik-search', api.holodilnik_start_search)
    app.router.add_get('/cabinet/api/pipeline/cards/{id}/holodilnik-status', api.holodilnik_get_status)
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/holodilnik-toggle', api.holodilnik_toggle_select)
```

- [ ] **Step 3: Smoke check**

Run: `python -c "from cabinet import api, routes; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add cabinet/api.py cabinet/routes.py
git commit -m "feat(holodilnik): API endpoints — start_search/get_status/toggle"
```

---

## Phase 3 — Frontend

### Task 3.1: Подмодалка HTML

**Files:** `cabinet/templates/_modal_holodilnik.html`

- [ ] **Step 1: Создать шаблон**

```html
<div class="modal-overlay" id="hl-modal" hidden>
  <div class="modal-window hl-window">
    <button class="modal-close" id="hl-modal-close" aria-label="Закрыть">×</button>
    <div class="modal-head">
      <h2>Поставщики · holodilnik.ru</h2>
      <p class="hl-subtitle" id="hl-subtitle"></p>
    </div>

    <div class="modal-body">
      <!-- Loading state -->
      <div id="hl-loading" class="hl-loading">
        <div class="hl-spinner">⏳</div>
        <div class="hl-loading-step" id="hl-loading-step">Запускаю поиск…</div>
        <div class="hl-loading-progress" id="hl-loading-progress">0/?</div>
      </div>

      <!-- Error state -->
      <div id="hl-error" class="hl-error" hidden>
        <strong>Не удалось выполнить поиск</strong>
        <p class="hl-error-msg" id="hl-error-msg"></p>
        <button class="btn btn-secondary btn-sm" id="hl-retry-btn">Попробовать снова</button>
      </div>

      <!-- Results state -->
      <div id="hl-results" hidden></div>
    </div>

    <div class="modal-footer hl-footer" id="hl-footer" hidden>
      <span id="hl-summary">Выбрано 0 · 0 ₽</span>
      <span style="margin-left: auto"></span>
      <button class="btn btn-ghost btn-sm" id="hl-rerun-btn">🔄 Заново</button>
      <button class="btn btn-primary btn-sm" id="hl-copy-skus">📋 Артикулы</button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Включить в _modal_card.html (внутри pipeline.html уже включается _modal_card.html). Меняю pipeline.html чтобы _modal_holodilnik рядом.**

В `cabinet/templates/pipeline.html` — после `{% include "_modal_card.html" %}` добавить:
```html
{% include "_modal_holodilnik.html" %}
```

- [ ] **Step 3: Commit**

```bash
git add cabinet/templates/_modal_holodilnik.html cabinet/templates/pipeline.html
git commit -m "feat(holodilnik): modal scaffold (loading/error/results states)"
```

### Task 3.2: CSS подмодалки

**Files:** `cabinet/static/css/pages/pipeline.css`

- [ ] **Step 1: Добавить стили**

В конец `pipeline.css`:
```css
/* ============= HOLODILNIK MODAL ============= */
.hl-window {
  width: 1100px;
  max-width: 96vw;
  max-height: 92vh;
}
.hl-subtitle {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--muted);
  font-family: var(--font-mono);
}
.hl-loading {
  text-align: center;
  padding: 60px 20px;
}
.hl-spinner {
  font-size: 48px;
  margin-bottom: 16px;
  animation: hl-pulse 1.5s ease-in-out infinite;
}
@keyframes hl-pulse {
  0%, 100% { opacity: 0.5; }
  50% { opacity: 1; }
}
.hl-loading-step {
  font-size: 14px;
  color: var(--text);
  margin-bottom: 8px;
}
.hl-loading-progress {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--muted);
}
.hl-error {
  padding: 20px;
  background: rgba(177, 61, 40, 0.08);
  border: 1px solid rgba(177, 61, 40, 0.25);
  border-radius: 6px;
  color: var(--alert);
}
.hl-error-msg {
  font-size: 12px;
  color: var(--sub);
  margin: 8px 0 14px;
}
.hl-position {
  margin-bottom: 24px;
}
.hl-position-head {
  background: var(--bg-2);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 10px 14px;
  margin-bottom: 10px;
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
}
.hl-position-name {
  font-weight: 600;
  font-size: 13px;
  color: var(--text);
}
.hl-position-meta {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--muted);
}
.hl-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}
@media (max-width: 900px) {
  .hl-grid { grid-template-columns: repeat(3, 1fr); }
}
@media (max-width: 600px) {
  .hl-grid { grid-template-columns: repeat(2, 1fr); }
}
.hl-item {
  background: var(--bg-raised);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 10px;
  position: relative;
  transition: border-color 0.12s;
}
.hl-item:hover { border-color: var(--accent-line); }
.hl-item.selected {
  border-color: var(--accent);
  background: var(--accent-dim);
}
.hl-item .hl-photo {
  width: 100%;
  aspect-ratio: 1;
  background: var(--bg-2);
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--muted);
  font-size: 26px;
  margin-bottom: 6px;
  overflow: hidden;
}
.hl-item .hl-photo img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.hl-item .hl-name {
  font-size: 11.5px;
  line-height: 1.3;
  color: var(--text);
  height: 30px;
  overflow: hidden;
  margin-bottom: 4px;
  font-weight: 500;
}
.hl-item .hl-sku {
  font-family: var(--font-mono);
  font-size: 9px;
  color: var(--muted);
  margin-bottom: 4px;
}
.hl-item .hl-price {
  font-family: var(--font-mono);
  font-weight: 700;
  font-size: 13px;
  color: var(--text);
}
.hl-item .hl-out-of-stock {
  font-size: 10px;
  color: var(--alert);
  margin-top: 4px;
}
.hl-item .hl-check {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 18px;
  height: 18px;
  accent-color: var(--accent);
  cursor: pointer;
}
.hl-footer {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 24px;
  border-top: 1px solid var(--line);
  background: var(--bg-2);
}
.hl-footer #hl-summary {
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--text);
  font-weight: 600;
}
```

- [ ] **Step 2: Bump version в pipeline.html**

В `cabinet/templates/pipeline.html`:
```
- <link rel="stylesheet" href="/cabinet/static/css/pages/pipeline.css?v=5">
+ <link rel="stylesheet" href="/cabinet/static/css/pages/pipeline.css?v=6">
```

- [ ] **Step 3: Commit**

```bash
git add cabinet/static/css/pages/pipeline.css cabinet/templates/pipeline.html
git commit -m "feat(holodilnik): modal CSS — loading, error, results grid"
```

### Task 3.3: JS — open/poll/render

**Files:** `cabinet/static/js/pages/holodilnik.js`, `cabinet/templates/pipeline.html`

- [ ] **Step 1: Создать новый файл**

`cabinet/static/js/pages/holodilnik.js`:
```javascript
/* Cabinet — Holodilnik supplier search modal.
   Async + polling. Никакого innerHTML — только createElement / textContent. */
(function () {
  const { Toast } = window.Cabinet;

  const modal = document.getElementById('hl-modal');
  if (!modal) return;

  const closeBtn = document.getElementById('hl-modal-close');
  const subtitle = document.getElementById('hl-subtitle');
  const loadingEl = document.getElementById('hl-loading');
  const loadingStep = document.getElementById('hl-loading-step');
  const loadingProgress = document.getElementById('hl-loading-progress');
  const errorEl = document.getElementById('hl-error');
  const errorMsg = document.getElementById('hl-error-msg');
  const retryBtn = document.getElementById('hl-retry-btn');
  const resultsEl = document.getElementById('hl-results');
  const footerEl = document.getElementById('hl-footer');
  const summaryEl = document.getElementById('hl-summary');
  const rerunBtn = document.getElementById('hl-rerun-btn');
  const copyBtn = document.getElementById('hl-copy-skus');

  let currentCardId = null;
  let currentPollInterval = null;
  let currentResults = null;

  function setState(state) {
    loadingEl.hidden = state !== 'loading';
    errorEl.hidden = state !== 'error';
    resultsEl.hidden = state !== 'results';
    footerEl.hidden = state !== 'results';
  }

  function openModal(cardId, tenderName) {
    currentCardId = cardId;
    subtitle.textContent = tenderName || ('Карточка #' + cardId);
    modal.hidden = false;
    setState('loading');
    loadingStep.textContent = 'Запускаю поиск…';
    loadingProgress.textContent = '';
    startSearch({ force: false });
  }

  function closeModal() {
    modal.hidden = true;
    if (currentPollInterval) {
      clearInterval(currentPollInterval);
      currentPollInterval = null;
    }
    currentCardId = null;
  }

  closeBtn.addEventListener('click', closeModal);
  modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !modal.hidden) closeModal();
  });

  async function startSearch({ force }) {
    setState('loading');
    loadingStep.textContent = force ? 'Запускаю поиск заново…' : 'Запускаю поиск…';
    try {
      const r = await fetch(
        '/cabinet/api/pipeline/cards/' + currentCardId + '/holodilnik-search',
        {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ force }),
        },
      );
      const data = await r.json();
      if (r.status === 200 && data.cached) {
        renderResults(data.results);
        return;
      }
      if (r.status === 202 && data.task_id) {
        startPolling(data.task_id);
        return;
      }
      showError(data.error || 'Неизвестная ошибка');
    } catch (e) {
      showError(e.message || 'Ошибка соединения');
    }
  }

  function startPolling(taskId) {
    let attempts = 0;
    const maxAttempts = 150; // 5 минут при 2с интервале
    currentPollInterval = setInterval(async () => {
      attempts += 1;
      if (attempts > maxAttempts) {
        clearInterval(currentPollInterval);
        showError('Поиск занял больше 5 минут. Попробуйте позже.');
        return;
      }
      try {
        const r = await fetch(
          '/cabinet/api/pipeline/cards/' + currentCardId + '/holodilnik-status?task_id=' + encodeURIComponent(taskId),
          { credentials: 'same-origin' },
        );
        if (!r.ok) return;
        const data = await r.json();
        if (data.progress) loadingProgress.textContent = data.progress;
        if (data.current_step) loadingStep.textContent = data.current_step;

        if (data.status === 'done') {
          clearInterval(currentPollInterval);
          currentPollInterval = null;
          renderResults(data.results);
        } else if (data.status === 'error') {
          clearInterval(currentPollInterval);
          currentPollInterval = null;
          showError(data.error || 'Ошибка');
        }
      } catch (e) {
        // ignore single poll failures
      }
    }, 2000);
  }

  function showError(msg) {
    setState('error');
    errorMsg.textContent = msg;
  }

  retryBtn.addEventListener('click', () => startSearch({ force: true }));
  rerunBtn.addEventListener('click', () => startSearch({ force: true }));

  copyBtn.addEventListener('click', async () => {
    const skus = collectSelectedSkus();
    if (!skus.length) {
      Toast.show('Не выбрано ни одной модели', 'alert');
      return;
    }
    try {
      await navigator.clipboard.writeText(skus.join('\n'));
      Toast.show('✓ Артикулы скопированы (' + skus.length + ')', 'positive');
    } catch (e) {
      // Fallback — prompt
      window.prompt('Скопируйте артикулы:', skus.join('\n'));
    }
  });

  function collectSelectedSkus() {
    return Array.from(resultsEl.querySelectorAll('.hl-item.selected'))
      .map(el => el.dataset.sku)
      .filter(Boolean);
  }

  function renderResults(blob) {
    currentResults = blob;
    setState('results');
    resultsEl.replaceChildren();

    const positions = blob.positions || [];
    if (!positions.length) {
      const empty = document.createElement('div');
      empty.className = 'hl-error';
      empty.textContent = 'Не нашли подходящих моделей. Попробуйте обновить ТЗ.';
      resultsEl.appendChild(empty);
      footerEl.hidden = true;
      return;
    }

    positions.forEach((pos, idx) => {
      const section = document.createElement('div');
      section.className = 'hl-position';

      const head = document.createElement('div');
      head.className = 'hl-position-head';
      const name = document.createElement('span');
      name.className = 'hl-position-name';
      name.textContent = pos.tz_text || ('Позиция ' + (idx + 1));
      head.appendChild(name);
      const meta = document.createElement('span');
      meta.className = 'hl-position-meta';
      const cnt = (pos.results || []).length;
      meta.textContent = cnt + ' моделей' + (pos.ai_query ? ' · «' + pos.ai_query + '»' : '');
      head.appendChild(meta);
      section.appendChild(head);

      const grid = document.createElement('div');
      grid.className = 'hl-grid';
      (pos.results || []).forEach(item => {
        grid.appendChild(buildItemCard(item, idx));
      });
      section.appendChild(grid);

      resultsEl.appendChild(section);
    });

    refreshSummary();
  }

  function buildItemCard(item, positionIdx) {
    const card = document.createElement('div');
    card.className = 'hl-item' + (item.selected ? ' selected' : '');
    card.dataset.sku = item.sku || '';
    card.dataset.positionIdx = String(positionIdx);

    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'hl-check';
    cb.checked = !!item.selected;
    cb.addEventListener('change', () => onToggleSelect(card, positionIdx, item.sku, cb.checked));
    card.appendChild(cb);

    const photo = document.createElement('a');
    photo.className = 'hl-photo';
    photo.href = item.url || '#';
    photo.target = '_blank';
    photo.rel = 'noopener';
    if (item.img && /^https?:\/\//.test(item.img)) {
      const img = document.createElement('img');
      img.src = item.img;
      img.alt = '';
      img.loading = 'lazy';
      photo.appendChild(img);
    } else {
      photo.textContent = '📦';
    }
    card.appendChild(photo);

    const nameEl = document.createElement('div');
    nameEl.className = 'hl-name';
    nameEl.textContent = item.name || '';
    card.appendChild(nameEl);

    const skuEl = document.createElement('div');
    skuEl.className = 'hl-sku';
    skuEl.textContent = '№ ' + (item.sku || '');
    card.appendChild(skuEl);

    const priceEl = document.createElement('div');
    priceEl.className = 'hl-price';
    priceEl.textContent = (item.price ? item.price.toLocaleString('ru-RU') + ' ₽' : '—');
    card.appendChild(priceEl);

    if (item.in_stock === false) {
      const oos = document.createElement('div');
      oos.className = 'hl-out-of-stock';
      oos.textContent = 'Нет в наличии';
      card.appendChild(oos);
    }

    return card;
  }

  async function onToggleSelect(cardEl, positionIdx, sku, selected) {
    cardEl.classList.toggle('selected', selected);
    refreshSummary();
    try {
      await fetch(
        '/cabinet/api/pipeline/cards/' + currentCardId + '/holodilnik-toggle',
        {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ position_idx: positionIdx, sku, selected }),
        },
      );
    } catch (e) {
      // если не сохранилось — UI уже отражает выбор, на следующее открытие свежий load
    }
  }

  function refreshSummary() {
    const selected = resultsEl.querySelectorAll('.hl-item.selected');
    let totalPrice = 0;
    selected.forEach(el => {
      const priceEl = el.querySelector('.hl-price');
      const txt = priceEl ? priceEl.textContent.replace(/[^\d]/g, '') : '';
      if (txt) totalPrice += parseInt(txt, 10);
    });
    summaryEl.textContent = 'Выбрано: ' + selected.length + ' · ' + totalPrice.toLocaleString('ru-RU') + ' ₽';
  }

  // Public API
  window.Cabinet = window.Cabinet || {};
  window.Cabinet.Holodilnik = { open: openModal };
})();
```

- [ ] **Step 2: Подключить script в `pipeline.html`**

В блок `{% block page_js %}` добавить ПЕРЕД `pipeline.js`:
```html
<script src="/cabinet/static/js/pages/holodilnik.js?v=1"></script>
```

- [ ] **Step 3: Commit**

```bash
git add cabinet/static/js/pages/holodilnik.js cabinet/templates/pipeline.html
git commit -m "feat(holodilnik): JS — open/poll/render with checkboxes and copy-SKUs"
```

### Task 3.4: Wire-up кнопки в pipeline.js

**Files:** `cabinet/static/js/pages/pipeline.js`

- [ ] **Step 1: Найти и заменить supplierBtn handler**

Текущий код в `pipeline.js` (renderModal):
```javascript
    const supplierBtn = document.getElementById('cm-btn-supplier');
    supplierBtn.hidden = c.stage !== 'RFQ';
    supplierBtn.onclick = () => Toast.show('Функция в разработке (holodilnik integration)', 'alert');
```

Заменить на:
```javascript
    const supplierBtn = document.getElementById('cm-btn-supplier');
    supplierBtn.hidden = c.stage !== 'RFQ';
    supplierBtn.onclick = () => {
      if (window.Cabinet.Holodilnik) {
        const tenderName = (c.data && c.data.name) || ('Тендер ' + c.tender_number);
        window.Cabinet.Holodilnik.open(c.id, tenderName);
      } else {
        Toast.show('Holodilnik UI не загружен', 'alert');
      }
    };
```

- [ ] **Step 2: Bump version**

В `pipeline.html`:
```
- <script src="/cabinet/static/js/pages/pipeline.js?v=4"></script>
+ <script src="/cabinet/static/js/pages/pipeline.js?v=5"></script>
```

- [ ] **Step 3: Commit**

```bash
git add cabinet/static/js/pages/pipeline.js cabinet/templates/pipeline.html
git commit -m "feat(holodilnik): wire 'Найти на holodilnik.ru' button to open modal"
```

---

## Phase 4 — Polish + smoke

### Task 4.1: openai в requirements

**Files:** `requirements.txt`

- [ ] **Step 1: Проверить наличие openai**

Run: `grep -i "^openai" requirements.txt`

Если НЕТ — добавить `openai>=1.0.0` в файл.

(Проект уже использует OpenAI в других местах — `tender_sniper/ai_*` — поэтому скорее всего есть. Step просто на всякий случай.)

- [ ] **Step 2: bs4 проверка**

Run: `grep -iE "^beautifulsoup|^bs4" requirements.txt`
Expected: `beautifulsoup4>=...` присутствует.

Если нет — `beautifulsoup4>=4.12.0`.

- [ ] **Step 3: Commit (если были изменения)**

```bash
git add requirements.txt
git commit -m "chore(holodilnik): ensure openai + beautifulsoup4 in requirements"
```

### Task 4.2: Финальный smoke + push

- [ ] **Step 1: Smoke import всего**

Run:
```bash
python -c "
from cabinet import api, routes, holodilnik_service
print('imports OK')
print('endpoints:', [n for n in dir(api) if 'holodilnik' in n])
"
```
Expected: `imports OK` + 3 endpoint names.

- [ ] **Step 2: Run tests**

Run: `pytest tests/unit/test_holodilnik_service.py -v`
Expected: все passed (или ≥3, если skipped какие-то по env).

- [ ] **Step 3: Push**

```bash
git push origin main
```

- [ ] **Step 4: Manual smoke в браузере (после deploy)**

1. Открыть `https://cabinet.tendersniper.ru/cabinet/pipeline`
2. Найти карточку или создать → перетащить в стадию RFQ
3. Кликнуть карточку → открывается pipeline-модалка
4. Кликнуть «Найти на holodilnik.ru» → открывается hl-modal в loading-state
5. Через 30-90 сек видим сетку моделей по позициям
6. Чекбоксы — переключаются, footer показывает «Выбрано N»
7. «📋 Артикулы» — копирует SKU в clipboard
8. Закрыть модалку → открыть снова → результат из кэша мгновенно
9. «🔄 Заново» — игнорирует кэш

Если что-то падает:
- Открыть DevTools → Network → проверить `/holodilnik-search` и `/holodilnik-status` ответы
- Railway logs `railway logs | grep -i holodilnik`

### Task 4.3: Если HTML-парсер не работает на реальных данных

**Признак:** в `_TASKS[task_id].results.positions[*].results` массивы пустые, хотя на сайте моделей много.

**Действие:** скачать `tests/unit/fixtures/holodilnik_search.html` заново (с актуальной разметкой) → открыть в браузере → DevTools → найти реальные классы карточек товаров → исправить `candidates = soup.select(...)` в `_parse_search_html`.

Этот шаг — итеративный. Селекторы holodilnik могут поменяться от версии сайта.

---

## Self-Review Checklist

После реализации:

- [ ] **Spec coverage:** §1 цель, §3 архитектура, §4 модель данных, §5 backend, §6 UI — всё есть в Tasks 1.1-3.4.
- [ ] **No innerHTML:** `grep -n "innerHTML" cabinet/static/js/pages/holodilnik.js` пусто.
- [ ] **RBAC:** все 3 endpoint декорированы `@require_team_member` и проверяют `card.company_id`.
- [ ] **Тестирование:** unit для AI keyword (mock), для parser (fixture), для toggle_selected (DB).
- [ ] **Cache TTL:** 24 часа, в `data.suppliers.holodilnik.expires_at`.
- [ ] **Стоимость:** AI keyword ~$0.001/позиция × 5-10 = $0.01/тендер. ОК.
- [ ] **No mocks of HTTP в проде:** `_fetch_holodilnik_search` всегда делает реальный запрос.
- [ ] **Безопасность:** rendering через textContent / createElement, никакого raw HTML.

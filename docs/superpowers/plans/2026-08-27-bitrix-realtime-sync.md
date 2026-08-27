# Real-time синк Bitrix24 → Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Заменить 5-минутный неполный поллинг Bitrix24 → Pipeline на событийный push (секунды) для создания/изменения/удаления сделок и полей, плюс лёгкий поллинг для комментариев (у Bitrix нет push-события на комментарий).

**Architecture:** Bitrix «исходящий вебхук» (без OAuth) шлёт событие с ID сделки на новый эндпоинт `/webhook/bitrix24/events`; хендлер не доверяет телу запроса и перезапрашивает актуальные данные через уже сохранённый входящий вебхук компании (`crm.deal.get`), затем апсертит карточку и логирует изменения полей в историю. Отдельный фоновый job раз в ~90 сек батчем подтягивает новые комментарии по отслеживаемым сделкам. Старый 5-минутный полный поллинг остаётся как сетка безопасности, но снижается до 1 раза в час.

**Tech Stack:** Python 3.11, aiohttp (веб-сервер и REST-клиент), SQLAlchemy async + PostgreSQL (prod) / SQLite+aiosqlite (тесты), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-08-27-bitrix-realtime-sync-design.md`

## Global Constraints

- Интеграция с Bitrix — best-effort: любая ошибка логируется, никогда не роняет кабинет или основной поток бота (см. существующий docstring `cabinet/bitrix_sync.py`).
- Инбаунд-хендлер отвечает Bitrix HTTP 200 максимально быстро; вся реальная обработка — в фоновой задаче (`fire_and_forget`, уже определена в `cabinet/bitrix_sync.py`).
- Никогда не доверять данным из тела вебхук-запроса Bitrix напрямую — только ID сделки/событие; актуальные поля всегда перезапрашиваются через `crm.deal.get` с использованием **собственного** сохранённого webhook компании.
- Удаление сделки в Bitrix → карточка архивируется (`archived_at`), не удаляется физически.
- `OPPORTUNITY` (сумма) из Bitrix не перезаписывает `PipelineCard.sale_price` — это независимый ручной расчёт маржи в кабинете.
- Если `ASSIGNED_BY_ID` в Bitrix не сопоставляется ни с одним `sniper_users.data['bitrix_user_id']` этой же компании — `assignee_user_id` не трогаем, только пишем отображаемое имя в `data['bitrix_assigned_name']`.
- Anti-rollback для стадий сохраняется как есть: если карточка в Pipeline уже дальше по `_STAGE_ORDER`, откат назад из Bitrix не делаем.
- Тесты, трогающие БД, используют `sqlite+aiosqlite` in-memory + monkeypatch `DatabaseSession` (см. `tests/unit/test_kp_adapter.py` — образец fixture). Raw SQL с Postgres-специфичным синтаксисом (`::text`, `->>'`) не использовать в новом коде — фильтровать в Python после ORM-запроса, чтобы тесты работали одинаково на SQLite и Postgres.

---

## Файловая структура (что создаём/трогаем)

- `bot/handlers/bitrix24.py` — новые REST-хелперы: `get_bitrix24_deal`, `list_deal_timeline_comments`, `batch_list_deal_comments`.
- `cabinet/bitrix_sync.py` — основная логика: секреты инбаунда, извлечение общих функций создания карточки из сделки, диффер полей, синк ответственного, диспетчер событий, синк комментариев. Файл уже 734 строки — растёт до ~1100; не разбиваем на части в этом подпроекте (следующий, кто тронет файл, может вынести секции в отдельные модули, если разрастётся дальше).
- `bot/health_check.py` — новый aiohttp-хендлер `bitrix24_events_handler` + регистрация роута.
- `tender_sniper/jobs/bitrix_comment_sync.py` — новый фоновый job (поллинг комментариев).
- `tender_sniper/jobs/bitrix_pull_sync.py` — снижение интервала с 300 до 3600 сек.
- `bot/main.py` — запуск нового job'а рядом с существующим `bitrix_pull_loop`.
- `cabinet/api.py` — новые хендлеры `get_bitrix_inbound_settings`, `rotate_bitrix_inbound_secret`.
- `cabinet/routes.py` — регистрация двух новых роутов.
- `cabinet/templates/settings.html` — новый блок «Обратный синк из Bitrix».
- `cabinet/static/js/pages/settings.js` — загрузка/рендер/ротация инбаунд-секрета.
- `cabinet/static/js/pages/pipeline.js` — расширение `formatHistoryAction` новыми типами записей истории.
- `tests/unit/test_bitrix24_rest_helpers.py` — новый файл, тесты REST-хелперов.
- `tests/unit/test_bitrix_sync_characterization.py` — новый файл, «замораживает» текущее поведение `import_deals_to_pipeline`/`pull_changes_from_bitrix` перед рефакторингом.
- `tests/unit/test_bitrix_sync_events.py` — новый файл, тесты `handle_deal_event` и всех новых функций.
- `tests/unit/test_bitrix_comment_sync.py` — новый файл, тесты синка комментариев.
- `tests/unit/test_bitrix_inbound_settings_api.py` — новый файл, тесты API-хендлеров настроек.

---

### Task 1: Bitrix24 REST-хелперы (get deal, list comments, batch comments)

**Files:**
- Modify: `bot/handlers/bitrix24.py` (добавить импорт `Dict, List, Any` в typing, добавить 3 функции после `update_bitrix24_deal_stage`, строка ~231)
- Test: `tests/unit/test_bitrix24_rest_helpers.py`

**Interfaces:**
- Produces: `get_bitrix24_deal(webhook_url: str, deal_id: str) -> Optional[dict]`, `list_deal_timeline_comments(webhook_url: str, deal_id: str, since_id: int = 0) -> List[dict]`, `batch_list_deal_comments(webhook_url: str, deal_since: Dict[str, int]) -> Dict[str, List[dict]]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_bitrix24_rest_helpers.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from aioresponses import aioresponses

from bot.handlers.bitrix24 import (
    get_bitrix24_deal, list_deal_timeline_comments, batch_list_deal_comments,
)

WEBHOOK = 'https://portal.bitrix24.ru/rest/1/token/'


@pytest.mark.asyncio
async def test_get_bitrix24_deal_returns_result():
    with aioresponses() as m:
        m.post(WEBHOOK + 'crm.deal.get.json', payload={'result': {'ID': '5', 'TITLE': 'Deal'}})
        deal = await get_bitrix24_deal(WEBHOOK, '5')
    assert deal == {'ID': '5', 'TITLE': 'Deal'}


@pytest.mark.asyncio
async def test_get_bitrix24_deal_returns_none_on_http_error():
    with aioresponses() as m:
        m.post(WEBHOOK + 'crm.deal.get.json', status=500)
        deal = await get_bitrix24_deal(WEBHOOK, '5')
    assert deal is None


@pytest.mark.asyncio
async def test_get_bitrix24_deal_returns_none_on_exception():
    with aioresponses() as m:
        m.post(WEBHOOK + 'crm.deal.get.json', exception=ConnectionError('boom'))
        deal = await get_bitrix24_deal(WEBHOOK, '5')
    assert deal is None


@pytest.mark.asyncio
async def test_list_deal_timeline_comments_no_since():
    with aioresponses() as m:
        m.get(
            WEBHOOK + 'crm.timeline.comment.list.json?filter%5BENTITY_ID%5D=5&filter%5BENTITY_TYPE%5D=deal',
            payload={'result': [{'ID': '1', 'COMMENT': 'hi'}]},
        )
        comments = await list_deal_timeline_comments(WEBHOOK, '5')
    assert comments == [{'ID': '1', 'COMMENT': 'hi'}]


@pytest.mark.asyncio
async def test_list_deal_timeline_comments_with_since():
    with aioresponses() as m:
        m.get(
            WEBHOOK + 'crm.timeline.comment.list.json'
            '?filter%5BENTITY_ID%5D=5&filter%5BENTITY_TYPE%5D=deal&filter%5B%3EID%5D=10',
            payload={'result': []},
        )
        comments = await list_deal_timeline_comments(WEBHOOK, '5', since_id=10)
    assert comments == []


@pytest.mark.asyncio
async def test_batch_list_deal_comments_empty_input_returns_empty():
    result = await batch_list_deal_comments(WEBHOOK, {})
    assert result == {}


@pytest.mark.asyncio
async def test_batch_list_deal_comments_maps_by_deal_id():
    with aioresponses() as m:
        m.post(WEBHOOK + 'batch.json', payload={
            'result': {'result': {'5': [{'ID': '1'}], '7': []}},
        })
        result = await batch_list_deal_comments(WEBHOOK, {'5': 0, '7': 3})
    assert result == {'5': [{'ID': '1'}], '7': []}


@pytest.mark.asyncio
async def test_batch_list_deal_comments_chunks_over_50():
    deal_since = {str(i): 0 for i in range(75)}
    calls = []

    def _payload_for_chunk(chunk_size):
        return {'result': {'result': {str(i): [] for i in range(chunk_size)}}}

    with aioresponses() as m:
        # первый батч — 50 сделок, второй — оставшиеся 25
        m.post(WEBHOOK + 'batch.json', payload=_payload_for_chunk(50))
        m.post(WEBHOOK + 'batch.json', payload=_payload_for_chunk(25))
        result = await batch_list_deal_comments(WEBHOOK, deal_since)
    assert len(result) >= 50  # обе пачки учтены (ключи из двух ответов объединяются в rerекции ниже)
```

- [ ] **Step 2: Run tests to verify they fail**

`requirements.txt` в этом репозитории содержит только прод-зависимости — `pytest`/`pytest-asyncio` тоже туда не включены (см. `tests/unit/test_kp_adapter.py`, использующий их без записи в requirements.txt), поэтому `aioresponses` — такая же тестовая зависимость окружения, а не строка в `requirements.txt`.

Run: `pip3 install aioresponses`, затем `pytest tests/unit/test_bitrix24_rest_helpers.py -v`
Expected: FAIL с `ImportError: cannot import name 'get_bitrix24_deal'`

- [ ] **Step 3: Implement the three helpers**

```python
# bot/handlers/bitrix24.py — заменить строку "from typing import Optional, Tuple" на:
from typing import Optional, Tuple, Dict, List, Any

# Добавить после update_bitrix24_deal_stage (после строки ~231, перед комментарием
# "# Маппинг закона → ID перечисления в Битрикс24"):


async def get_bitrix24_deal(webhook_url: str, deal_id: str) -> Optional[dict]:
    """Возвращает актуальные поля сделки через crm.deal.get, или None при ошибке."""
    if not webhook_url.endswith('/'):
        webhook_url += '/'
    endpoint = webhook_url + 'crm.deal.get.json'
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.post(endpoint, json={'id': deal_id}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('result')
                logger.warning(f"get_bitrix24_deal HTTP {resp.status}")
                return None
    except Exception as e:
        logger.error(f"get_bitrix24_deal error: {e}")
        return None


async def list_deal_timeline_comments(webhook_url: str, deal_id: str, since_id: int = 0) -> List[dict]:
    """Комментарии ленты сделки. since_id>0 — только с ID больше указанного."""
    if not webhook_url.endswith('/'):
        webhook_url += '/'
    endpoint = webhook_url + 'crm.timeline.comment.list.json'
    params = [('filter[ENTITY_ID]', deal_id), ('filter[ENTITY_TYPE]', 'deal')]
    if since_id:
        params.append(('filter[>ID]', str(since_id)))
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(endpoint, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('result') or []
                logger.warning(f"list_deal_timeline_comments HTTP {resp.status}")
                return []
    except Exception as e:
        logger.error(f"list_deal_timeline_comments error: {e}")
        return []


async def batch_list_deal_comments(webhook_url: str, deal_since: Dict[str, int]) -> Dict[str, List[dict]]:
    """Батчем (Bitrix `batch` method, до 50 подзапросов за раз) тянет новые
    комментарии для набора сделок. deal_since: {deal_id: since_id}.
    Возвращает {deal_id: [новые комментарии]} — отсутствующие/ошибочные сделки
    просто не попадают в результат (best-effort).
    """
    if not deal_since:
        return {}
    if not webhook_url.endswith('/'):
        webhook_url += '/'
    endpoint = webhook_url + 'batch.json'
    results: Dict[str, List[dict]] = {}
    items = list(deal_since.items())
    for i in range(0, len(items), 50):
        chunk = items[i:i + 50]
        cmd: Dict[str, str] = {}
        for deal_id, since_id in chunk:
            query = f'filter[ENTITY_ID]={deal_id}&filter[ENTITY_TYPE]=deal'
            if since_id:
                query += f'&filter[>ID]={since_id}'
            cmd[str(deal_id)] = f'crm.timeline.comment.list?{query}'
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.post(endpoint, json={'halt': 0, 'cmd': cmd}) as resp:
                    if resp.status != 200:
                        logger.warning(f"batch_list_deal_comments HTTP {resp.status}")
                        continue
                    data = await resp.json()
                    batch_result = (data.get('result') or {}).get('result') or {}
                    for deal_id, _ in chunk:
                        results[str(deal_id)] = batch_result.get(str(deal_id)) or []
        except Exception as e:
            logger.error(f"batch_list_deal_comments error: {e}")
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_bitrix24_rest_helpers.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add bot/handlers/bitrix24.py tests/unit/test_bitrix24_rest_helpers.py
git commit -m "feat(bitrix): add REST helpers for deal fetch and timeline comments"
```

---

### Task 2: Инбаунд-секрет компании (генерация/проверка)

**Files:**
- Modify: `cabinet/bitrix_sync.py` (добавить после `_get_company_webhook`, ~строка 72)
- Test: `tests/unit/test_bitrix_sync_events.py` (создать файл, начать с этого блока)

**Interfaces:**
- Consumes: `Company`, `SniperUser`, `DatabaseSession` (уже импортированы в файле).
- Produces: `get_or_create_inbound_secret(company_id: int) -> Optional[str]`, `rotate_inbound_secret(company_id: int) -> Optional[str]`, `verify_inbound_secret(company_id: int, secret: str) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_bitrix_sync_events.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm.attributes import flag_modified

import cabinet.bitrix_sync as bitrix_sync
from database import Base, SniperUser, Company


class FakeDatabaseSession:
    factory = None

    async def __aenter__(self):
        self.session = FakeDatabaseSession.factory()
        return self.session

    async def __aexit__(self, et, ev, tb):
        if et is not None:
            await self.session.rollback()
        else:
            await self.session.commit()
        await self.session.close()


@pytest_asyncio.fixture
async def db(monkeypatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bitrix_events_test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    FakeDatabaseSession.factory = factory
    monkeypatch.setattr(bitrix_sync, "DatabaseSession", FakeDatabaseSession)

    async with factory() as s:
        owner = SniperUser(telegram_id=111)
        s.add(owner); await s.flush()
        company = Company(name="Team", owner_user_id=owner.id)
        s.add(company); await s.flush()
        await s.commit()
        company_id = company.id

    yield company_id
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_or_create_inbound_secret_is_stable(db):
    company_id = db
    secret1 = await bitrix_sync.get_or_create_inbound_secret(company_id)
    secret2 = await bitrix_sync.get_or_create_inbound_secret(company_id)
    assert secret1 == secret2
    assert len(secret1) >= 16


@pytest.mark.asyncio
async def test_rotate_inbound_secret_changes_value(db):
    company_id = db
    secret1 = await bitrix_sync.get_or_create_inbound_secret(company_id)
    secret2 = await bitrix_sync.rotate_inbound_secret(company_id)
    assert secret1 != secret2
    assert await bitrix_sync.get_or_create_inbound_secret(company_id) == secret2


@pytest.mark.asyncio
async def test_verify_inbound_secret(db):
    company_id = db
    secret = await bitrix_sync.get_or_create_inbound_secret(company_id)
    assert await bitrix_sync.verify_inbound_secret(company_id, secret) is True
    assert await bitrix_sync.verify_inbound_secret(company_id, 'wrong') is False
    assert await bitrix_sync.verify_inbound_secret(company_id, '') is False


@pytest.mark.asyncio
async def test_verify_inbound_secret_unknown_company_is_false(db):
    assert await bitrix_sync.verify_inbound_secret(999999, 'anything') is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_bitrix_sync_events.py -v`
Expected: FAIL с `AttributeError: module 'cabinet.bitrix_sync' has no attribute 'get_or_create_inbound_secret'`

- [ ] **Step 3: Implement the secret helpers**

```python
# cabinet/bitrix_sync.py — добавить "import secrets" к существующим импортам
# (после "import re", строка 18), и добавить функции после _get_company_webhook
# (после строки 71, перед "# ============================================\n# Push: ..."):


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
        return bool(stored) and stored == secret
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_bitrix_sync_events.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add cabinet/bitrix_sync.py tests/unit/test_bitrix_sync_events.py
git commit -m "feat(bitrix): per-company inbound secret for outgoing webhook auth"
```

---

### Task 3: Characterization-тесты текущего поведения (до рефакторинга)

Цель шага — зафиксировать поведение `import_deals_to_pipeline` и математики стадий из `pull_changes_from_bitrix` ДО того, как Task 4/5 вынесут из них общие функции. Если после рефакторинга эти тесты продолжат проходить без изменений — рефакторинг ничего не сломал.

**Files:**
- Test: `tests/unit/test_bitrix_sync_characterization.py`

**Interfaces:**
- Consumes: `cabinet.bitrix_sync.import_deals_to_pipeline`, `cabinet.bitrix_sync.pull_changes_from_bitrix` (текущие, ещё не рефакторенные).

- [ ] **Step 1: Write the tests against current behavior**

```python
# tests/unit/test_bitrix_sync_characterization.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

import cabinet.bitrix_sync as bitrix_sync
from database import Base, SniperUser, Company, PipelineCard


class FakeDatabaseSession:
    factory = None

    async def __aenter__(self):
        self.session = FakeDatabaseSession.factory()
        return self.session

    async def __aexit__(self, et, ev, tb):
        if et is not None:
            await self.session.rollback()
        else:
            await self.session.commit()
        await self.session.close()


@pytest_asyncio.fixture
async def setup(monkeypatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/char_test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    FakeDatabaseSession.factory = factory
    monkeypatch.setattr(bitrix_sync, "DatabaseSession", FakeDatabaseSession)

    async with factory() as s:
        owner = SniperUser(telegram_id=222, data={'bitrix24_webhook_url': 'https://x.bitrix24.ru/rest/1/tok/', 'bitrix24_enabled': True})
        s.add(owner); await s.flush()
        company = Company(name="Team", owner_user_id=owner.id)
        s.add(company); await s.flush()
        await s.commit()
        company_id, owner_id = company.id, owner.id

    yield company_id, owner_id, factory
    await engine.dispose()


def _deal(deal_id, stage='NEW', title='Поставка бумаги', number='0123456789012345678'):
    return {
        'ID': deal_id, 'STAGE_ID': stage, 'TITLE': title,
        'OPPORTUNITY': '150000', 'CLOSEDATE': '2026-09-01T00:00:00+03:00',
        'UF_CRM_TENDER_CUSTOMER': 'ООО Ромашка', 'UF_CRM_TENDER_REGION': 'Москва',
        'COMMENTS': f'Тендер {number}',
    }


@pytest.mark.asyncio
async def test_import_creates_card_with_expected_stage_and_source(setup, monkeypatch):
    company_id, owner_id, factory = setup

    async def fake_fetch_all_deals(webhook):
        return [_deal('501', stage='NEW')]
    monkeypatch.setattr(bitrix_sync, "_fetch_all_deals", fake_fetch_all_deals)

    result = await bitrix_sync.import_deals_to_pipeline(company_id)
    assert result['imported'] == 1

    async with factory() as s:
        card = (await s.execute(select(PipelineCard).where(PipelineCard.company_id == company_id))).scalar_one()
        assert card.stage == 'FOUND'
        assert card.source == 'bitrix_import'
        assert card.data['bitrix_deal_id'] == '501'
        assert card.data['name'] == 'Поставка бумаги'


@pytest.mark.asyncio
async def test_import_skips_existing_card_but_backfills_deal_id(setup, monkeypatch):
    company_id, owner_id, factory = setup
    async with factory() as s:
        s.add(PipelineCard(
            company_id=company_id, tender_number='0123456789012345678',
            stage='IN_WORK', assignee_user_id=owner_id, created_by=owner_id,
            data={'name': 'уже в pipeline'},
        ))
        await s.commit()

    async def fake_fetch_all_deals(webhook):
        return [_deal('501')]
    monkeypatch.setattr(bitrix_sync, "_fetch_all_deals", fake_fetch_all_deals)

    result = await bitrix_sync.import_deals_to_pipeline(company_id)
    assert result['imported'] == 0
    assert result['skipped'] == 1

    async with factory() as s:
        card = (await s.execute(select(PipelineCard).where(PipelineCard.company_id == company_id))).scalar_one()
        assert card.stage == 'IN_WORK'  # не тронуто
        assert card.data['bitrix_deal_id'] == '501'  # но подставлено


@pytest.mark.asyncio
async def test_pull_updates_stage_for_mapped_stage_and_ignores_unmapped(setup, monkeypatch):
    company_id, owner_id, factory = setup
    async with factory() as s:
        card = PipelineCard(
            company_id=company_id, tender_number='t1', stage='FOUND',
            assignee_user_id=owner_id, created_by=owner_id,
            data={'bitrix_deal_id': '900'},
        )
        s.add(card)
        await s.commit()

    async def fake_fetch_modified_deals(webhook, since):
        return [{'ID': '900', 'STAGE_ID': 'WON'}]
    monkeypatch.setattr(bitrix_sync, "_fetch_modified_deals", fake_fetch_modified_deals)

    result = await bitrix_sync.pull_changes_from_bitrix(company_id)
    assert result['updated'] == 1

    async with factory() as s:
        card = (await s.execute(select(PipelineCard).where(PipelineCard.company_id == company_id))).scalar_one()
        assert card.stage == 'RESULT'
        assert card.result == 'won'


@pytest.mark.asyncio
async def test_pull_does_not_rollback_further_along_card(setup, monkeypatch):
    company_id, owner_id, factory = setup
    async with factory() as s:
        card = PipelineCard(
            company_id=company_id, tender_number='t2', stage='SUBMITTED',
            assignee_user_id=owner_id, created_by=owner_id,
            data={'bitrix_deal_id': '901'},
        )
        s.add(card)
        await s.commit()

    async def fake_fetch_modified_deals(webhook, since):
        return [{'ID': '901', 'STAGE_ID': 'NEW'}]  # NEW -> FOUND, это "назад"
    monkeypatch.setattr(bitrix_sync, "_fetch_modified_deals", fake_fetch_modified_deals)

    result = await bitrix_sync.pull_changes_from_bitrix(company_id)
    assert result['updated'] == 0

    async with factory() as s:
        card = (await s.execute(select(PipelineCard).where(PipelineCard.company_id == company_id))).scalar_one()
        assert card.stage == 'SUBMITTED'  # не откатили
```

- [ ] **Step 2: Run tests to verify they pass against current (pre-refactor) code**

Run: `pytest tests/unit/test_bitrix_sync_characterization.py -v`
Expected: PASS (4 passed) — это описание уже существующего поведения, не новой фичи.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_bitrix_sync_characterization.py
git commit -m "test(bitrix): characterize current import/pull behavior before refactor"
```

---

### Task 4: Вынести общую логику создания карточки из Bitrix-сделки

**Files:**
- Modify: `cabinet/bitrix_sync.py` (добавить функции перед `import_deals_to_pipeline`, рефакторить тело `import_deals_to_pipeline`)

**Interfaces:**
- Produces: `_TRACKED_DEAL_FIELDS: List[str]`, `_snapshot_from_deal(deal: dict) -> dict`, `_build_card_data_from_deal(deal: dict, cache: Optional[TenderCache], tender_number: str) -> dict`, `_create_card_from_deal(session, company_id: int, owner_user_id: int, tender_number: str, deal: dict, cache: Optional[TenderCache], source: str, history_action: str) -> PipelineCard`.
- Consumes (не меняются): `_IMPORT_STAGE_MAP`, `PipelineCard`, `PipelineCardHistory`, `Decimal`, `flag_modified`.

- [ ] **Step 1: Add the shared functions (no behavior change yet)**

```python
# cabinet/bitrix_sync.py — добавить перед "async def import_deals_to_pipeline"
# (перед строкой ~360), после блока _IMPORT_STAGE_MAP/_extract_tender_number*:

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
```

- [ ] **Step 2: Refactor `import_deals_to_pipeline` to use the shared functions**

Заменить блок создания карточки внутри цикла `for deal in deals:` (текущие строки ~446-509, от `# Собираем data` до `skipped += 1` в ветке IntegrityError) на:

```python
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
```

(Блок выше строки — `if exists: ... continue` и получение `cache` — не меняется, только замена самого создания карточки. Убедиться, что `Decimal` и `PipelineCardHistory`, `flag_modified` остаются импортированы — они уже есть в файле.)

- [ ] **Step 3: Run characterization tests to confirm no behavior change**

Run: `pytest tests/unit/test_bitrix_sync_characterization.py tests/unit/test_bitrix_sync_events.py -v`
Expected: PASS (все тесты из Task 2 и Task 3 всё ещё зелёные)

- [ ] **Step 4: Commit**

```bash
git add cabinet/bitrix_sync.py
git commit -m "refactor(bitrix): extract card-from-deal creation into shared helper"
```

---

### Task 5: Вынести stage-mapping логику из `pull_changes_from_bitrix` + исправить непортируемый поиск карточки

**Ruling (added after Task 3's characterization run):** Task 3's characterization tests revealed
that `pull_changes_from_bitrix`'s card lookup uses Postgres-only `data::text LIKE` raw SQL
(`cabinet/bitrix_sync.py:704-719` at the time of this ruling). This works in production (Postgres)
but (a) fails outright under SQLite — every pull silently degrades to a no-op counted as an
`error`, defeating this plan's whole test strategy for this function, and (b) is a latent
correctness bug even on Postgres: the pattern `%"bitrix_deal_id": 900%` has no closing-quote
boundary, so it can false-positive match deal id `9000`, `9001`, etc. This task's scope is
expanded to fix this now (rather than in Task 6, where it was originally planned) — `Produces`
below adds `_find_card_by_bitrix_deal_id`, moved up from what was Task 6. Task 6 will consume it
as already-defined rather than redefining it.

**Files:**
- Modify: `cabinet/bitrix_sync.py`

**Interfaces:**
- Produces: `_pull_stage_update(card: PipelineCard, stage_id: str) -> Optional[Dict[str, Any]]` — мутирует `card.stage`/`card.result` на месте, если применимо, и возвращает payload для истории; иначе `None` и `card` не тронута.
- Produces: `_find_card_by_bitrix_deal_id(session, company_id: int, deal_id: str) -> Optional[PipelineCard]` — портируемый (SQLite+Postgres) поиск карточки компании по `data['bitrix_deal_id']`, сравнением в Python вместо raw-SQL JSON-каста. Заменяет собой блок `text("SELECT id FROM pipeline_cards WHERE company_id = :cid AND data::text LIKE :pattern LIMIT 1")` внутри `pull_changes_from_bitrix`.

- [ ] **Step 1: Add the helpers**

```python
# cabinet/bitrix_sync.py — добавить перед "async def pull_changes_from_bitrix"
# (перед строкой ~608), после блока _STAGE_ORDER:


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
```

- [ ] **Step 2: Refactor `pull_changes_from_bitrix` to use both helpers**

Внутри `for deal in deals:` заменить блок от `async with DatabaseSession() as session:` (текущие строки ~704-719, включая старый raw-SQL lookup через `text(...)`) до `updated += 1` (текущая строка ~702, после рефакторинга ниже) на:

```python
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
```

(Строка `mapping = _PULL_STAGE_MAP.get(stage_id)` и последующий `if not mapping: continue`,
которые в текущем коде идут ПЕРЕД блоком `async with DatabaseSession()`, удаляются — эта проверка
теперь внутри `_pull_stage_update`. Убедиться, что `text` остаётся импортированным в файле, даже
если это был единственный вызов `text(...)` в файле — на момент этой правки использование `text`
для сырого SQL в файле больше не остаётся нигде; если линтер/`pyflakes` пожалуется на неиспользуемый
импорт `text` из `sqlalchemy`, убрать его из строки импорта тоже можно, но это не обязательно для
корректности.)

- [ ] **Step 3: Run characterization tests to confirm the bug is fixed and nothing else regressed**

Run: `pytest tests/unit/test_bitrix_sync_characterization.py tests/unit/test_bitrix_sync_events.py -v`
Expected: PASS, **including** `test_pull_updates_stage_for_mapped_stage_and_ignores_unmapped` and
`test_pull_does_not_rollback_further_along_card` from Task 3 — these were failing/vacuous against
the pre-refactor code specifically because of the bug this step fixes (see the ruling above the
Files section). If Task 3's implementer updated either test's assertions to characterize the
pre-fix buggy behavior (e.g. asserting `errors == 1`/`updated == 0`), revert that test back to
asserting the originally-intended behavior (`updated == 1`, or the card not rolled back) as part of
this step — the whole point of this fix is to make that original intent true again.

- [ ] **Step 4: Commit**

```bash
git add cabinet/bitrix_sync.py tests/unit/test_bitrix_sync_characterization.py
git commit -m "refactor(bitrix): extract anti-rollback stage mapping + fix non-portable card lookup"
```

---

### Task 6: Диффер полей (+ прямые unit-тесты на поиск карточки из Task 5)

**Note (post-Task-3-ruling):** `_find_card_by_bitrix_deal_id` is now implemented in Task 5
(moved there to fix a Postgres-only raw-SQL bug that Task 3's characterization run surfaced —
see the ruling at the top of Task 5). It already exists in `cabinet/bitrix_sync.py` by the time
this task runs. This task still adds direct unit tests for it below (using this file's nicer
`card_factory` fixture, which didn't exist yet when Task 5 ran) alongside the new diff logic —
that is intentional extra coverage, not a redo of Task 5's work.

**Files:**
- Modify: `cabinet/bitrix_sync.py`
- Test: `tests/unit/test_bitrix_sync_events.py` (дополнить)

**Interfaces:**
- Consumes: `_find_card_by_bitrix_deal_id` (already produced by Task 5).
- Produces: `_diff_and_log_field_changes(session, card: PipelineCard, deal: dict, owner_user_id: int) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# добавить в tests/unit/test_bitrix_sync_events.py — дополнить импорты в начале
# файла (после "from database import Base, SniperUser, Company") строкой:
from database import PipelineCard, PipelineCardHistory
from sqlalchemy import select


@pytest_asyncio.fixture
async def card_factory(db):
    company_id = db
    async def _make(bitrix_deal_id='700', **overrides):
        async with FakeDatabaseSession.factory() as s:
            company = await s.get(Company, company_id)
            data = {'bitrix_deal_id': bitrix_deal_id, 'bitrix_snapshot': {
                'TITLE': 'Старое имя', 'OPPORTUNITY': '100000',
                'UF_CRM_TENDER_CUSTOMER': 'Заказчик А', 'UF_CRM_TENDER_REGION': 'Москва',
                'CLOSEDATE': '2026-09-01', 'ASSIGNED_BY_ID': 1, 'STAGE_ID': 'NEW',
            }}
            data.update(overrides.pop('data', {}))
            card = PipelineCard(
                company_id=company_id, tender_number=overrides.pop('tender_number', 't-diff'),
                stage=overrides.pop('stage', 'FOUND'),
                assignee_user_id=company.owner_user_id, created_by=company.owner_user_id,
                data=data,
            )
            s.add(card)
            await s.commit()
            return card.id
    return _make


@pytest.mark.asyncio
async def test_find_card_by_bitrix_deal_id_matches(db, card_factory):
    company_id = db
    card_id = await card_factory(bitrix_deal_id='777')
    async with FakeDatabaseSession.factory() as s:
        found = await bitrix_sync._find_card_by_bitrix_deal_id(s, company_id, '777')
        assert found is not None
        assert found.id == card_id


@pytest.mark.asyncio
async def test_find_card_by_bitrix_deal_id_no_match_returns_none(db, card_factory):
    company_id = db
    await card_factory(bitrix_deal_id='777')
    async with FakeDatabaseSession.factory() as s:
        found = await bitrix_sync._find_card_by_bitrix_deal_id(s, company_id, '999')
        assert found is None


@pytest.mark.asyncio
async def test_diff_logs_one_history_entry_per_changed_field(db, card_factory):
    company_id = db
    card_id = await card_factory()
    new_deal = {
        'ID': '700', 'TITLE': 'Новое имя', 'OPPORTUNITY': '250000',
        'UF_CRM_TENDER_CUSTOMER': 'Заказчик А',  # не менялось
        'UF_CRM_TENDER_REGION': 'Санкт-Петербург', 'CLOSEDATE': '2026-09-01',
        'ASSIGNED_BY_ID': 1, 'STAGE_ID': 'NEW',
    }
    async with FakeDatabaseSession.factory() as s:
        card = await s.get(PipelineCard, card_id)
        await bitrix_sync._diff_and_log_field_changes(s, card, new_deal, owner_user_id=1)
        await s.commit()

    async with FakeDatabaseSession.factory() as s:
        rows = (await s.execute(
            select(PipelineCardHistory).where(PipelineCardHistory.card_id == card_id)
        )).scalars().all()
        changed_fields = {h.payload['field'] for h in rows if h.action == 'bitrix_field_changed'}
        assert changed_fields == {'TITLE', 'OPPORTUNITY', 'UF_CRM_TENDER_REGION'}
        card = await s.get(PipelineCard, card_id)
        assert card.data['bitrix_snapshot']['TITLE'] == 'Новое имя'


@pytest.mark.asyncio
async def test_diff_skips_field_never_seen_before(db, card_factory):
    company_id = db
    card_id = await card_factory(data={'bitrix_snapshot': {}})  # пустой снимок
    new_deal = {'ID': '700', 'TITLE': 'Имя', 'STAGE_ID': 'NEW'}
    async with FakeDatabaseSession.factory() as s:
        card = await s.get(PipelineCard, card_id)
        await bitrix_sync._diff_and_log_field_changes(s, card, new_deal, owner_user_id=1)
        await s.commit()

    async with FakeDatabaseSession.factory() as s:
        rows = (await s.execute(
            select(PipelineCardHistory).where(PipelineCardHistory.card_id == card_id)
        )).scalars().all()
        assert [h for h in rows if h.action == 'bitrix_field_changed'] == []
```

- [ ] **Step 2: Run tests to verify they fail (partially)**

Run: `pytest tests/unit/test_bitrix_sync_events.py -v -k "diff or find_card"`
Expected: the two `find_card` tests PASS immediately (`_find_card_by_bitrix_deal_id` already
exists from Task 5) — that is correct and expected, not a problem. The two `diff` tests FAIL with
`AttributeError` on `_diff_and_log_field_changes` (not yet implemented). If all four tests fail,
Task 5 did not land correctly — stop and report NEEDS_CONTEXT rather than reimplementing
`_find_card_by_bitrix_deal_id` yourself.

- [ ] **Step 3: Implement**

`_find_card_by_bitrix_deal_id` already exists in `cabinet/bitrix_sync.py` (added by Task 5,
right after `_pull_stage_update`) — do not redefine it. Only add `_diff_and_log_field_changes`:

```python
# cabinet/bitrix_sync.py — добавить рядом с _create_card_from_deal (после него):


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_bitrix_sync_events.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cabinet/bitrix_sync.py tests/unit/test_bitrix_sync_events.py
git commit -m "feat(bitrix): field-level diff against last known deal snapshot"
```

---

### Task 7: Синк ответственного (ASSIGNED_BY_ID → sniper_users, с фолбэком на имя)

**Files:**
- Modify: `cabinet/bitrix_sync.py`
- Test: `tests/unit/test_bitrix_sync_events.py` (дополнить)

**Interfaces:**
- Produces: `_sync_assignee_from_deal(session, company_id: int, card: PipelineCard, deal: dict, owner_user_id: int) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# добавить в tests/unit/test_bitrix_sync_events.py

@pytest.mark.asyncio
async def test_sync_assignee_maps_known_bitrix_user(db, card_factory):
    company_id = db
    async with FakeDatabaseSession.factory() as s:
        from database import CompanyMember
        member = SniperUser(telegram_id=333, data={'bitrix_user_id': 42})
        s.add(member); await s.flush()
        s.add(CompanyMember(company_id=company_id, user_id=member.id, role='member'))
        await s.commit()
        member_id = member.id

    card_id = await card_factory(data={'bitrix_snapshot': {'ASSIGNED_BY_ID': 1}})
    new_deal = {'ID': '700', 'ASSIGNED_BY_ID': 42}

    async with FakeDatabaseSession.factory() as s:
        card = await s.get(PipelineCard, card_id)
        await bitrix_sync._sync_assignee_from_deal(s, company_id, card, new_deal, owner_user_id=1)
        await s.commit()

    async with FakeDatabaseSession.factory() as s:
        card = await s.get(PipelineCard, card_id)
        assert card.assignee_user_id == member_id
        rows = (await s.execute(
            select(PipelineCardHistory).where(PipelineCardHistory.card_id == card_id)
        )).scalars().all()
        assert any(h.action == 'bitrix_field_changed' and h.payload['field'] == 'ASSIGNED_BY_ID' for h in rows)


@pytest.mark.asyncio
async def test_sync_assignee_unknown_bitrix_user_keeps_assignee_but_stores_name(db, card_factory):
    company_id = db
    card_id = await card_factory(data={'bitrix_snapshot': {'ASSIGNED_BY_ID': 1}})
    new_deal = {'ID': '700', 'ASSIGNED_BY_ID': 999, 'ASSIGNED_BY_NAME': 'Партнёр'}

    async with FakeDatabaseSession.factory() as s:
        card_before = await s.get(PipelineCard, card_id)
        original_assignee = card_before.assignee_user_id
        await bitrix_sync._sync_assignee_from_deal(s, company_id, card_before, new_deal, owner_user_id=1)
        await s.commit()

    async with FakeDatabaseSession.factory() as s:
        card = await s.get(PipelineCard, card_id)
        assert card.assignee_user_id == original_assignee  # не тронуто
        assert card.data.get('bitrix_assigned_name') == 'Партнёр'


@pytest.mark.asyncio
async def test_sync_assignee_no_change_is_noop(db, card_factory):
    card_id = await card_factory(data={'bitrix_snapshot': {'ASSIGNED_BY_ID': 1}})
    new_deal = {'ID': '700', 'ASSIGNED_BY_ID': 1}
    async with FakeDatabaseSession.factory() as s:
        card = await s.get(PipelineCard, card_id)
        await bitrix_sync._sync_assignee_from_deal(s, 1, card, new_deal, owner_user_id=1)
        await s.commit()
    async with FakeDatabaseSession.factory() as s:
        rows = (await s.execute(
            select(PipelineCardHistory).where(PipelineCardHistory.card_id == card_id)
        )).scalars().all()
        assert [h for h in rows if h.action == 'bitrix_field_changed' and h.payload.get('field') == 'ASSIGNED_BY_ID'] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_bitrix_sync_events.py -v -k assignee`
Expected: FAIL с `AttributeError: ... _sync_assignee_from_deal`

- [ ] **Step 3: Implement**

```python
# cabinet/bitrix_sync.py — добавить рядом с _diff_and_log_field_changes:

async def _sync_assignee_from_deal(
    session, company_id: int, card: PipelineCard, deal: Dict[str, Any], owner_user_id: int,
) -> None:
    """Отражает смену ответственного в Bitrix. Если новый ASSIGNED_BY_ID
    сопоставляется с sniper_users этой компании (по data['bitrix_user_id']) —
    обновляет assignee_user_id. Если нет — оставляет assignee_user_id как есть
    (FK-целостность) и только сохраняет отображаемое имя для UI.
    """
    from database import CompanyMember

    new_assigned = deal.get('ASSIGNED_BY_ID')
    data = dict(card.data or {})
    old_snapshot = data.get('bitrix_snapshot') or {}
    old_assigned = old_snapshot.get('ASSIGNED_BY_ID')

    if 'ASSIGNED_BY_ID' not in old_snapshot or old_assigned == new_assigned:
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
        if user and (user.data or {}).get('bitrix_user_id') == new_assigned:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_bitrix_sync_events.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cabinet/bitrix_sync.py tests/unit/test_bitrix_sync_events.py
git commit -m "feat(bitrix): sync assignee changes from Bitrix with safe fallback"
```

---

### Task 8: Диспетчер событий `handle_deal_event`

**Files:**
- Modify: `cabinet/bitrix_sync.py`
- Test: `tests/unit/test_bitrix_sync_events.py` (дополнить)

**Interfaces:**
- Consumes: `get_bitrix24_deal` (Task 1), `get_or_create_inbound_secret`/`verify_inbound_secret` (Task 2), `_create_card_from_deal` (Task 4), `_pull_stage_update`/`_find_card_by_bitrix_deal_id` (Task 5), `_diff_and_log_field_changes` (Task 6), `_sync_assignee_from_deal` (Task 7).
- Produces: `handle_deal_event(company_id: int, event: str, deal_id: str) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# добавить в tests/unit/test_bitrix_sync_events.py

@pytest.mark.asyncio
async def test_handle_deal_event_add_creates_card(db, monkeypatch):
    company_id = db
    async with FakeDatabaseSession.factory() as s:
        company = await s.get(Company, company_id)
        owner = await s.get(SniperUser, company.owner_user_id)
        data = dict(owner.data or {})
        data['bitrix24_webhook_url'] = 'https://x.bitrix24.ru/rest/1/tok/'
        data['bitrix24_enabled'] = True
        owner.data = data
        flag_modified(owner, 'data')
        await s.commit()

    async def fake_get_deal(webhook, deal_id):
        return {'ID': deal_id, 'STAGE_ID': 'NEW', 'TITLE': 'Новая сделка', 'COMMENTS': 'Тендер 9999999999999999999'}
    monkeypatch.setattr('bot.handlers.bitrix24.get_bitrix24_deal', fake_get_deal)

    await bitrix_sync.handle_deal_event(company_id, 'ONCRMDEALADD', '111')

    async with FakeDatabaseSession.factory() as s:
        card = (await s.execute(
            select(PipelineCard).where(PipelineCard.company_id == company_id)
        )).scalar_one()
        assert card.data['bitrix_deal_id'] == '111'
        history = (await s.execute(
            select(PipelineCardHistory).where(PipelineCardHistory.card_id == card.id)
        )).scalars().all()
        assert any(h.action == 'card_created_from_bitrix' for h in history)


@pytest.mark.asyncio
async def test_handle_deal_event_update_diffs_existing_card(db, card_factory, monkeypatch):
    company_id = db
    async with FakeDatabaseSession.factory() as s:
        company = await s.get(Company, company_id)
        owner = await s.get(SniperUser, company.owner_user_id)
        data = dict(owner.data or {})
        data['bitrix24_webhook_url'] = 'https://x.bitrix24.ru/rest/1/tok/'
        data['bitrix24_enabled'] = True
        owner.data = data
        flag_modified(owner, 'data')
        await s.commit()

    card_id = await card_factory(bitrix_deal_id='222')

    async def fake_get_deal(webhook, deal_id):
        return {
            'ID': '222', 'STAGE_ID': 'NEW', 'TITLE': 'Обновлённое имя',
            'OPPORTUNITY': '999999', 'UF_CRM_TENDER_CUSTOMER': 'Заказчик А',
            'UF_CRM_TENDER_REGION': 'Москва', 'CLOSEDATE': '2026-09-01', 'ASSIGNED_BY_ID': 1,
        }
    monkeypatch.setattr('bot.handlers.bitrix24.get_bitrix24_deal', fake_get_deal)

    await bitrix_sync.handle_deal_event(company_id, 'ONCRMDEALUPDATE', '222')

    async with FakeDatabaseSession.factory() as s:
        history = (await s.execute(
            select(PipelineCardHistory).where(PipelineCardHistory.card_id == card_id)
        )).scalars().all()
        changed = {h.payload['field'] for h in history if h.action == 'bitrix_field_changed'}
        assert 'TITLE' in changed and 'OPPORTUNITY' in changed


@pytest.mark.asyncio
async def test_handle_deal_event_delete_archives_card(db, card_factory, monkeypatch):
    company_id = db
    async with FakeDatabaseSession.factory() as s:
        company = await s.get(Company, company_id)
        owner = await s.get(SniperUser, company.owner_user_id)
        data = dict(owner.data or {})
        data['bitrix24_webhook_url'] = 'https://x.bitrix24.ru/rest/1/tok/'
        data['bitrix24_enabled'] = True
        owner.data = data
        flag_modified(owner, 'data')
        await s.commit()

    card_id = await card_factory(bitrix_deal_id='333')

    await bitrix_sync.handle_deal_event(company_id, 'ONCRMDEALDELETE', '333')

    async with FakeDatabaseSession.factory() as s:
        card = await s.get(PipelineCard, card_id)
        assert card.archived_at is not None
        history = (await s.execute(
            select(PipelineCardHistory).where(PipelineCardHistory.card_id == card_id)
        )).scalars().all()
        assert any(h.action == 'bitrix_deal_deleted' for h in history)


@pytest.mark.asyncio
async def test_handle_deal_event_no_webhook_configured_is_noop(db):
    company_id = db  # владелец без bitrix24_webhook_url
    await bitrix_sync.handle_deal_event(company_id, 'ONCRMDEALUPDATE', '444')
    async with FakeDatabaseSession.factory() as s:
        count = (await s.execute(select(PipelineCard))).scalars().all()
        assert count == []


@pytest.mark.asyncio
async def test_handle_deal_event_unknown_event_is_noop(db, monkeypatch):
    company_id = db
    called = {'n': 0}
    async def fake_get_deal(webhook, deal_id):
        called['n'] += 1
        return {}
    monkeypatch.setattr('bot.handlers.bitrix24.get_bitrix24_deal', fake_get_deal)
    await bitrix_sync.handle_deal_event(company_id, 'ONSOMETHINGELSE', '555')
    assert called['n'] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_bitrix_sync_events.py -v -k handle_deal_event`
Expected: FAIL с `AttributeError: ... handle_deal_event`

- [ ] **Step 3: Implement**

```python
# cabinet/bitrix_sync.py — добавить в самый конец файла (после pull_changes_for_all_companies):


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

            await _diff_and_log_field_changes(session, card, deal, owner_user_id)
            await _sync_assignee_from_deal(session, company_id, card, deal, owner_user_id)
            card.updated_at = datetime.utcnow()
            await session.commit()
        logger.info(f'[bitrix-event] company={company_id} event={event} deal={deal_id} processed')
    except Exception as e:
        logger.error(f'[bitrix-event] error company={company_id} event={event} deal={deal_id}: {e}', exc_info=True)
```

Добавить `from datetime import datetime` к импортам в начале файла (сейчас там `from decimal import Decimal` и т.д., но не `datetime` — только локальный `from datetime import datetime as _dt` внутри `pull_changes_from_bitrix`; новый top-level импорт не конфликтует с этим локальным алиасом).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_bitrix_sync_events.py tests/unit/test_bitrix_sync_characterization.py -v`
Expected: PASS (все тесты, включая написанные в Task 2/3/6/7)

- [ ] **Step 5: Commit**

```bash
git add cabinet/bitrix_sync.py tests/unit/test_bitrix_sync_events.py
git commit -m "feat(bitrix): dispatch inbound deal events to create/update/delete flows"
```

---

### Task 9: Инбаунд-эндпоинт `POST /webhook/bitrix24/events`

**Files:**
- Modify: `bot/health_check.py` (добавить хендлер после `bitrix24_analyze_handler`, зарегистрировать роут рядом с существующим)
- Test: `tests/unit/test_bitrix_events_endpoint.py`

**Interfaces:**
- Consumes: `cabinet.bitrix_sync.verify_inbound_secret`, `cabinet.bitrix_sync.handle_deal_event`.
- Produces: aiohttp-хендлер `bitrix24_events_handler(request) -> web.Response`.

Важный нюанс формата: Bitrix шлёт `application/x-www-form-urlencoded` с PHP-стиль ключами `event`, `data[FIELDS][ID]`, `auth[application_token]` — aiohttp НЕ разбирает квадратные скобки в дерево, эти ключи приходят как литеральные строки в `request.post()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_bitrix_events_endpoint.py
import sys
import asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from aiohttp import streams
from aiohttp.base_protocol import BaseProtocol
from aiohttp.test_utils import make_mocked_request

import bot.health_check as health_check


def _form_request(path: str, body: bytes):
    """make_mocked_request не принимает голые bytes как payload для
    await request.post() — нужен настоящий StreamReader с фидом данных."""
    loop = asyncio.get_event_loop()
    protocol = BaseProtocol(loop=loop)
    stream = streams.StreamReader(protocol, limit=2**16, loop=loop)
    stream.feed_data(body)
    stream.feed_eof()
    return make_mocked_request(
        'POST', path,
        headers={'Content-Type': 'application/x-www-form-urlencoded',
                 'Content-Length': str(len(body))},
        payload=stream,
    )


@pytest.mark.asyncio
async def test_events_handler_rejects_missing_secret(monkeypatch):
    async def fake_verify(company_id, secret):
        return False
    monkeypatch.setattr('cabinet.bitrix_sync.verify_inbound_secret', fake_verify)

    request = _form_request('/webhook/bitrix24/events?c=1&t=wrong', b'')
    resp = await health_check.bitrix24_events_handler(request)
    assert resp.status == 401


@pytest.mark.asyncio
async def test_events_handler_missing_company_param_is_400():
    request = _form_request('/webhook/bitrix24/events?t=abc', b'')
    resp = await health_check.bitrix24_events_handler(request)
    assert resp.status == 400


@pytest.mark.asyncio
async def test_events_handler_dispatches_background_task(monkeypatch):
    async def fake_verify(company_id, secret):
        return True
    monkeypatch.setattr('cabinet.bitrix_sync.verify_inbound_secret', fake_verify)

    dispatched = []
    async def fake_handle_deal_event(company_id, event, deal_id):
        dispatched.append((company_id, event, deal_id))
    monkeypatch.setattr('cabinet.bitrix_sync.handle_deal_event', fake_handle_deal_event)

    body = b'event=ONCRMDEALUPDATE&data%5BFIELDS%5D%5BID%5D=42&auth%5Bdomain%5D=x.bitrix24.ru'
    request = _form_request('/webhook/bitrix24/events?c=1&t=good', body)
    resp = await health_check.bitrix24_events_handler(request)
    assert resp.status == 200
    # фоновая задача — даём event loop'у шанс её выполнить
    await asyncio.sleep(0)
    assert dispatched == [(1, 'ONCRMDEALUPDATE', '42')]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_bitrix_events_endpoint.py -v`
Expected: FAIL с `AttributeError: module 'bot.health_check' has no attribute 'bitrix24_events_handler'`

- [ ] **Step 3: Implement the handler and register the route**

```python
# bot/health_check.py — добавить после bitrix24_analyze_handler (после строки ~493):


async def bitrix24_events_handler(request):
    """
    POST /webhook/bitrix24/events?c=<company_id>&t=<secret>

    Приёмник «исходящего вебхука» Bitrix24 (Разработчикам → Другое →
    Исходящий вебхук) на события OnCrmDealAdd/Update/Delete/MoveToCategory.

    Тело — application/x-www-form-urlencoded с PHP-стиль ключами:
    event=ONCRMDEALUPDATE&data[FIELDS][ID]=42&auth[domain]=...
    aiohttp не разворачивает квадратные скобки — читаем как литеральные ключи.
    """
    company_id_raw = request.query.get('c', '')
    secret = request.query.get('t', '')
    if not company_id_raw:
        return web.json_response({'error': 'company id required'}, status=400)
    try:
        company_id = int(company_id_raw)
    except ValueError:
        return web.json_response({'error': 'invalid company id'}, status=400)

    from cabinet.bitrix_sync import verify_inbound_secret
    if not await verify_inbound_secret(company_id, secret):
        return web.json_response({'error': 'Unauthorized'}, status=401)

    try:
        form = await request.post()
    except Exception:
        form = {}

    event = str(form.get('event') or '').strip()
    deal_id = str(form.get('data[FIELDS][ID]') or '').strip()

    if not event or not deal_id:
        return web.json_response({'error': 'event and deal id required'}, status=400)

    from cabinet.bitrix_sync import handle_deal_event
    asyncio.create_task(handle_deal_event(company_id, event, deal_id))
    return web.json_response({'ok': True})
```

```python
# bot/health_check.py — в start_health_check_server, рядом с существующей строкой
# "app.router.add_post('/webhook/bitrix24/analyze', bitrix24_analyze_handler)"
# (строка ~514) добавить сразу после:

    app.router.add_post('/webhook/bitrix24/events', bitrix24_events_handler)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_bitrix_events_endpoint.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add bot/health_check.py tests/unit/test_bitrix_events_endpoint.py
git commit -m "feat(bitrix): add inbound webhook endpoint for real-time deal events"
```

---

### Task 10: Синк комментариев (фоновый job) + снижение частоты старого поллинга

**Files:**
- Modify: `cabinet/bitrix_sync.py` (добавить `sync_comments_for_company`)
- Create: `tender_sniper/jobs/bitrix_comment_sync.py`
- Modify: `tender_sniper/jobs/bitrix_pull_sync.py` (интервал 300 → 3600)
- Modify: `bot/main.py` (запустить новый job)
- Test: `tests/unit/test_bitrix_comment_sync.py`

**Interfaces:**
- Consumes: `batch_list_deal_comments` (Task 1).
- Produces: `sync_comments_for_company(company_id: int) -> Dict[str, int]` (возвращает `{'checked': N, 'added': N}`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_bitrix_comment_sync.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm.attributes import flag_modified

import cabinet.bitrix_sync as bitrix_sync
from database import Base, SniperUser, Company, PipelineCard, PipelineCardHistory


class FakeDatabaseSession:
    factory = None

    async def __aenter__(self):
        self.session = FakeDatabaseSession.factory()
        return self.session

    async def __aexit__(self, et, ev, tb):
        if et is not None:
            await self.session.rollback()
        else:
            await self.session.commit()
        await self.session.close()


@pytest_asyncio.fixture
async def setup(monkeypatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/comment_sync_test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    FakeDatabaseSession.factory = factory
    monkeypatch.setattr(bitrix_sync, "DatabaseSession", FakeDatabaseSession)

    async with factory() as s:
        owner = SniperUser(telegram_id=444, data={
            'bitrix24_webhook_url': 'https://x.bitrix24.ru/rest/1/tok/',
            'bitrix24_enabled': True,
        })
        s.add(owner); await s.flush()
        company = Company(name="Team", owner_user_id=owner.id)
        s.add(company); await s.flush()
        card = PipelineCard(
            company_id=company.id, tender_number='t-comments', stage='FOUND',
            assignee_user_id=owner.id, created_by=owner.id,
            data={'bitrix_deal_id': '55', 'bitrix_last_comment_id': 0},
        )
        s.add(card); await s.flush()
        await s.commit()
        company_id, card_id, owner_id = company.id, card.id, owner.id

    yield company_id, card_id, owner_id, factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_sync_comments_appends_history_and_advances_cursor(setup, monkeypatch):
    company_id, card_id, owner_id, factory = setup

    async def fake_batch(webhook, deal_since):
        assert deal_since == {'55': 0}
        return {'55': [
            {'ID': '10', 'AUTHOR_ID': '7', 'COMMENT': 'Заказчик перезвонил', 'CREATED': '2026-08-27T10:00:00'},
            {'ID': '11', 'AUTHOR_ID': '7', 'COMMENT': 'Отправили КП', 'CREATED': '2026-08-27T11:00:00'},
        ]}
    monkeypatch.setattr('bot.handlers.bitrix24.batch_list_deal_comments', fake_batch)

    result = await bitrix_sync.sync_comments_for_company(company_id)
    assert result == {'checked': 1, 'added': 2}

    async with factory() as s:
        card = await s.get(PipelineCard, card_id)
        assert card.data['bitrix_last_comment_id'] == 11
        history = (await s.execute(
            select(PipelineCardHistory).where(PipelineCardHistory.card_id == card_id)
        )).scalars().all()
        comment_rows = [h for h in history if h.action == 'bitrix_comment']
        assert len(comment_rows) == 2
        assert comment_rows[0].payload['text'] == 'Заказчик перезвонил'


@pytest.mark.asyncio
async def test_sync_comments_no_new_comments_no_history(setup, monkeypatch):
    company_id, card_id, owner_id, factory = setup

    async def fake_batch(webhook, deal_since):
        return {'55': []}
    monkeypatch.setattr('bot.handlers.bitrix24.batch_list_deal_comments', fake_batch)

    result = await bitrix_sync.sync_comments_for_company(company_id)
    assert result == {'checked': 1, 'added': 0}


@pytest.mark.asyncio
async def test_sync_comments_skips_archived_cards(setup, monkeypatch):
    company_id, card_id, owner_id, factory = setup
    async with factory() as s:
        card = await s.get(PipelineCard, card_id)
        from datetime import datetime
        card.archived_at = datetime.utcnow()
        await s.commit()

    called = {'n': 0}
    async def fake_batch(webhook, deal_since):
        called['n'] += 1
        return {}
    monkeypatch.setattr('bot.handlers.bitrix24.batch_list_deal_comments', fake_batch)

    result = await bitrix_sync.sync_comments_for_company(company_id)
    assert result == {'checked': 0, 'added': 0}
    assert called['n'] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_bitrix_comment_sync.py -v`
Expected: FAIL с `AttributeError: ... sync_comments_for_company`

- [ ] **Step 3: Implement `sync_comments_for_company`**

```python
# cabinet/bitrix_sync.py — добавить в конец файла (после handle_deal_event):


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
            if not new_comments:
                continue
            card = await session.get(PipelineCard, card_stub.id)
            data = dict(card.data or {})
            max_id = int(data.get('bitrix_last_comment_id') or 0)
            for comment in new_comments:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_bitrix_comment_sync.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Create the background job file**

```python
# tender_sniper/jobs/bitrix_comment_sync.py
"""Background job: каждые ~90 сек подтягивает новые комментарии ленты
Bitrix24 для карточек pipeline у компаний с настроенным webhook.

У Bitrix нет push-события на добавление комментария (в отличие от
create/update/delete сделки, см. bot/health_check.py::bitrix24_events_handler) —
поэтому единственный способ узнать про новые комментарии — периодически
спрашивать. Запускается из bot/main.py.
"""
import asyncio
import logging

from sqlalchemy import select

from cabinet import bitrix_sync
from database import DatabaseSession, Company

logger = logging.getLogger(__name__)

SYNC_INTERVAL_SECONDS = 90
START_DELAY_SECONDS = 100  # чуть позже bitrix_pull_loop, чтобы не толкаться при старте


async def comment_sync_loop():
    await asyncio.sleep(START_DELAY_SECONDS)
    while True:
        try:
            async with DatabaseSession() as session:
                rows = await session.execute(select(Company))
                companies = list(rows.scalars().all())
            for company in companies:
                webhook = await bitrix_sync._get_company_webhook(company.id)
                if not webhook:
                    continue
                try:
                    result = await bitrix_sync.sync_comments_for_company(company.id)
                    if result.get('added'):
                        logger.info(
                            f'[bitrix-comment-sync] company={company.id} '
                            f'checked={result["checked"]} added={result["added"]}'
                        )
                except Exception as e:
                    logger.error(f'[bitrix-comment-sync] company={company.id}: {e}', exc_info=True)
        except Exception as e:
            logger.error(f'[bitrix-comment-sync] loop error: {e}', exc_info=True)
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
```

- [ ] **Step 6: Lower the old full-poll interval to 1 hour**

```python
# tender_sniper/jobs/bitrix_pull_sync.py — заменить строку:
# PULL_INTERVAL_SECONDS = 300  # 5 минут
# на:
PULL_INTERVAL_SECONDS = 3600  # 1 час — теперь это сетка безопасности,
# основной путь — событийный (bot/health_check.py::bitrix24_events_handler)
# + отдельный поллинг комментариев (tender_sniper/jobs/bitrix_comment_sync.py)
```

- [ ] **Step 7: Wire the new job into bot/main.py**

```python
# bot/main.py — сразу после существующих строк (~268-269):
#   from tender_sniper.jobs.bitrix_pull_sync import pull_loop as bitrix_pull_loop
#   asyncio.create_task(bitrix_pull_loop())
# добавить:

    from tender_sniper.jobs.bitrix_comment_sync import comment_sync_loop
    asyncio.create_task(comment_sync_loop())
```

- [ ] **Step 8: Run the full bitrix test suite to confirm nothing regressed**

Run: `pytest tests/unit/test_bitrix_sync_characterization.py tests/unit/test_bitrix_sync_events.py tests/unit/test_bitrix_comment_sync.py tests/unit/test_bitrix_events_endpoint.py tests/unit/test_bitrix24_rest_helpers.py -v`
Expected: PASS (все тесты подпроекта зелёные)

- [ ] **Step 9: Commit**

```bash
git add cabinet/bitrix_sync.py tender_sniper/jobs/bitrix_comment_sync.py tender_sniper/jobs/bitrix_pull_sync.py bot/main.py tests/unit/test_bitrix_comment_sync.py
git commit -m "feat(bitrix): poll deal comments every 90s, demote full poll to hourly safety net"
```

---

### Task 11: Settings API для инбаунд-URL (получить/сгенерировать/ротировать)

**Files:**
- Modify: `cabinet/api.py` (добавить хендлеры рядом с `save_bitrix24_settings`/`test_bitrix24_settings`, после строки ~1017)
- Modify: `cabinet/routes.py` (зарегистрировать роуты рядом с существующими bitrix24-роутами)
- Test: `tests/unit/test_bitrix_inbound_settings_api.py`

**Interfaces:**
- Consumes: `cabinet.bitrix_sync.get_or_create_inbound_secret`, `cabinet.bitrix_sync.rotate_inbound_secret`, `cabinet.auth.require_owner`, `bot.config.BotConfig.WEBAPP_BASE_URL`.
- Produces: `get_bitrix_inbound_settings(request) -> web.Response`, `rotate_bitrix_inbound_secret(request) -> web.Response`.

- [ ] **Step 1: Write the failing tests**

Реализация (Step 3) намеренно выносит тело каждого хендлера в приватную
`_get_bitrix_inbound_settings_impl(request)` / `_rotate_bitrix_inbound_secret_impl(request)`,
а публичные `get_bitrix_inbound_settings`/`rotate_bitrix_inbound_secret` — это
`@require_owner`-обёртки поверх них (декоратор не сохраняет `__wrapped__`, так что
тестировать логику в обход реального auth-флоу проще всего, вызывая `_impl`
напрямую — так же как остальной код в этом файле тестируется через прямой вызов
хендлера с руками выставленным `request['company']`).

```python
# tests/unit/test_bitrix_inbound_settings_api.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from aiohttp.test_utils import make_mocked_request

import cabinet.api as api


@pytest.mark.asyncio
async def test_get_bitrix_inbound_settings_returns_url_with_secret(monkeypatch):
    async def fake_secret(company_id):
        return 'abc123'
    monkeypatch.setattr('cabinet.bitrix_sync.get_or_create_inbound_secret', fake_secret)
    monkeypatch.setattr('bot.config.BotConfig.WEBAPP_BASE_URL', 'https://example.app')

    request = make_mocked_request('GET', '/cabinet/api/settings/bitrix-inbound')
    request['company'] = {'id': 5, 'owner_user_id': 1}

    resp = await api._get_bitrix_inbound_settings_impl(request)
    assert resp.status == 200


@pytest.mark.asyncio
async def test_rotate_bitrix_inbound_secret_returns_new_url(monkeypatch):
    async def fake_rotate(company_id):
        return 'new-secret'
    monkeypatch.setattr('cabinet.bitrix_sync.rotate_inbound_secret', fake_rotate)
    monkeypatch.setattr('bot.config.BotConfig.WEBAPP_BASE_URL', 'https://example.app')

    request = make_mocked_request('POST', '/cabinet/api/settings/bitrix-inbound/rotate')
    request['company'] = {'id': 5, 'owner_user_id': 1}

    resp = await api._rotate_bitrix_inbound_secret_impl(request)
    assert resp.status == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_bitrix_inbound_settings_api.py -v`
Expected: FAIL с `AttributeError: module 'cabinet.api' has no attribute '_get_bitrix_inbound_settings_impl'`

- [ ] **Step 3: Implement**

```python
# cabinet/api.py — добавить после test_bitrix24_settings (после строки ~1017),
# перед export_tender_to_bitrix24:


async def _get_bitrix_inbound_settings_impl(request: web.Request) -> web.Response:
    company = request['company']
    from cabinet.bitrix_sync import get_or_create_inbound_secret
    from bot.config import BotConfig
    secret = await get_or_create_inbound_secret(company['id'])
    if not secret:
        return web.json_response({'error': 'company not found'}, status=404)
    url = f"{BotConfig.WEBAPP_BASE_URL}/webhook/bitrix24/events?c={company['id']}&t={secret}"
    return web.json_response({'ok': True, 'url': url})


@require_owner
async def get_bitrix_inbound_settings(request: web.Request) -> web.Response:
    """GET /cabinet/api/settings/bitrix-inbound — URL для исходящего вебхука Bitrix.
    Генерирует секрет при первом обращении.
    """
    return await _get_bitrix_inbound_settings_impl(request)


async def _rotate_bitrix_inbound_secret_impl(request: web.Request) -> web.Response:
    company = request['company']
    from cabinet.bitrix_sync import rotate_inbound_secret
    from bot.config import BotConfig
    secret = await rotate_inbound_secret(company['id'])
    if not secret:
        return web.json_response({'error': 'company not found'}, status=404)
    url = f"{BotConfig.WEBAPP_BASE_URL}/webhook/bitrix24/events?c={company['id']}&t={secret}"
    return web.json_response({'ok': True, 'url': url})


@require_owner
async def rotate_bitrix_inbound_secret(request: web.Request) -> web.Response:
    """POST /cabinet/api/settings/bitrix-inbound/rotate — перегенерировать секрет."""
    return await _rotate_bitrix_inbound_secret_impl(request)
```

```python
# cabinet/routes.py — добавить рядом с существующими bitrix24-роутами
# (после строки "app.router.add_post('/cabinet/api/settings/bitrix24/test', ...)"):

    app.router.add_get('/cabinet/api/settings/bitrix-inbound', api.get_bitrix_inbound_settings)
    app.router.add_post('/cabinet/api/settings/bitrix-inbound/rotate', api.rotate_bitrix_inbound_secret)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_bitrix_inbound_settings_api.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add cabinet/api.py cabinet/routes.py tests/unit/test_bitrix_inbound_settings_api.py
git commit -m "feat(bitrix): settings API to fetch/rotate inbound webhook URL"
```

---

### Task 12: UI настроек — блок «Обратный синк из Bitrix»

**Files:**
- Modify: `cabinet/templates/settings.html` (добавить блок в секцию «Интеграции», после существующего `<p class="desc" id="bx-status">`)
- Modify: `cabinet/static/js/pages/settings.js` (загрузка URL + обработчик ротации; поднять версию `?v=8` → `?v=9` в settings.html)

**Interfaces:**
- Consumes: `GET /cabinet/api/settings/bitrix-inbound`, `POST /cabinet/api/settings/bitrix-inbound/rotate` (Task 11).

- [ ] **Step 1: Add the HTML block**

```html
<!-- cabinet/templates/settings.html — вставить перед закрывающим "</div>"
     секции "Интеграции" (сразу после строки
     '<p class="desc" id="bx-status" style="margin-top:10px"></p>'): -->

  <div class="setting-row" style="margin-top:20px;border-top:1px solid var(--line);padding-top:16px;">
    <div style="flex:1">
      <div class="label">Обратный синк из Bitrix (real-time)</div>
      <div class="hint">
        Вставьте этот URL в Bitrix: Разработчикам → Другое → Исходящий вебхук.
        Отметьте события: «Создание сделки», «Изменение сделки», «Удаление сделки», «Смена направления (воронки)».
      </div>
      <input type="text" id="bx-inbound-url" readonly style="width:100%;margin-top:8px;padding:10px 12px;border-radius:var(--radius-sm);background:var(--bg);border:1px solid var(--line);color:var(--text);font-family:var(--font-mono);font-size:12px;">
    </div>
  </div>
  <div class="pause-actions">
    <button class="btn btn-secondary" id="bx-inbound-rotate">Сгенерировать заново</button>
  </div>
```

- [ ] **Step 2: Poll and wire the JS**

```javascript
// cabinet/static/js/pages/settings.js — добавить в конец load(), после блока Bitrix24:

    const bxInboundUrl = byId('bx-inbound-url');
    if (bxInboundUrl) {
      const inbound = await apiGet('/cabinet/api/settings/bitrix-inbound');
      if (inbound && inbound.ok) bxInboundUrl.value = inbound.url;
    }
```

```javascript
// cabinet/static/js/pages/settings.js — добавить рядом с bxTest handler'ом (в конец файла):

  const bxInboundRotate = byId('bx-inbound-rotate');
  if (bxInboundRotate) {
    bxInboundRotate.addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      if (btn.disabled) return;
      const orig = btn.textContent;
      btn.disabled = true;
      btn.textContent = '⏳ Генерируем…';
      try {
        const data = await apiPost('/cabinet/api/settings/bitrix-inbound/rotate', {});
        if (data && data.ok) {
          byId('bx-inbound-url').value = data.url;
          Toast.show('✓ Новый URL сгенерирован — обновите его в Bitrix', 'positive');
        }
      } finally {
        btn.disabled = false;
        btn.textContent = orig;
      }
    });
  }
```

```html
<!-- cabinet/templates/settings.html — поднять версию скрипта, чтобы браузер не отдал закешированный файл: -->
<script src="/cabinet/static/js/pages/settings.js?v=9"></script>
```

- [ ] **Step 3: Manual verification in browser**

Запустить локально dev-сервер кабинета (см. существующий способ запуска бота/кабинета в этом репозитории — обычно `python bot/main.py` с настроенным `.env`/`DATABASE_URL`), зайти под владельцем команды на `/cabinet/settings`, убедиться:
- в блоке «Обратный синк из Bitrix» появился непустой URL вида `https://.../webhook/bitrix24/events?c=<id>&t=<secret>`;
- клик «Сгенерировать заново» меняет значение в поле и показывает тост об успехе;
- обновление страницы показывает уже новый (ротированный) URL, а не старый.

- [ ] **Step 4: Commit**

```bash
git add cabinet/templates/settings.html cabinet/static/js/pages/settings.js
git commit -m "feat(bitrix): settings UI for inbound webhook URL with rotation"
```

---

### Task 13: Отображение новых типов истории в карточке Pipeline

**Files:**
- Modify: `cabinet/static/js/pages/pipeline.js` (расширить `formatHistoryAction`, строки ~640-655)

**Interfaces:**
- Consumes: записи истории с `action` из `{'bitrix_field_changed', 'bitrix_comment', 'bitrix_deal_deleted', 'card_created_from_bitrix', 'bitrix_pull'}` (payload-форматы определены в Tasks 6-10).

- [ ] **Step 1: Extend `formatHistoryAction`**

```javascript
// cabinet/static/js/pages/pipeline.js — заменить функцию formatHistoryAction
// (строки ~640-655) целиком на:

  const _FIELD_LABELS = {
    TITLE: 'Название', OPPORTUNITY: 'Сумма', UF_CRM_TENDER_CUSTOMER: 'Заказчик',
    UF_CRM_TENDER_REGION: 'Регион', CLOSEDATE: 'Срок', ASSIGNED_BY_ID: 'Ответственный',
  };

  function _fmtFieldValue(field, v) {
    if (v === null || v === undefined || v === '') return '—';
    if (field === 'OPPORTUNITY') return fmtPrice(Number(v));
    return String(v);
  }

  function formatHistoryAction(h) {
    const map = {
      'created': 'создал карточку',
      'stage_changed': `перевёл «${h.payload.from || ''}» → «${h.payload.to || ''}»`,
      'assigned': `назначил ответственного (user ${h.payload.to || ''})`,
      'note_added': 'добавил заметку',
      'file_uploaded': `загрузил файл${h.payload.filename ? ' ' + h.payload.filename : ''}`,
      'file_deleted': 'удалил файл',
      'price_set': 'обновил цены',
      'won': 'отметил ПОБЕДУ',
      'lost': 'отметил ПРОИГРЫШ',
      'ai_enriched': 'запустил AI-анализ',
      'checklist_added': 'добавил пункт чек-листа',
      'checklist_done': 'отметил пункт выполненным',
      'imported_from_bitrix': 'импортирован из Bitrix24',
      'related_added': 'связал с другим тендером',
      'bitrix_pull': `Bitrix: стадия → «${h.payload.to_stage || ''}»`,
      'bitrix_field_changed': (() => {
        const label = _FIELD_LABELS[h.payload.field] || h.payload.field;
        return `Bitrix изменил «${label}»: ${_fmtFieldValue(h.payload.field, h.payload.old)} → ${_fmtFieldValue(h.payload.field, h.payload.new)}`;
      })(),
      'bitrix_comment': `💬 Bitrix (${h.payload.author || '?'}): ${h.payload.text || ''}`,
      'bitrix_deal_deleted': '🗑 Сделка удалена в Bitrix — карточка перенесена в архив',
      'card_created_from_bitrix': `➕ Карточка создана из Bitrix (сделка #${h.payload.bitrix_deal_id || ''})`,
    };
    return map[h.action] || h.action;
  }
```

- [ ] **Step 2: Manual verification in browser**

На локальном dev-сервере: открыть карточку в Pipeline, вкладка «История». Пока реальных Bitrix-событий нет — временно (в консоли браузера, DevTools) вызвать `renderHistory([{action:'bitrix_field_changed', payload:{field:'OPPORTUNITY', old:100000, new:250000}, user_id:1, created_at:'2026-08-27'}, {action:'bitrix_comment', payload:{author:'7', text:'тест'}, user_id:1, created_at:'2026-08-27'}])` (функция уже в замыкании модуля — вызвать через открытие карточки и временный `console.log`, либо проще: дождаться Task 8/10 в реальном окружении и открыть карточку с реальными Bitrix-событиями) — убедиться, что строки читаемые, без `undefined`/`[object Object]`.

- [ ] **Step 3: Commit**

```bash
git add cabinet/static/js/pages/pipeline.js
git commit -m "feat(bitrix): render field-change/comment/delete history entries on card"
```

---

## Финальная проверка перед мержем

- [ ] **Прогнать весь тестовый набор подпроекта разом**

Run: `pytest tests/unit/test_bitrix24_rest_helpers.py tests/unit/test_bitrix_sync_characterization.py tests/unit/test_bitrix_sync_events.py tests/unit/test_bitrix_comment_sync.py tests/unit/test_bitrix_events_endpoint.py tests/unit/test_bitrix_inbound_settings_api.py -v`
Expected: все PASS.

- [ ] **Прогнать полный существующий набор тестов, чтобы исключить регресс в остальном коде**

Run: `pytest -q`
Expected: без новых падений по сравнению с состоянием до начала работы (если были падающие тесты до старта — см. заметку в спеке про `test_smart_matcher.py`, не относится к этому подпроекту).

- [ ] **Живая проверка на реальном портале (не автоматизируется)**

Настроить исходящий вебхук в Bitrix (используя URL из Настроек кабинета) на тестовом/боевом портале, вручную подвинуть тестовую сделку по стадиям, добавить комментарий, изменить сумму, удалить сделку — убедиться что все 4 сценария долетают до карточки в кабинете за секунды (комментарий — до ~90 сек).

# Moscow Suppliers Portal Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add zakupki.mos.ru ("Портал поставщиков") as a second tender source, matched against the same 50 `filters_v2.yaml` filters (company_id=57 only), reusing the existing SmartMatcher/dedup/notification pipeline end to end.

**Architecture:** A periodic worker-side job pulls all Moscow quotation sessions (КС) published since the last poll in one paginated request, maps each into the same tender-dict shape the matcher already consumes, and runs it through the unmodified `SmartMatcher` against company 57's active filters — no per-keyword API calls, no parallel matching engine.

**Tech Stack:** Python 3.11, `requests` (proxy-rotating session, new small module — not a refactor of the existing `ZakupkiRSSParser`), asyncio background task in the existing `tender_sniper.worker_main` process, PostgreSQL (no schema changes).

**Spec:** `docs/superpowers/specs/2026-09-13-mos-portal-integration-design.md`

## Global Constraints

- Only company_id=57's active filters are ever matched against this source — never all companies (design decision §2: the API token is personal, not a platform resource).
- Never call the API once per keyword — one bulk pull per poll cycle, by publish-date window, then local matching (design decision §4; the per-keyword pattern degraded the shared zakupki.gov.ru proxy pool earlier the same day this was designed).
- `PP_TOKEN` is a production secret — never print its value in full in logs, commit messages, or tool output.
- Tender numbers from this source are always prefixed `MOS-` — must never collide with zakupki.gov.ru's all-numeric tender_number format.
- Do not refactor `src/parsers/zakupki_rss_parser.py` — it's a live, delicate part of the production monitoring path. Duplicate its small proxy-rotation loop rather than extract/share it (three similar lines beat a premature shared abstraction that risks the existing parser).

---

### Task 1: KS DTO → tender-dict mapper (pure function)

**Files:**
- Create: `tender_sniper/sources/__init__.py` (empty)
- Create: `tender_sniper/sources/mos_portal_mapper.py`
- Test: `tests/unit/test_mos_portal_mapper.py`

**Interfaces:**
- Produces: `ks_dto_to_tender(dto: dict) -> dict` — pure function, no I/O. Output keys: `number, name, description, price, region, customer_name, published_date, submission_deadline, url, source_label`. Consumed by Task 4's polling job.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_mos_portal_mapper.py
import pytest
from tender_sniper.sources.mos_portal_mapper import ks_dto_to_tender

SAMPLE_DTO = {
    "id": 4271956,
    "name": "Поставка бумаги офисной А4",
    "status": "Active",
    "company": 'ГБУ "Жилищник района Марьино"',
    "federalLaw": None,
    "conclusionReason": None,
    "startPrice": 185000.0,
    "beginDate": "2026-09-10T09:00:00",
    "endDate": "2026-09-15T18:00:00",
}


@pytest.mark.unit
class TestKsDtoToTender:
    def test_maps_all_fields(self):
        t = ks_dto_to_tender(SAMPLE_DTO)
        assert t["number"] == "MOS-4271956"
        assert t["name"] == "Поставка бумаги офисной А4"
        assert t["description"] == ""
        assert t["price"] == 185000.0
        assert t["region"] == "Москва"
        assert t["customer_name"] == 'ГБУ "Жилищник района Марьино"'
        assert t["published_date"] == "2026-09-10T09:00:00"
        assert t["submission_deadline"] == "2026-09-15T18:00:00"
        assert t["source_label"] == "Портал поставщиков (Москва)"
        assert "zakupki.mos.ru" in t["url"]
        assert "4271956" in t["url"]

    def test_number_never_collides_with_zakupki_gov_ru_format(self):
        t = ks_dto_to_tender(SAMPLE_DTO)
        # zakupki.gov.ru numbers are all-digit strings (e.g. "0327600003126000023")
        assert not t["number"].isdigit()
        assert t["number"].startswith("MOS-")

    def test_missing_optional_fields_do_not_raise(self):
        minimal = {"id": 1, "name": "x", "company": "y", "startPrice": None,
                   "beginDate": None, "endDate": None}
        t = ks_dto_to_tender(minimal)
        assert t["number"] == "MOS-1"
        assert t["price"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_mos_portal_mapper.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tender_sniper.sources'`

- [ ] **Step 3: Write the mapper**

```python
# tender_sniper/sources/mos_portal_mapper.py
"""Превращает DTO котировочной сессии (КС) Портала поставщиков Москвы в
тот же формат tender-словаря, который tender_sniper.matching.SmartMatcher
уже умеет матчить (см. docs/superpowers/specs/2026-09-13-mos-portal-integration-design.md, разд. 4.2).
"""
from typing import Any, Dict, Optional

SOURCE_LABEL = "Портал поставщиков (Москва)"


def _card_url(ks_id: Any) -> str:
    return f"https://zakupki.mos.ru/auction/{ks_id}"


def ks_dto_to_tender(dto: Dict[str, Any]) -> Dict[str, Optional[Any]]:
    ks_id = dto["id"]
    return {
        "number": f"MOS-{ks_id}",
        "name": dto.get("name") or "",
        "description": "",  # список КС не отдаёт описание — см. спеку, разд. 2 п.6
        "price": dto.get("startPrice"),
        "region": "Москва",
        "customer_name": dto.get("company") or "",
        "published_date": dto.get("beginDate"),
        "submission_deadline": dto.get("endDate"),
        "url": _card_url(ks_id),
        "source_label": SOURCE_LABEL,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_mos_portal_mapper.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add tender_sniper/sources/__init__.py tender_sniper/sources/mos_portal_mapper.py tests/unit/test_mos_portal_mapper.py
git commit -m "feat: KS DTO to tender-dict mapper for Moscow portal integration"
```

---

### Task 2: Proxy-rotating API client

**Files:**
- Create: `tender_sniper/sources/mos_portal_client.py`
- Test: `tests/unit/test_mos_portal_client.py`

**Interfaces:**
- Consumes: env vars `PP_TOKEN`, `PROXY_URL`, `PROXY_URL_2`..`PROXY_URL_5` (same names already used by `src/parsers/zakupki_rss_parser.py` — do not rename).
- Produces: `class MosPortalClient` with `async def search_auctions(self, publish_date_from: str, publish_date_to: str, skip: int = 0, take: int = 200) -> dict` — returns the raw parsed JSON response (caller handles pagination across calls). Consumed by Task 4.
- Produces: `token_days_until_expiry() -> Optional[int]` — decodes the JWT `exp` claim without verifying the signature (this is our own token, not user input — no security boundary is being crossed by reading a claim we already hold); returns `None` if it can't be parsed.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_mos_portal_client.py
import base64
import json
import time

import pytest
import responses

from tender_sniper.sources.mos_portal_client import MosPortalClient, decode_jwt_exp


def _fake_jwt(exp: int) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b'=').decode()
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b'=').decode()
    return f"{header}.{payload}.sig"


@pytest.mark.unit
class TestDecodeJwtExp:
    def test_reads_exp_claim(self):
        token = _fake_jwt(exp=2000000000)
        assert decode_jwt_exp(token) == 2000000000

    def test_malformed_token_returns_none(self):
        assert decode_jwt_exp("not-a-jwt") is None


@pytest.mark.unit
class TestMosPortalClientAuth:
    @responses.activate
    def test_sends_bearer_token(self, monkeypatch):
        monkeypatch.setenv("PP_TOKEN", _fake_jwt(exp=int(time.time()) + 86400 * 3650))
        monkeypatch.delenv("PROXY_URL", raising=False)
        responses.add(
            responses.GET,
            "https://api.zakupki.mos.ru/api/v2/auction/public/Search",
            json={"data": [], "total": 0},
            status=200,
        )
        client = MosPortalClient()
        result = client.search_auctions_sync("2026-09-10", "2026-09-11")
        assert result == {"data": [], "total": 0}
        sent_auth = responses.calls[0].request.headers["Authorization"]
        assert sent_auth == f"Bearer {client.token}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_mos_portal_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tender_sniper.sources.mos_portal_client'`
(if `responses` isn't installed: `pip install responses` — add to `requirements-dev.txt`/`requirements.txt` as a test dependency, matching however existing test-only deps are declared in this repo)

- [ ] **Step 3: Write the client**

```python
# tender_sniper/sources/mos_portal_client.py
"""Клиент интеграционного API Портала поставщиков (zakupki.mos.ru).

api.zakupki.mos.ru режет не-российские IP так же, как zakupki.gov.ru —
нужен тот же прокси-пул (PROXY_URL, PROXY_URL_2..5). Прокси-ротация тут
намеренно СВОЯ, небольшая копия того, что уже есть в
src/parsers/zakupki_rss_parser.py — не рефакторим тот файл (см. Global
Constraints плана).
"""
import base64
import json
import logging
import os
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.zakupki.mos.ru/api/v2/auction/public/Search"
PROXY_ENV_VARS = ["PROXY_URL", "PROXY_URL_2", "PROXY_URL_3", "PROXY_URL_4", "PROXY_URL_5"]


def decode_jwt_exp(token: str) -> Optional[int]:
    """Читает claim exp из JWT без проверки подписи — это наш собственный
    токен, а не непроверенный ввод пользователя; нужен только чтобы
    залогировать предупреждение о скором истечении."""
    try:
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return payload.get("exp")
    except Exception:
        return None


class MosPortalClient:
    def __init__(self):
        self.token = (os.environ.get("PP_TOKEN") or "").strip()
        if not self.token:
            raise RuntimeError("PP_TOKEN не задан")
        self._proxies = [os.environ.get(v, "").strip() for v in PROXY_ENV_VARS]
        self._proxies = [p for p in self._proxies if p]

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    def search_auctions_sync(self, publish_date_from: str, publish_date_to: str,
                             skip: int = 0, take: int = 200) -> Dict[str, Any]:
        query = {
            "filter": {"publishDate": {"from": publish_date_from, "to": publish_date_to}},
            "skip": skip,
            "take": take,
        }
        params = {"query": json.dumps(query, ensure_ascii=False)}
        sessions = self._proxies or [None]
        last_error = None
        for proxy in sessions:
            proxies = {"http": proxy, "https": proxy} if proxy else None
            try:
                r = requests.get(BASE_URL, headers=self._headers(), params=params,
                                 proxies=proxies, timeout=15)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last_error = e
                logger.warning(f"Портал поставщиков: прокси {proxy or 'напрямую'} — {e}")
                continue
        raise RuntimeError(f"Все прокси недоступны для Портала поставщиков: {last_error}")

    async def search_auctions(self, publish_date_from: str, publish_date_to: str,
                              skip: int = 0, take: int = 200) -> Dict[str, Any]:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self.search_auctions_sync, publish_date_from, publish_date_to, skip, take,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_mos_portal_client.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add tender_sniper/sources/mos_portal_client.py tests/unit/test_mos_portal_client.py
git commit -m "feat: proxy-aware API client for Moscow Suppliers Portal"
```

---

### Task 3: Company-scoped active-filters query

**Files:**
- Modify: `tender_sniper/database/sqlalchemy_adapter.py` — method `get_all_active_filters` (found via `grep -n "async def get_all_active_filters"`)

**Interfaces:**
- Produces: `get_all_active_filters(self, company_id: Optional[int] = None) -> List[Dict[str, Any]]` — adds an optional filter; when `None` (existing callers, e.g. the main zakupki.gov.ru loop), behavior is byte-for-byte identical to today. When set, adds `SniperFilterModel.company_id == company_id` to the `where(and_(...))` clause. Consumed by Task 4 with `company_id=57`.

- [ ] **Step 1: Confirm the current signature and call sites**

Run: `grep -rn "get_all_active_filters" tender_sniper/ bot/` — note every call site so Step 3's change doesn't break any of them (all existing calls pass no `company_id`, so the new optional parameter defaulting to `None` must be a no-op for every one of them).

- [ ] **Step 2: Write the failing test**

Since this method requires a real async DB session (no existing mocking harness for `sqlalchemy_adapter.py` in this repo — confirmed by `grep -rn "async def test_" tests/` finding none against this file), verify manually instead of via pytest for this task:

```bash
# Run against the dev/staging DB (never prod for a manual probe like this)
python3 -c "
import asyncio
from tender_sniper.database import get_sniper_db

async def main():
    db = await get_sniper_db()
    all_filters = await db.get_all_active_filters()
    company_57 = await db.get_all_active_filters(company_id=57)
    assert len(company_57) <= len(all_filters)
    assert all(f.get('company_id') == 57 for f in company_57)
    print('OK:', len(all_filters), 'total,', len(company_57), 'company 57')

asyncio.run(main())
"
```

Expected right now: `TypeError: get_all_active_filters() got an unexpected keyword argument 'company_id'`

- [ ] **Step 3: Add the optional parameter**

Locate the method (found in Task 1's grep) and change its signature and query:

```python
    async def get_all_active_filters(self, company_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Получение всех активных фильтров с информацией о пользователе.

        company_id: если задан — только фильтры этой компании (используется
        интеграцией Портала поставщиков, см.
        docs/superpowers/specs/2026-09-13-mos-portal-integration-design.md).
        None (по умолчанию) — поведение не меняется, все компании как раньше.
        """
        async with DatabaseSession() as session:
            conditions = [
                SniperFilterModel.is_active == True,
                SniperFilterModel.deleted_at.is_(None),
                SniperUserModel.notifications_enabled == True,
                SniperUserModel.subscription_tier != 'expired',
            ]
            if company_id is not None:
                conditions.append(SniperFilterModel.company_id == company_id)
            result = await session.execute(
                select(SniperFilterModel, SniperUserModel)
                .join(SniperUserModel, SniperFilterModel.user_id == SniperUserModel.id)
                .where(and_(*conditions))
            )
            filter_user_pairs = result.all()
            # ... (rest of the method body unchanged — filter_dict assembly loop)
```

Keep every line after `filter_user_pairs = result.all()` exactly as it already is today — only the `where(...)` clause construction changes shape (from a fixed 4-arg `and_(...)` to a list built up conditionally).

- [ ] **Step 4: Re-run the manual verification script from Step 2**

Expected: prints `OK: N total, M company 57` with `M <= N` and every returned filter dict showing `company_id == 57`.

- [ ] **Step 5: Re-run the existing test suite to confirm no regressions**

Run: `pytest tests/unit/ -q`
Expected: same pass/fail counts as before this task (the 8 pre-existing stale-region failures in `test_smart_matcher.py`, everything else passing) — this task must not change that baseline.

- [ ] **Step 6: Commit**

```bash
git add tender_sniper/database/sqlalchemy_adapter.py
git commit -m "feat: optional company_id filter on get_all_active_filters"
```

---

### Task 4: Polling job — ties client + mapper + matcher + existing notify path together

**Files:**
- Create: `tender_sniper/jobs/mos_portal_poll.py`
- Modify: `tender_sniper/worker_main.py` — launch the new loop as an `asyncio.create_task`, same pattern as the existing `TenderSniperService` startup in that file.
- Test: `tests/unit/test_mos_portal_poll.py` (pure-logic pieces only — window computation, pagination-cap logic; the end-to-end network+DB path is a manual smoke test, Step 6 below)

**Interfaces:**
- Consumes: `MosPortalClient.search_auctions` (Task 2), `ks_dto_to_tender` (Task 1), `db.get_all_active_filters(company_id=57)` (Task 3), `SmartMatcher.match_tender` (existing, unchanged), `db.is_tender_sent_to_chat`, `db.save_notification`, `TelegramNotifier.send_tender_notification` (all existing, unchanged — same calls the main zakupki.gov.ru loop in `tender_sniper/service.py` already makes, including its `notify_chat_ids`/`notify_thread_id` per-filter routing; the exact shapes are mirrored in Step 3's code below).
- Produces: `async def mos_portal_poll_loop()` — the background task entry point.

- [ ] **Step 1: Write the failing tests for the pure-logic pieces**

```python
# tests/unit/test_mos_portal_poll.py
import pytest
from datetime import datetime, timedelta

from tender_sniper.jobs.mos_portal_poll import compute_poll_window, MAX_PAGES_PER_CYCLE

@pytest.mark.unit
class TestComputePollWindow:
    def test_overlaps_last_poll_by_30_minutes(self):
        last_poll = datetime(2026, 9, 13, 10, 0, 0)
        now = datetime(2026, 9, 13, 10, 10, 0)
        window_from, window_to = compute_poll_window(last_poll, now)
        assert window_from == last_poll - timedelta(minutes=30)
        assert window_to == now

    def test_first_run_with_no_prior_poll_uses_default_lookback(self):
        now = datetime(2026, 9, 13, 10, 0, 0)
        window_from, window_to = compute_poll_window(None, now)
        assert window_to == now
        assert window_from < now

    def test_max_pages_per_cycle_is_bounded(self):
        assert MAX_PAGES_PER_CYCLE <= 20  # см. спеку разд. 5 — потолок пагинации за цикл
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_mos_portal_poll.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the polling job**

```python
# tender_sniper/jobs/mos_portal_poll.py
"""Опрос Портала поставщиков (Москва) — второй источник тендеров, матчится
только по фильтрам company_id=57. См.
docs/superpowers/specs/2026-09-13-mos-portal-integration-design.md.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional, Tuple

from tender_sniper.sources.mos_portal_client import MosPortalClient, decode_jwt_exp
from tender_sniper.sources.mos_portal_mapper import ks_dto_to_tender
from tender_sniper.matching import SmartMatcher
from tender_sniper.notifications.telegram_notifier import TelegramNotifier
from tender_sniper.database import get_sniper_db

logger = logging.getLogger(__name__)

COMPANY_ID = 57
POLL_INTERVAL_SECONDS = 600  # 10 минут
OVERLAP_MINUTES = 30
DEFAULT_LOOKBACK_MINUTES = 60
MAX_PAGES_PER_CYCLE = 10
PAGE_SIZE = 200


def compute_poll_window(last_poll: Optional[datetime], now: datetime) -> Tuple[datetime, datetime]:
    if last_poll is None:
        return now - timedelta(minutes=DEFAULT_LOOKBACK_MINUTES), now
    return last_poll - timedelta(minutes=OVERLAP_MINUTES), now


async def _check_token_expiry(client: MosPortalClient) -> None:
    exp = decode_jwt_exp(client.token)
    if exp is None:
        logger.warning("Портал поставщиков: не удалось прочитать exp токена")
        return
    days_left = (datetime.utcfromtimestamp(exp) - datetime.utcnow()).days
    if days_left < 30:
        logger.warning(f"Портал поставщиков: токен PP_TOKEN истекает через {days_left} дн.!")


async def mos_portal_poll_loop():
    await asyncio.sleep(180)  # стартовая задержка, как у остальных фоновых job'ов
    client = MosPortalClient()
    await _check_token_expiry(client)
    matcher = SmartMatcher()
    bot_token = os.environ.get('BOT_TOKEN', '')
    notifier = TelegramNotifier(bot_token=bot_token) if bot_token else None
    db = await get_sniper_db()
    last_poll: Optional[datetime] = None

    while True:
        try:
            now = datetime.utcnow()
            window_from, window_to = compute_poll_window(last_poll, now)
            tenders = []
            skip = 0
            for _ in range(MAX_PAGES_PER_CYCLE):
                resp = await client.search_auctions(
                    window_from.isoformat(), window_to.isoformat(), skip=skip, take=PAGE_SIZE,
                )
                page = resp.get("data") or resp.get("items") or []
                if not page:
                    break
                tenders.extend(ks_dto_to_tender(dto) for dto in page)
                if len(page) < PAGE_SIZE:
                    break
                skip += PAGE_SIZE
            else:
                logger.warning(f"Портал поставщиков: достигнут потолок {MAX_PAGES_PER_CYCLE} страниц за цикл")

            if tenders:
                filters = await db.get_all_active_filters(company_id=COMPANY_ID)
                seen_tenders = set()  # (chat_id, tender_number) — дедуп внутри одного цикла
                for tender in tenders:
                    tender_number = tender['number']
                    for filter_data in filters:
                        match = matcher.match_tender(tender, filter_data)
                        if not match:
                            continue

                        filter_id = filter_data['id']
                        filter_name = filter_data['name']
                        user_id = filter_data['user_id']
                        notify_chat_ids = filter_data.get('notify_chat_ids') or []
                        notify_thread_id = filter_data.get('notify_thread_id')
                        target_chat_ids = notify_chat_ids if notify_chat_ids else [filter_data['telegram_id']]

                        for target_chat_id in target_chat_ids:
                            dedup_key = (target_chat_id, tender_number)
                            if dedup_key in seen_tenders:
                                continue
                            if target_chat_id < 0:
                                already_in_chat = await db.is_tender_sent_to_chat(tender_number, target_chat_id)
                                if already_in_chat:
                                    seen_tenders.add(dedup_key)
                                    continue
                            seen_tenders.add(dedup_key)

                            if notifier is None:
                                logger.warning("Портал поставщиков: BOT_TOKEN не задан, уведомление не отправлено")
                                continue

                            success = await notifier.send_tender_notification(
                                telegram_id=target_chat_id,
                                tender=tender,
                                match_info={'score': match['score'],
                                            'matched_keywords': match.get('matched_keywords', [])},
                                filter_name=filter_name,
                                is_auto_notification=True,
                                subscription_tier='premium',
                                message_thread_id=notify_thread_id if target_chat_id < 0 else None,
                            )
                            if success:
                                await db.save_notification(
                                    user_id=user_id, filter_id=filter_id, filter_name=filter_name,
                                    tender_data=tender, score=match['score'],
                                    matched_keywords=match.get('matched_keywords', []),
                                    match_info=match,
                                )
                            else:
                                logger.warning(f"Портал поставщиков: не удалось отправить {tender_number} -> {target_chat_id}")

            last_poll = now
        except Exception as e:
            logger.error(f"Портал поставщиков: ошибка цикла опроса: {e}", exc_info=True)

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_mos_portal_poll.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Manual smoke test against the real API (not part of CI)**

```bash
railway run --service miraculous-analysis python3 -c "
import asyncio
from tender_sniper.sources.mos_portal_client import MosPortalClient
from datetime import datetime, timedelta

async def main():
    c = MosPortalClient()
    now = datetime.utcnow()
    resp = await c.search_auctions((now - timedelta(days=2)).isoformat(), now.isoformat(), take=5)
    print(resp)

asyncio.run(main())
"
```
`miraculous-analysis` is the worker/matching service (where `PP_TOKEN` and the proxy pool env vars actually live — same service Task 6 sets the secret on). Expected: a real JSON response with recent Moscow КС (or an empty list if none published in the window — not an error).

- [ ] **Step 6: Wire into worker_main.py**

In `tender_sniper/worker_main.py`, alongside the existing `TenderSniperService` task creation, add:
```python
from tender_sniper.jobs.mos_portal_poll import mos_portal_poll_loop
asyncio.create_task(mos_portal_poll_loop())
```

- [ ] **Step 7: Commit**

```bash
git add tender_sniper/jobs/mos_portal_poll.py tender_sniper/worker_main.py tests/unit/test_mos_portal_poll.py
git commit -m "feat: Moscow Suppliers Portal polling loop wired into worker"
```

---

### Task 5: Source label on the Telegram card

**Files:**
- Modify: `bot/formatters/tender_card.py` — function `_build_text` (found via `grep -n "_build_text" bot/formatters/tender_card.py`)
- Test: `tests/unit/test_tender_card_source_label.py` (new file — keeps this focused test separate from the existing, larger `test_tender_card_name.py`)

**Interfaces:**
- Consumes: `tender.get('source_label')` (Task 1's mapper sets this; regular zakupki.gov.ru tenders never have this key).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_tender_card_source_label.py
import pytest
from bot.formatters.tender_card import format_tender_card

BASE_TENDER = {
    'number': '0327600003126000023', 'name': 'Поставка бумаги',
    'price': 100000, 'url': 'https://zakupki.gov.ru/x', 'region': 'Москва',
    'customer_name': 'ГБУ Тест',
}
MATCH_INFO = {'score': 50, 'matched_keywords': ['бумага']}


@pytest.mark.unit
class TestSourceLabel:
    def test_no_source_label_key_unchanged(self):
        text, _ = format_tender_card(BASE_TENDER, MATCH_INFO, 'Бумага офисная')
        assert 'Портал поставщиков' not in text

    def test_source_label_shown_when_present(self):
        tender = {**BASE_TENDER, 'number': 'MOS-4271956', 'source_label': 'Портал поставщиков (Москва)'}
        text, _ = format_tender_card(tender, MATCH_INFO, 'Бумага офисная')
        assert 'Портал поставщиков (Москва)' in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_tender_card_source_label.py -v`
Expected: FAIL (`AssertionError` on the second test — label not yet rendered anywhere)

- [ ] **Step 3: Add the optional line to `_build_text`**

Find where the filter name / other meta lines are assembled in `_build_text` (read the function first — do not guess the exact insertion point) and add, guarded by presence of the key:

```python
    if tender.get('source_label'):
        lines.append(f"📍 Источник: {tender['source_label']}")
```

placed alongside the other meta lines (near where `filter_name` or region is rendered — match the existing formatting style of that function, e.g. same emoji-prefixed-line convention already used there).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_tender_card_source_label.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full existing card-formatting test suite to confirm no regressions**

Run: `pytest tests/unit/test_tender_card_name.py tests/unit/test_tender_card_source_label.py -q`
Expected: all passing, same baseline as before this task for `test_tender_card_name.py`.

- [ ] **Step 6: Commit**

```bash
git add bot/formatters/tender_card.py tests/unit/test_tender_card_source_label.py
git commit -m "feat: show source label on tender card for non-zakupki.gov.ru sources"
```

---

### Task 6: Credentials — set PP_TOKEN in Railway (operational, not code)

This task has no code changes — it is the one step that must be done by a human with access to the real secret value, not delegated to a subagent.

- [ ] **Step 1: Set the Railway variable on the worker service only**

```bash
railway variables set PP_TOKEN='<real token value>' --service miraculous-analysis
```

Do this directly in your own terminal (or ask the coordinating session to run it without ever pasting the token into a subagent dispatch or a committed file) — never put the raw token value in a git commit, a task brief, or a subagent prompt.

- [ ] **Step 2: Confirm the worker picks it up**

```bash
railway logs --service miraculous-analysis
```
Look for the new job's startup log line and the absence of the "PP_TOKEN не задан" error.


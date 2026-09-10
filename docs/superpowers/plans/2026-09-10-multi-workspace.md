# Multi-workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `sniper_filters`/`sniper_notifications` company-scoped instead of user-scoped so (a) any cabinet team member can manage filters/see the feed, and (b) a second, fully isolated `Company` workspace can be bootstrapped with its own filters and notification channel — reusing the fact that `PipelineCard` and Bitrix sync are already `company_id`-scoped.

**Architecture:** Add `company_id` to `sniper_filters` and `sniper_notifications` (keeping `user_id` as creator/DM-fallback). Relax `company_members` from "one company per user" to "one row per (user, company)" pair so the account owner can belong to two companies, and add session-level active-company tracking + a cabinet switcher for that case. All cabinet filter/feed API routes move from `@require_auth`+`user_id` to `@require_team_member`+`company_id`. The Telegram/Max bot's own filter commands are untouched (`user_id`-scoped, stays personal).

**Tech Stack:** Python 3.11, aiohttp, SQLAlchemy (async) + Alembic, PostgreSQL, Jinja2 (`aiohttp_jinja2`), pytest.

**Spec:** `docs/superpowers/specs/2026-09-10-multi-workspace-design.md`

## Global Constraints

- Single production database, no staging environment — the migration runs automatically on deploy (`bot/main.py:132` calls `alembic upgrade head` on startup). Task 1's migration must be tested against a local Postgres copy before merging to `main`.
- Zero behavior change for any user with exactly one company membership (this is everyone today except the account owner once Task 8 runs) — `get_active_company`/`get_company_for_user` must return identically to today for the single-membership case.
- No test-DB fixture infrastructure exists in this repo (`tests/` has no `conftest.py`, and no existing test touches `cabinet/`, `team_service.py`, or `pipeline_service.py`). Follow the codebase's actual convention: write real pytest unit tests only for pure functions (no DB); for DB/route-wiring changes, the task's verification step is a concrete manual check (exact command to run), matching how `bitrix_sync.py`/`pipeline_service.py` shipped without a DB test suite.
- The bootstrap script (Task 8) must not hardcode the new company's name, notify chat id, or any people — those are CLI arguments at run time, never committed to the repo (the whole point of the second workspace is that it isn't visible in code anyone with repo access can read).

---

### Task 1: Schema migration — company_id + active_company_id + relax company_members

**Files:**
- Modify: `database.py:129-185` (`SniperFilter`)
- Modify: `database.py:188-229` (`SniperNotification`)
- Modify: `database.py:842-856` (`WebSession`)
- Modify: `database.py:1041-1053` (`CompanyMember`)
- Create: `alembic/versions/20260910_multi_workspace.py`

**Interfaces:**
- Produces: `sniper_filters.company_id` (nullable int, FK `companies.id`), `sniper_notifications.company_id` (nullable int, FK `companies.id`), `web_sessions.active_company_id` (nullable int, FK `companies.id`), `company_members` unique constraint `uq_company_members_user_company` on `(user_id, company_id)` replacing `uq_company_members_user`. Every later task relies on these column/constraint names exactly.

- [ ] **Step 1: Update the SQLAlchemy models in `database.py`**

In `SniperFilter` (around line 134, right after the existing `user_id` column):

```python
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=True, index=True)
```

In `SniperNotification` (around line 193, right after `user_id`):

```python
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=True, index=True)
```

In `WebSession` (around line 852, after `ip_address`):

```python
    active_company_id = Column(Integer, ForeignKey('companies.id'), nullable=True)
```

In `CompanyMember` (`__table_args__`, replace the existing tuple):

```python
    __table_args__ = (
        UniqueConstraint('user_id', 'company_id', name='uq_company_members_user_company'),
        Index('ix_company_members_company', 'company_id'),
    )
```

- [ ] **Step 2: Write the migration**

Create `alembic/versions/20260910_multi_workspace.py`:

```python
"""multi-workspace: company_id on filters/notifications, active_company_id
on web_sessions, relax company_members to (user_id, company_id)

Revision ID: 20260910_multi_workspace
Revises: 20260717_kp
Create Date: 2026-09-10

"""
from alembic import op
import sqlalchemy as sa


revision = '20260910_multi_workspace'
down_revision = '20260717_kp'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. New columns (nullable — backfilled below; stays nullable for any
    #    user who has never had a cabinet company).
    op.add_column('sniper_filters', sa.Column('company_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_sniper_filters_company_id_companies',
        'sniper_filters', 'companies', ['company_id'], ['id'],
    )
    op.create_index('ix_sniper_filters_company_id', 'sniper_filters', ['company_id'])

    op.add_column('sniper_notifications', sa.Column('company_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_sniper_notifications_company_id_companies',
        'sniper_notifications', 'companies', ['company_id'], ['id'],
    )
    op.create_index('ix_sniper_notifications_company_id', 'sniper_notifications', ['company_id'])

    op.add_column('web_sessions', sa.Column('active_company_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_web_sessions_active_company_id_companies',
        'web_sessions', 'companies', ['active_company_id'], ['id'],
    )

    # 2. Relax "one company per user" so a user can belong to more than one
    #    company (still at most once per company).
    op.drop_constraint('uq_company_members_user', 'company_members', type_='unique')
    op.create_unique_constraint(
        'uq_company_members_user_company', 'company_members', ['user_id', 'company_id'],
    )

    # 3. Backfill. Every user who owns at least one filter gets a company —
    #    reusing an existing membership if they have one, otherwise creating
    #    one exactly like team_service.get_or_create_company_for_user does
    #    lazily today (name = 'Команда {first_name or f"User {id}"}',
    #    role='owner'). Then filters/notifications for that user get
    #    company_id set.
    bind = op.get_bind()

    filter_owner_ids = [row[0] for row in bind.execute(
        sa.text('SELECT DISTINCT user_id FROM sniper_filters WHERE company_id IS NULL')
    ).fetchall()]

    for user_id in filter_owner_ids:
        existing = bind.execute(
            sa.text('SELECT company_id FROM company_members WHERE user_id = :uid LIMIT 1'),
            {'uid': user_id},
        ).fetchone()

        if existing:
            company_id = existing[0]
        else:
            name_row = bind.execute(
                sa.text('SELECT first_name FROM sniper_users WHERE id = :uid'),
                {'uid': user_id},
            ).fetchone()
            name_base = (name_row[0] if name_row and name_row[0] else f'User {user_id}')
            company_id = bind.execute(
                sa.text(
                    "INSERT INTO companies (name, owner_user_id, created_at) "
                    "VALUES (:name, :uid, now()) RETURNING id"
                ),
                {'name': f'Команда {name_base}', 'uid': user_id},
            ).scalar()
            bind.execute(
                sa.text(
                    "INSERT INTO company_members (company_id, user_id, role, joined_at) "
                    "VALUES (:cid, :uid, 'owner', now())"
                ),
                {'cid': company_id, 'uid': user_id},
            )

        bind.execute(
            sa.text('UPDATE sniper_filters SET company_id = :cid WHERE user_id = :uid AND company_id IS NULL'),
            {'cid': company_id, 'uid': user_id},
        )
        bind.execute(
            sa.text('UPDATE sniper_notifications SET company_id = :cid WHERE user_id = :uid AND company_id IS NULL'),
            {'cid': company_id, 'uid': user_id},
        )

    # Notifications from users with no filters at all (e.g. instant-search-
    # only users) — backfill from an existing membership if they have one.
    # If they don't, company_id stays NULL (they've never used the cabinet
    # team features, nothing regresses).
    remaining = [row[0] for row in bind.execute(
        sa.text('SELECT DISTINCT user_id FROM sniper_notifications WHERE company_id IS NULL')
    ).fetchall()]
    for user_id in remaining:
        existing = bind.execute(
            sa.text('SELECT company_id FROM company_members WHERE user_id = :uid LIMIT 1'),
            {'uid': user_id},
        ).fetchone()
        if existing:
            bind.execute(
                sa.text('UPDATE sniper_notifications SET company_id = :cid WHERE user_id = :uid AND company_id IS NULL'),
                {'cid': existing[0], 'uid': user_id},
            )


def downgrade() -> None:
    # Lossy if any user has already joined a second company (that user
    # would violate the restored single-company constraint) — acceptable
    # for a downgrade of a not-yet-used feature.
    op.drop_constraint('uq_company_members_user_company', 'company_members', type_='unique')
    op.create_unique_constraint('uq_company_members_user', 'company_members', ['user_id'])

    op.drop_constraint('fk_web_sessions_active_company_id_companies', 'web_sessions', type_='foreignkey')
    op.drop_column('web_sessions', 'active_company_id')

    op.drop_index('ix_sniper_notifications_company_id', table_name='sniper_notifications')
    op.drop_constraint('fk_sniper_notifications_company_id_companies', 'sniper_notifications', type_='foreignkey')
    op.drop_column('sniper_notifications', 'company_id')

    op.drop_index('ix_sniper_filters_company_id', table_name='sniper_filters')
    op.drop_constraint('fk_sniper_filters_company_id_companies', 'sniper_filters', type_='foreignkey')
    op.drop_column('sniper_filters', 'company_id')
```

- [ ] **Step 3: Verify against a local Postgres copy**

This is a data migration touching every row in `sniper_filters` and `sniper_notifications` on the one production database (no staging) — do not skip this step.

```bash
# Point DATABASE_URL at a local Postgres loaded from a recent prod dump, then:
python -m alembic upgrade head
python -m alembic current   # expect: 20260910_multi_workspace (head)
```

Then check backfill correctness:

```bash
psql "$DATABASE_URL" -c "SELECT count(*) FROM sniper_filters WHERE company_id IS NULL;"
psql "$DATABASE_URL" -c "SELECT count(*) FROM sniper_notifications WHERE company_id IS NULL AND user_id IN (SELECT user_id FROM sniper_filters);"
```

Expected: the first count is 0 (every filter got a company); the second is 0 (every notification belonging to a filter-owning user got a company too).

- [ ] **Step 4: Commit**

```bash
git add database.py alembic/versions/20260910_multi_workspace.py
git commit -m "feat: company_id on filters/notifications, allow multi-company membership

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Adapter layer — company-aware filter/notification/session methods

**Files:**
- Modify: `tender_sniper/database/sqlalchemy_adapter.py:419-453` (`create_filter`)
- Modify: `tender_sniper/database/sqlalchemy_adapter.py:455-471` (`get_user_filters` — add sibling method)
- Modify: `tender_sniper/database/sqlalchemy_adapter.py:662-716` (`_filter_to_dict`)
- Modify: `tender_sniper/database/sqlalchemy_adapter.py:876-985` (`save_notification`)
- Modify: `tender_sniper/database/sqlalchemy_adapter.py:3087-3129` (`get_web_session` + new `set_session_active_company`)
- Modify: `tender_sniper/database/sqlalchemy_adapter.py:15-37` (imports)

**Interfaces:**
- Consumes: `company_id` columns from Task 1.
- Produces: `create_filter(user_id, name, company_id=None, **kwargs)`, `get_company_filters(company_id, active_only=True) -> List[Dict]`, `_filter_to_dict(...)` now includes `'company_id'` key, `save_notification(...)` stamps `company_id` automatically, `get_web_session(...)` dict now includes `'active_company_id'`, `set_session_active_company(session_token, company_id) -> None`. Task 5 (`cabinet/api.py`) and Task 4 (`cabinet/auth.py`) call these by name.

- [ ] **Step 1: Add the `CompanyMember` import**

In `tender_sniper/database/sqlalchemy_adapter.py`, add to the `from database import (...)` block (around line 34, after `WebSession as WebSessionModel`):

```python
    CompanyMember as CompanyMemberModel,
```

- [ ] **Step 2: `_filter_to_dict` — add `company_id`**

At `tender_sniper/database/sqlalchemy_adapter.py:679`, right after `'user_id': filter_obj.user_id,`:

```python
            'company_id': getattr(filter_obj, 'company_id', None),
```

- [ ] **Step 3: `create_filter` — accept `company_id`**

Replace the signature and constructor at lines 419-450:

```python
    async def create_filter(self, user_id: int, name: str, company_id: int = None, **kwargs) -> int:
        """Создание фильтра."""
        async with DatabaseSession() as session:
            filter_obj = SniperFilterModel(
                user_id=user_id,
                company_id=company_id,
                name=name,
```

(keep the rest of the constructor body — `keywords=kwargs.get(...)` etc. — unchanged).

- [ ] **Step 4: Add `get_company_filters` next to `get_user_filters`**

After `get_active_filters` (line 475), insert:

```python
    async def get_company_filters(self, company_id: int, active_only: bool = True) -> List[Dict[str, Any]]:
        """Фильтры компании (для кабинета — видны всем членам команды)."""
        async with DatabaseSession() as session:
            query = select(SniperFilterModel).where(
                and_(
                    SniperFilterModel.company_id == company_id,
                    SniperFilterModel.deleted_at.is_(None),
                )
            )
            if active_only:
                query = query.where(SniperFilterModel.is_active == True)

            result = await session.execute(query.order_by(SniperFilterModel.created_at.desc()))
            filters = result.scalars().all()
            return [self._filter_to_dict(f) for f in filters]
```

- [ ] **Step 5: `save_notification` — stamp `company_id`**

In `save_notification` (around line 941-959), before constructing `SniperNotificationModel`, resolve the company id: prefer the matched filter's company, fall back to the notifying user's own (oldest) company membership:

```python
            resolved_company_id = None
            if filter_id:
                filter_row = await session.get(SniperFilterModel, filter_id)
                if filter_row:
                    resolved_company_id = filter_row.company_id
            if resolved_company_id is None:
                membership = await session.scalar(
                    select(CompanyMemberModel)
                    .where(CompanyMemberModel.user_id == user_id)
                    .order_by(CompanyMemberModel.joined_at)
                    .limit(1)
                )
                if membership:
                    resolved_company_id = membership.company_id

            try:
              notification = SniperNotificationModel(
                user_id=user_id,
                company_id=resolved_company_id,
                filter_id=filter_id,
```

(keep the rest of the constructor and the `try`/`except IntegrityError` block unchanged — only the two new lines shown above are inserted).

- [ ] **Step 6: `get_web_session` — expose `active_company_id`**

In `get_web_session` (line 3115), add to the returned dict:

```python
            return {
                'id': ws.id,
                'user_id': ws.user_id,
                'session_token': ws.session_token,
                'expires_at': ws.expires_at.isoformat() if ws.expires_at else None,
                'ip_address': ws.ip_address,
                'active_company_id': ws.active_company_id,
            }
```

- [ ] **Step 7: Add `set_session_active_company`**

Right after `get_web_session` (before `delete_web_session`, around line 3122):

```python
    async def set_session_active_company(self, session_token: str, company_id: int) -> None:
        """Сохраняет выбранный воркспейс для мультикомпанийного юзера."""
        async with DatabaseSession() as session:
            await session.execute(
                update(WebSessionModel)
                .where(WebSessionModel.session_token == session_token)
                .values(active_company_id=company_id)
            )
```

- [ ] **Step 8: Manual verification**

No DB test fixture exists in this repo for this adapter (consistent with the rest of `sqlalchemy_adapter.py` — no existing tests touch it). Verify by running the bot locally against the migrated local DB from Task 1 and exercising one filter create/list round trip:

```bash
python -c "
import asyncio
from tender_sniper.database import get_sniper_db

async def check():
    db = await get_sniper_db()
    fid = await db.create_filter(user_id=1, company_id=1, name='smoke-test', keywords=['test'])
    filters = await db.get_company_filters(1)
    assert any(f['id'] == fid and f['company_id'] == 1 for f in filters), filters
    print('OK')

asyncio.run(check())
"
```

Expected output: `OK`. Delete the `smoke-test` filter row afterward.

- [ ] **Step 9: Commit**

```bash
git add tender_sniper/database/sqlalchemy_adapter.py
git commit -m "feat: company-scoped filter/notification adapter methods

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: team_service — active-company resolution

**Files:**
- Modify: `cabinet/team_service.py:59-68` (`get_company_for_user`)
- Modify: `cabinet/team_service.py` (add new functions after `get_or_create_company_for_user`, i.e. after line 86)
- Test: `tests/test_team_service_active_company.py`

**Interfaces:**
- Consumes: `Company`, `CompanyMember` models (Task 1's relaxed constraint).
- Produces: `_pick_active_company(companies: List[Dict], active_company_id: Optional[int]) -> Optional[Dict]` (pure), `list_companies_for_user(user_id: int) -> List[Dict]` (each dict has `_company_dict` fields plus `'role'`), `get_active_company(user_id: int, session_token: Optional[str]) -> Optional[Dict]`, `add_member_to_company(company_id: int, user_id: int, role: str = 'member') -> Dict`. Task 4 (`cabinet/auth.py`) calls `get_active_company`; Task 6 (`routes.py`) calls `list_companies_for_user` and `get_active_company`; Task 8 (bootstrap script) calls `add_member_to_company`.

- [ ] **Step 1: Write the failing test for `_pick_active_company`**

Create `tests/test_team_service_active_company.py`:

```python
from cabinet.team_service import _pick_active_company


def test_no_companies_returns_none():
    assert _pick_active_company([], None) is None


def test_single_company_returned_regardless_of_active_id():
    company = {'id': 1, 'name': 'Solo'}
    assert _pick_active_company([company], None) == company
    assert _pick_active_company([company], 999) == company


def test_multi_company_uses_valid_active_id():
    a = {'id': 1, 'name': 'A'}
    b = {'id': 2, 'name': 'B'}
    assert _pick_active_company([a, b], 2) == b


def test_multi_company_falls_back_to_oldest_when_active_id_invalid():
    a = {'id': 1, 'name': 'A'}
    b = {'id': 2, 'name': 'B'}
    assert _pick_active_company([a, b], 999) == a


def test_multi_company_falls_back_to_oldest_when_no_active_id():
    a = {'id': 1, 'name': 'A'}
    b = {'id': 2, 'name': 'B'}
    assert _pick_active_company([a, b], None) == a
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_team_service_active_company.py -v`
Expected: FAIL — `ImportError: cannot import name '_pick_active_company'`

- [ ] **Step 3: Implement `_pick_active_company` and the rest of Task 3**

In `cabinet/team_service.py`, replace `get_company_for_user` (currently lines 59-68) with:

```python
async def get_company_for_user(user_id: int) -> Optional[Dict]:
    """Возвращает первую (по вступлению) компанию юзера или None."""
    async with DatabaseSession() as session:
        membership = await session.scalar(
            select(CompanyMember).where(CompanyMember.user_id == user_id)
            .order_by(CompanyMember.joined_at).limit(1)
        )
        if not membership:
            return None
        company = await session.get(Company, membership.company_id)
        return _company_dict(company) if company else None
```

Then, after `get_or_create_company_for_user` (currently ends at line 86), add:

```python
async def list_companies_for_user(user_id: int) -> List[Dict]:
    """Все компании юзера, по порядку вступления (обычно одна)."""
    async with DatabaseSession() as session:
        result = await session.execute(
            select(CompanyMember, Company)
            .join(Company, Company.id == CompanyMember.company_id)
            .where(CompanyMember.user_id == user_id)
            .order_by(CompanyMember.joined_at)
        )
        out = []
        for member, company in result.all():
            d = _company_dict(company)
            d['role'] = member.role
            out.append(d)
        return out


def _pick_active_company(companies: List[Dict], active_company_id: Optional[int]) -> Optional[Dict]:
    """0 -> None. 1 -> она же (`companies` is the only membership, zero
    behavior change for single-company users). >1 -> active_company_id if
    it matches a membership, else the oldest (companies[0], since
    list_companies_for_user orders by joined_at ascending)."""
    if not companies:
        return None
    if len(companies) == 1:
        return companies[0]
    if active_company_id is not None:
        for c in companies:
            if c['id'] == active_company_id:
                return c
    return companies[0]


async def get_active_company(user_id: int, session_token: Optional[str]) -> Optional[Dict]:
    """Активная компания для сессии. Для юзеров с одним членством —
    всегда оно, без похода в web_sessions."""
    companies = await list_companies_for_user(user_id)
    active_company_id = None
    if session_token and len(companies) > 1:
        from tender_sniper.database import get_sniper_db
        db = await get_sniper_db()
        ws = await db.get_web_session(session_token)
        active_company_id = ws.get('active_company_id') if ws else None
    return _pick_active_company(companies, active_company_id)


async def add_member_to_company(company_id: int, user_id: int, role: str = 'member') -> Dict:
    """Добавляет юзера в компанию напрямую, минуя проверку «уже в одной
    команде» из accept_invite. Используется только для бутстрапа второго
    воркспейса — владелец должен состоять сразу в двух компаниях, а
    accept_invite намеренно продолжает блокировать это для всех остальных."""
    async with DatabaseSession() as session:
        member = CompanyMember(company_id=company_id, user_id=user_id, role=role)
        session.add(member)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return {'ok': False, 'error': 'Уже состоит в этой компании'}
    return {'ok': True}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_team_service_active_company.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add cabinet/team_service.py tests/test_team_service_active_company.py
git commit -m "feat: active-company resolution for multi-company membership

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: cabinet/auth.py — require_team_member uses active-company resolution

**Files:**
- Modify: `cabinet/auth.py:123-156` (`require_team_member`)

**Interfaces:**
- Consumes: `team_service.get_active_company(user_id, session_token)` from Task 3.
- Produces: `request['company']` now reflects the session's active company for multi-membership users (unchanged shape/behavior for single-membership users).

- [ ] **Step 1: Update `require_team_member`**

Replace the body of `require_team_member` (`cabinet/auth.py:129-155`) — specifically the import line and the `company = await get_company_for_user(...)` call — with:

```python
    async def wrapper(request):
        user = await get_current_user(request)
        if not user:
            if '/api/' in request.path:
                return web.json_response({'error': 'Unauthorized'}, status=401)
            raise web.HTTPFound('/cabinet/login')

        from cabinet.team_service import (
            get_active_company, get_or_create_company_for_user,
        )
        company = await get_active_company(user['user_id'], user.get('session_token'))
        is_api = '/api/' in request.path
        if not company:
            if is_api:
                return web.json_response({'error': 'Not in any team'}, status=403)
            if auto_create_for_pages:
                company = await get_or_create_company_for_user(user['user_id'])
            else:
                raise web.HTTPFound('/cabinet/pipeline')

        request['user'] = user
        request['company'] = company
        request['role'] = (
            'owner' if company['owner_user_id'] == user['user_id'] else 'member'
        )
        return await handler(request)
    return wrapper
```

- [ ] **Step 2: Manual verification**

No existing test touches `require_team_member` (no cabinet test infra in this repo — see Global Constraints). Verify by running the bot locally and loading `/cabinet/pipeline` as the single-company account used in Task 2's smoke test — it should load exactly as before (single membership, no switcher, same company).

- [ ] **Step 3: Commit**

```bash
git add cabinet/auth.py
git commit -m "feat: require_team_member resolves the session's active company

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: cabinet/api.py — company-scope the filter endpoints + switch endpoint

**Files:**
- Modify: `cabinet/api.py:252-467` (`get_filters`, `update_filter`, `create_filter`, `delete_filter`, `toggle_filter`, `get_filter_notify_targets`, `update_filter_notify_targets`)
- Modify: `cabinet/api.py` (add `company_switch` after the pipeline API section, e.g. after line 1144)

**Interfaces:**
- Consumes: `db.get_company_filters`, `create_filter(..., company_id=...)` (Task 2); `require_team_member` (Task 4); `team_service.list_companies_for_user` (Task 3).
- Produces: `company_switch(request) -> web.Response`, registered as a route in Task 6.

- [ ] **Step 1: Swap the decorator and imports**

At the top of `cabinet/api.py`, change line 14:

```python
from .auth import require_auth, require_team_member
```

- [ ] **Step 2: `get_filters` — company-scoped**

Replace lines 252-261:

```python
@require_team_member
async def get_filters(request: web.Request) -> web.Response:
    """GET /cabinet/api/filters — фильтры компании."""
    company = request['company']
    active_only_param = request.query.get('active_only', 'true').lower()
    active_only = active_only_param not in ('false', '0', 'no')
    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()
    filters = await db.get_company_filters(company['id'], active_only=active_only)
    return web.json_response({'filters': filters})
```

- [ ] **Step 3: `update_filter` — ownership check by company**

Replace lines 264-289:

```python
@require_team_member
async def update_filter(request: web.Request) -> web.Response:
    """PUT/POST /cabinet/api/filters/:id — обновление фильтра."""
    company = request['company']
    filter_id = int(request.match_info['id'])

    try:
        data = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()

    filter_data = await db.get_filter_by_id(filter_id)
    if not filter_data or filter_data.get('company_id') != company['id']:
        return web.json_response({'error': 'Filter not found'}, status=404)

    allowed = {'name', 'keywords', 'exclude_keywords', 'price_min', 'price_max',
               'regions', 'law_type', 'is_active', 'tender_types', 'ai_intent'}
    filtered = {k: v for k, v in data.items() if k in allowed}

    await db.update_filter(filter_id, **filtered)
    return web.json_response({'ok': True})
```

- [ ] **Step 4: `create_filter` — company-scoped, quota counted per company**

Replace lines 296-345. The per-user subscription tier still caps how many filters *that creator* may add, but the count it's checked against is now the whole company's active filters (not just the creator's own) — otherwise every trial-tier member could add 3 "free" filters on top of each other with no real limit at company level:

```python
@require_team_member
async def create_filter(request: web.Request) -> web.Response:
    """POST /cabinet/api/filters/create — создание нового фильтра."""
    user = request['user']
    company = request['company']
    try:
        data = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    name = (data.get('name') or '').strip()
    if not name:
        return web.json_response({'error': 'Name is required'}, status=400)

    keywords = data.get('keywords', [])
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(',') if k.strip()]

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()

    user_info = await db.get_user_by_telegram_id(user['telegram_id'])
    filters_limit = user_info.get('filters_limit', 3) if user_info else 3
    existing = await db.get_company_filters(company['id'], active_only=False)
    active_count = sum(1 for f in existing if f.get('is_active') and not f.get('deleted_at'))
    if active_count >= filters_limit:
        return web.json_response({'error': f'Достигнут лимит фильтров ({filters_limit})'}, status=400)

    exclude_kw = data.get('exclude_keywords', [])
    if isinstance(exclude_kw, str):
        exclude_kw = [k.strip() for k in exclude_kw.split(',') if k.strip()]

    filter_id = await db.create_filter(
        user_id=user['user_id'],
        company_id=company['id'],
        name=name,
        keywords=keywords,
        exclude_keywords=exclude_kw,
        price_min=data.get('price_min'),
        price_max=data.get('price_max'),
        regions=data.get('regions', []),
        law_type=data.get('law_type'),
        tender_types=data.get('tender_types', []),
        is_active=True,
    )

    if data.get('ai_intent') and filter_id:
        await db.update_filter(filter_id, ai_intent=data['ai_intent'])

    return web.json_response({'ok': True, 'filter_id': filter_id})
```

- [ ] **Step 5: `delete_filter`, `toggle_filter` — ownership check by company**

Replace lines 348-380 (both functions, same pattern as Step 3 — swap decorator to `@require_team_member`, `user = request['user']` → `company = request['company']`, and the ownership check to `filter_data.get('company_id') != company['id']`):

```python
@require_team_member
async def delete_filter(request: web.Request) -> web.Response:
    """DELETE /cabinet/api/filters/:id — мягкое удаление фильтра."""
    company = request['company']
    filter_id = int(request.match_info['id'])

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()

    filter_data = await db.get_filter_by_id(filter_id)
    if not filter_data or filter_data.get('company_id') != company['id']:
        return web.json_response({'error': 'Filter not found'}, status=404)

    await db.delete_filter(filter_id)
    return web.json_response({'ok': True})


@require_team_member
async def toggle_filter(request: web.Request) -> web.Response:
    """POST /cabinet/api/filters/:id/toggle — переключить is_active."""
    company = request['company']
    filter_id = int(request.match_info['id'])

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()

    filter_data = await db.get_filter_by_id(filter_id)
    if not filter_data or filter_data.get('company_id') != company['id']:
        return web.json_response({'error': 'Filter not found'}, status=404)

    new_state = not filter_data.get('is_active', True)
    await db.update_filter(filter_id, is_active=new_state)
    return web.json_response({'ok': True, 'is_active': new_state})
```

- [ ] **Step 6: `get_filter_notify_targets`, `update_filter_notify_targets` — ownership check by company**

Replace lines 383-467. Only the decorator and the ownership-check line change in each (`user['user_id']` → `company['id']` comparison against `filter_data.get('company_id')`); the personal-chat/group-chat logic inside stays tied to the *requesting* user's own Telegram identity (`user['telegram_id']`), since notify targets are still concrete chat ids, not company concepts:

```python
@require_team_member
async def get_filter_notify_targets(request: web.Request) -> web.Response:
    """GET /cabinet/api/filters/:id/notify-targets — куда направляются уведомления."""
    user = request['user']
    company = request['company']
    filter_id = int(request.match_info['id'])

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()

    filter_data = await db.get_filter_by_id(filter_id)
    if not filter_data or filter_data.get('company_id') != company['id']:
        return web.json_response({'error': 'Filter not found'}, status=404)

    current_targets = filter_data.get('notify_chat_ids') or []
    user_tg_id = user['telegram_id']

    personal_enabled = (not current_targets) or (user_tg_id in current_targets)

    groups = await db.get_user_groups(user_tg_id)
    groups_payload = [
        {
            'chat_id': g['telegram_id'],
            'name': g['name'],
            'enabled': g['telegram_id'] in current_targets,
        }
        for g in groups
    ]

    return web.json_response({
        'personal': {'chat_id': user_tg_id, 'enabled': personal_enabled},
        'groups': groups_payload,
    })


@require_team_member
async def update_filter_notify_targets(request: web.Request) -> web.Response:
    """POST /cabinet/api/filters/:id/notify-targets — сохранить таргеты."""
    user = request['user']
    company = request['company']
    filter_id = int(request.match_info['id'])

    try:
        data = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    chat_ids = data.get('chat_ids')
    if not isinstance(chat_ids, list):
        return web.json_response({'error': 'chat_ids must be a list'}, status=400)

    try:
        chat_ids = [int(cid) for cid in chat_ids]
    except (TypeError, ValueError):
        return web.json_response({'error': 'chat_ids must contain integers'}, status=400)

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()

    filter_data = await db.get_filter_by_id(filter_id)
    if not filter_data or filter_data.get('company_id') != company['id']:
        return web.json_response({'error': 'Filter not found'}, status=404)

    user_tg_id = user['telegram_id']
    valid_chat_ids = {user_tg_id}
    for g in await db.get_user_groups(user_tg_id):
        valid_chat_ids.add(g['telegram_id'])

    invalid = [cid for cid in chat_ids if cid not in valid_chat_ids]
    if invalid:
        return web.json_response(
            {'error': f'Chat IDs not allowed: {invalid}'}, status=403
        )

    deduped = list(dict.fromkeys(chat_ids))
    is_default = not deduped or deduped == [user_tg_id]
    await db.update_filter(filter_id, notify_chat_ids=None if is_default else deduped)

    return web.json_response({'ok': True})
```

- [ ] **Step 7: Add the `company_switch` endpoint**

After the pipeline API section (around line 1144, right after `pipeline_create_manual`), add:

```python
@require_team_member
async def company_switch(request: web.Request) -> web.Response:
    """POST /cabinet/api/company/switch — переключить активный воркспейс."""
    user = request['user']
    try:
        data = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    try:
        company_id = int(data.get('company_id'))
    except (TypeError, ValueError):
        return web.json_response({'error': 'company_id required'}, status=400)

    from cabinet.team_service import list_companies_for_user
    companies = await list_companies_for_user(user['user_id'])
    if not any(c['id'] == company_id for c in companies):
        return web.json_response({'error': 'Not a member of this company'}, status=403)

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()
    await db.set_session_active_company(user['session_token'], company_id)

    return web.json_response({'ok': True, 'company_id': company_id})
```

- [ ] **Step 8: Manual verification**

Run the bot locally against the Task 1 local DB. As the single-company smoke-test account: `GET /cabinet/api/filters` should return the same filters as before the change (now via `company_id`), and creating/toggling/deleting a filter through the cabinet UI should work exactly as before.

- [ ] **Step 9: Commit**

```bash
git add cabinet/api.py
git commit -m "feat: company-scope cabinet filter endpoints, add workspace switch API

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: routes.py — register switch route + expose workspaces in template context

**Files:**
- Modify: `cabinet/routes.py:24-34` (`_global_ctx_processor`)
- Modify: `cabinet/routes.py` (route registration, near line 155)

**Interfaces:**
- Consumes: `team_service.list_companies_for_user`, `team_service.get_active_company` (Task 3); `api.company_switch` (Task 5).
- Produces: Jinja2 context vars `workspaces` (list, only non-empty when the user has >1 membership) and `active_company_id`, available in every cabinet template — Task 7 consumes these in `_sidebar.html`.

- [ ] **Step 1: Extend `_global_ctx_processor`**

Replace `cabinet/routes.py:24-34`:

```python
async def _global_ctx_processor(request):
    """Глобальный context для всех шаблонов: is_admin_user, ADMIN_USER_ID, workspaces."""
    is_admin = False
    workspaces = []
    active_company_id = None
    try:
        user = await get_current_user(request)
        if user:
            admin_id = int(os.getenv('ADMIN_USER_ID') or os.getenv('ADMIN_TELEGRAM_ID') or '0')
            is_admin = bool(admin_id and user.get('telegram_id') == admin_id)

            from cabinet.team_service import list_companies_for_user, get_active_company
            memberships = await list_companies_for_user(user['user_id'])
            if len(memberships) > 1:
                workspaces = memberships
                active = await get_active_company(user['user_id'], user.get('session_token'))
                active_company_id = active['id'] if active else None
    except Exception:
        pass
    return {
        'is_admin_user': is_admin,
        'workspaces': workspaces,
        'active_company_id': active_company_id,
    }
```

- [ ] **Step 2: Register the switch route**

Near the other `/cabinet/api/team/*` routes (`cabinet/routes.py`, around line 155), add:

```python
    app.router.add_post('/cabinet/api/company/switch', api.company_switch)
```

- [ ] **Step 3: Manual verification**

Load any cabinet page as the single-company smoke-test account — response should render identically to before (no switcher, since `workspaces` stays `[]` for a single membership).

- [ ] **Step 4: Commit**

```bash
git add cabinet/routes.py
git commit -m "feat: wire workspace switcher context + switch route

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: _sidebar.html — workspace switcher UI

**Files:**
- Modify: `cabinet/templates/_sidebar.html:60-66`

**Interfaces:**
- Consumes: `workspaces`, `active_company_id` template context (Task 6); posts to `/cabinet/api/company/switch` (Task 5).

- [ ] **Step 1: Add the switcher, gated on `workspaces`**

Replace `cabinet/templates/_sidebar.html:60-66`:

```html
  <div class="user">
    <div class="avatar">{{ (user_name or 'U')[0] | upper }}</div>
    <div class="meta">
      <div class="name">{{ user_name or 'Пользователь' }}</div>
      <div class="tier">{{ user_tier or '' }}</div>
    </div>
  </div>

  {% if workspaces %}
  <div class="workspace-switcher">
    <select id="workspace-select" onchange="switchWorkspace(this.value)">
      {% for w in workspaces %}
      <option value="{{ w.id }}" {% if w.id == active_company_id %}selected{% endif %}>{{ w.name }}</option>
      {% endfor %}
    </select>
  </div>
  <script>
    async function switchWorkspace(companyId) {
      const res = await fetch('/cabinet/api/company/switch', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({company_id: parseInt(companyId, 10)}),
      });
      if (res.ok) {
        window.location.reload();
      }
    }
  </script>
  {% endif %}
</aside>
```

(Note: the original file's closing `</aside>` at line 67 is now folded into this replacement — don't leave a duplicate.)

- [ ] **Step 2: Manual verification**

Load `/cabinet/` as the single-company smoke-test account — sidebar should render with no switcher (unchanged from before this task). This can only be visually confirmed for the >1-membership case after Task 8 creates a second company for the owner.

- [ ] **Step 3: Commit**

```bash
git add cabinet/templates/_sidebar.html
git commit -m "feat: workspace switcher UI in cabinet sidebar

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: Bootstrap the second (isolated) workspace

**Files:**
- Create: `scripts/bootstrap_second_workspace.py`

**Interfaces:**
- Consumes: `Company`, `CompanyMember`, `SniperFilter`, `SniperUser` models with `company_id` (Task 1).
- Produces: a new `Company` row, an `owner`-role `CompanyMember` row for the account owner, and copies of the source company's active filters under the new `company_id` with `notify_chat_ids` pointed at the new group chat.

- [ ] **Step 1: Write the script**

Create `scripts/bootstrap_second_workspace.py`, following the existing seeder convention (`scripts/seed_filter_mri_ct_2026_06.py`) but with every sensitive value (company name, notify chat id, owner telegram id) taken as required CLI arguments — never hardcoded, since this file is committed to the repo:

```python
"""
Bootstrap второго (изолированного) воркспейса: новая Company + owner
membership + копия активных фильтров текущей компании с новым
notify_chat_ids.

Ничего чувствительного (название компании, chat_id, кто владелец) не
хардкодится — всё аргументами командной строки.

Запуск:
  cd ~/Desktop/tender-ai-bot-fresh
  python -m scripts.bootstrap_second_workspace \
      --owner-telegram-id <telegram_id> \
      --company-name "<name>" \
      --notify-chat-id <chat_id> \
      --dry                                    # план без записи

  # затем без --dry для боевого запуска
"""
import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

from sqlalchemy import select

from database import DatabaseSession, SniperUser, SniperFilter, Company, CompanyMember

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("bootstrap_second_workspace")


async def main(owner_telegram_id: int, company_name: str, notify_chat_id: int, dry: bool = False) -> None:
    async with DatabaseSession() as session:
        owner = await session.scalar(
            select(SniperUser).where(SniperUser.telegram_id == owner_telegram_id)
        )
        if not owner:
            log.error("owner telegram_id=%s не найден", owner_telegram_id)
            return

        existing = await session.scalar(select(Company).where(Company.name == company_name))
        if existing:
            log.info("[skip] компания '%s' уже существует (id=%s)", company_name, existing.id)
            return

        source_membership = await session.scalar(
            select(CompanyMember)
            .where(CompanyMember.user_id == owner.id)
            .order_by(CompanyMember.joined_at)
            .limit(1)
        )
        if not source_membership:
            log.error("у owner нет ни одной компании — сначала откройте /cabinet")
            return
        source_company_id = source_membership.company_id

        source_filters = (
            await session.scalars(
                select(SniperFilter).where(
                    SniperFilter.company_id == source_company_id,
                    SniperFilter.is_active == True,
                    SniperFilter.deleted_at.is_(None),
                )
            )
        ).all()

        log.info(
            "новая компания '%s', источник company_id=%s, фильтров к копированию=%d",
            company_name, source_company_id, len(source_filters),
        )
        for f in source_filters:
            log.info("  + '%s'", f.name)

        if dry:
            log.info("DRY: компания и %d фильтр(ов) не записаны", len(source_filters))
            return

        new_company = Company(name=company_name, owner_user_id=owner.id)
        session.add(new_company)
        await session.flush()

        session.add(CompanyMember(company_id=new_company.id, user_id=owner.id, role='owner'))

        for f in source_filters:
            session.add(SniperFilter(
                user_id=owner.id,
                company_id=new_company.id,
                name=f.name,
                keywords=f.keywords,
                exclude_keywords=f.exclude_keywords,
                price_min=f.price_min,
                price_max=f.price_max,
                regions=f.regions,
                customer_types=f.customer_types,
                tender_types=f.tender_types,
                law_type=f.law_type,
                purchase_stage=f.purchase_stage,
                purchase_method=f.purchase_method,
                okpd2_codes=f.okpd2_codes,
                min_deadline_days=f.min_deadline_days,
                customer_keywords=f.customer_keywords,
                exact_match=f.exact_match,
                purchase_number=f.purchase_number,
                customer_inn=f.customer_inn,
                excluded_customer_inns=f.excluded_customer_inns,
                excluded_customer_keywords=f.excluded_customer_keywords,
                execution_regions=f.execution_regions,
                publication_days=f.publication_days,
                primary_keywords=f.primary_keywords,
                secondary_keywords=f.secondary_keywords,
                search_in=f.search_in,
                ai_intent=f.ai_intent,
                expanded_keywords=f.expanded_keywords,
                notify_chat_ids=[notify_chat_id],
                is_active=True,
            ))

        await session.commit()
        log.info(
            "OK: компания id=%s создана, скопировано %d фильтр(ов). "
            "Пригласить людей: /cabinet/team в кабинете, переключившись на этот воркспейс.",
            new_company.id, len(source_filters),
        )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--owner-telegram-id", type=int, required=True)
    p.add_argument("--company-name", type=str, required=True)
    p.add_argument("--notify-chat-id", type=int, required=True)
    p.add_argument("--dry", action="store_true", help="dry-run без записи в БД")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(
        owner_telegram_id=args.owner_telegram_id,
        company_name=args.company_name,
        notify_chat_id=args.notify_chat_id,
        dry=args.dry,
    ))
```

- [ ] **Step 2: Dry-run against the local DB**

```bash
python -m scripts.bootstrap_second_workspace \
    --owner-telegram-id <telegram_id> --company-name "<name>" --notify-chat-id <chat_id> --dry
```

Expected: logs the list of filters that would be copied, no DB writes (re-running Step 2 afterward gives the exact same list — confirms nothing was written).

- [ ] **Step 3: Commit the script (not any real run output)**

```bash
git add scripts/bootstrap_second_workspace.py
git commit -m "feat: bootstrap script for a second isolated company workspace

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

- [ ] **Step 4: Real run — do this manually, outside the plan/commit history**

Once Tasks 1-7 are deployed to production: run the script for real (no `--dry`) with the actual company name, chat id, and telegram id as CLI arguments — do not paste these into any commit, PR description, or plan file. Then, in the cabinet, switch to the new workspace (Task 7's switcher) and use the existing `/cabinet/team` invite flow to add people.

---

## Self-Review Notes

- **Spec coverage:** Data model (Task 1), cabinet RBAC (Tasks 3-6), matching engine company stamping (Task 2 Step 5), Bitrix/kanban (no task — already correct, confirmed in spec), second-workspace bootstrap (Task 8). All spec sections have a task.
- **Type consistency checked:** `get_active_company` (Task 3) is the exact name called in Task 4 and Task 6. `get_company_filters` (Task 2) is the exact name called in Task 5. `set_session_active_company` (Task 2) matches Task 5's `company_switch`. `add_member_to_company` (Task 3) is defined for Task 8's manual invite step (used ad hoc, not scripted, since only the owner needs it and the cabinet UI already covers regular invites).
- **No placeholders:** every step has literal code, not descriptions of code.

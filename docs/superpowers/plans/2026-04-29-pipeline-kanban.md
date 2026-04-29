# Pipeline / Kanban Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Реализовать собственный Kanban-pipeline в `/cabinet/pipeline` для замены Bitrix24 как CRM. Доска на 6 стадий, team workspace на 5-6 человек, миграция из Bitrix.

**Architecture:** Server-render через aiohttp-jinja2 + vanilla JS с Sortable.js для drag-n-drop, optimistic UI на drop. SQLAlchemy async на PostgreSQL, 8 новых таблиц. Файлы в Railway Volume `/app/uploads`. Без live-обновлений (F5 для синка).

**Tech Stack:** Python 3.11, aiogram 3.x (для существующего бота, но pipeline не использует), aiohttp + aiohttp-jinja2 (кабинет), SQLAlchemy 2.x async, Alembic, vanilla JS + Sortable.js (vendored), pytest для тестов.

**Spec:** `docs/superpowers/specs/2026-04-27-pipeline-kanban-design.md`

**Phases (~15 рабочих дней суммарно):**
1. Foundation: DB models + Alembic migration
2. Team workspace (Company/Member/Invite)
3. Pipeline core (cards CRUD, board UI, drag)
4. Card modal + tabs (details/notes/history)
5. Files + checklist + relations
6. AI enrichment + Owner dashboard
7. Bitrix migration script + archive background job
8. Polish + e2e smoke

---

## File Structure

**Создаётся:**

| Файл | Назначение |
|---|---|
| `alembic/versions/20260429_pipeline_tables.py` | Alembic миграция (8 таблиц) |
| `database.py` (изменения) | 8 новых model classes |
| `cabinet/pipeline_service.py` | Доменная логика pipeline (move, set_result, archive) |
| `cabinet/team_service.py` | Доменная логика team (invite tokens, member CRUD, dashboard agg) |
| `cabinet/templates/pipeline.html` | Доска (server-render) |
| `cabinet/templates/pipeline_archive.html` | Архив lose-карточек |
| `cabinet/templates/team.html` | Owner dashboard + управление командой |
| `cabinet/templates/invite.html` | Приём инвайта |
| `cabinet/templates/_modal_card.html` | Общая модалка карточки |
| `cabinet/static/css/pages/pipeline.css` | Стили доски + модалки |
| `cabinet/static/css/pages/team.css` | Стили team page |
| `cabinet/static/css/pages/invite.css` | Стили invite page |
| `cabinet/static/js/pages/pipeline.js` | Drag, optimistic UI, модалка |
| `cabinet/static/js/pages/team.js` | Метрики, инвайт-ссылки |
| `cabinet/static/js/vendor/Sortable.min.js` | Sortable.js (vendored) |
| `scripts/migrate_bitrix_to_pipeline.py` | One-shot миграция |
| `tender_sniper/jobs/archive_lost_cards.py` | Background job (раз в день) |
| `tests/unit/test_pipeline_service.py` | Unit-тесты move/result/archive |
| `tests/unit/test_team_service.py` | Unit-тесты invites/members |
| `tests/unit/test_pipeline_rbac.py` | RBAC checks |
| `tests/integration/test_pipeline_e2e.py` | E2E flow + Bitrix migration smoke |

**Изменяется:**

| Файл | Изменения |
|---|---|
| `cabinet/api.py` | +~22 новых endpoint (pipeline + team) |
| `cabinet/routes.py` | Регистрация новых маршрутов |
| `cabinet/auth.py` | Helper-middleware: `require_team_member`, `require_owner` |
| `cabinet/templates/_sidebar.html` | Пункты «Pipeline», «Команда» |
| `cabinet/templates/dashboard.html` | Кнопка «В работу» на карточке тендера |
| `cabinet/static/js/dashboard.js` | Обработчик кнопки «В работу» |
| `bot/main.py` | Регистрация archive job в startup |

---

## Phase 1 — Foundation: DB models + Alembic

**Цель фазы:** 8 новых таблиц созданы, модели определены, миграция применяется чисто.

### Task 1.1: Добавить модели в `database.py`

**Files:**
- Modify: `database.py` (добавить 8 классов в конец, перед `__all__`)

- [ ] **Step 1: Открыть `database.py`, найти место перед последним классом, добавить импорт `Numeric`**

В `database.py` импорт уже включает большинство нужного. Проверить наличие в строке импортов SQLAlchemy: `Numeric`. Если нет — добавить.

```python
from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Boolean, DateTime, Date,
    ForeignKey, JSON, Index, UniqueConstraint, Numeric,
)
```

- [ ] **Step 2: Добавить 8 классов моделей в конец файла (перед `Base.metadata` если он там есть; иначе просто в конец)**

```python
class Company(Base):
    __tablename__ = 'companies'
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    owner_user_id = Column(Integer, ForeignKey('sniper_users.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class CompanyMember(Base):
    __tablename__ = 'company_members'
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('sniper_users.id'), nullable=False)
    role = Column(String(16), nullable=False)
    joined_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint('user_id', name='uq_company_members_user'),
        Index('ix_company_members_company', 'company_id'),
    )


class TeamInvite(Base):
    __tablename__ = 'team_invites'
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False)
    token = Column(String(64), nullable=False, unique=True)
    created_by = Column(Integer, ForeignKey('sniper_users.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    max_uses = Column(Integer, default=10, nullable=False)
    used_count = Column(Integer, default=0, nullable=False)


class PipelineCard(Base):
    __tablename__ = 'pipeline_cards'
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False, index=True)
    tender_number = Column(String(40), nullable=False, index=True)
    stage = Column(String(20), nullable=False, default='FOUND')
    assignee_user_id = Column(Integer, ForeignKey('sniper_users.id'), nullable=True)
    filter_id = Column(Integer, ForeignKey('sniper_filters.id'), nullable=True)
    source = Column(String(20), nullable=False, default='feed')
    result = Column(String(10), nullable=True)
    purchase_price = Column(Numeric(14, 2), nullable=True)
    sale_price = Column(Numeric(14, 2), nullable=True)
    ai_summary = Column(Text, nullable=True)
    ai_recommendation = Column(String(40), nullable=True)
    ai_enriched_at = Column(DateTime, nullable=True)
    archived_at = Column(DateTime, nullable=True)
    data = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(Integer, ForeignKey('sniper_users.id'), nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint('company_id', 'tender_number', name='uq_pipeline_company_tender'),
        Index('ix_pipeline_company_stage', 'company_id', 'stage'),
        Index('ix_pipeline_company_archived', 'company_id', 'archived_at'),
    )


class PipelineCardHistory(Base):
    __tablename__ = 'pipeline_card_history'
    id = Column(Integer, primary_key=True)
    card_id = Column(Integer, ForeignKey('pipeline_cards.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('sniper_users.id'), nullable=False)
    action = Column(String(40), nullable=False)
    payload = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class PipelineCardNote(Base):
    __tablename__ = 'pipeline_card_notes'
    id = Column(Integer, primary_key=True)
    card_id = Column(Integer, ForeignKey('pipeline_cards.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('sniper_users.id'), nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class PipelineCardFile(Base):
    __tablename__ = 'pipeline_card_files'
    id = Column(Integer, primary_key=True)
    card_id = Column(Integer, ForeignKey('pipeline_cards.id', ondelete='CASCADE'), nullable=False, index=True)
    uploaded_by = Column(Integer, ForeignKey('sniper_users.id'), nullable=False)
    filename = Column(String(255), nullable=False)
    size = Column(Integer, nullable=False)
    mime_type = Column(String(100), nullable=False)
    path = Column(String(500), nullable=False)
    is_generated = Column(Boolean, default=False, nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class PipelineCardChecklist(Base):
    __tablename__ = 'pipeline_card_checklist'
    id = Column(Integer, primary_key=True)
    card_id = Column(Integer, ForeignKey('pipeline_cards.id', ondelete='CASCADE'), nullable=False, index=True)
    text = Column(String(500), nullable=False)
    done = Column(Boolean, default=False, nullable=False)
    position = Column(Integer, default=0, nullable=False)
    created_by = Column(Integer, ForeignKey('sniper_users.id'), nullable=False)
    done_by = Column(Integer, ForeignKey('sniper_users.id'), nullable=True)
    done_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class PipelineCardRelation(Base):
    __tablename__ = 'pipeline_card_relations'
    id = Column(Integer, primary_key=True)
    card_id = Column(Integer, ForeignKey('pipeline_cards.id', ondelete='CASCADE'), nullable=False, index=True)
    related_card_id = Column(Integer, ForeignKey('pipeline_cards.id', ondelete='CASCADE'), nullable=False)
    kind = Column(String(40), nullable=False)
    created_by = Column(Integer, ForeignKey('sniper_users.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint('card_id', 'related_card_id', name='uq_card_relation'),
    )
```

- [ ] **Step 3: Sanity-проверка синтаксиса**

Run: `python -c "import database; print('OK')"`
Expected: prints `OK`. Если падает с ImportError для `Numeric` — вернуться к Step 1.

- [ ] **Step 4: Commit**

```bash
git add database.py
git commit -m "feat(pipeline): add 8 SQLAlchemy models for kanban tables"
```

### Task 1.2: Создать Alembic миграцию

**Files:**
- Create: `alembic/versions/20260429_pipeline_tables.py`

- [ ] **Step 1: Сгенерировать миграцию вручную (autogenerate ненадёжен с async)**

Создать файл `alembic/versions/20260429_pipeline_tables.py`:

```python
"""Add pipeline tables: companies, company_members, team_invites,
pipeline_cards, pipeline_card_history, pipeline_card_notes,
pipeline_card_files, pipeline_card_checklist, pipeline_card_relations.

Revision ID: 20260429_pipeline
Revises: <PREVIOUS_HEAD>
Create Date: 2026-04-29
"""
from alembic import op
import sqlalchemy as sa

revision = '20260429_pipeline'
down_revision = None  # ВАЖНО: заменить на актуальный head перед применением (см. Step 2)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'companies',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('owner_user_id', sa.Integer, sa.ForeignKey('sniper_users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        'company_members',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('company_id', sa.Integer, sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('sniper_users.id'), nullable=False),
        sa.Column('role', sa.String(16), nullable=False),
        sa.Column('joined_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('user_id', name='uq_company_members_user'),
    )
    op.create_index('ix_company_members_company', 'company_members', ['company_id'])

    op.create_table(
        'team_invites',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('company_id', sa.Integer, sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('token', sa.String(64), nullable=False, unique=True),
        sa.Column('created_by', sa.Integer, sa.ForeignKey('sniper_users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime, nullable=False),
        sa.Column('revoked_at', sa.DateTime, nullable=True),
        sa.Column('max_uses', sa.Integer, nullable=False, server_default='10'),
        sa.Column('used_count', sa.Integer, nullable=False, server_default='0'),
    )

    op.create_table(
        'pipeline_cards',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('company_id', sa.Integer, sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('tender_number', sa.String(40), nullable=False),
        sa.Column('stage', sa.String(20), nullable=False, server_default='FOUND'),
        sa.Column('assignee_user_id', sa.Integer, sa.ForeignKey('sniper_users.id'), nullable=True),
        sa.Column('filter_id', sa.Integer, sa.ForeignKey('sniper_filters.id'), nullable=True),
        sa.Column('source', sa.String(20), nullable=False, server_default='feed'),
        sa.Column('result', sa.String(10), nullable=True),
        sa.Column('purchase_price', sa.Numeric(14, 2), nullable=True),
        sa.Column('sale_price', sa.Numeric(14, 2), nullable=True),
        sa.Column('ai_summary', sa.Text, nullable=True),
        sa.Column('ai_recommendation', sa.String(40), nullable=True),
        sa.Column('ai_enriched_at', sa.DateTime, nullable=True),
        sa.Column('archived_at', sa.DateTime, nullable=True),
        sa.Column('data', sa.JSON, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('created_by', sa.Integer, sa.ForeignKey('sniper_users.id'), nullable=False),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('company_id', 'tender_number', name='uq_pipeline_company_tender'),
    )
    op.create_index('ix_pipeline_cards_company_id', 'pipeline_cards', ['company_id'])
    op.create_index('ix_pipeline_cards_tender_number', 'pipeline_cards', ['tender_number'])
    op.create_index('ix_pipeline_company_stage', 'pipeline_cards', ['company_id', 'stage'])
    op.create_index('ix_pipeline_company_archived', 'pipeline_cards', ['company_id', 'archived_at'])

    op.create_table(
        'pipeline_card_history',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('card_id', sa.Integer, sa.ForeignKey('pipeline_cards.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('sniper_users.id'), nullable=False),
        sa.Column('action', sa.String(40), nullable=False),
        sa.Column('payload', sa.JSON, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_pipeline_card_history_card_id', 'pipeline_card_history', ['card_id'])

    op.create_table(
        'pipeline_card_notes',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('card_id', sa.Integer, sa.ForeignKey('pipeline_cards.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('sniper_users.id'), nullable=False),
        sa.Column('text', sa.Text, nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_pipeline_card_notes_card_id', 'pipeline_card_notes', ['card_id'])

    op.create_table(
        'pipeline_card_files',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('card_id', sa.Integer, sa.ForeignKey('pipeline_cards.id', ondelete='CASCADE'), nullable=False),
        sa.Column('uploaded_by', sa.Integer, sa.ForeignKey('sniper_users.id'), nullable=False),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('size', sa.Integer, nullable=False),
        sa.Column('mime_type', sa.String(100), nullable=False),
        sa.Column('path', sa.String(500), nullable=False),
        sa.Column('is_generated', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('uploaded_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_pipeline_card_files_card_id', 'pipeline_card_files', ['card_id'])

    op.create_table(
        'pipeline_card_checklist',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('card_id', sa.Integer, sa.ForeignKey('pipeline_cards.id', ondelete='CASCADE'), nullable=False),
        sa.Column('text', sa.String(500), nullable=False),
        sa.Column('done', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('position', sa.Integer, nullable=False, server_default='0'),
        sa.Column('created_by', sa.Integer, sa.ForeignKey('sniper_users.id'), nullable=False),
        sa.Column('done_by', sa.Integer, sa.ForeignKey('sniper_users.id'), nullable=True),
        sa.Column('done_at', sa.DateTime, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_pipeline_card_checklist_card_id', 'pipeline_card_checklist', ['card_id'])

    op.create_table(
        'pipeline_card_relations',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('card_id', sa.Integer, sa.ForeignKey('pipeline_cards.id', ondelete='CASCADE'), nullable=False),
        sa.Column('related_card_id', sa.Integer, sa.ForeignKey('pipeline_cards.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(40), nullable=False),
        sa.Column('created_by', sa.Integer, sa.ForeignKey('sniper_users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('card_id', 'related_card_id', name='uq_card_relation'),
    )
    op.create_index('ix_pipeline_card_relations_card_id', 'pipeline_card_relations', ['card_id'])


def downgrade() -> None:
    op.drop_table('pipeline_card_relations')
    op.drop_table('pipeline_card_checklist')
    op.drop_table('pipeline_card_files')
    op.drop_table('pipeline_card_notes')
    op.drop_table('pipeline_card_history')
    op.drop_table('pipeline_cards')
    op.drop_table('team_invites')
    op.drop_table('company_members')
    op.drop_table('companies')
```

- [ ] **Step 2: Узнать актуальный alembic head и подставить в `down_revision`**

Run: `alembic heads`
Скопировать revision id (например, `20260418_email_columns`) и заменить `down_revision = None` на `down_revision = '<этот id>'`.

- [ ] **Step 3: Применить миграцию локально (если есть локальная БД)**

Run: `alembic upgrade head`
Expected: `Running upgrade <prev> -> 20260429_pipeline, ...`. Без ошибок.

Если локальной БД нет — пропустить, проверим в smoke-test после деплоя.

- [ ] **Step 4: Откатить и вновь применить — проверка downgrade**

Run: `alembic downgrade -1 && alembic upgrade head`
Expected: обе команды без ошибок.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/20260429_pipeline_tables.py
git commit -m "feat(pipeline): alembic migration for 8 kanban tables"
```

### Task 1.3: Module exports + smoke import

**Files:**
- Modify: `database.py` (если есть `__all__`, добавить новые модели)

- [ ] **Step 1: Если в `database.py` есть `__all__` — добавить новые имена**

Найти `__all__ = [...]`. Если есть — добавить:

```python
__all__ = [
    # ... существующие ...
    'Company', 'CompanyMember', 'TeamInvite', 'PipelineCard',
    'PipelineCardHistory', 'PipelineCardNote', 'PipelineCardFile',
    'PipelineCardChecklist', 'PipelineCardRelation',
]
```

Если `__all__` отсутствует — пропустить шаг.

- [ ] **Step 2: Smoke-тест импорта всех моделей**

Run:
```bash
python -c "from database import Company, CompanyMember, TeamInvite, PipelineCard, PipelineCardHistory, PipelineCardNote, PipelineCardFile, PipelineCardChecklist, PipelineCardRelation; print('all imports OK')"
```
Expected: `all imports OK`.

- [ ] **Step 3: Commit (если были изменения)**

```bash
git add database.py
git commit -m "chore(pipeline): export new models in database.__all__"
```

---

## Phase 2 — Team workspace

**Цель фазы:** Юзер может быть в команде, owner создаёт инвайты, member присоединяется по ссылке. RBAC middleware готов.

### Task 2.1: `cabinet/team_service.py` — базовые операции (TDD)

**Files:**
- Create: `cabinet/team_service.py`
- Create: `tests/unit/test_team_service.py`

- [ ] **Step 1: Написать failing test для `get_or_create_company_for_user`**

Создать `tests/unit/test_team_service.py`:

```python
import pytest
from datetime import datetime
from cabinet.team_service import (
    get_or_create_company_for_user, get_company_for_user, list_members,
)


@pytest.mark.asyncio
async def test_get_or_create_creates_new_company_for_solo_user(db_session, make_user):
    user = await make_user(first_name='Solo')
    company = await get_or_create_company_for_user(user['id'])
    assert company['owner_user_id'] == user['id']
    assert 'Solo' in company['name']

    members = await list_members(company['id'])
    assert len(members) == 1
    assert members[0]['user_id'] == user['id']
    assert members[0]['role'] == 'owner'


@pytest.mark.asyncio
async def test_get_or_create_returns_existing_for_member(db_session, make_user):
    owner = await make_user(first_name='Boss')
    company = await get_or_create_company_for_user(owner['id'])
    same = await get_or_create_company_for_user(owner['id'])
    assert same['id'] == company['id']


@pytest.mark.asyncio
async def test_get_company_for_user_returns_none_if_no_membership(db_session, make_user):
    user = await make_user()
    assert await get_company_for_user(user['id']) is None
```

- [ ] **Step 2: Run test — должно упасть с ImportError**

Run: `pytest tests/unit/test_team_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cabinet.team_service'`.

- [ ] **Step 3: Создать `cabinet/team_service.py` с минимальной реализацией**

```python
"""Доменная логика team workspace.

Все функции принимают и возвращают plain dicts. Сессия БД создаётся через
DatabaseSession (как в database/sqlalchemy_adapter.py)."""

import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import secrets

from sqlalchemy import select, update, func
from sqlalchemy.exc import IntegrityError

from database import (
    DatabaseSession, SniperUser, Company, CompanyMember, TeamInvite,
)

logger = logging.getLogger(__name__)


def _company_dict(company: Company) -> Dict:
    return {
        'id': company.id,
        'name': company.name,
        'owner_user_id': company.owner_user_id,
        'created_at': company.created_at,
    }


def _member_dict(member: CompanyMember) -> Dict:
    return {
        'id': member.id,
        'company_id': member.company_id,
        'user_id': member.user_id,
        'role': member.role,
        'joined_at': member.joined_at,
    }


async def get_company_for_user(user_id: int) -> Optional[Dict]:
    """Возвращает company юзера или None если он не в команде."""
    async with DatabaseSession() as session:
        membership = await session.scalar(
            select(CompanyMember).where(CompanyMember.user_id == user_id)
        )
        if not membership:
            return None
        company = await session.get(Company, membership.company_id)
        return _company_dict(company) if company else None


async def get_or_create_company_for_user(user_id: int) -> Dict:
    """Возвращает существующую company юзера или создаёт новую с ним как owner."""
    existing = await get_company_for_user(user_id)
    if existing:
        return existing

    async with DatabaseSession() as session:
        user = await session.get(SniperUser, user_id)
        name_base = (user.first_name if user and user.first_name else f'User {user_id}')
        company = Company(name=f'Команда {name_base}', owner_user_id=user_id)
        session.add(company)
        await session.flush()
        member = CompanyMember(company_id=company.id, user_id=user_id, role='owner')
        session.add(member)
        await session.commit()
        return _company_dict(company)


async def list_members(company_id: int) -> List[Dict]:
    async with DatabaseSession() as session:
        result = await session.execute(
            select(CompanyMember).where(CompanyMember.company_id == company_id)
            .order_by(CompanyMember.joined_at)
        )
        return [_member_dict(m) for m in result.scalars().all()]
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_team_service.py -v`
Expected: 3 passed. (Может потребовать pytest fixtures — см. ниже.)

Если фикстур `db_session` или `make_user` нет — добавить в `tests/conftest.py`:

```python
import pytest
from database import DatabaseSession, SniperUser

@pytest.fixture
async def db_session():
    """Чистая сессия — зависит от существующей тест-БД (см. CLAUDE.md)."""
    async with DatabaseSession() as session:
        yield session

@pytest.fixture
async def make_user():
    counter = {'n': 0}
    async def _make(**kwargs):
        counter['n'] += 1
        async with DatabaseSession() as session:
            user = SniperUser(
                telegram_id=900000 + counter['n'],
                first_name=kwargs.get('first_name', f'TestUser{counter["n"]}'),
                subscription_tier='trial',
            )
            session.add(user)
            await session.commit()
            return {'id': user.id, 'telegram_id': user.telegram_id}
    return _make
```

- [ ] **Step 5: Commit**

```bash
git add cabinet/team_service.py tests/unit/test_team_service.py tests/conftest.py
git commit -m "feat(team): get_or_create_company_for_user + list_members"
```

### Task 2.2: Invite token CRUD (TDD)

**Files:**
- Modify: `cabinet/team_service.py`
- Modify: `tests/unit/test_team_service.py`

- [ ] **Step 1: Добавить failing tests для инвайтов**

В конец `tests/unit/test_team_service.py`:

```python
from cabinet.team_service import (
    create_invite, validate_invite_token, accept_invite, revoke_invite,
    list_active_invites,
)


@pytest.mark.asyncio
async def test_create_invite_returns_token(db_session, make_user):
    owner = await make_user()
    company = await get_or_create_company_for_user(owner['id'])
    invite = await create_invite(company['id'], created_by=owner['id'])
    assert len(invite['token']) >= 32
    assert invite['expires_at'] > datetime.utcnow()


@pytest.mark.asyncio
async def test_validate_token_returns_invite_when_valid(db_session, make_user):
    owner = await make_user()
    company = await get_or_create_company_for_user(owner['id'])
    invite = await create_invite(company['id'], created_by=owner['id'])
    found = await validate_invite_token(invite['token'])
    assert found is not None
    assert found['company_id'] == company['id']


@pytest.mark.asyncio
async def test_validate_token_returns_none_when_revoked(db_session, make_user):
    owner = await make_user()
    company = await get_or_create_company_for_user(owner['id'])
    invite = await create_invite(company['id'], created_by=owner['id'])
    await revoke_invite(invite['id'], by_user_id=owner['id'])
    assert await validate_invite_token(invite['token']) is None


@pytest.mark.asyncio
async def test_accept_invite_adds_member(db_session, make_user):
    owner = await make_user()
    company = await get_or_create_company_for_user(owner['id'])
    invite = await create_invite(company['id'], created_by=owner['id'])

    new_user = await make_user()
    result = await accept_invite(invite['token'], new_user['id'])
    assert result['ok'] is True
    members = await list_members(company['id'])
    assert any(m['user_id'] == new_user['id'] and m['role'] == 'member' for m in members)


@pytest.mark.asyncio
async def test_accept_invite_rejects_user_in_other_company(db_session, make_user):
    owner_a = await make_user()
    company_a = await get_or_create_company_for_user(owner_a['id'])
    owner_b = await make_user()
    await get_or_create_company_for_user(owner_b['id'])  # owner_b уже в своей команде
    invite = await create_invite(company_a['id'], created_by=owner_a['id'])

    result = await accept_invite(invite['token'], owner_b['id'])
    assert result['ok'] is False
    assert 'already' in result['error'].lower() or 'команд' in result['error']
```

- [ ] **Step 2: Run tests — должны упасть на ImportError**

Run: `pytest tests/unit/test_team_service.py -v -k invite`
Expected: ImportError на `create_invite`.

- [ ] **Step 3: Реализовать invite-функции в `cabinet/team_service.py`**

```python
INVITE_TTL_DAYS = 7
INVITE_DEFAULT_MAX_USES = 10


def _invite_dict(invite: TeamInvite) -> Dict:
    return {
        'id': invite.id,
        'company_id': invite.company_id,
        'token': invite.token,
        'created_by': invite.created_by,
        'created_at': invite.created_at,
        'expires_at': invite.expires_at,
        'revoked_at': invite.revoked_at,
        'max_uses': invite.max_uses,
        'used_count': invite.used_count,
    }


async def create_invite(company_id: int, created_by: int,
                        max_uses: int = INVITE_DEFAULT_MAX_USES) -> Dict:
    async with DatabaseSession() as session:
        token = secrets.token_urlsafe(24)
        invite = TeamInvite(
            company_id=company_id,
            token=token,
            created_by=created_by,
            expires_at=datetime.utcnow() + timedelta(days=INVITE_TTL_DAYS),
            max_uses=max_uses,
            used_count=0,
        )
        session.add(invite)
        await session.commit()
        return _invite_dict(invite)


async def validate_invite_token(token: str) -> Optional[Dict]:
    """Возвращает invite если валиден (не отозван, не expired, есть свободные uses)."""
    async with DatabaseSession() as session:
        invite = await session.scalar(select(TeamInvite).where(TeamInvite.token == token))
        if not invite:
            return None
        if invite.revoked_at is not None:
            return None
        if invite.expires_at < datetime.utcnow():
            return None
        if invite.used_count >= invite.max_uses:
            return None
        return _invite_dict(invite)


async def revoke_invite(invite_id: int, by_user_id: int) -> bool:
    async with DatabaseSession() as session:
        invite = await session.get(TeamInvite, invite_id)
        if not invite:
            return False
        invite.revoked_at = datetime.utcnow()
        await session.commit()
        return True


async def list_active_invites(company_id: int) -> List[Dict]:
    async with DatabaseSession() as session:
        result = await session.execute(
            select(TeamInvite).where(
                TeamInvite.company_id == company_id,
                TeamInvite.revoked_at.is_(None),
                TeamInvite.expires_at > datetime.utcnow(),
            ).order_by(TeamInvite.created_at.desc())
        )
        return [_invite_dict(i) for i in result.scalars().all()]


async def accept_invite(token: str, user_id: int) -> Dict:
    """Принять инвайт. Возвращает {ok: bool, error?: str, company_id?: int}."""
    invite = await validate_invite_token(token)
    if not invite:
        return {'ok': False, 'error': 'Ссылка недействительна или истекла'}

    existing_company = await get_company_for_user(user_id)
    if existing_company:
        if existing_company['id'] == invite['company_id']:
            return {'ok': True, 'company_id': existing_company['id'], 'already': True}
        return {'ok': False, 'error': f"Already in another team ({existing_company['name']}). Сначала покиньте её."}

    async with DatabaseSession() as session:
        member = CompanyMember(
            company_id=invite['company_id'], user_id=user_id, role='member',
        )
        session.add(member)
        await session.execute(
            update(TeamInvite).where(TeamInvite.id == invite['id'])
            .values(used_count=TeamInvite.used_count + 1)
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return {'ok': False, 'error': 'Уже состоите в команде'}

    return {'ok': True, 'company_id': invite['company_id']}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_team_service.py -v -k invite`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add cabinet/team_service.py tests/unit/test_team_service.py
git commit -m "feat(team): invite tokens — create/validate/revoke/accept"
```

### Task 2.3: Member management (remove, leave)

**Files:**
- Modify: `cabinet/team_service.py`
- Modify: `tests/unit/test_team_service.py`

- [ ] **Step 1: Добавить failing tests**

```python
@pytest.mark.asyncio
async def test_remove_member_owner_can_remove(db_session, make_user):
    owner = await make_user()
    company = await get_or_create_company_for_user(owner['id'])
    new_user = await make_user()
    invite = await create_invite(company['id'], created_by=owner['id'])
    await accept_invite(invite['token'], new_user['id'])

    from cabinet.team_service import remove_member
    result = await remove_member(company['id'], new_user['id'], by_user_id=owner['id'])
    assert result['ok'] is True
    members = await list_members(company['id'])
    assert all(m['user_id'] != new_user['id'] for m in members)


@pytest.mark.asyncio
async def test_remove_member_cannot_remove_owner(db_session, make_user):
    owner = await make_user()
    company = await get_or_create_company_for_user(owner['id'])
    from cabinet.team_service import remove_member
    result = await remove_member(company['id'], owner['id'], by_user_id=owner['id'])
    assert result['ok'] is False


@pytest.mark.asyncio
async def test_leave_team_member_can_leave(db_session, make_user):
    owner = await make_user()
    company = await get_or_create_company_for_user(owner['id'])
    new_user = await make_user()
    invite = await create_invite(company['id'], created_by=owner['id'])
    await accept_invite(invite['token'], new_user['id'])

    from cabinet.team_service import leave_team
    result = await leave_team(new_user['id'])
    assert result['ok'] is True
    assert await get_company_for_user(new_user['id']) is None


@pytest.mark.asyncio
async def test_leave_team_owner_cannot_leave(db_session, make_user):
    owner = await make_user()
    await get_or_create_company_for_user(owner['id'])
    from cabinet.team_service import leave_team
    result = await leave_team(owner['id'])
    assert result['ok'] is False
```

- [ ] **Step 2: Реализовать `remove_member` и `leave_team`**

```python
async def remove_member(company_id: int, target_user_id: int, by_user_id: int) -> Dict:
    """Owner-only: удаляет члена. Owner себя не может удалить."""
    async with DatabaseSession() as session:
        company = await session.get(Company, company_id)
        if not company:
            return {'ok': False, 'error': 'Команда не найдена'}
        if company.owner_user_id != by_user_id:
            return {'ok': False, 'error': 'Только owner может удалять членов'}
        if target_user_id == company.owner_user_id:
            return {'ok': False, 'error': 'Owner не может удалить сам себя'}

        membership = await session.scalar(
            select(CompanyMember).where(
                CompanyMember.company_id == company_id,
                CompanyMember.user_id == target_user_id,
            )
        )
        if not membership:
            return {'ok': False, 'error': 'Член не найден'}
        await session.delete(membership)
        await session.commit()
        return {'ok': True}


async def leave_team(user_id: int) -> Dict:
    """Member выходит из команды. Owner не может."""
    async with DatabaseSession() as session:
        membership = await session.scalar(
            select(CompanyMember).where(CompanyMember.user_id == user_id)
        )
        if not membership:
            return {'ok': False, 'error': 'Вы не в команде'}
        company = await session.get(Company, membership.company_id)
        if company and company.owner_user_id == user_id:
            return {'ok': False, 'error': 'Owner не может покинуть команду'}
        await session.delete(membership)
        await session.commit()
        return {'ok': True}
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/unit/test_team_service.py -v`
Expected: все тесты проходят (3 + 5 + 4 = 12).

- [ ] **Step 4: Commit**

```bash
git add cabinet/team_service.py tests/unit/test_team_service.py
git commit -m "feat(team): remove_member and leave_team"
```

### Task 2.4: RBAC middleware

**Files:**
- Modify: `cabinet/auth.py`

- [ ] **Step 1: Добавить middleware-helpers**

В конец `cabinet/auth.py`:

```python
from cabinet.team_service import get_company_for_user


def require_team_member(handler):
    """Проверяет членство в команде. Кладёт company и role в request."""
    async def wrapper(request):
        user = await get_current_user(request)
        if not user:
            if '/api/' in request.path:
                return web.json_response({'error': 'Unauthorized'}, status=401)
            raise web.HTTPFound('/cabinet/login')
        company = await get_company_for_user(user['user_id'])
        if not company:
            if '/api/' in request.path:
                return web.json_response({'error': 'Not in any team'}, status=403)
            raise web.HTTPFound('/cabinet/pipeline')
        request['user'] = user
        request['company'] = company
        request['role'] = 'owner' if company['owner_user_id'] == user['user_id'] else 'member'
        return await handler(request)
    return wrapper


def require_owner(handler):
    """То же что require_team_member, но требует роль owner."""
    inner = require_team_member(handler)
    async def wrapper(request):
        # require_team_member уже все проверит и проставит role
        # Перехватим после неё
        # Хитрый трюк: вызываем inner с фейковым handler-ом, который проверяет role
        async def check_owner(req):
            if req.get('role') != 'owner':
                return web.json_response({'error': 'Owner only'}, status=403)
            return await handler(req)
        return await require_team_member(check_owner)(request)
    return wrapper
```

- [ ] **Step 2: Smoke import**

Run: `python -c "from cabinet.auth import require_team_member, require_owner; print('OK')"`
Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add cabinet/auth.py
git commit -m "feat(team): require_team_member and require_owner middleware"
```

---

## Phase 3 — Pipeline core: cards + board UI

**Цель фазы:** Юзер открывает `/cabinet/pipeline`, видит 6 колонок, может создать карточку из ленты («В работу»), перетаскивать между стадиями. Без модалки (она в Phase 4) — клик на карточку открывает простую страницу деталей.

### Task 3.1: `cabinet/pipeline_service.py` — stages + create card (TDD)

**Files:**
- Create: `cabinet/pipeline_service.py`
- Create: `tests/unit/test_pipeline_service.py`

- [ ] **Step 1: Stages constants и helper-функции**

Создать `cabinet/pipeline_service.py`:

```python
"""Доменная логика Pipeline: создание карточек, смена стадий, история."""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any

from sqlalchemy import select, update, func, and_

from database import (
    DatabaseSession, PipelineCard, PipelineCardHistory,
    SniperFilter, TenderCache,
)

logger = logging.getLogger(__name__)

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

SOURCE_FEED = 'feed'
SOURCE_MANUAL = 'manual'
SOURCE_BITRIX_IMPORT = 'bitrix_import'

RESULT_WON = 'won'
RESULT_LOST = 'lost'


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
        'purchase_price': float(card.purchase_price) if card.purchase_price is not None else None,
        'sale_price': float(card.sale_price) if card.sale_price is not None else None,
        'ai_summary': card.ai_summary,
        'ai_recommendation': card.ai_recommendation,
        'ai_enriched_at': card.ai_enriched_at,
        'archived_at': card.archived_at,
        'data': card.data or {},
        'created_at': card.created_at,
        'created_by': card.created_by,
        'updated_at': card.updated_at,
    }


def calc_margin(purchase: Optional[float], sale: Optional[float]) -> Optional[Dict]:
    """Возвращает {abs, pct, color} или None если данных не хватает."""
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
```

- [ ] **Step 2: Тест на calc_margin**

`tests/unit/test_pipeline_service.py`:

```python
import pytest
from cabinet.pipeline_service import calc_margin


def test_calc_margin_positive_high():
    m = calc_margin(80.0, 100.0)
    assert m['pct'] == 20.0
    assert m['color'] == 'positive'


def test_calc_margin_borderline():
    m = calc_margin(98.0, 100.0)
    assert 1.9 < m['pct'] < 2.1
    assert m['color'] == 'warn'


def test_calc_margin_negative():
    m = calc_margin(110.0, 100.0)
    assert m['color'] == 'alert'


def test_calc_margin_returns_none_for_missing_data():
    assert calc_margin(None, 100.0) is None
    assert calc_margin(100.0, None) is None
    assert calc_margin(100.0, 0) is None
```

Run: `pytest tests/unit/test_pipeline_service.py::test_calc_margin_positive_high -v`
Expected: 4 passed (если запустить весь файл).

- [ ] **Step 3: Создать функцию `create_card_from_tender`**

Добавить в `cabinet/pipeline_service.py`:

```python
async def create_card_from_tender(
    company_id: int,
    tender_number: str,
    creator_user_id: int,
    filter_id: Optional[int] = None,
    source: str = SOURCE_FEED,
) -> Dict:
    """Создаёт карточку в стадии FOUND. Берёт мета из tender_cache.
    Если карточка уже есть в этой company — возвращает {error, existing_card}.
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
        cache_data = {}
        if cache:
            cache_data = {
                'name': cache.name,
                'customer': cache.customer,
                'region': cache.region,
                'price_max': float(cache.price) if cache.price is not None else None,
                'deadline': cache.deadline.isoformat() if cache.deadline else None,
                'url': f'https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber={tender_number}',
                'law_type': cache.law_type,
            }

        sale_price = cache_data.get('price_max')
        card = PipelineCard(
            company_id=company_id,
            tender_number=tender_number,
            stage=STAGE_FOUND,
            assignee_user_id=creator_user_id,
            filter_id=filter_id,
            source=source,
            sale_price=Decimal(str(sale_price)) if sale_price else None,
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
```

- [ ] **Step 4: Smoke import**

Run: `python -c "from cabinet.pipeline_service import create_card_from_tender, calc_margin; print('OK')"`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add cabinet/pipeline_service.py tests/unit/test_pipeline_service.py
git commit -m "feat(pipeline): pipeline_service with calc_margin and create_card_from_tender"
```

### Task 3.2: Move card stage + result (TDD)

**Files:**
- Modify: `cabinet/pipeline_service.py`
- Modify: `tests/unit/test_pipeline_service.py`

- [ ] **Step 1: Failing test для move + set_result**

Добавить в `tests/unit/test_pipeline_service.py`:

```python
import pytest
from cabinet.pipeline_service import (
    create_card_from_tender, move_card_stage, set_card_result,
    STAGE_FOUND, STAGE_IN_WORK, STAGE_RESULT, RESULT_WON, RESULT_LOST,
)


@pytest.mark.asyncio
async def test_move_card_stage_creates_history(db_session, make_company_with_card):
    card, owner = await make_company_with_card()
    result = await move_card_stage(card['id'], STAGE_IN_WORK, by_user_id=owner['id'])
    assert result['ok'] is True
    assert result['card']['stage'] == STAGE_IN_WORK
    # История содержит запись
    from database import PipelineCardHistory
    from sqlalchemy import select
    async with __import__('database').DatabaseSession() as s:
        history = (await s.execute(
            select(PipelineCardHistory).where(PipelineCardHistory.card_id == card['id'])
        )).scalars().all()
        actions = [h.action for h in history]
        assert 'stage_changed' in actions


@pytest.mark.asyncio
async def test_set_card_result_won(db_session, make_company_with_card):
    card, owner = await make_company_with_card()
    result = await set_card_result(card['id'], RESULT_WON, by_user_id=owner['id'])
    assert result['ok'] is True
    assert result['card']['stage'] == STAGE_RESULT
    assert result['card']['result'] == 'won'


@pytest.mark.asyncio
async def test_move_card_invalid_stage(db_session, make_company_with_card):
    card, owner = await make_company_with_card()
    result = await move_card_stage(card['id'], 'INVALID_STAGE', by_user_id=owner['id'])
    assert result['ok'] is False
    assert 'invalid' in result['error'].lower() or 'недопустим' in result['error']
```

Также добавить в `tests/conftest.py` фикстуру:

```python
@pytest.fixture
async def make_company_with_card(make_user):
    """Создаёт user, company, и одну карточку с минимальными данными."""
    from cabinet.team_service import get_or_create_company_for_user
    from cabinet.pipeline_service import create_card_from_tender
    counter = {'n': 0}
    async def _make():
        counter['n'] += 1
        owner = await make_user()
        company = await get_or_create_company_for_user(owner['id'])
        result = await create_card_from_tender(
            company['id'], f'TEST{counter["n"]:013d}', creator_user_id=owner['id']
        )
        return result['card'], owner
    return _make
```

- [ ] **Step 2: Реализовать `move_card_stage` и `set_card_result`**

Добавить в `cabinet/pipeline_service.py`:

```python
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
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/unit/test_pipeline_service.py -v`
Expected: 4 (margin) + 3 (move/result) = 7 passed.

- [ ] **Step 4: Commit**

```bash
git add cabinet/pipeline_service.py tests/unit/test_pipeline_service.py tests/conftest.py
git commit -m "feat(pipeline): move_card_stage and set_card_result with history"
```

### Task 3.3: List cards для доски, get_card_full

**Files:**
- Modify: `cabinet/pipeline_service.py`

- [ ] **Step 1: Добавить две функции**

```python
async def list_company_cards(company_id: int, include_archived: bool = False) -> List[Dict]:
    """Все карточки команды. По умолчанию без архива."""
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
    """Возвращает карточку только если она в этой company (RBAC)."""
    async with DatabaseSession() as session:
        card = await session.scalar(
            select(PipelineCard).where(
                PipelineCard.id == card_id,
                PipelineCard.company_id == company_id,
            )
        )
        return _card_dict(card) if card else None
```

- [ ] **Step 2: Smoke import**

Run: `python -c "from cabinet.pipeline_service import list_company_cards, get_card; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add cabinet/pipeline_service.py
git commit -m "feat(pipeline): list_company_cards and get_card with company-scoped access"
```

### Task 3.4: API endpoints для pipeline core

**Files:**
- Modify: `cabinet/api.py` (добавить новый раздел)
- Modify: `cabinet/routes.py`

- [ ] **Step 1: В `cabinet/api.py` добавить раздел Pipeline API**

В конец `cabinet/api.py`:

```python
# ============================================
# PIPELINE API
# ============================================

from cabinet.auth import require_team_member, require_owner
from cabinet import pipeline_service, team_service


async def pipeline_create_from_feed(request: web.Request) -> web.Response:
    """POST /cabinet/api/pipeline/from-feed/<tender_number> — взять в работу из ленты."""
    user = request['user']
    company = request['company']
    tender_number = request.match_info['tender_number']

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    filter_id = body.get('filter_id')

    result = await pipeline_service.create_card_from_tender(
        company_id=company['id'],
        tender_number=tender_number,
        creator_user_id=user['user_id'],
        filter_id=filter_id,
        source=pipeline_service.SOURCE_FEED,
    )
    if 'error' in result:
        if result['error'] == 'already_exists':
            return web.json_response({
                'error': 'already_in_pipeline',
                'card_id': result['existing_card']['id'],
                'stage': result['existing_card']['stage'],
            }, status=409)
        return web.json_response({'error': result['error']}, status=400)
    return web.json_response({'ok': True, 'card': result['card']})


pipeline_create_from_feed = require_team_member(pipeline_create_from_feed)


async def pipeline_move_stage(request: web.Request) -> web.Response:
    """POST /cabinet/api/pipeline/cards/<id>/stage — drag-n-drop endpoint."""
    user = request['user']
    company = request['company']
    card_id = int(request.match_info['id'])
    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)
    new_stage = body.get('stage')
    if not new_stage:
        return web.json_response({'error': 'stage required'}, status=400)

    card = await pipeline_service.get_card(card_id, company['id'])
    if not card:
        return web.json_response({'error': 'Card not found'}, status=404)

    if new_stage == pipeline_service.STAGE_RESULT:
        return web.json_response({
            'error': 'use_result_endpoint',
            'message': 'Use POST /cards/:id/result with {result: won|lost}',
        }, status=400)

    result = await pipeline_service.move_card_stage(card_id, new_stage, by_user_id=user['user_id'])
    if not result['ok']:
        return web.json_response({'error': result['error']}, status=400)
    return web.json_response({'ok': True, 'card': result['card']})


pipeline_move_stage = require_team_member(pipeline_move_stage)


async def pipeline_set_result(request: web.Request) -> web.Response:
    user = request['user']
    company = request['company']
    card_id = int(request.match_info['id'])
    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)
    res = body.get('result')
    if res not in ('won', 'lost'):
        return web.json_response({'error': 'result must be won or lost'}, status=400)

    card = await pipeline_service.get_card(card_id, company['id'])
    if not card:
        return web.json_response({'error': 'Card not found'}, status=404)

    result = await pipeline_service.set_card_result(card_id, res, by_user_id=user['user_id'])
    return web.json_response({'ok': result['ok'], 'card': result.get('card')})


pipeline_set_result = require_team_member(pipeline_set_result)


async def pipeline_get_card(request: web.Request) -> web.Response:
    company = request['company']
    card_id = int(request.match_info['id'])
    card = await pipeline_service.get_card(card_id, company['id'])
    if not card:
        return web.json_response({'error': 'Not found'}, status=404)
    return web.json_response({'card': card})


pipeline_get_card = require_team_member(pipeline_get_card)
```

- [ ] **Step 2: Зарегистрировать routes в `cabinet/routes.py`**

Найти секцию JSON API и добавить:

```python
    # JSON API — Pipeline
    app.router.add_post('/cabinet/api/pipeline/from-feed/{tender_number}', api.pipeline_create_from_feed)
    app.router.add_get('/cabinet/api/pipeline/cards/{id}', api.pipeline_get_card)
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/stage', api.pipeline_move_stage)
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/result', api.pipeline_set_result)
```

- [ ] **Step 3: Smoke — стартануть кабинет локально (если возможно) и curl-ом убедиться что 401/403 возвращаются**

Run (в отдельном терминале): `python -m bot.main` (или скрипт запуска кабинета из CLAUDE.md).
Run: `curl -X POST http://127.0.0.1:8181/cabinet/api/pipeline/cards/1/stage -d '{"stage":"IN_WORK"}' -H 'Content-Type: application/json'`
Expected: `{"error": "Unauthorized"}` (401).

Если локально не получается — пропустить, проверим в e2e в Phase 8.

- [ ] **Step 4: Commit**

```bash
git add cabinet/api.py cabinet/routes.py
git commit -m "feat(pipeline): API endpoints for card create/move/result/get"
```

### Task 3.5: Page route + базовый pipeline.html (без модалки)

**Files:**
- Create: `cabinet/templates/pipeline.html`
- Create: `cabinet/static/css/pages/pipeline.css`
- Create: `cabinet/static/js/pages/pipeline.js`
- Create: `cabinet/static/js/vendor/Sortable.min.js` (download)
- Modify: `cabinet/routes.py`
- Modify: `cabinet/api.py` (добавить page handler)

- [ ] **Step 1: Скачать Sortable.js (vendored)**

Run: `curl -o cabinet/static/js/vendor/Sortable.min.js https://cdn.jsdelivr.net/npm/sortablejs@1.15.2/Sortable.min.js`
Expected: файл создан (~30Kb).

- [ ] **Step 2: Page handler в `cabinet/api.py`** (рядом с другими page handlers, не в API секции)

```python
async def pipeline_page(request: web.Request) -> web.Response:
    """GET /cabinet/pipeline — server-render Kanban доски."""
    user = request['user']
    company = request['company']
    cards = await pipeline_service.list_company_cards(company['id'])
    members = await team_service.list_members(company['id'])

    # Группируем по стадиям
    by_stage = {s: [] for s in pipeline_service.ALL_STAGES}
    for c in cards:
        if c['stage'] in by_stage:
            by_stage[c['stage']].append(c)

    members_map = {m['user_id']: m for m in members}
    # Подгрузим имена sniper_users для members
    from database import DatabaseSession, SniperUser
    from sqlalchemy import select as sa_select
    async with DatabaseSession() as session:
        result = await session.execute(
            sa_select(SniperUser).where(SniperUser.id.in_(list(members_map.keys())))
        )
        users_by_id = {u.id: {'first_name': u.first_name, 'username': u.username} for u in result.scalars()}
    for m in members:
        m['display_name'] = (users_by_id.get(m['user_id'], {}).get('first_name')
                             or users_by_id.get(m['user_id'], {}).get('username')
                             or f'User {m["user_id"]}')

    return aiohttp_jinja2.render_template(
        'pipeline.html', request, {
            'active_page': 'pipeline',
            'user_name': user.get('username') or user.get('first_name') or 'Вы',
            'user_tier': user.get('subscription_tier', ''),
            'company_name': company['name'],
            'is_owner': company['owner_user_id'] == user['user_id'],
            'stages': pipeline_service.ALL_STAGES,
            'stage_labels': pipeline_service.STAGE_LABELS,
            'cards_by_stage': by_stage,
            'members': members,
        }
    )

pipeline_page = require_team_member(pipeline_page)
```

(`aiohttp_jinja2` уже импортирован в api.py — проверить.)

- [ ] **Step 3: Создать шаблон `cabinet/templates/pipeline.html`**

```html
{% extends "_base.html" %}
{% block title %}Pipeline — Tender Sniper{% endblock %}
{% block page_css %}
  <link rel="stylesheet" href="/cabinet/static/css/pages/pipeline.css?v=1">
{% endblock %}
{% block main %}
<div class="page-header">
  <div class="eyebrow">CRM</div>
  <h1>Pipeline <em>команды</em></h1>
  <div class="summary">{{ company_name }} · {{ members|length }} человек</div>
</div>

<div class="pipeline-actions">
  <a href="/cabinet/pipeline/archive" class="btn btn-ghost">Архив</a>
  {% if is_owner %}
    <a href="/cabinet/team" class="btn btn-secondary">Команда</a>
  {% endif %}
  <button class="btn btn-primary" id="btn-create-manual">+ Создать вручную</button>
</div>

<div class="kb-board">
  {% for stage in stages %}
    <div class="kb-col {% if stage in ['RFQ','QUOTED'] %}supplier{% endif %}" data-stage="{{ stage }}">
      <div class="kb-col-head">
        <span class="kb-col-title">{{ stage_labels[stage] }}</span>
        <span class="kb-count">{{ cards_by_stage[stage]|length }}</span>
      </div>
      <div class="kb-col-body" data-stage="{{ stage }}">
        {% for c in cards_by_stage[stage] %}
          <div class="kb-card{% if c.result == 'won' %} won{% elif c.result == 'lost' %} lost{% endif %}"
               data-card-id="{{ c.id }}" data-tender="{{ c.tender_number }}">
            <div class="t">{{ c.data.name or 'Тендер ' + c.tender_number }}</div>
            <div class="row">
              {% if c.data.price_max %}
                <span class="price">{{ '{:,.0f}'.format(c.data.price_max).replace(',',' ') }} ₽</span>
              {% endif %}
              {% if c.data.deadline %}<span class="due">{{ c.data.deadline }}</span>{% endif %}
            </div>
            {% if c.data.customer %}<div class="cust">{{ c.data.customer }}</div>{% endif %}
            <div class="row footer-row">
              {% if c.data.region %}<span class="region">{{ c.data.region }}</span>{% endif %}
              {% if c.assignee_user_id %}<span class="avatar">{{ c.assignee_user_id }}</span>{% endif %}
            </div>
          </div>
        {% endfor %}
      </div>
    </div>
  {% endfor %}
</div>
{% endblock %}
{% block page_js %}
<script src="/cabinet/static/js/vendor/Sortable.min.js"></script>
<script src="/cabinet/static/js/pages/pipeline.js?v=1"></script>
{% endblock %}
```

- [ ] **Step 4: CSS — минимум для доски**

`cabinet/static/css/pages/pipeline.css`:

```css
.pipeline-actions { display: flex; gap: 8px; margin: 16px 0 24px; align-items: center; }
.pipeline-actions .btn-primary { margin-left: auto; }

.kb-board {
  display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px;
  background: var(--bg); border: 1px solid var(--line);
  border-radius: 8px; padding: 14px;
  min-height: 60vh;
}
.kb-col {
  background: var(--bg-2); border: 1px solid var(--line); border-radius: 6px;
  padding: 8px 7px 10px; display: flex; flex-direction: column; gap: 6px; min-height: 200px;
}
.kb-col.supplier { background: rgba(74,106,140,0.08); border-color: rgba(74,106,140,0.25); }
.kb-col-head {
  display: flex; align-items: center; justify-content: space-between;
  font-family: var(--font-mono); font-size: 10px; font-weight: 600;
  letter-spacing: 0.10em; text-transform: uppercase; color: var(--muted);
  padding: 2px 4px 6px; border-bottom: 1px solid var(--line-soft);
}
.kb-count { background: var(--bg-raised); color: var(--text); font-weight: 600; border-radius: 4px; padding: 1px 6px; }
.kb-col-body { display: flex; flex-direction: column; gap: 6px; min-height: 100px; }
.kb-card {
  background: var(--bg-raised); border: 1px solid var(--line); border-radius: 4px;
  padding: 8px 9px; cursor: grab;
}
.kb-card:active { cursor: grabbing; }
.kb-card.won { border-left: 3px solid var(--positive); }
.kb-card.lost { border-left: 3px solid var(--alert); opacity: 0.55; }
.kb-card .t { font-size: 12px; line-height: 1.35; color: var(--text); margin-bottom: 5px;
  overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.kb-card .row { font-family: var(--font-mono); font-size: 9.5px; display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.kb-card .row + .row { margin-top: 3px; }
.kb-card .price { color: var(--text); font-weight: 600; font-size: 11px; }
.kb-card .due { color: var(--muted); }
.kb-card .due.hot { color: var(--alert); font-weight: 700; }
.kb-card .cust { color: var(--sub); font-size: 10px; margin-top: 3px; line-height: 1.3; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-card .region { color: var(--muted); font-size: 9px; }
.kb-card .avatar {
  margin-left: auto; width: 16px; height: 16px; border-radius: 50%;
  background: var(--accent); color: #fff; display: inline-flex; align-items: center; justify-content: center;
  font-size: 8.5px; font-weight: 700; font-family: var(--font-ui);
}
.sortable-ghost { opacity: 0.4; }
.sortable-drag { box-shadow: 0 6px 14px rgba(0,0,0,0.18); }
```

- [ ] **Step 5: JS — drag + optimistic UI**

`cabinet/static/js/pages/pipeline.js`:

```javascript
(function () {
  const { Toast } = window.Cabinet;
  const STAGE_RESULT = 'RESULT';

  async function moveCard(cardId, newStage, fromColEl, oldStage) {
    try {
      const resp = await fetch('/cabinet/api/pipeline/cards/' + cardId + '/stage', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stage: newStage }),
      });
      const data = await resp.json();
      if (!resp.ok || !data.ok) {
        throw new Error(data.error || 'Не удалось переместить');
      }
      Toast.show('✓ Перемещено', 'positive');
      updateCounts();
    } catch (e) {
      Toast.show(e.message || 'Ошибка', 'alert');
      // Откат: возвращаем карточку обратно
      const card = document.querySelector('[data-card-id="' + cardId + '"]');
      if (card && fromColEl) fromColEl.appendChild(card);
      updateCounts();
    }
  }

  async function setResult(cardId, result) {
    try {
      const resp = await fetch('/cabinet/api/pipeline/cards/' + cardId + '/result', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ result: result }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || 'Ошибка');
      Toast.show(result === 'won' ? '✓ Выиграно' : '✓ Проиграно', 'positive');
      window.location.reload();
    } catch (e) {
      Toast.show(e.message, 'alert');
    }
  }

  function updateCounts() {
    document.querySelectorAll('.kb-col').forEach(col => {
      const cnt = col.querySelectorAll('.kb-card').length;
      const badge = col.querySelector('.kb-count');
      if (badge) badge.textContent = cnt;
    });
  }

  function initSortable() {
    document.querySelectorAll('.kb-col-body').forEach(body => {
      const stage = body.dataset.stage;
      Sortable.create(body, {
        group: 'pipeline',
        animation: 150,
        ghostClass: 'sortable-ghost',
        dragClass: 'sortable-drag',
        onAdd: (evt) => {
          const card = evt.item;
          const cardId = parseInt(card.dataset.cardId, 10);
          const targetStage = body.dataset.stage;
          const fromColEl = evt.from;
          if (targetStage === STAGE_RESULT) {
            // Возвращаем карточку обратно и просим выбрать Win/Lose
            evt.from.appendChild(card);
            updateCounts();
            const choice = prompt('Перетаскивание в «Результат». Введите:\n  won — Победа\n  lost — Проигрыш\n  (или закройте чтобы отменить)');
            if (choice === 'won' || choice === 'lost') {
              setResult(cardId, choice);
            }
            return;
          }
          moveCard(cardId, targetStage, fromColEl, evt.from.dataset.stage);
        },
      });
    });
  }

  function initManualCreate() {
    const btn = document.getElementById('btn-create-manual');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      const num = prompt('Номер тендера на zakupki.gov.ru:');
      if (!num) return;
      const trimmed = num.trim();
      const resp = await fetch('/cabinet/api/pipeline/from-feed/' + encodeURIComponent(trimmed), {
        method: 'POST', credentials: 'same-origin',
      });
      const data = await resp.json();
      if (resp.ok && data.ok) {
        Toast.show('✓ Карточка создана', 'positive');
        setTimeout(() => window.location.reload(), 700);
      } else if (resp.status === 409) {
        Toast.show('Уже в Pipeline (стадия: ' + data.stage + ')', 'alert');
      } else {
        Toast.show(data.error || 'Ошибка создания', 'alert');
      }
    });
  }

  initSortable();
  initManualCreate();
})();
```

- [ ] **Step 6: Зарегистрировать страницу в routes.py**

```python
    # Pipeline pages
    app.router.add_get('/cabinet/pipeline', api.pipeline_page)
```

- [ ] **Step 7: Smoke**

Запустить кабинет, открыть `/cabinet/pipeline`. Ожидание: 6 пустых колонок (если карточек нет). Если зайти впервые — должна быть автоматически создана команда (требует обновлённого middleware с `auto_create=True` — добавим на следующем шаге).

- [ ] **Step 8: Авто-создание команды на первом заходе**

Открыть `cabinet/auth.py`, найти `require_team_member` и заменить:

```python
def require_team_member(handler, auto_create_for_pages: bool = True):
    async def wrapper(request):
        user = await get_current_user(request)
        if not user:
            if '/api/' in request.path:
                return web.json_response({'error': 'Unauthorized'}, status=401)
            raise web.HTTPFound('/cabinet/login')
        from cabinet.team_service import get_company_for_user, get_or_create_company_for_user
        company = await get_company_for_user(user['user_id'])
        if not company:
            is_api = '/api/' in request.path
            if is_api:
                return web.json_response({'error': 'Not in any team'}, status=403)
            if auto_create_for_pages:
                company = await get_or_create_company_for_user(user['user_id'])
            else:
                raise web.HTTPFound('/cabinet/pipeline')
        request['user'] = user
        request['company'] = company
        request['role'] = 'owner' if company['owner_user_id'] == user['user_id'] else 'member'
        return await handler(request)
    return wrapper
```

- [ ] **Step 9: Smoke (повторно)**

Открыть `/cabinet/pipeline` — должны быть 6 пустых колонок и ты как owner.

- [ ] **Step 10: Commit**

```bash
git add cabinet/api.py cabinet/routes.py cabinet/auth.py \
        cabinet/templates/pipeline.html cabinet/static/css/pages/pipeline.css \
        cabinet/static/js/pages/pipeline.js cabinet/static/js/vendor/Sortable.min.js
git commit -m "feat(pipeline): board page + drag with optimistic UI + manual create"
```

### Task 3.6: Кнопка «В работу» в ленте `/cabinet/`

**Files:**
- Modify: `cabinet/templates/dashboard.html` (или там где рендерится feed-карточка)
- Modify: `cabinet/static/js/dashboard.js`

- [ ] **Step 1: Найти место рендера карточки тендера в `cabinet/static/js/dashboard.js`**

Открыть файл, найти функцию рендера feed-item.

- [ ] **Step 2: Добавить кнопку «В работу» рядом с существующими (Bitrix, Избранное)**

В DOM-генерации карточки добавить:

```javascript
const btnWork = document.createElement('button');
btnWork.className = 'btn btn-secondary btn-sm';
btnWork.textContent = '→ В работу';
btnWork.addEventListener('click', async (e) => {
  e.stopPropagation();
  const resp = await fetch('/cabinet/api/pipeline/from-feed/' + encodeURIComponent(tender.number), {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filter_id: tender.filter_id || null }),
  });
  const data = await resp.json();
  if (resp.ok && data.ok) {
    window.Cabinet.Toast.show('✓ Добавлено в Pipeline', 'positive');
  } else if (resp.status === 409) {
    window.Cabinet.Toast.show('Уже в Pipeline (' + data.stage + ')', 'alert');
  } else {
    window.Cabinet.Toast.show(data.error || 'Ошибка', 'alert');
  }
});
actionsContainer.appendChild(btnWork);
```

(Точное место зависит от текущего dashboard.js — найти переменную типа `actionsContainer` или аналог.)

- [ ] **Step 3: Smoke в браузере**

Открыть `/cabinet/`, нажать «В работу» на тендере → Toast «Добавлено». Открыть `/cabinet/pipeline` → карточка в колонке «Найденные».

- [ ] **Step 4: Commit**

```bash
git add cabinet/static/js/dashboard.js cabinet/templates/dashboard.html
git commit -m "feat(pipeline): 'В работу' button in feed creates pipeline card"
```

### Task 3.7: Sidebar link на Pipeline

**Files:**
- Modify: `cabinet/templates/_sidebar.html`

- [ ] **Step 1: Добавить пункт меню**

В `_sidebar.html` найти секцию пунктов и добавить:

```html
<a href="/cabinet/pipeline" class="nav-item {% if active_page == 'pipeline' %}active{% endif %}">
  <span class="nav-icon">📋</span>
  <span class="nav-label">Pipeline</span>
</a>
```

- [ ] **Step 2: Smoke + commit**

```bash
git add cabinet/templates/_sidebar.html
git commit -m "feat(pipeline): sidebar nav link"
```

---

## Phase 4 — Модалка карточки + базовые табы (Детали + Заметки + История)

**Цель фазы:** Клик по карточке открывает большую модалку с 5 табами (детали/заметки/файлы/чек-лист/история). В этой фазе — Details, Notes, History. Files и Checklist в Phase 5.

### Task 4.1: API для note CRUD + history list

**Files:**
- Modify: `cabinet/pipeline_service.py`
- Modify: `cabinet/api.py`
- Modify: `cabinet/routes.py`

- [ ] **Step 1: Добавить функции в `pipeline_service.py`**

```python
async def add_note(card_id: int, text: str, by_user_id: int) -> Dict:
    text = (text or '').strip()
    if not text:
        return {'ok': False, 'error': 'Заметка пуста'}
    async with DatabaseSession() as session:
        from database import PipelineCardNote
        note = PipelineCardNote(card_id=card_id, user_id=by_user_id, text=text)
        session.add(note)
        history = PipelineCardHistory(
            card_id=card_id, user_id=by_user_id, action='note_added', payload={},
        )
        session.add(history)
        await session.commit()
        return {'ok': True, 'note': {
            'id': note.id, 'text': note.text, 'user_id': note.user_id, 'created_at': note.created_at,
        }}


async def list_notes(card_id: int) -> List[Dict]:
    async with DatabaseSession() as session:
        from database import PipelineCardNote
        result = await session.execute(
            select(PipelineCardNote).where(PipelineCardNote.card_id == card_id)
            .order_by(PipelineCardNote.created_at.desc())
        )
        return [{
            'id': n.id, 'text': n.text, 'user_id': n.user_id, 'created_at': n.created_at,
        } for n in result.scalars().all()]


async def list_history(card_id: int, limit: int = 100) -> List[Dict]:
    async with DatabaseSession() as session:
        result = await session.execute(
            select(PipelineCardHistory).where(PipelineCardHistory.card_id == card_id)
            .order_by(PipelineCardHistory.created_at.desc()).limit(limit)
        )
        return [{
            'id': h.id, 'user_id': h.user_id, 'action': h.action,
            'payload': h.payload or {}, 'created_at': h.created_at,
        } for h in result.scalars().all()]


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
```

- [ ] **Step 2: API endpoints в `cabinet/api.py`**

В секцию Pipeline API — добавить `pipeline_card_full`, `pipeline_add_note`, `pipeline_set_assignee`, `pipeline_set_prices`, `pipeline_delete_card` (см. полные сигнатуры в спеке §5).

- [ ] **Step 3: Routes**

```python
    app.router.add_get('/cabinet/api/pipeline/cards/{id}/full', api.pipeline_card_full)
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/notes', api.pipeline_add_note)
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/assignee', api.pipeline_set_assignee)
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/prices', api.pipeline_set_prices)
    app.router.add_delete('/cabinet/api/pipeline/cards/{id}', api.pipeline_delete_card)
```

- [ ] **Step 4: Commit**

```bash
git add cabinet/pipeline_service.py cabinet/api.py cabinet/routes.py
git commit -m "feat(pipeline): card full + notes + assignee + prices + delete"
```

### Task 4.2: Шаблон `_modal_card.html` с 5 табами

**Files:**
- Create: `cabinet/templates/_modal_card.html`
- Modify: `cabinet/templates/pipeline.html` (include модалки + data-attributes для JS)

См. полную разметку модалки в спеке §9 «Модалка карточки». Структура: header с title+ссылкой на zakupki, 5 табов (data-tab="details/notes/files/checklist/history"), 5 секций tab-pane.

- [ ] **Step 1: Создать `_modal_card.html`** — DOM-разметка модалки, 5 табов, формы для prices/stage/assignee, кнопки win/lost/delete, list-контейнеры для notes/files/checklist/history.
- [ ] **Step 2: Подключить include в `pipeline.html`** после `</div>` доски: `{% include "_modal_card.html" %}`.
- [ ] **Step 3: Передать `members`, `is_owner`, `current_user_id` в data-attributes** на `.page-header`.
- [ ] **Step 4: Commit**

```bash
git add cabinet/templates/_modal_card.html cabinet/templates/pipeline.html
git commit -m "feat(pipeline): modal scaffold with 5 tabs"
```

### Task 4.3: Модалка JS — открытие, табы, Details/Notes/History

**Files:**
- Modify: `cabinet/static/js/pages/pipeline.js`
- Modify: `cabinet/static/css/pages/pipeline.css`

**КРИТИЧНО:** Никогда не используй `el.innerHTML = ...` для DOM-манипуляций. Только `el.textContent`, `el.replaceChildren()`, `createElement` + `appendChild`. Это правило безопасности (XSS prevention).

- [ ] **Step 1: Добавить modal-стили в `pipeline.css`**

```css
.modal-overlay {
  position: fixed; inset: 0; background: rgba(20,18,15,0.55);
  display: flex; align-items: center; justify-content: center;
  z-index: 1000; padding: 24px;
}
.modal-overlay[hidden] { display: none; }
.modal-window {
  background: var(--bg-raised); border-radius: 10px;
  width: 720px; max-width: 100%; max-height: 88vh;
  display: flex; flex-direction: column; overflow: hidden;
  box-shadow: 0 20px 60px rgba(0,0,0,0.25);
  position: relative;
}
.modal-close {
  position: absolute; top: 12px; right: 14px; background: none;
  border: none; font-size: 28px; cursor: pointer; color: var(--muted);
}
.modal-head { padding: 18px 24px 6px; }
.modal-head h2 { margin: 0 0 4px; font-size: 18px; line-height: 1.3; }
.modal-head a { font-size: 12px; color: var(--accent); text-decoration: none; }
.modal-tabs { display: flex; gap: 0; padding: 0 24px; border-bottom: 1px solid var(--line); }
.modal-tab { background: none; border: none; padding: 10px 14px; cursor: pointer;
  font-size: 13px; color: var(--muted); border-bottom: 2px solid transparent; }
.modal-tab.active { color: var(--accent); border-bottom-color: var(--accent); font-weight: 600; }
.modal-body { padding: 18px 24px 22px; overflow-y: auto; flex: 1; }
.tab-pane { display: none; }
.tab-pane.active { display: block; }
.margin-box { padding: 8px 12px; border-radius: 6px; margin: 8px 0 16px;
  font-family: var(--font-mono); font-size: 13px; }
.margin-box.positive { background: rgba(86,122,63,0.1); color: var(--positive); }
.margin-box.warn { background: rgba(180,140,40,0.1); color: #8b6e1c; }
.margin-box.alert { background: rgba(177,61,40,0.1); color: var(--alert); }
.card-meta { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin: 14px 0 18px; }
.card-meta .label { display: block; font-family: var(--font-mono); font-size: 10px; text-transform: uppercase; letter-spacing: 0.12em; color: var(--muted); margin-bottom: 4px; }
.ai-block { background: var(--bg-2); border-radius: 6px; padding: 12px 14px; margin: 14px 0; }
.ai-block-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.modal-actions { display: flex; gap: 8px; margin-top: 14px; flex-wrap: wrap; }
.modal-actions .btn-danger { margin-left: auto; }
.notes-list { margin-bottom: 12px; max-height: 320px; overflow-y: auto; }
.note-item { padding: 8px 10px; border-bottom: 1px solid var(--line-soft); font-size: 13px; }
.note-item .note-meta { font-family: var(--font-mono); font-size: 10px; color: var(--muted); margin-top: 4px; }
#cm-note-input { width: 100%; resize: vertical; padding: 10px; border: 1px solid var(--line); border-radius: 4px; background: var(--bg); margin-bottom: 8px; }
#cm-history .history-item { padding: 6px 0; border-bottom: 1px solid var(--line-soft); font-size: 12px; }
#cm-history .history-meta { font-family: var(--font-mono); font-size: 10px; color: var(--muted); }
```

- [ ] **Step 2: JS — модалка + табы + рендер Details/Notes/History**

В `pipeline.js` (продолжение IIFE):

```javascript
const modal = document.getElementById('card-modal');
const modalClose = document.getElementById('card-modal-close');
const headerEl = document.querySelector('.page-header');
const teamMembers = JSON.parse(headerEl.dataset.members || '[]');
const isOwner = headerEl.dataset.isOwner === '1';

let openCardId = null;

function openModal(cardId) {
  openCardId = cardId;
  modal.hidden = false;
  document.querySelectorAll('.modal-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === 'details'));
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.toggle('active', p.dataset.tab === 'details'));
  loadCardFull(cardId);
}

function closeModal() { modal.hidden = true; openCardId = null; }

modalClose.addEventListener('click', closeModal);
modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !modal.hidden) closeModal(); });

document.querySelectorAll('.modal-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    const target = tab.dataset.tab;
    document.querySelectorAll('.modal-tab').forEach(t => t.classList.toggle('active', t === tab));
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.toggle('active', p.dataset.tab === target));
  });
});

document.querySelectorAll('.kb-card').forEach(card => {
  card.addEventListener('click', (e) => {
    if (e.target.closest('button')) return;
    openModal(parseInt(card.dataset.cardId, 10));
  });
});

async function loadCardFull(cardId) {
  const resp = await fetch('/cabinet/api/pipeline/cards/' + cardId + '/full', { credentials: 'same-origin' });
  if (!resp.ok) { Toast.show('Не удалось загрузить', 'alert'); return; }
  const data = await resp.json();
  renderCardModal(data);
}

function renderCardModal(data) {
  const c = data.card;
  document.getElementById('cm-title').textContent = c.data?.name || ('Тендер ' + c.tender_number);
  document.getElementById('cm-zakupki-link').href = c.data?.url || '#';

  // Stage select — используем replaceChildren для безопасной очистки
  const stageSel = document.getElementById('cm-stage');
  stageSel.replaceChildren();
  ['FOUND','IN_WORK','RFQ','QUOTED','SUBMITTED'].forEach(s => {
    const opt = document.createElement('option');
    opt.value = s;
    opt.textContent = ({'FOUND':'Найденные','IN_WORK':'Взято в работу','RFQ':'Запрос предложений','QUOTED':'Получено КП','SUBMITTED':'Участвуем'})[s];
    if (c.stage === s) opt.selected = true;
    stageSel.appendChild(opt);
  });
  stageSel.onchange = async () => {
    const r = await fetch('/cabinet/api/pipeline/cards/' + c.id + '/stage', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stage: stageSel.value }),
    });
    Toast.show(r.ok ? '✓ Стадия обновлена' : 'Ошибка', r.ok ? 'positive' : 'alert');
  };

  // Assignee select
  const asSel = document.getElementById('cm-assignee');
  asSel.replaceChildren();
  teamMembers.forEach(m => {
    const opt = document.createElement('option');
    opt.value = m.user_id;
    opt.textContent = m.display_name || ('User ' + m.user_id);
    if (c.assignee_user_id === m.user_id) opt.selected = true;
    asSel.appendChild(opt);
  });
  asSel.onchange = async () => {
    const r = await fetch('/cabinet/api/pipeline/cards/' + c.id + '/assignee', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: parseInt(asSel.value, 10) }),
    });
    if (r.ok) Toast.show('✓ Ответственный обновлён', 'positive');
  };

  // Prices
  document.getElementById('cm-purchase').value = c.purchase_price || '';
  document.getElementById('cm-sale').value = c.sale_price || '';
  ['cm-purchase','cm-sale'].forEach(id => {
    document.getElementById(id).onchange = async () => {
      const purchase = parseFloat(document.getElementById('cm-purchase').value) || null;
      const sale = parseFloat(document.getElementById('cm-sale').value) || null;
      await fetch('/cabinet/api/pipeline/cards/' + c.id + '/prices', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ purchase_price: purchase, sale_price: sale }),
      });
      Toast.show('✓ Цены сохранены', 'positive');
      loadCardFull(c.id);
    };
  });

  // Margin
  const marginEl = document.getElementById('cm-margin');
  if (data.margin) {
    marginEl.hidden = false;
    marginEl.className = 'margin-box ' + data.margin.color;
    marginEl.textContent = `Маржа: ${Math.round(data.margin.abs).toLocaleString('ru-RU')} ₽ (${data.margin.pct.toFixed(1)}%)`;
  } else {
    marginEl.hidden = true;
  }

  // Meta
  document.getElementById('cm-customer').textContent = c.data?.customer || '—';
  document.getElementById('cm-region').textContent = c.data?.region || '—';
  document.getElementById('cm-deadline').textContent = c.data?.deadline || '—';

  // AI block
  document.getElementById('cm-ai-summary').textContent = c.ai_summary || 'Анализ ещё не запускался';
  document.getElementById('cm-ai-recommendation').textContent = c.ai_recommendation ? ('Рекомендация: ' + c.ai_recommendation) : '';

  // Action buttons
  const supplierBtn = document.getElementById('cm-btn-supplier');
  supplierBtn.hidden = (c.stage !== 'RFQ');
  supplierBtn.onclick = () => Toast.show('Функция в разработке', 'alert');

  const delBtn = document.getElementById('cm-btn-delete');
  delBtn.hidden = !isOwner;
  delBtn.onclick = async () => {
    if (!confirm('Удалить карточку безвозвратно?')) return;
    const r = await fetch('/cabinet/api/pipeline/cards/' + c.id, { method: 'DELETE', credentials: 'same-origin' });
    if (r.ok) { Toast.show('Удалено', 'positive'); closeModal(); window.location.reload(); }
  };

  document.getElementById('cm-btn-won').onclick = () => setResult(c.id, 'won');
  document.getElementById('cm-btn-lost').onclick = () => setResult(c.id, 'lost');

  // Notes — безопасный рендер через createElement
  const notesList = document.getElementById('cm-notes-list');
  notesList.replaceChildren();
  data.notes.forEach(n => {
    const div = document.createElement('div');
    div.className = 'note-item';
    const text = document.createElement('div');
    text.textContent = n.text;
    div.appendChild(text);
    const meta = document.createElement('div');
    meta.className = 'note-meta';
    meta.textContent = `User ${n.user_id} · ${n.created_at}`;
    div.appendChild(meta);
    notesList.appendChild(div);
  });
  document.getElementById('cm-note-add').onclick = async () => {
    const inp = document.getElementById('cm-note-input');
    const text = inp.value.trim();
    if (!text) return;
    const r = await fetch('/cabinet/api/pipeline/cards/' + c.id + '/notes', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    const d = await r.json();
    if (d.ok) { inp.value = ''; Toast.show('✓ Заметка добавлена', 'positive'); loadCardFull(c.id); }
    else Toast.show(d.error || 'Ошибка', 'alert');
  };

  // History
  const histEl = document.getElementById('cm-history');
  histEl.replaceChildren();
  data.history.forEach(h => {
    const div = document.createElement('div');
    div.className = 'history-item';
    const text = document.createElement('div');
    text.textContent = formatHistoryAction(h);
    div.appendChild(text);
    const meta = document.createElement('div');
    meta.className = 'history-meta';
    meta.textContent = `User ${h.user_id} · ${h.created_at}`;
    div.appendChild(meta);
    histEl.appendChild(div);
  });
}

function formatHistoryAction(h) {
  const map = {
    'created': 'создал карточку',
    'stage_changed': `перевёл из «${h.payload.from}» в «${h.payload.to}»`,
    'assigned': `назначил ответственного (user ${h.payload.to})`,
    'note_added': 'добавил заметку',
    'file_uploaded': 'загрузил файл',
    'file_deleted': 'удалил файл',
    'price_set': 'обновил цены',
    'won': 'отметил победу',
    'lost': 'отметил проигрыш',
    'ai_enriched': 'запустил AI-анализ',
    'checklist_added': 'добавил пункт чек-листа',
    'checklist_done': 'отметил пункт выполненным',
    'imported_from_bitrix': 'импортирован из Bitrix24',
  };
  return map[h.action] || h.action;
}
```

- [ ] **Step 3: Smoke в браузере**

Открыть `/cabinet/pipeline`, кликнуть карточку → модалка → 5 табов → Details содержит данные → стадию можно менять → можно добавить заметку → история показывает действия.

- [ ] **Step 4: Commit**

```bash
git add cabinet/static/js/pages/pipeline.js cabinet/static/css/pages/pipeline.css
git commit -m "feat(pipeline): card modal with details/notes/history tabs"
```

---

## Phase 5 — Files, Checklist, Relations

**Цель:** Дописать функционал 3 оставшихся областей — файлы (upload/download/delete с лимитами), чек-лист (CRUD + drag-reorder), связанные тендеры (manual link).

### Task 5.1: Backend — file hosting (upload, list, download, delete)

**Files:**
- Modify: `cabinet/pipeline_service.py`
- Modify: `cabinet/api.py`

- [ ] **Step 1: Helpers в `pipeline_service.py`**

Константы и санитизация:

```python
import os, re
from pathlib import Path

UPLOAD_ROOT = Path(os.environ.get('UPLOAD_ROOT', '/app/uploads'))
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
TEAM_FILE_QUOTA = 1024 * 1024 * 1024  # 1 GB

def _safe_filename(name: str) -> str:
    name = re.sub(r'[^\w\s.\-а-яА-ЯёЁ]', '_', name, flags=re.UNICODE)
    name = name.replace('..', '_').strip()[:200] or 'file'
    return name

async def list_files(card_id: int) -> List[Dict]:
    async with DatabaseSession() as session:
        from database import PipelineCardFile
        result = await session.execute(
            select(PipelineCardFile).where(PipelineCardFile.card_id == card_id)
            .order_by(PipelineCardFile.uploaded_at.desc())
        )
        return [{
            'id': f.id, 'filename': f.filename, 'size': f.size,
            'mime_type': f.mime_type, 'uploaded_by': f.uploaded_by,
            'uploaded_at': f.uploaded_at, 'is_generated': f.is_generated,
        } for f in result.scalars().all()]

async def total_team_files_size(company_id: int) -> int:
    async with DatabaseSession() as session:
        from database import PipelineCardFile
        result = await session.execute(
            select(func.coalesce(func.sum(PipelineCardFile.size), 0))
            .select_from(PipelineCardFile.__table__.join(PipelineCard.__table__))
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
        from database import PipelineCardFile
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
            'mime_type': pf.mime_type, 'uploaded_at': pf.uploaded_at,
        }}

async def delete_file(file_id: int, company_id: int, by_user_id: int) -> Dict:
    async with DatabaseSession() as session:
        from database import PipelineCardFile
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
        from database import PipelineCardFile
        pf = await session.get(PipelineCardFile, file_id)
        if not pf:
            return None
        card = await session.get(PipelineCard, pf.card_id)
        if not card or card.company_id != company_id:
            return None
        return {'path': pf.path, 'filename': pf.filename, 'mime_type': pf.mime_type}
```

- [ ] **Step 2: API endpoints**

```python
async def pipeline_list_files(request):
    company = request['company']; card_id = int(request.match_info['id'])
    if not await pipeline_service.get_card(card_id, company['id']):
        return web.json_response({'error': 'Not found'}, status=404)
    files = await pipeline_service.list_files(card_id)
    return web.json_response({'files': files})

pipeline_list_files = require_team_member(pipeline_list_files)


async def pipeline_upload_file(request):
    user = request['user']; company = request['company']
    card_id = int(request.match_info['id'])
    if not await pipeline_service.get_card(card_id, company['id']):
        return web.json_response({'error': 'Not found'}, status=404)
    reader = await request.multipart()
    field = await reader.next()
    if not field or field.name != 'file':
        return web.json_response({'error': 'No file field'}, status=400)
    filename = field.filename or 'file'
    mime = field.headers.get('Content-Type', 'application/octet-stream')
    content = bytearray()
    while True:
        chunk = await field.read_chunk(65536)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > pipeline_service.MAX_FILE_SIZE:
            return web.json_response({'error': 'File too large'}, status=413)
    result = await pipeline_service.save_file(
        card_id, company['id'], filename, bytes(content), mime, user['user_id']
    )
    return web.json_response(result, status=200 if result['ok'] else 400)

pipeline_upload_file = require_team_member(pipeline_upload_file)


async def pipeline_delete_file(request):
    user = request['user']; company = request['company']
    file_id = int(request.match_info['fid'])
    result = await pipeline_service.delete_file(file_id, company['id'], user['user_id'])
    return web.json_response(result, status=200 if result['ok'] else 403)

pipeline_delete_file = require_team_member(pipeline_delete_file)


async def pipeline_download_file(request):
    company = request['company']
    file_id = int(request.match_info['fid'])
    info = await pipeline_service.get_file_for_download(file_id, company['id'])
    if not info:
        return web.json_response({'error': 'Not found'}, status=404)
    headers = {
        'Content-Disposition': f'attachment; filename*=UTF-8\'\'{info["filename"]}',
        'Content-Type': info['mime_type'],
    }
    return web.FileResponse(info['path'], headers=headers)

pipeline_download_file = require_team_member(pipeline_download_file)
```

- [ ] **Step 3: Routes**

```python
    app.router.add_get('/cabinet/api/pipeline/cards/{id}/files', api.pipeline_list_files)
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/files', api.pipeline_upload_file)
    app.router.add_delete('/cabinet/api/pipeline/files/{fid}', api.pipeline_delete_file)
    app.router.add_get('/cabinet/api/pipeline/files/{fid}/download', api.pipeline_download_file)
```

- [ ] **Step 4: Volume mount в Railway dashboard**

Manual: открыть Railway → service `tender-ai-bot` → Settings → Volumes → добавить volume mount `/app/uploads` (size 1 GB).

- [ ] **Step 5: Smoke + commit**

```bash
git add cabinet/pipeline_service.py cabinet/api.py cabinet/routes.py
git commit -m "feat(pipeline): file upload/download/delete on Railway Volume"
```

### Task 5.2: Frontend — Files tab в модалке

**Files:**
- Modify: `cabinet/static/js/pages/pipeline.js`

В функцию `renderCardModal` добавить блок Files (после Notes):

```javascript
loadFiles(c.id);
document.getElementById('cm-file-upload-btn').onclick = () => {
  document.getElementById('cm-file-input').click();
};
document.getElementById('cm-file-input').onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  if (file.size > 10 * 1024 * 1024) { Toast.show('Файл больше 10 MB', 'alert'); return; }
  const fd = new FormData();
  fd.append('file', file);
  const r = await fetch('/cabinet/api/pipeline/cards/' + c.id + '/files', {
    method: 'POST', credentials: 'same-origin', body: fd,
  });
  const d = await r.json();
  if (d.ok) { Toast.show('✓ Загружено', 'positive'); loadFiles(c.id); }
  else Toast.show(d.error || 'Ошибка', 'alert');
};
```

```javascript
async function loadFiles(cardId) {
  const r = await fetch('/cabinet/api/pipeline/cards/' + cardId + '/files', { credentials: 'same-origin' });
  const data = await r.json();
  const list = document.getElementById('cm-files-list');
  list.replaceChildren();
  if (!data.files.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'Файлов пока нет';
    list.appendChild(empty);
    return;
  }
  data.files.forEach(f => {
    const row = document.createElement('div');
    row.className = 'file-row';
    const a = document.createElement('a');
    a.href = '/cabinet/api/pipeline/files/' + f.id + '/download';
    a.textContent = f.filename;
    a.target = '_blank';
    row.appendChild(a);
    const meta = document.createElement('span');
    meta.className = 'file-meta';
    meta.textContent = `${(f.size/1024).toFixed(1)} KB · ${f.uploaded_at}`;
    row.appendChild(meta);
    const del = document.createElement('button');
    del.className = 'btn btn-ghost btn-sm';
    del.textContent = '×';
    del.onclick = async () => {
      if (!confirm('Удалить файл?')) return;
      const dr = await fetch('/cabinet/api/pipeline/files/' + f.id, { method: 'DELETE', credentials: 'same-origin' });
      if (dr.ok) { Toast.show('Удалено', 'positive'); loadFiles(cardId); }
    };
    row.appendChild(del);
    list.appendChild(row);
  });
}
```

CSS для files в `pipeline.css`:

```css
.file-row { display: flex; align-items: center; gap: 12px; padding: 8px 0; border-bottom: 1px solid var(--line-soft); font-size: 13px; }
.file-row a { color: var(--accent); text-decoration: none; flex: 1; word-break: break-all; }
.file-row .file-meta { font-family: var(--font-mono); font-size: 10px; color: var(--muted); }
```

- [ ] **Commit**

```bash
git add cabinet/static/js/pages/pipeline.js cabinet/static/css/pages/pipeline.css
git commit -m "feat(pipeline): files tab — upload/download/delete UI"
```

### Task 5.3: Checklist CRUD (backend + frontend)

**Files:** `cabinet/pipeline_service.py`, `cabinet/api.py`, `cabinet/routes.py`, `cabinet/static/js/pages/pipeline.js`.

- [ ] **Backend — функции в `pipeline_service.py`:**
  - `add_checklist_item(card_id, text, by_user_id)` — INSERT, history `checklist_added`
  - `toggle_checklist(item_id, done, by_user_id)` — UPDATE done + done_by/done_at, history `checklist_done` (только при done=True)
  - `delete_checklist(item_id, by_user_id)` — DELETE, без истории
  - `list_checklist(card_id)` — SELECT order by position

- [ ] **API endpoints:**
  - `GET /cabinet/api/pipeline/cards/{id}/checklist`
  - `POST /cabinet/api/pipeline/cards/{id}/checklist` body `{text}`
  - `PATCH /cabinet/api/pipeline/checklist/{cid}` body `{done?, text?}`
  - `DELETE /cabinet/api/pipeline/checklist/{cid}`

- [ ] **JS — рендер чек-листа в модалке:** контейнер `#cm-checklist`, для каждого item — `<label>` с checkbox + textContent + кнопка ×; toggle через PATCH; добавление через input + Enter/+.

- [ ] **Commit:** `feat(pipeline): checklist CRUD`

### Task 5.4: Relations (manual link)

**Files:** `cabinet/pipeline_service.py`, `cabinet/api.py`.

- [ ] **Функции:**
  - `add_relation(card_id, related_tender_number, kind, by_user_id, company_id)` — найти target card в `pipeline_cards` той же company, INSERT relation
  - `list_relations(card_id)` — JOIN на pipeline_cards для имён related

- [ ] **Endpoints:**
  - `POST /cabinet/api/pipeline/cards/{id}/relations` body `{related_tender_number, kind: 'manual'}`
  - `GET /cabinet/api/pipeline/cards/{id}/relations`
  - `DELETE /cabinet/api/pipeline/cards/{id}/relations/{rid}`

- [ ] **JS — секция «Связанные» в Details tab:** список ссылок «Открыть карточку № …», input «номер тендера для связи».

- [ ] **Commit:** `feat(pipeline): manual card relations`

---

## Phase 6 — AI enrichment + Owner dashboard + Team page

**Цель:** AI-обогащение карточки по кнопке. Страница `/cabinet/team` с метриками для owner. Страница `/cabinet/invite/<token>`.

### Task 6.1: AI-enrichment endpoint + квота

**Files:** `cabinet/pipeline_service.py`, `cabinet/api.py`.

- [ ] **`enrich_card_with_ai(card_id, by_user_id)` в pipeline_service:**

```python
async def enrich_card_with_ai(card_id: int, by_user_id: int) -> Dict:
    """Запускает AI-анализ. Проверяет квоту owner-а, инкрементит счётчик.
    Возвращает {ok, started: True} сразу — реальная работа в фоне.
    """
    async with DatabaseSession() as session:
        card = await session.get(PipelineCard, card_id)
        if not card:
            return {'ok': False, 'error': 'Не найдено'}
        company = await session.get(Company, card.company_id)
        if not company:
            return {'ok': False, 'error': 'Команда не найдена'}
        owner = await session.get(SniperUser, company.owner_user_id)
        if not owner:
            return {'ok': False, 'error': 'Owner не найден'}

        # Проверка квоты: используем sniper_users.ai_analyses_used_month
        # TODO: тариф premium = безлимит
        used = owner.ai_analyses_used_month or 0
        # Лимит: pro=500, premium=∞ (безлимит). Берём из подписки.
        from bot.handlers.subscriptions import SUBSCRIPTION_TIERS
        tier = owner.subscription_tier or 'trial'
        # Здесь упрощение: pro=500, premium/business=безлимит, иначе 0
        limits = {'pro': 500, 'premium': 999999, 'business': 999999}
        limit = limits.get(tier, 0)
        if limit == 0 or used >= limit:
            return {'ok': False, 'error': 'Квота AI исчерпана', 'status': 402}

        # Инкрементим квоту НЕМЕДЛЕННО (защита от race)
        owner.ai_analyses_used_month = used + 1
        await session.commit()

    # Background task
    asyncio.create_task(_do_ai_enrich(card_id, by_user_id))
    return {'ok': True, 'started': True}


async def _do_ai_enrich(card_id: int, by_user_id: int):
    try:
        from tender_sniper.ai_summarizer import summarize_tender
        from tender_sniper.ai_relevance_checker import check_relevance
        async with DatabaseSession() as session:
            card = await session.get(PipelineCard, card_id)
            if not card:
                return
            tender_data = card.data or {}
            summary = await summarize_tender(tender_data)
            recommendation = await check_relevance(tender_data, {'name': 'pipeline'})
            card.ai_summary = summary[:2000] if summary else None
            card.ai_recommendation = (recommendation or '')[:40]
            card.ai_enriched_at = datetime.utcnow()
            history = PipelineCardHistory(
                card_id=card_id, user_id=by_user_id, action='ai_enriched', payload={},
            )
            session.add(history)
            await session.commit()
    except Exception as e:
        logger.error(f'AI enrich failed for card {card_id}: {e}', exc_info=True)
```

(Точные имена существующих AI-функций в `tender_sniper/ai_summarizer.py` и `ai_relevance_checker.py` могут отличаться — проверить и адаптировать импорты.)

- [ ] **Endpoint:**

```python
async def pipeline_ai_enrich(request):
    user = request['user']; company = request['company']
    card_id = int(request.match_info['id'])
    if not await pipeline_service.get_card(card_id, company['id']):
        return web.json_response({'error': 'Not found'}, status=404)
    result = await pipeline_service.enrich_card_with_ai(card_id, user['user_id'])
    if not result['ok']:
        return web.json_response(result, status=result.get('status', 400))
    return web.json_response(result, status=202)

pipeline_ai_enrich = require_team_member(pipeline_ai_enrich)
```

Route: `POST /cabinet/api/pipeline/cards/{id}/ai-enrich`.

- [ ] **JS:** в модалке кнопка `cm-ai-run` → POST → polling `/cards/:id` каждые 2с (max 30 итераций) → когда `ai_enriched_at != null`, обновить summary/recommendation в DOM.

- [ ] **Commit:** `feat(pipeline): AI enrichment with team owner quota`

### Task 6.2: Team page `/cabinet/team` + dashboard метрики

**Files:**
- Create: `cabinet/templates/team.html`, `team.css`, `js/pages/team.js`
- Modify: `cabinet/api.py` (page handler + dashboard endpoint), `routes.py`

- [ ] **Backend `team_dashboard(company_id)`** — возвращает:

```python
async def team_dashboard(company_id: int) -> Dict:
    async with DatabaseSession() as session:
        # Активных карточек total
        total = await session.scalar(
            select(func.count()).select_from(PipelineCard)
            .where(PipelineCard.company_id == company_id, PipelineCard.archived_at.is_(None))
        )
        # По стадиям
        result = await session.execute(
            select(PipelineCard.stage, func.count()).select_from(PipelineCard)
            .where(PipelineCard.company_id == company_id, PipelineCard.archived_at.is_(None))
            .group_by(PipelineCard.stage)
        )
        by_stage = {s: c for s, c in result.all()}
        # На члена
        result = await session.execute(
            select(PipelineCard.assignee_user_id, func.count()).select_from(PipelineCard)
            .where(PipelineCard.company_id == company_id, PipelineCard.archived_at.is_(None))
            .group_by(PipelineCard.assignee_user_id)
        )
        per_member = {uid: c for uid, c in result.all() if uid is not None}
        # Зависшие (>7 дней без updated_at)
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=7)
        stale = await session.execute(
            select(PipelineCard).where(
                PipelineCard.company_id == company_id,
                PipelineCard.archived_at.is_(None),
                PipelineCard.updated_at < cutoff,
                PipelineCard.stage != 'RESULT',
            ).limit(20)
        )
        stale_list = [{
            'id': c.id, 'tender_number': c.tender_number,
            'stage': c.stage, 'assignee_user_id': c.assignee_user_id,
            'updated_at': c.updated_at,
        } for c in stale.scalars().all()]
        # Win/Lose за last_30_days
        cutoff_30 = datetime.utcnow() - timedelta(days=30)
        won = await session.scalar(
            select(func.count()).select_from(PipelineCard)
            .where(PipelineCard.company_id == company_id,
                   PipelineCard.result == 'won', PipelineCard.updated_at >= cutoff_30)
        )
        lost = await session.scalar(
            select(func.count()).select_from(PipelineCard)
            .where(PipelineCard.company_id == company_id,
                   PipelineCard.result == 'lost', PipelineCard.updated_at >= cutoff_30)
        )
        won_sum = await session.scalar(
            select(func.coalesce(func.sum(PipelineCard.sale_price), 0)).select_from(PipelineCard)
            .where(PipelineCard.company_id == company_id,
                   PipelineCard.result == 'won', PipelineCard.updated_at >= cutoff_30)
        )
        margin_sum = await session.scalar(
            select(func.coalesce(func.sum(PipelineCard.sale_price - PipelineCard.purchase_price), 0))
            .select_from(PipelineCard)
            .where(PipelineCard.company_id == company_id,
                   PipelineCard.result == 'won',
                   PipelineCard.purchase_price.is_not(None),
                   PipelineCard.updated_at >= cutoff_30)
        )
        return {
            'total_active': total or 0,
            'by_stage': by_stage,
            'per_member': per_member,
            'stale': stale_list,
            'last30': {
                'won': won or 0, 'lost': lost or 0,
                'won_sum': float(won_sum or 0),
                'margin_sum': float(margin_sum or 0),
            },
        }
```

В `team_service.py`: re-export через `from cabinet.pipeline_service import ...` либо размещение в `team_service.py`.

- [ ] **Page handler `team_page`** — рендер `team.html` с `members`, `dashboard` (если owner), `invites` (если owner), `is_owner`. Member видит только себя и кнопку «Покинуть».

- [ ] **API endpoints:**
  - `GET /cabinet/api/team/dashboard` (owner-only) → JSON с метриками
  - `GET /cabinet/api/team/invites` (owner) — список активных
  - `POST /cabinet/api/team/invites` (owner) → создание + URL
  - `DELETE /cabinet/api/team/invites/{id}` (owner) → revoke
  - `GET /cabinet/api/team/members`
  - `DELETE /cabinet/api/team/members/{id}` (owner)
  - `POST /cabinet/api/team/leave` (member) → `leave_team`

- [ ] **`team.html`** — секция членов (список с кнопками удалить), секция инвайтов (только owner — список + кнопка «Создать»), секция метрик (только owner — bar-chart по стадиям, список зависших, win/lose, ₽ выигрышей и маржи), кнопка «Покинуть команду» (member).

- [ ] **`team.js`** — fetch `/api/team/dashboard` после load, рендер метрик через createElement (без innerHTML), обработчики кнопок инвайтов/удалить/покинуть.

- [ ] **Commit:** `feat(team): owner dashboard with team metrics + member CRUD`

### Task 6.3: Invite page `/cabinet/invite/<token>`

**Files:**
- Create: `cabinet/templates/invite.html`, `invite.css`
- Modify: `cabinet/api.py`, `routes.py`

- [ ] **`invite_page` handler:**
  - Если не залогинен → redirect на `/cabinet/login?next=/cabinet/invite/<token>`
  - Если залогинен → `validate_invite_token(token)`:
    - Невалиден → render `invite.html` с error message
    - Валиден → `accept_invite(token, user_id)`:
      - `ok=True, already=True` → редирект на `/cabinet/pipeline` с Toast
      - `ok=True` → редирект с Toast «Добро пожаловать»
      - `ok=False` → render с error

- [ ] **`invite.html`** — простая страница: либо «Приглашение в команду {name}» + кнопка «Принять», либо ошибка.

- [ ] **Routes:** `app.router.add_get('/cabinet/invite/{token}', api.invite_page)`

- [ ] **Commit:** `feat(team): invite link accept page`

---

## Phase 7 — Bitrix migration script + Archive job

**Цель:** Перенести все активные сделки из Bitrix24 в pipeline. Запустить background job для архивирования lose-карточек.

### Task 7.1: Скрипт `scripts/migrate_bitrix_to_pipeline.py`

**Files:** `scripts/migrate_bitrix_to_pipeline.py`.

Алгоритм (см. спек §7):
1. Аргументы: `--company-id <id>`, `--dry-run`, `--limit <N>`.
2. Читает `BITRIX_WEBHOOK_URL` из env (тот же что в `bot/handlers/bitrix24.py`).
3. Постранично вызывает `crm.deal.list?start=N` пока `total > N`.
4. Для каждой сделки:
   - Извлечь `UF_CRM_TENDER_NUMBER` или fallback парсингом TITLE/COMMENTS.
   - Лукап в `tender_cache` — если нет, лог warning, skip.
   - Маппинг:
     ```python
     STAGE_MAP = {
         'NEW': 'FOUND',
         'UC_OZCYR2': 'IN_WORK',
         'LOSE': 'RESULT',  # + result='lost'
     }
     ```
   - INSERT в `pipeline_cards` с `source='bitrix_import'`, `created_by=company.owner_user_id`, `assignee_user_id=company.owner_user_id`, `data={...мета из tender_cache + bitrix_deal_id, bitrix_original_stage}`, AI-поля если есть.
   - INSERT history `imported_from_bitrix`.
5. Constraint `uq_pipeline_company_tender` обеспечивает идемпотентность — если карточка есть, лог warning skip.
6. По окончании: `Imported: X, Skipped: Y, Errors: Z`. Non-zero exit code если ошибки.

```python
# scripts/migrate_bitrix_to_pipeline.py
import argparse, asyncio, os, logging, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import aiohttp
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from database import DatabaseSession, Company, PipelineCard, PipelineCardHistory, TenderCache

logger = logging.getLogger(__name__)

STAGE_MAP = {'NEW': 'FOUND', 'UC_OZCYR2': 'IN_WORK', 'LOSE': 'RESULT'}

async def fetch_deals(webhook_url, start=0):
    async with aiohttp.ClientSession() as session:
        async with session.get(f'{webhook_url}crm.deal.list', params={'start': start}) as resp:
            return await resp.json()

async def migrate(company_id: int, dry_run: bool = False, limit: int = 1000):
    webhook = os.environ['BITRIX_WEBHOOK_URL']
    if not webhook.endswith('/'):
        webhook += '/'

    imported = skipped = errors = 0
    start = 0
    processed = 0
    while processed < limit:
        data = await fetch_deals(webhook, start)
        deals = data.get('result', [])
        if not deals:
            break

        for deal in deals:
            processed += 1
            try:
                tender_number = (
                    deal.get('UF_CRM_TENDER_NUMBER')
                    or _extract_tender_number(deal.get('TITLE', '') + ' ' + deal.get('COMMENTS', ''))
                )
                if not tender_number:
                    skipped += 1
                    continue

                async with DatabaseSession() as session:
                    cache = await session.scalar(
                        select(TenderCache).where(TenderCache.tender_number == tender_number)
                    )
                    if not cache:
                        logger.warning(f'Tender {tender_number} not in cache — skip')
                        skipped += 1
                        continue

                    company = await session.get(Company, company_id)
                    bitrix_stage = deal.get('STAGE_ID', 'NEW')
                    new_stage = STAGE_MAP.get(bitrix_stage, 'IN_WORK')
                    result = 'lost' if bitrix_stage == 'LOSE' else None

                    if dry_run:
                        logger.info(f'DRY: {tender_number} → {new_stage}')
                        imported += 1
                        continue

                    card = PipelineCard(
                        company_id=company_id,
                        tender_number=tender_number,
                        stage=new_stage,
                        assignee_user_id=company.owner_user_id,
                        source='bitrix_import',
                        result=result,
                        ai_summary=deal.get('UF_CRM_AI_SUMMARY'),
                        ai_recommendation=(deal.get('UF_CRM_AI_RECOMMENDATION') or '')[:40] or None,
                        data={
                            'name': cache.name,
                            'customer': cache.customer,
                            'region': cache.region,
                            'price_max': float(cache.price) if cache.price else None,
                            'deadline': cache.deadline.isoformat() if cache.deadline else None,
                            'url': f'https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber={tender_number}',
                            'bitrix_deal_id': deal.get('ID'),
                            'bitrix_original_stage': bitrix_stage,
                            'bitrix_opportunity': deal.get('OPPORTUNITY'),
                        },
                        created_by=company.owner_user_id,
                    )
                    session.add(card)
                    await session.flush()
                    history = PipelineCardHistory(
                        card_id=card.id, user_id=company.owner_user_id,
                        action='imported_from_bitrix',
                        payload={'bitrix_deal_id': deal.get('ID'), 'original_stage': bitrix_stage},
                    )
                    session.add(history)
                    try:
                        await session.commit()
                        imported += 1
                    except IntegrityError:
                        await session.rollback()
                        logger.warning(f'Tender {tender_number} already in pipeline')
                        skipped += 1
            except Exception as e:
                logger.error(f'Error processing deal: {e}', exc_info=True)
                errors += 1

        if 'next' not in data:
            break
        start = data['next']

    print(f'Imported: {imported}, Skipped: {skipped}, Errors: {errors}')
    return errors == 0


def _extract_tender_number(text):
    m = re.search(r'\b\d{19,20}\b', text or '')
    return m.group() if m else None


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--company-id', type=int, required=True)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--limit', type=int, default=1000)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO)
    success = asyncio.run(migrate(args.company_id, args.dry_run, args.limit))
    sys.exit(0 if success else 1)
```

- [ ] **Smoke (dry-run):**

```bash
railway run --service=tender-ai-bot python -m scripts.migrate_bitrix_to_pipeline --company-id 1 --dry-run
```

- [ ] **Реальный запуск (после ручного подтверждения):**

```bash
railway run --service=tender-ai-bot python -m scripts.migrate_bitrix_to_pipeline --company-id 1
```

- [ ] **Commit:** `feat(pipeline): bitrix migration script (idempotent, dry-run support)`

### Task 7.2: Background job для архивации lose-карточек

**Files:** `tender_sniper/jobs/archive_lost_cards.py`, `bot/main.py`.

- [ ] **Создать `tender_sniper/jobs/archive_lost_cards.py`:**

```python
import asyncio, logging
from datetime import datetime, timedelta
from sqlalchemy import update
from database import DatabaseSession, PipelineCard

logger = logging.getLogger(__name__)
ARCHIVE_AGE_DAYS = 90


async def archive_old_lost_cards():
    cutoff = datetime.utcnow() - timedelta(days=ARCHIVE_AGE_DAYS)
    async with DatabaseSession() as session:
        result = await session.execute(
            update(PipelineCard)
            .where(
                PipelineCard.result == 'lost',
                PipelineCard.archived_at.is_(None),
                PipelineCard.updated_at < cutoff,
            )
            .values(archived_at=datetime.utcnow())
        )
        await session.commit()
        if result.rowcount:
            logger.info(f'Archived {result.rowcount} lost cards older than {ARCHIVE_AGE_DAYS}d')


async def archive_loop():
    """Запускать раз в 24 часа."""
    while True:
        try:
            await archive_old_lost_cards()
        except Exception as e:
            logger.error(f'archive job failed: {e}', exc_info=True)
        await asyncio.sleep(86400)
```

- [ ] **Регистрация в `bot/main.py`** (рядом с другими background tasks startup):

```python
from tender_sniper.jobs.archive_lost_cards import archive_loop
asyncio.create_task(archive_loop())
```

- [ ] **Commit:** `feat(pipeline): archive lost cards background job (90d cutoff)`

### Task 7.3: Pipeline archive page `/cabinet/pipeline/archive`

**Files:** `cabinet/templates/pipeline_archive.html`, `cabinet/api.py`, `routes.py`.

- [ ] **`pipeline_archive_page` handler** — `list_archived_cards(company_id)`, render как простой список карточек (без drag, без модалки в этой фазе).
- [ ] **Возможность вернуть карточку из архива (только owner):** endpoint `POST /cabinet/api/pipeline/cards/{id}/unarchive` → `archived_at = None`.
- [ ] **Commit:** `feat(pipeline): archive page with unarchive (owner)`

---

## Phase 8 — Polish + e2e smoke

### Task 8.1: Integration test (e2e flow)

**Files:** `tests/integration/test_pipeline_e2e.py`.

```python
@pytest.mark.asyncio
async def test_full_card_lifecycle(db_session, make_user):
    # 1. Создать owner и company
    owner = await make_user()
    company = await get_or_create_company_for_user(owner['id'])

    # 2. Добавить tender_cache (mock)
    async with DatabaseSession() as session:
        from database import TenderCache
        from datetime import date
        tender = TenderCache(
            tender_number='0123456789012345001', name='Test Tender',
            customer='Test Customer', region='Москва', price=100000,
            deadline=date(2026, 5, 30), law_type='44',
        )
        session.add(tender)
        await session.commit()

    # 3. Создать карточку
    result = await create_card_from_tender(
        company['id'], '0123456789012345001', creator_user_id=owner['id'],
    )
    assert 'card' in result
    card_id = result['card']['id']

    # 4. Прогнать через все стадии
    for stage in ['IN_WORK', 'RFQ', 'QUOTED', 'SUBMITTED']:
        r = await move_card_stage(card_id, stage, by_user_id=owner['id'])
        assert r['ok']

    # 5. Установить win
    r = await set_card_result(card_id, 'won', by_user_id=owner['id'])
    assert r['ok']
    assert r['card']['stage'] == 'RESULT'
    assert r['card']['result'] == 'won'

    # 6. Добавить заметку, цены, файл
    await add_note(card_id, 'Тестовая заметка', by_user_id=owner['id'])
    await set_prices(card_id, purchase_price=80000, sale_price=100000, by_user_id=owner['id'])

    # 7. Проверить history
    history = await list_history(card_id)
    actions = [h['action'] for h in history]
    assert 'created' in actions
    assert 'won' in actions
    assert 'note_added' in actions
    assert 'price_set' in actions
```

Run: `pytest tests/integration/test_pipeline_e2e.py -v`
Expected: passed.

- [ ] **Commit:** `test(pipeline): e2e card lifecycle integration test`

### Task 8.2: RBAC tests

**Files:** `tests/unit/test_pipeline_rbac.py`.

Тесты:
- Юзер не в команде → 403 на любой pipeline endpoint.
- Member может move_card_stage. Owner тоже.
- Member НЕ может delete карточку — 403. Owner может.
- Member НЕ может добавить инвайт — 403. Owner может.
- Member НЕ может удалить другого члена — 403. Owner может (но не себя).

- [ ] **Commit:** `test(pipeline): RBAC tests`

### Task 8.3: Manual smoke checklist

Открыть deploy, проверить вручную:

- [ ] Зайти на `/cabinet/pipeline` под Николаем — автосоздание team, пустая доска
- [ ] В ленте кликнуть «В работу» на тендере → карточка в FOUND
- [ ] Перетащить карточку drag-n-drop через все стадии
- [ ] Открыть модалку → добавить заметку → добавить пункт чек-листа → загрузить файл (тестовый PDF) → запустить AI-анализ (если квота)
- [ ] Drop в RESULT → выбор Win/Lose → карточка отмечена
- [ ] Зайти `/cabinet/team` → создать инвайт-ссылку
- [ ] Открыть инвайт-URL во второй сессии (логин под другим юзером) → присоединиться к команде
- [ ] Из модалки удалить файл → удалить карточку (только owner)
- [ ] Прогнать `migrate_bitrix_to_pipeline.py --dry-run` → проверить вывод
- [ ] Запустить реальную миграцию → проверить что карточки появились на доске

### Task 8.4: Deploy + Bitrix decommission

- [ ] Push в main → wait Railway healthcheck
- [ ] Real Bitrix migration script run
- [ ] Командные тесты (1-2 дня) — Николай + 1 коллега работают параллельно в Pipeline и Bitrix
- [ ] Decommission Bitrix24: ~2 недели после полного перехода

---

## Self-Review Checklist

После реализации всех Phases:

- [ ] **Spec coverage:** Каждая секция спека (1-21) реализована или явно отложена в backlog.
- [ ] **All tests passing:** `pytest tests/ -v` зелёный.
- [ ] **No innerHTML in JS:** `grep -rn "innerHTML" cabinet/static/js/` пусто (XSS prevention).
- [ ] **No raw user input in SQL:** все запросы — SQLAlchemy ORM, никакого raw text.
- [ ] **File upload security:** path traversal невозможен (`_safe_filename`), размер проверен, mime-тип проверен.
- [ ] **RBAC consistency:** все pipeline endpoints проверяют `company_id`. Никаких `WHERE id = X` без `AND company_id = ?`.
- [ ] **Migration idempotent:** повторный запуск миграции Bitrix не дублирует.






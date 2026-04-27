# Pipeline / Kanban в кабинете — Design Spec

**Дата:** 2026-04-27
**Автор:** Николай Чижик (с агентом)
**Статус:** Draft, ожидает финального ревью

---

## 1. Цель и контекст

### Что строим

`/cabinet/pipeline` — Kanban-доска для команды Николая в веб-кабинете Tender Sniper. Полная замена Bitrix24 как CRM-инструмента для работы с тендерами.

### Зачем

- **Уйти от платного Bitrix24** (~3 000 ₽/мес).
- **Подходящий под bench**: текущий Bitrix-флоу Николая — это b2b-перепродажи (тендер → запрос у поставщика → КП → подача заявки → результат). Стандартный Bitrix-pipeline не отражает эту специфику.
- **Доступ для всей команды (5-6 человек)** — общая доска, видимость кто чем занят, контроль работы.
- **Будущая интеграция с holodilnik.ru** (поиск артикулов поставщика под тендер) — стадии «Запрос предложений» и «Получено КП» — естественные точки входа для этой фичи.

### Цели MVP

1. Заменить Bitrix24 для команды Николая (5-6 человек) к концу разработки.
2. Импортировать существующие активные сделки из Bitrix.
3. Дать единую доску с 6 стадиями процесса b2b-перепродажи.
4. Базовый team workspace: owner + members, инвайты по ссылке.
5. Owner dashboard для контроля работы команды.

### Non-goals (отложено на v2)

- Live-обновления через WebSocket/SSE.
- Push-уведомления (TG/email/in-app).
- Custom стадии (только захардкоженные 6).
- Mobile-оптимизированная доска.
- Авто-предложение связанных тендеров (только ручная привязка).
- Кастомные роли сверх owner/member.
- Multi-company per user (один пользователь = одна команда).
- Реальная интеграция с holodilnik.ru — кнопка-заглушка в MVP, реализация в отдельном спеке.

---

## 2. Scope

### In-scope

- Новая страница `/cabinet/pipeline` с Kanban-доской на 6 стадий.
- Модалка карточки тендера с табами: Детали, Заметки, Файлы, Чек-лист, История.
- Страница `/cabinet/pipeline/archive` для проигранных карточек старше 90 дней.
- Страница `/cabinet/team` — owner dashboard + управление командой.
- Инвайт-flow `/cabinet/invite/<token>`.
- Кнопка «В работу» на карточке тендера в ленте `/cabinet/`.
- Скрипт миграции из Bitrix24.
- Данные: 8 новых таблиц в БД.
- API: ~15 endpoints.
- Drag-and-drop через Sortable.js.
- Optimistic UI для быстрого drag.
- Файловое хранилище на Railway Volume.

### Out-of-scope

См. Non-goals выше. Holodilnik-интеграция — отдельный спек.

---

## 3. Архитектура

### Подход: server-render + optimistic JS (гибрид)

- **Server-render** доски при первом заходе — Jinja2 рендерит весь HTML за один запрос. Быстрый first paint. Соответствует архитектурному принципу v3 («vanilla JS + лёгкие утилиты, без фреймворков»).
- **Sortable.js** (vendor-локально, ~30Kb, без CDN) — для drag-n-drop карточек между колонками.
- **Optimistic UI** для drag: при drop карточка сразу переезжает в DOM, параллельно отправляется `POST /api/pipeline/cards/:id/stage`. Если сервер ответил 4xx/5xx — карточка возвращается на старое место + Toast с ошибкой.
- **JSON API** для всех операций модалки (создание, заметки, файлы, чек-лист, AI-обогащение, win/lose).
- **Без live-обновления** — F5 для синхронизации с действиями команды. Команда 5-6 человек, низкий шанс одновременной работы над одной карточкой.

### Стек

- Backend: aiohttp + aiohttp-jinja2 (как остальной кабинет)
- ORM: SQLAlchemy async (как везде)
- Frontend: vanilla JS + Sortable.js
- Шаблоны: Jinja2 на базе `_base.html` v3
- CSS: per-page файл `cabinet/static/css/pages/pipeline.css` + `team.css`, базовые токены/компоненты v3 уже есть

### Файлы

```
cabinet/
  templates/
    pipeline.html                 — доска
    pipeline_archive.html         — архив
    team.html                     — owner dashboard + управление командой
    invite.html                   — приём инвайта
    _modal_card.html              — общая модалка карточки (include в pipeline.html)
  static/
    css/pages/
      pipeline.css
      team.css
      invite.css
    js/pages/
      pipeline.js                 — drag, optimistic UI, модалка
      team.js                     — статистика, инвайт-ссылки
    vendor/
      Sortable.min.js             — Sortable.js, vendored

cabinet/
  routes.py                       — регистрация маршрутов pipeline/team/invite
  api.py                          — новые endpoints (~15 шт)
  pipeline_service.py             — доменная логика (move card, set result, archive)
  team_service.py                 — invite tokens, member CRUD, dashboard agg

scripts/
  migrate_bitrix_to_pipeline.py   — one-shot миграция

alembic/versions/
  20260427_pipeline_tables.py     — миграция БД
```

---

## 4. Модель данных

### Новые таблицы

```python
class Company(Base):
    __tablename__ = 'companies'
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    owner_user_id = Column(Integer, ForeignKey('sniper_users.id'), nullable=False)
    plan_quota_data = Column(JSON, default=dict)  # snapshot тарифа owner на момент использования
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class CompanyMember(Base):
    __tablename__ = 'company_members'
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('sniper_users.id'), nullable=False)
    role = Column(String(16), nullable=False)  # 'owner' | 'member'
    joined_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint('user_id', name='uq_company_members_user'),  # один user = одна команда
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
    max_uses = Column(Integer, default=10)
    used_count = Column(Integer, default=0)


class PipelineCard(Base):
    __tablename__ = 'pipeline_cards'
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False, index=True)
    tender_number = Column(String(40), nullable=False, index=True)
    stage = Column(String(20), nullable=False, default='FOUND')  # FOUND/IN_WORK/RFQ/QUOTED/SUBMITTED/RESULT
    assignee_user_id = Column(Integer, ForeignKey('sniper_users.id'), nullable=True)
    filter_id = Column(Integer, ForeignKey('sniper_filters.id'), nullable=True)
    source = Column(String(20), nullable=False, default='feed')  # feed/manual/bitrix_import
    result = Column(String(10), nullable=True)  # 'won' | 'lost' | null
    purchase_price = Column(Numeric(14, 2), nullable=True)  # закупочная (от поставщика)
    sale_price = Column(Numeric(14, 2), nullable=True)     # наша цена в заявке (default = max contract price)
    ai_summary = Column(Text, nullable=True)
    ai_recommendation = Column(String(40), nullable=True)
    ai_enriched_at = Column(DateTime, nullable=True)
    archived_at = Column(DateTime, nullable=True)  # для lose >90 дней
    data = Column(JSON, default=dict)  # кэш базовой инфо тендера: name, customer, region, deadline, price_max, url
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
    # 'created'|'stage_changed'|'assigned'|'note_added'|'file_uploaded'|'file_deleted'|
    # 'price_set'|'won'|'lost'|'ai_enriched'|'checklist_added'|'checklist_done'|
    # 'imported_from_bitrix'|'related_added'
    payload = Column(JSON, default=dict)  # детали действия (from_stage, to_stage, file_id, note_id и т.д.)
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
    path = Column(String(500), nullable=False)  # /app/uploads/<company_id>/<card_id>/<file_id>_<filename>
    is_generated = Column(Boolean, default=False)  # true если auto-сгенерирован через document_generator
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
    kind = Column(String(40), nullable=False)  # 'similar_customer' | 'similar_keywords' | 'manual'
    created_by = Column(Integer, ForeignKey('sniper_users.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint('card_id', 'related_card_id', name='uq_card_relation'),
    )
```

### Стадии

```python
STAGE_FOUND = 'FOUND'           # Найденные
STAGE_IN_WORK = 'IN_WORK'       # Взято в работу
STAGE_RFQ = 'RFQ'               # Запрос предложений (поставщику)
STAGE_QUOTED = 'QUOTED'         # Получено КП
STAGE_SUBMITTED = 'SUBMITTED'   # Участвуем (заявка подана)
STAGE_RESULT = 'RESULT'         # Результат (Win/Lose различаются полем result)

ALL_STAGES = [STAGE_FOUND, STAGE_IN_WORK, STAGE_RFQ, STAGE_QUOTED, STAGE_SUBMITTED, STAGE_RESULT]

STAGE_LABELS = {
    'FOUND': 'Найденные',
    'IN_WORK': 'Взято в работу',
    'RFQ': 'Запрос предложений',
    'QUOTED': 'Получено КП',
    'SUBMITTED': 'Участвуем',
    'RESULT': 'Результат',
}
```

> **Открытый вопрос:** «Найденные» как название первой колонки немного двусмысленно (звучит как «автоматически нашлось»), хотя по флоу это «взяли в работу из ленты, ещё не оценено». Альтернативы: «Новые», «На оценке». Решение оставляется за финальной правкой UI.

### Расчёт маржи

```python
margin = sale_price - purchase_price
margin_pct = (margin / sale_price) * 100  # если sale_price > 0
```

Отображается на карточке если оба поля заполнены. Цвет: зелёный (>5%), оранжевый (0-5%), красный (<0%).

---

## 5. Routes

### Pages

| Path | Метод | Описание | Доступ |
|---|---|---|---|
| `/cabinet/pipeline` | GET | Доска (server-render всех активных карточек команды) | team_member |
| `/cabinet/pipeline/archive` | GET | Lose-карточки старше 90 дней | team_member |
| `/cabinet/team` | GET | Owner dashboard + управление командой | owner_or_member (member видит только себя) |
| `/cabinet/invite/<token>` | GET | Страница приёма инвайта (требует Telegram Login Widget) | public |

### API

| Path | Метод | Описание |
|---|---|---|
| `/cabinet/api/pipeline/from-feed/<tender_number>` | POST | Создать карточку из ленты («В работу») → стадия FOUND |
| `/cabinet/api/pipeline/cards` | POST | Создать карточку вручную (form: tender_number) |
| `/cabinet/api/pipeline/cards/:id` | GET | Полные данные карточки + history + notes + files + checklist |
| `/cabinet/api/pipeline/cards/:id` | DELETE | Удалить (только owner) |
| `/cabinet/api/pipeline/cards/:id/stage` | POST | Сменить стадию: `{stage: 'IN_WORK'}` |
| `/cabinet/api/pipeline/cards/:id/result` | POST | Win/Lose: `{result: 'won' | 'lost'}` (автоматом → stage=RESULT) |
| `/cabinet/api/pipeline/cards/:id/assignee` | POST | Назначить ответственного: `{user_id: 5}` |
| `/cabinet/api/pipeline/cards/:id/prices` | POST | `{purchase_price?, sale_price?}` |
| `/cabinet/api/pipeline/cards/:id/notes` | POST | Добавить заметку: `{text}` |
| `/cabinet/api/pipeline/cards/:id/files` | POST | Загрузить файл (multipart/form-data) |
| `/cabinet/api/pipeline/cards/:id/files/:fid` | DELETE | Удалить файл |
| `/cabinet/api/pipeline/cards/:id/checklist` | POST | Добавить пункт: `{text}` |
| `/cabinet/api/pipeline/cards/:id/checklist/:cid` | PATCH | Toggle done: `{done: true}` |
| `/cabinet/api/pipeline/cards/:id/checklist/:cid` | DELETE | Удалить пункт |
| `/cabinet/api/pipeline/cards/:id/relations` | POST | Привязать тендер: `{related_tender_number, kind}` |
| `/cabinet/api/pipeline/cards/:id/ai-enrich` | POST | Запустить AI-обогащение (по кнопке) |
| `/cabinet/api/team/invites` | GET | Список активных инвайтов (only owner) |
| `/cabinet/api/team/invites` | POST | Создать инвайт (only owner) → `{token, url, expires_at}` |
| `/cabinet/api/team/invites/:id` | DELETE | Отозвать (only owner) |
| `/cabinet/api/team/members` | GET | Список членов |
| `/cabinet/api/team/members/:id` | DELETE | Удалить члена (only owner; нельзя удалить самого owner) |
| `/cabinet/api/team/dashboard` | GET | Метрики (only owner) |

### Middleware

```python
async def require_team_member(request, handler):
    """Проверяет что user в company_members. Кладёт company и role в request."""

async def require_owner(request, handler):
    """Только для owner-only действий."""
```

---

## 6. Авторизация и роли

### Модель доступа

- **owner** — полные права: управление командой, удаление членов, создание инвайтов, удаление карточек, смена настроек.
- **member** — может: создавать карточки, перемещать стадии, назначать ответственных (любого из команды), редактировать заметки/файлы/чек-лист (свои и чужие), смотреть всю доску и архив.
- **member НЕ может**: удалять карточки, управлять командой, отзывать инвайты, удалять других членов.

### Один user = одна команда

Уникальный constraint `uq_company_members_user`. Если пользователь уже в одной команде и переходит по инвайт-ссылке другой — ошибка «Уже состоите в команде X. Сначала покиньте её».

### Создание команды

При первом заходе owner-кандидата (любой пользователь) в `/cabinet/pipeline`:
- Если в `company_members` нет записи → создаётся `Company` (name = «Команда {first_name}»), пользователь добавляется как owner.
- Иначе работает в существующей команде.

Это означает что каждый пользователь, открывший Pipeline, автоматически создаёт свою команду как owner. Для одиночек pipeline работает корректно (команда из одного человека).

### Существующие пользователи без team_member

Существующие пользователи Telegram/Max бота (без записи в `company_members`) до первого захода в Pipeline продолжают работать с TG/Max ботом без изменений. Pipeline им просто недоступен (попытка открыть `/cabinet/pipeline` → автоматически создаст команду).

---

## 7. Bitrix24 → Pipeline миграция

### Скрипт `scripts/migrate_bitrix_to_pipeline.py`

Параметры:
```bash
python -m scripts.migrate_bitrix_to_pipeline --company-id 1 [--dry-run] [--limit 1000]
```

### Алгоритм

1. Читает `BITRIX_WEBHOOK_URL` из env (тот же, что используется ботом).
2. `crm.deal.list` постранично, размер страницы 50, `start=0,50,100,...`.
3. Для каждой сделки:
   - Извлекает `UF_CRM_TENDER_NUMBER` (если есть) или fallback к парсингу из TITLE/COMMENTS.
   - Ищет `tender_cache` по номеру → если нет, скипает с warning в лог.
   - Маппинг стадий:
     - `NEW` → `FOUND`
     - `UC_OZCYR2` (AI-стадия Николая) → `IN_WORK`
     - `LOSE` → `RESULT` + `result='lost'`
     - Любая другая → `IN_WORK` (безопасный дефолт)
   - Переносит:
     - `UF_CRM_AI_SUMMARY` → `pipeline_cards.ai_summary`
     - `UF_CRM_AI_RECOMMENDATION` → `ai_recommendation`
     - `OPPORTUNITY` → `data.bitrix_opportunity` (для справки, не используется в логике)
     - `STAGE_ID` → `data.bitrix_original_stage`
     - Bitrix `ID` → `data.bitrix_deal_id`
   - `source = 'bitrix_import'`
   - `created_by = company.owner_user_id`
   - `assignee_user_id = company.owner_user_id` (по умолчанию — на owner; в Bitrix есть `ASSIGNED_BY_ID` но матчить его на наших users нельзя без доп. логики)
   - Создаёт запись в `pipeline_card_history` с `action='imported_from_bitrix'`.
4. `--dry-run` — печатает план миграции без записи в БД.
5. По завершении выводит сводку: `Imported: X, Skipped: Y, Errors: Z`.

### Идемпотентность

Constraint `uq_pipeline_company_tender (company_id, tender_number)` — если карточка с таким tender_number уже есть в команде, скрипт скипает с warning. Можно безопасно перезапускать.

### Обработка ошибок

- Если Bitrix API вернул ошибку → retry с exponential backoff (3 попытки).
- Если конкретный deal не парсится → лог с warning, продолжает остальное.
- В конце — non-zero exit code если были ошибки в финальной сводке.

### Запуск

Запускается **один раз вручную** Николаем после деплоя. Не интегрируется в CI/CD. Не запускается автоматически на старте бота.

---

## 8. Команда и инвайты

### Создание инвайт-ссылки

Owner на `/cabinet/team` жмёт «Создать инвайт-ссылку»:
- Генерируется token: `secrets.token_urlsafe(24)` → 32 символа URL-safe
- TTL: 7 дней с момента создания
- max_uses: 10 (захардкожено для MVP)
- URL: `https://tendersniper.ru/cabinet/invite/<token>`
- Отображается в UI с кнопками «Скопировать», «Отозвать»

### Приём инвайта

Юзер открывает `https://tendersniper.ru/cabinet/invite/<token>`:
1. Проверка токена:
   - Существует, не отозван, `expires_at > now`, `used_count < max_uses` → ok.
   - Иначе → страница «Ссылка недействительна» с описанием причины.
2. Если юзер не залогинен → Telegram Login Widget. После логина — продолжаем.
3. Проверка членства:
   - Уже в `company_members` этой company → редирект на `/cabinet/pipeline` с Toast «Вы уже в команде».
   - Уже в `company_members` другой company → страница «Уже состоите в команде "X". Сначала покиньте её через owner-а той команды».
   - Нет членства нигде → создаётся запись `(company_id, user_id, role='member')`. `team_invites.used_count += 1`. Редирект на `/cabinet/pipeline` с Toast «Добро пожаловать в команду!».
4. История: добавляется запись `pipeline_card_history`? Нет, history только для карточек. Для team действий — отдельный лог не делаем в MVP (можно добавить в v2).

### Удаление члена

Owner на `/cabinet/team` рядом с member-ом видит кнопку «Удалить»:
- Удаляется запись из `company_members`.
- Карточки, где он был `assignee` — переназначаются на owner (с записью в `card_history`: `action='assigned'`, `payload={'reason': 'previous_assignee_removed'}`).
- Owner себя удалить не может.
- Если в команде остался только owner — это ок (одиночная команда).

### Покинуть команду

Member на `/cabinet/team` видит кнопку «Покинуть команду» — удаляет себя из `company_members`. Карточки переходят к owner. Owner покинуть свою команду не может (UI без кнопки).

---

## 9. Доска UI (`/cabinet/pipeline`)

### Раскладка

- **Главная зона:** 6 колонок Kanban на всю ширину main-области (после sidebar 220px). На 1280px-экране ~190-200px на колонку.
- **Right rail** скрыт на странице Pipeline (доска занимает всё).
- **Шапка:** заголовок «Pipeline» + кнопка «Создать вручную» + кнопка «Архив» + ссылка на `/cabinet/team`.

### Колонки

- 6 колонок: Найденные / Взято в работу / Запрос предложений / Получено КП / Участвуем / Результат
- Колонки 3-4 (RFQ + QUOTED) подсвечены синим (`--supplier-dim`) — визуально выделяем стадии работы с поставщиком.
- Над каждой колонкой — заголовок (mono uppercase) + счётчик карточек.
- Под счётчиком в колонке RFQ — мини-индикатор «всего запросов отправлено» (на будущее с holodilnik).

### Карточка (полная плотность)

Поля видимые на доске:
- Название (2 строки max, ellipsis)
- Цена (mono, bold)
- Дедлайн (если ≤ 3 дня — красная hot-метка, иначе серая)
- Заказчик (1 строка, ellipsis)
- Регион (mono, мелкий)
- AI-балл (если есть) — пилюля `AI 78` accent-цвет
- Маржа (если есть) — зелёная пилюля `+18%`
- Тег фильтра — серая пилюля
- Аватар ответственного (16px, инициалы) — справа внизу

Win-карточки: левый бордер 3px зелёный.
Lose-карточки: левый бордер 3px красный + opacity 0.55.

### Drag-n-drop

- Sortable.js на каждой колонке (`<div class="kb-col">`).
- При drop:
  1. Optimistic: карточка сразу в DOM целевой колонки, счётчики обновляются.
  2. `POST /api/pipeline/cards/:id/stage` с `{stage: 'IN_WORK'}`.
  3. Если ok → ничего не меняем.
  4. Если ошибка → возвращаем карточку обратно, Toast с сообщением ошибки.
- Drag через стадии разрешён в любом направлении.
- Drag в колонку RESULT не разрешён напрямую (требует выбора Win/Lose) — drop в RESULT открывает модалку с кнопками «Победа / Проигрыш».

### Модалка карточки

Открывается по клику на карточку. Большая модалка (~700px wide) с табами:

**Таб «Детали»**
- Заголовок (название тендера) + ссылка на zakupki.gov.ru
- Стадия (dropdown — альтернатива drag) + Ответственный (dropdown с членами команды)
- Цена тендера, дедлайн подачи, регион, заказчик
- AI-summary + AI-recommendation (если есть) + кнопка «Запустить AI-анализ» (если нет)
- Закупочная цена (input) + расчёт маржи
- Кнопка «Победа» / «Проигрыш» (если стадия SUBMITTED)
- Кнопка «Удалить карточку» (только owner)
- Кнопка «Найти на holodilnik.ru» (заглушка для MVP, см. §11)

**Таб «Заметки»**
- Список заметок с автором и датой (mono mini)
- Textarea + кнопка «Добавить»

**Таб «Файлы»**
- Список файлов: иконка-тип + имя + размер + автор + дата + кнопка скачать + кнопка удалить (свои или owner)
- Дроп-зона для загрузки + кнопка «Выбрать файл»
- Лимит: 10MB на файл, 1GB на команду суммарно

**Таб «Чек-лист»**
- Список пунктов с checkboxes + автором (если done)
- Input + кнопка «Добавить пункт»
- Reorder через drag (Sortable.js — переиспользуем уже подключённый)

**Таб «История»**
- Список действий в обратном порядке (новые сверху)
- Формат: `Аватар + "Имя {action_text}" + дата (mono mini)`
- Action_text формируется по `action` + `payload` (например `"переместил из «Изучаем» в «Заявка»"`)

### Создать вручную

Кнопка в шапке открывает мини-модалку с одним полем «Номер тендера». При вводе:
- Лукап в `tender_cache` → если есть → создаём карточку с заполненной `data` из кэша.
- Если нет → AJAX-парсинг zakupki.gov.ru (используем существующий `tender_sniper/parser`), создаём карточку с распарсенной мета.
- Если парсинг упал → Toast «Тендер не найден на zakupki.gov.ru».

---

## 10. Owner dashboard `/cabinet/team`

### Структура страницы

**Секция 1: Команда**
- Заголовок «Команда: {company_name}»
- Список членов: имя + telegram_username + роль + дата вступления + кнопка «Удалить» (только для member-ов, owner кнопку не имеет).
- Внизу: текущий тариф owner-а («Quota: AI 500/мес, GPT 50/мес»).

**Секция 2: Инвайт-ссылки** (только owner)
- Список активных инвайтов: токен (последние 6 символов), создан, expires, used_count/max_uses, кнопки «Скопировать ссылку», «Отозвать».
- Кнопка «Создать новую ссылку» → создаёт инвайт, копирует в clipboard.

**Секция 3: Метрики** (только owner)
Все цифры — за last_30_days:
- Всего активных карточек: N
- По стадиям: горизонтальная полоса с цветовыми сегментами (FOUND/IN_WORK/RFQ/QUOTED/SUBMITTED/RESULT)
- Карточек на члена: bar-chart по членам команды
- «Зависшие» (без movement >7 дней): список карточек с ответственными
- Конверсия: Win N / Lose M / в работе K
- Сумма выигранных тендеров за месяц: ₽
- Сумма закрытой маржи (sum of `sale_price - purchase_price` для Win-карточек): ₽

### Секция «Покинуть команду» (только member)

Если зашёл member, секции 2-3 скрыты. Внизу — кнопка «Покинуть команду» с подтверждением.

---

## 11. Holodilnik integration point (заглушка для MVP)

В модалке карточки в табе «Детали» показывается кнопка **«Найти на holodilnik.ru»**:
- Видна только когда стадия = `RFQ` (Запрос предложений).
- В MVP кнопка отображается, но при клике показывает Toast «Функция в разработке».
- Реальная реализация — отдельный спек `2026-MM-DD-holodilnik-integration-design.md`.

Архитектурная подготовка под holodilnik:
- Поле `pipeline_cards.data.suppliers` (JSON) зарезервировано: `[{provider: 'holodilnik', sku, price, available, queried_at}, ...]`
- Поле `pipeline_cards.data.tz_extracted` (JSON) — извлечённые из тендерной документации позиции/характеристики.

---

## 12. AI-обогащение

### Поток

- Карточка создаётся пустая (без AI). На карточке нет AI-балла, нет AI-summary.
- Юзер открывает модалку → таб «Детали» → если AI пуст, показывается кнопка «Запустить AI-анализ».
- Клик → `POST /api/pipeline/cards/:id/ai-enrich`:
  - Проверка квоты AI на месяц у owner команды (`sniper_users.ai_analyses_used_month` vs тариф).
  - Если квота исчерпана → 402 Payment Required + Toast «Квота AI исчерпана».
  - Если ОК → запускается background task через `asyncio.create_task`:
    1. Получить полную инфу тендера из `tender_cache` (если нет — парсить zakupki.gov.ru).
    2. Вызвать `tender_sniper.ai_summarizer.summarize(tender_data)` → summary.
    3. Вызвать `tender_sniper.ai_relevance_checker.check(tender_data, filter_data)` → recommendation.
    4. UPDATE `pipeline_cards` set `ai_summary`, `ai_recommendation`, `ai_enriched_at`.
    5. INSERT в `pipeline_card_history` action='ai_enriched'.
    6. INCREMENT `sniper_users.ai_analyses_used_month` для owner.
  - API возвращает 202 Accepted сразу.
  - Юзер видит loading-спиннер в модалке, который через polling каждые 2с проверяет `ai_enriched_at`. Через 30-60с появляются summary + recommendation.

### Источник квоты

Квота берётся с тарифа `owner_user_id`. Если owner — `pro` (500 AI/мес), вся команда расходует из этих 500. Если `premium` — безлимит.

---

## 13. Файлы и хранилище

### Railway Volume

- Volume mount: `/app/uploads` (привязан к сервису в Railway).
- Структура: `/app/uploads/<company_id>/<card_id>/<file_id>_<sanitized_filename>`.
- Имя файла санитизируется: убираются path traversal, лимит 200 символов.
- На старте бота — `os.makedirs(/app/uploads, exist_ok=True)`.

### Лимиты

- 10MB на файл (захардкожено).
- 1GB на team суммарно (проверка перед загрузкой через `SUM(size) FROM pipeline_card_files JOIN pipeline_cards WHERE company_id=?`).
- Превышение → 413 Payload Too Large + Toast.

### Раздача

- `GET /cabinet/api/pipeline/files/:fid/download`:
  - Проверка членства в команде → 403 если нет.
  - Чтение файла из `path` через aiofiles.
  - `Content-Disposition: attachment; filename="<original_filename>"` (с RFC 5987 encoding для кириллицы).
- Через aiohttp `web.FileResponse` (стримит без загрузки в память).

### Бэкап

В MVP **не делаем**. Railway Volumes у Pro-плана имеют snapshots (раз в день) — этого достаточно. В backlog — миграция на S3-совместимое хранилище если упрёмся в лимит.

---

## 14. Кнопка «В работу» в ленте

В существующей ленте `/cabinet/` (после v3 редизайна) на каждой карточке тендера в feed-list уже есть набор кнопок. Добавляется кнопка **«В работу»** (видна только если юзер в команде):

Поток:
1. Клик → `POST /cabinet/api/pipeline/from-feed/<tender_number>`
2. Сервер:
   - Получает `company_id` юзера.
   - Если уже есть карточка с таким `(company_id, tender_number)` → 409 Conflict + Toast «Уже в Pipeline (стадия "X")» + кнопка «Открыть» в Toast.
   - Иначе создаёт карточку: `stage=FOUND`, `assignee_user_id=current_user`, `source='feed'`, `data={...}` из кэша/парсера.
   - Записывает в `card_history` action='created'.
3. Клиент:
   - Toast «Добавлено в Pipeline» с кнопкой «Открыть».
   - Если открыт `/cabinet/pipeline` в другой вкладке — там увидит на F5.

---

## 15. Архив

### Логика архивации

- Карточки с `result='lost'` через 90 дней автоматически получают `archived_at = now`.
- Background task в `tender_sniper/jobs/` запускается раз в день, делает:
  ```sql
  UPDATE pipeline_cards
  SET archived_at = NOW()
  WHERE result = 'lost'
    AND archived_at IS NULL
    AND updated_at < NOW() - INTERVAL '90 days'
  ```
- Победы (`result='won'`) **никогда** не архивируются.

### Поведение в UI

- Доска `/cabinet/pipeline` показывает карточки `WHERE archived_at IS NULL`.
- Архив `/cabinet/pipeline/archive` показывает только `WHERE archived_at IS NOT NULL` — простой список без drag, можно открывать модалку для просмотра.
- Из архива можно вернуть на доску (только owner) → `archived_at = NULL`, стадия = `RESULT`, `result = 'lost'` остаётся.

---

## 16. Тестирование

### Unit tests (pytest)

- `tests/unit/test_pipeline_service.py`:
  - Move card between stages — обновляется `stage` и `card_history` пишется
  - `set_result(won)` → стадия RESULT, result='won'
  - `archive_lost` background task — архивирует только lost старше 90 дней
  - Расчёт маржи — три сценария (зелёный/оранжевый/красный)
- `tests/unit/test_team_service.py`:
  - Invite token: создание, валидация, expiry, revoke, max_uses
  - Принять инвайт: новый user, уже в команде, в другой команде
  - Owner cannot remove self
- `tests/unit/test_pipeline_rbac.py`:
  - Member может перемещать стадии, не может удалять карточку
  - Owner может всё
  - Не-член команды получает 403

### Integration tests

- `tests/integration/test_pipeline_e2e.py`:
  - Создать карточку из ленты → стадии 1 → 2 → 3 → 4 → 5 → SUBMITTED → set_result(won) → archive (через 90 дней синхронизация даты)
  - Загрузка файла → проверка лимита 10MB → проверка quota команды
  - Bitrix import smoke: 5 mock-deals → проверка маппинга стадий и AI-полей

### Что НЕ тестируем в MVP

- Drag-n-drop UX в браузере (manual smoke-test).
- Ai-обогащение реальным запросом к OpenAI (mock).
- Telegram Login в инвайт-flow (manual smoke).

---

## 17. Производительность

- Доска: запрос всех карточек команды без архива. Оценка: типичная команда 5-6 человек, на доске одновременно ~30-100 карточек (по опыту Bitrix Николая). Без проблем для PostgreSQL с индексом `ix_pipeline_company_stage`.
- Модалка: при открытии — отдельный запрос на полные данные (`/cards/:id` с join по notes/files/checklist/history). История лимитируется 100 последних записей.
- AI-обогащение: background task через `asyncio.create_task`, не блокирует HTTP-ответ.
- Polling AI-результата: каждые 2с, max 60с (если не пришло — показываем «Превышено время ожидания»).

---

## 18. Безопасность

- **Path traversal в файлах:** `secure_filename` от werkzeug или собственная санитизация (убираем `..`, `/`, `\`, лимит 200 символов).
- **XSS в UI:** все user-input (заметки, чек-лист, имена файлов) — рендерится через `textContent` или Jinja2 escaping. Никакого `innerHTML` для user content.
- **CSRF:** все state-changing API endpoints проверяют origin и используют SameSite cookies (как остальной кабинет).
- **Авторизация:** middleware на уровне роутов. Никаких `WHERE company_id = ?` в API без проверки членства.
- **File upload:** `Content-Type` проверяется по реальной MIME (через `python-magic`), а не по заголовку. Запрещены `.exe`, `.sh`, `.html`.

---

## 19. Деплой и миграция

### Шаги деплоя MVP

1. Применить Alembic миграцию (8 новых таблиц) — автоматом на старте бота.
2. Создать Railway Volume `/app/uploads` (mount-point настраивается в Railway dashboard).
3. Деплой кода в `main` → auto-deploy.
4. Одноразовый запуск миграции Bitrix:
   - Открыть `/cabinet/pipeline` под Николаем → автоматом создаст команду (он становится owner).
   - SSH/Railway-CLI: `railway run python -m scripts.migrate_bitrix_to_pipeline --company-id <ID> --dry-run`
   - Если ОК → без `--dry-run`.
5. Smoke-тест: создать карточку вручную, перетащить, добавить заметку, загрузить файл, запустить AI-обогащение, удалить.
6. Создать первый инвайт, разослать команде.

### Откат

- Удаление таблиц через downgrade миграции.
- Файлы в Volume останутся (нужно удалить вручную если хочется).
- Bitrix продолжает работать параллельно в течение всего MVP-периода — отказ можно сделать через 1-2 недели после полного перехода команды.

---

## 20. Оценка по времени

Грубая разбивка (для последующего плана):

| Этап | Время |
|---|---|
| Alembic миграция + модели | 0.5 дня |
| Backend: Company/Team service + RBAC + invite flow | 1.5 дня |
| Backend: Pipeline service + API endpoints | 2 дня |
| Frontend: pipeline.html + drag (Sortable.js) + optimistic UI | 2 дня |
| Frontend: модалка карточки + табы | 2 дня |
| Frontend: team.html + dashboard метрики | 1 день |
| Frontend: invite.html + accept flow | 0.5 дня |
| File hosting (upload, download, лимиты) | 1 день |
| AI-обогащение + квоты | 0.5 дня |
| Bitrix migration script | 1 день |
| Background job для архивации | 0.5 дня |
| Тесты (unit + integration) | 1.5 дня |
| Smoke-тестирование, фиксы | 1 день |
| **Итого** | **~15 рабочих дней (~3 рабочих недели)** |

---

## 21. Открытые вопросы

1. **Название первой колонки.** «Найденные» vs «Новые» vs «На оценке». Решить в финальном UI-полировании.
2. **Holodilnik-кнопка** — заглушка в MVP. Реальная реализация в отдельном спеке.
3. **Email-уведомления** — отложены (см. отдельное feedback memory).
4. **Bitrix decommission** — момент полного отключения Bitrix24 решается после успешной миграции и недели работы команды на Pipeline.

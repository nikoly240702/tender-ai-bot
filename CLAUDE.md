# Tender Sniper — CLAUDE.md
_Обновлено: 2026-03-02_

## Проект
Telegram-бот для мониторинга тендеров (44-ФЗ / 223-ФЗ) с AI-скорингом и веб-кабинетом.
Деплой: Railway (auto-deploy с GitHub). Прод всегда на `main`.

---

## Как запускать локально
```bash
# Установка зависимостей
pip install -r requirements.txt

# Запуск бота
python -m bot.main

# Запуск веб-кабинета (порт 8181 — не 8080, там FastAPI admin)
python -c "
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path('.').resolve()))
from dotenv import load_dotenv; load_dotenv('.env')
async def run():
    from aiohttp import web
    from cabinet import setup_cabinet_routes
    app = web.Application()
    setup_cabinet_routes(app)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', 8181).start()
    print('http://127.0.0.1:8181/cabinet/login')
    await asyncio.sleep(3600)
asyncio.run(run())
"

# Запуск admin-панели (FastAPI, порт 8080)
python scripts/run_admin.py

# Применить миграции
alembic upgrade head
```

---

## Структура проекта
```
bot/
  main.py                      — точка входа
  handlers/
    sniper.py                  — ГЛАВНЫЙ файл (170K), ХРУПКИЙ — не трогать без нужды
    sniper_search.py           — инлайн-поиск (134K)
    sniper_wizard_new.py       — wizard создания фильтра (123K, FSM)
    user_management.py         — управление юзерами/тарифами (87K)
    menu_priority.py           — приоритетные callbacks (дубли ТОЛЬКО сюда)
    all_tenders.py             — просмотр всех тендеров
    subscriptions.py           — тарифы, YooKassa платежи, SUBSCRIPTION_TIERS
    company_profile.py         — реквизиты компании
    webapp.py                  — Google Sheets export + AI фичи
    tender_actions.py          — избранное, feedback
    referral.py                — реферальная программа
  utils/
    access_check.py            — проверка доступа к фичам по тарифу

tender_sniper/
  service.py                   — автомониторинг (цикл каждые 5 мин)
  instant_search.py            — поиск по фильтру (веб + инлайн)
  ai_relevance_checker.py      — AI скоринг (GPT-4o-mini, квоты по тарифам)
  ai_features.py               — feature gate для AI-фич (месячные лимиты)
  ai_summarizer.py             — AI резюме тендера
  ai_document_extractor.py     — извлечение данных из документов
  ai_keyword_recommender.py    — рекомендации ключевых слов
  regions.py                   — справочник регионов РФ
  matching/
    smart_matcher.py           — скоринг тендеров 0–100
  database/
    sqlalchemy_adapter.py      — ВСЕ методы БД (~2600 строк)

src/parsers/
  zakupki_rss_parser.py        — RSS парсер, HTTP через run_in_executor

cabinet/
  routes.py                    — HTML маршруты (8 страниц)
  api.py                       — REST API (~600 строк)
  auth.py                      — Telegram Login Widget + cookie-сессии
  templates/                   — 8 HTML страниц

landing/
  index.html                   — лендинг (статика, не задеплоен отдельно)

database.py                    — SQLAlchemy модели (единый файл)
bot/health_check.py            — health check + YooKassa payment webhook
alembic/versions/              — 33 миграции
```

---

## Две AI-системы — различать обязательно

### AI #1 — Скоринг (`ai_relevance_checker.py`)
Автоматический, фоновый. Пользователь не управляет им напрямую.
- Дневные лимиты: `TIER_LIMITS = {trial: 20, basic: 100, premium: 10000}`
- При исчерпании → fallback на keyword matching, тендер НЕ теряется
- Двойной счётчик: TTLCache (in-memory) + `ai_analyses_used_month` в БД
- `has_ai_unlimited` НЕ влияет на этот AI

### AI #2 — Фичи (`ai_features.py`)
Ручной, по запросу пользователя.
- Резюме тендера, красные флаги, рекомендации ключевых слов, документы
- Месячные лимиты: `AI_MONTHLY_LIMITS = {trial: 0, basic: 10, premium: 50}`
- `has_ai_unlimited = True` → безлимит (аддон AI Unlimited)

---

## Тарифные планы

| Тариф | Цена | Фильтры | Уведомлений/день | AI скоринг/день | AI фичи/мес |
|-------|------|---------|-----------------|----------------|------------|
| trial | бесплатно, 14 дней | 3 | 20 | 20 | — |
| basic | 1 490₽/мес | 5 | 100 | 100 | 10 |
| premium | 2 990₽/мес | 20 | безлимит (9999) | 10 000 | 50 |
| ai_unlimited (аддон) | +1 490₽/мес | — | — | — | безлимит |

Скидки при оплате вперёд: Basic — 4 020₽/3мес, 7 150₽/6мес | Premium — 8 070₽/3мес.

Оплата: YooKassa → webhook в `bot/health_check.py` → `db.update_user_subscription()` / `db.activate_ai_unlimited()`

---

## База данных
- PostgreSQL (Railway), адаптер: `tender_sniper/database/sqlalchemy_adapter.py`
- Получить инстанс: `db = await get_sniper_db()`
- Модели: `database.py` (SniperUser, SniperFilter, SniperNotification, ...)
- `SniperUser.data` — JSON-поле: `quiet_hours_start/end`, `monitoring_paused_until`, ...
- `SniperUser.filters_limit` / `notifications_limit` — хранятся в БД, устанавливаются при оплате
- Новые методы — только в `sqlalchemy_adapter.py`
- Новые миграции: `alembic revision --autogenerate -m "описание"`

---

## Пайплайн скоринга
```
RSS → тип (44/223) → ключевые слова → бюджет → регион
    → pre-score → красные флаги → AI скоринг (GPT-4o-mini)
    → финал score → score > порог → уведомление в Telegram
```

---

## Веб-кабинет — маршруты
```
GET  /cabinet/                          — дашборд (история + действия)
GET  /cabinet/filters                   — CRUD фильтров
GET  /cabinet/search                    — мгновенный поиск
GET  /cabinet/stats                     — статистика
GET  /cabinet/settings                  — настройки (пауза, тихие часы)
GET  /cabinet/profile                   — реквизиты компании
GET  /cabinet/documents                 — документы
GET  /cabinet/login                     — вход через Telegram

GET    /cabinet/api/filters             — список фильтров (?active_only=false)
POST   /cabinet/api/filters/create      — создать фильтр
PUT    /cabinet/api/filters/{id}        — обновить фильтр
DELETE /cabinet/api/filters/{id}        — удалить (soft delete)
POST   /cabinet/api/filters/{id}/toggle — вкл/выкл
GET    /cabinet/api/search              — поиск тендеров
GET    /cabinet/api/regions             — список регионов РФ
GET    /cabinet/api/stats               — статистика пользователя
POST   /cabinet/api/tenders/{n}/feedback        — интересно/пропустить
POST   /cabinet/api/tenders/{n}/export-sheets   — в Google Sheets
GET    /cabinet/api/settings            — настройки пользователя
POST   /cabinet/api/settings            — сохранить настройки
```

---

## Известные проблемы — НЕ ЛОМАТЬ

1. **Дублирующиеся callbacks** — все приоритетные обработчики в `menu_priority.py`, не дублировать в других файлах
2. **Тихие часы** — в `service.py` сохранять в БД ДО проверки тихих часов, иначе тендер потеряется
3. **Тип фильтра "товары"** — проверять только `name` тендера, не `summary`
4. **Синхронный RSS-парсер** — обёрнут в `run_in_executor`, не убирать эту обёртку
5. **sniper.py** — 170K, хрупкий. Любое изменение может сломать цепочку callbacks

---

## ENV переменные (обязательные)
```
BOT_TOKEN=
DATABASE_URL=postgresql://...
OPENAI_API_KEY=
```

## ENV переменные (опциональные)
```
ADMIN_USER_ID=          # Telegram ID для admin-доступа
SENTRY_DSN=
YOOKASSA_SHOP_ID=
YOOKASSA_API_KEY=
GOOGLE_SHEETS_CREDENTIALS=
GROQ_API_KEY=           # резервный AI
ANTHROPIC_API_KEY=      # резервный AI
PROXY_URL=socks5://...
```

---

## Деплой
```
git push origin main → Railway автоматически:
  1. docker build
  2. alembic upgrade head
  3. python -m bot.main
  4. health check /health на :8080
```

---

## Важные соглашения
- Все async DB операции через `await get_sniper_db()` — не создавать прямые соединения
- Новые DB методы — только в `sqlalchemy_adapter.py`
- Telegram `callback_data` должны быть уникальными — проверять `menu_priority.py` перед добавлением
- Не трогать `bot/handlers/sniper.py` без крайней нужды — он 170K и хрупкий

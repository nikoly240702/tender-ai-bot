# Tender Sniper — Комплексный пакет: Аудит и Промпт для Claude Code

*Обновлено: 19.02.2026 | Версия: 3.0 | Проект: Tender Sniper*

---

# ЧАСТЬ 1: ТЕХНИЧЕСКОЕ СОСТОЯНИЕ ПРОЕКТА

## Что это

Telegram-бот для автоматического мониторинга госзакупок (zakupki.gov.ru). Пользователь создаёт фильтры (ключевые слова, регионы, цена, тип закупки), бот каждые 5 минут парсит RSS, скорит тендеры и отправляет уведомления.

## Стек

- **Bot**: aiogram 3, FSM, PostgreSQL (Railway, auto-deploy from GitHub)
- **Parsing**: requests (sync) + feedparser, обогащение через HTML-парсинг страниц zakupki.gov.ru
- **Scoring**: SmartMatcher (keyword matching) + AI Relevance Checker (gpt-4o-mini)
- **Notifications**: Telegram inline messages с кнопками
- **Migrations**: Alembic

---

## Архитектура скоринга (pipeline)

```
RSS Feed (zakupki.gov.ru)
    │
    ▼
┌─────────────────────────┐
│  1. RSS парсинг         │  src/parsers/zakupki_rss_parser.py
│  - Ключевые слова       │  Отправляет keywords + regions в RSS URL
│  - Тип закупки          │  Client-side фильтрация по типу (товары/услуги/работы)
│  - Цена из RSS          │  Извлекает: номер, название, цена, дата, ссылка
│  - Регион в URL         │  НО: RSS не возвращает регион в ответе!
└────────────┬────────────┘
             ▼
┌─────────────────────────┐
│  2. Pre-scoring         │  tender_sniper/instant_search.py → smart_matcher.py
│  - Только keywords      │  MIN_PRESCORE = 1 (почти всё проходит)
│  - БЕЗ региона          │  Регион недоступен на этом этапе
│  - БЕЗ цены точной      │  Цена из RSS приблизительна
│  - STOP_WORDS фильтр    │  17 стоп-слов игнорируются
│  - Keywords <3 симв.     │  "ПО", "IT" и т.д. тоже игнорируются
└────────────┬────────────┘
             ▼
┌─────────────────────────┐
│  3. Enrichment (HTTP)   │  src/parsers/zakupki_rss_parser.py → HTML parsing
│  - Загрузка страницы    │  Кэш: до 500 тендеров в сессии (in-memory)
│  - Точная цена          │  Regex: "Начальная цена", "НМЦК"
│  - Регион заказчика     │  Из названия заказчика (52 региона) + адреса (regex)
│  - Дедлайн подачи       │  Regex: "Окончание подачи заявок"
│  - Название объекта     │  Полное из карточки закупки
│  - НЕТ таймаутов!       │  Медленная страница блокирует весь pipeline
└────────────┬────────────┘
             ▼
┌─────────────────────────┐
│  4. Final scoring       │  tender_sniper/instant_search.py → smart_matcher.py
│  - Keywords + вес       │  Compound +35, exact +25, partial +18, synonym +20
│  - Регион ✓             │  Strict: фильтр указал регионы → тендер ДОЛЖЕН совпасть
│  - НО: регион=None      │  Если регион не извлечён → ТЕНДЕР ПРОХОДИТ (нет reject)
│  - Цена ✓               │  ±20 баллов от диапазона
│  - Negative patterns    │  -5 за военные, медицинские, строительные паттерны
│  - Strict mode          │  8+ keywords → нужно ≥10% совпадений
│  Score: 0-100           │
└────────────┬────────────┘
             ▼
┌─────────────────────────┐
│  5. AI Verification     │  tender_sniper/ai_relevance_checker.py (gpt-4o-mini)
│  - Семантический анализ │  Промпт проверяет тип, тему, деятельность
│  - confidence 0-100     │  ACCEPT ≥ 40, REJECT < 25
│  - Composite boost:     │  confidence ≥ 60 → score +15
│    score = smart + ai   │  confidence 40-59 → score +10
│  - Кэш 24ч (in-memory) │  Квота: trial=20, basic=100, premium=10000/день
│  - Quota exceeded:      │  confidence=50 → +10 boost (БАГ: ложный boost)
└────────────┬────────────┘
             ▼
┌─────────────────────────┐
│  6. Notification        │  tender_sniper/service.py → telegram_notifier.py
│  - MIN_SCORE = 35       │  Composite score (smart_matcher + ai_boost)
│  - Dedup по БД          │  Unique: (user_id, filter_id, tender_number)
│  - Квота уведомлений    │  trial=20, basic=50, premium=100/день
│  - Тихие часы           │  Per-user настройка (UTC+3 фиксированно)
│  - Per-filter sending   │  Отправка сразу после каждого фильтра (не batch)
│  - Blocked user detect  │  TelegramForbiddenError → mark + деактивация фильтров
└─────────────────────────┘
```

---

## Модель данных фильтра (SniperFilter)

| Поле | Тип | Для скоринга? | Описание |
|------|-----|:---:|----------|
| `keywords` | JSON list | ✅ | Основные ключевые слова |
| `exclude_keywords` | JSON list | ✅ | Исключающие слова |
| `primary_keywords` | JSON list | ✅ | Вес 2x в scoring |
| `secondary_keywords` | JSON list | ✅ | Вес 1x в scoring |
| `regions` | JSON list | ✅ | Регионы заказчика |
| `execution_regions` | JSON list | ❌ | Место исполнения (Phase 2, не используется) |
| `price_min` / `price_max` | Float | ✅ | Диапазон цены |
| `tender_types` | JSON list | ✅ | Тип закупки (товары/услуги/работы) |
| `law_type` | String | ✅ | 44-ФЗ / 223-ФЗ |
| `customer_keywords` | JSON list | ❌ | Фильтр по заказчику (не в scoring) |
| `customer_inn` | JSON list | ❌ | Whitelist ИНН |
| `excluded_customer_inns` | JSON list | ❌ | Blacklist ИНН |
| `ai_intent` | Text | ✅ | AI-описание намерения фильтра |
| `expanded_keywords` | JSON list | ✅ | AI-синонимы |
| `search_in` | JSON list | ⚠️ | Где искать (title/desc/docs/customer) |
| `min_deadline_days` | Integer | ✅ | Мин. дней до дедлайна |
| `is_active` | Boolean | — | Пауза/активен |
| `deleted_at` | DateTime | — | Soft delete (корзина) |
| `notify_chat_ids` | JSON list | — | Маршрутизация в группы |

---

## Ключевые константы и пороги

| Константа | Значение | Файл | Описание |
|-----------|----------|------|----------|
| `MIN_PRESCORE_FOR_ENRICHMENT` | 1 | instant_search.py | Мин. score для загрузки страницы |
| `MIN_SCORE_FOR_NOTIFICATION` | 35 | service.py | Мин. composite score для отправки |
| `CONFIDENCE_THRESHOLD_ACCEPT` | 40 | ai_relevance_checker.py | AI confidence для прохождения |
| `CONFIDENCE_THRESHOLD_RECHECK` | 25 | ai_relevance_checker.py | Ниже — автоматический reject |
| AI boost (confidence ≥ 60) | +15 | instant_search.py | Добавка к smart_matcher score |
| AI boost (confidence 40-59) | +10 | instant_search.py | Добавка к smart_matcher score |
| Compound phrase match | +35 | smart_matcher.py | Точное совпадение фразы |
| Exact keyword match | +25 | smart_matcher.py | Точное слово с границами |
| Partial root match | +18 | smart_matcher.py | Корень 5+ символов |
| Synonym match | +20 | smart_matcher.py | Из словаря синонимов |
| Price bonus/penalty | ±20 | smart_matcher.py | Отклонение от диапазона |
| Negative pattern penalty | -5 | smart_matcher.py | Военные, мед. и т.д. (68 паттернов) |
| Strict mode penalty | -40% | smart_matcher.py | При 8+ keywords и < 10% match |
| Poll interval | 300s (5 мин) | service.py | Интервал мониторинга |
| Enrichment cache | 500 тендеров | instant_search.py | Max размер in-memory кэша |
| AI cache TTL | 24 часа | ai_relevance_checker.py | Время жизни AI кэша (in-memory) |
| STOP_WORDS | 17 слов | smart_matcher.py | закупка, услуга, поставка, система и т.д. |
| Min keyword length | 3 символа | smart_matcher.py | "ПО", "IT" игнорируются |

---

## Структура файлов (ключевые)

```
tender-ai-bot-fresh/
├── bot/
│   ├── handlers/
│   │   ├── start.py              — /start, главное меню, bot_blocked unmark
│   │   ├── sniper.py             — UI фильтров, корзина, управление
│   │   ├── sniper_search.py      — FSM создания фильтра
│   │   ├── admin_sniper.py       — Админ-панель (статистика, пользователи, тарифы)
│   │   └── menu_priority.py      — Priority callback handlers
│   ├── engagement_scheduler.py   — Дайджесты, follow-up, reactivation
│   └── config.py                 — BotConfig (tokens, admin ID)
│
├── tender_sniper/
│   ├── service.py                — Главный цикл мониторинга, отправка уведомлений
│   ├── instant_search.py         — Pipeline: pre-score → enrich → final score → AI
│   ├── matching/
│   │   └── smart_matcher.py      — Scoring: keywords, regions, price, synonyms
│   ├── ai_relevance_checker.py   — GPT-4o-mini верификация, intent generation
│   ├── ai_name_generator.py      — Короткие AI-названия тендеров
│   ├── notifications/
│   │   └── telegram_notifier.py  — Отправка в Telegram, blocked detection
│   ├── database/
│   │   └── sqlalchemy_adapter.py — DB операции, mark_blocked, soft delete
│   ├── parser.py                 — RealtimeParser (обёртка)
│   ├── config.py                 — Feature flags (YAML)
│   └── regions.py                — Федеральные округа → регионы mapping (НЕПОЛНЫЙ: 52/85)
│
├── src/parsers/
│   └── zakupki_rss_parser.py     — RSS парсинг, enrichment, type filter
│
├── database.py                   — SQLAlchemy модели (все таблицы)
└── alembic/                      — Миграции БД
```

---

# ЧАСТЬ 2: АУДИТ — ЧТО СДЕЛАНО И ЧТО ОСТАЛОСЬ

## ✅ Решено (за последнюю неделю)

| # | Проблема | Решение | Коммит |
|---|----------|---------|--------|
| 1 | Уведомления терялись при рестарте (batch в конце цикла) | Per-filter sending — отправка сразу после каждого фильтра | `4f634c9` |
| 2 | Нерелевантные тендеры с score 22 проходили | MIN_SCORE 20→35 | `f6417e8` |
| 3 | Мало тендеров после повышения порога | Composite score: SmartMatcher + AI boost (+10/+15) | `bdbc0f5` |
| 4 | Дайджест "0 тендеров" в группах | Skip groups в daily digest | `b7c3c40` |
| 5 | Заблокированные пользователи не отражались в админке | Detect TelegramForbiddenError → mark in DB → show in admin → auto-clear on /start | `2313272` |
| 6 | Deadline parsing ломался (strptime truncation) | Исправлен на прямой strptime без truncation | `4f634c9` |
| 7 | "Товары" фильтр ловил услуги | Type filter: check name only, skip if starts with "поставка" | ранее |
| 8 | Неизвестный регион → pre-score 0 (отсекал тендеры) | Убран регион из pre-score этапа | ранее |
| 9 | Pre-scoring слишком агрессивно отсекал | Снижен MIN_PRESCORE до 1 | ранее |
| 10 | AI doc analysis: слабые модели | Upgrade до gpt-4o для items pass | `94eb698` |
| 11 | Корзина для удалённых фильтров | Soft delete: deleted_at + trash bin UI + restore/permanent delete | ранее |

---

## ❗ ОТКРЫТЫЕ ПРОБЛЕМЫ (приоритизированные)

### P0 — Критические

#### [P0-1] Тендеры из чужих регионов проскакивают

**Проблема:** Пользователь указывает в фильтре конкретные регионы (например, Москва), но получает тендеры из других регионов.

**Root Cause (3 дыры):**
1. **Enrichment не извлёк регион** (27% тендеров) → тендер проходит без проверки региона (SmartMatcher пропускает если region=None)
2. **Неполный справочник**: захардкожено только 52 из 85 регионов РФ в `zakupki_rss_parser.py`
3. **Мусорные регионы**: из production-выборки — "г. него", "ул Республики", "г. а", "ЧЕЛЯБИНСКАЯ ОБЛАСТЬ КОРКИНСКИЙ" (район вместо региона), "Бурятия Республика" (инверсия)

**Где код:**
- `tender_sniper/regions.py` — справочник 52 регионов, ФО→регионы mapping
- `src/parsers/zakupki_rss_parser.py` строки 732-765 — извлечение из названия заказчика, строки 973-1090 — из адреса
- `tender_sniper/matching/smart_matcher.py` строки 504-625 — region matching, алиасы, нормализация

**Что нужно:**
1. Полный справочник 85 регионов с алиасами + валидация
2. Нормализация: регистр, инверсия ("Бурятия Республика"→"Республика Бурятия"), отсечение районов/улиц
3. Fallback по ИНН заказчика (первые 2 цифры = код региона)
4. Решение: что делать с region=None (пропускать с пониженным score? отклонять?)

---

#### [P0-2] Архивные тендеры в выдаче

**Проблема:** RSS-фид zakupki.gov.ru в определённых условиях возвращает тендеры 2014 и 2021 года. 30.6% выборки — архивные.

**Что нужно:**
1. Фильтр по pubDate (отсечь старше 90 дней) ПЕРЕД enrichment
2. Фильтр по deadline (отсечь истёкшие) ПОСЛЕ enrichment
3. Проверка дедлайна перед отправкой уведомления

---

### P1 — Важные

#### [P1-1] Короткие ключевые слова ("ПО", "IT", "ИБП")

**Проблема:** `smart_matcher.py` отбрасывает слова < 3 символов. "ПО", "IT", "ИБП", "ПК", "МФУ" — все игнорируются.

**Решение:** Whitelist коротких легитимных слов + exact match only (чтобы "ПО" не матчилось с "поставка").

---

#### [P1-2] ai_intent не обновляется при редактировании фильтра

**Проблема:** Генерируется только при создании. Если пользователь меняет keywords → AI проверяет по старому intent.

**Где:** `bot/handlers/sniper.py` (update handlers), `tender_sniper/ai_relevance_checker.py` (generate_intent)

---

#### [P1-3] AI квота: ложный boost при исчерпании

**Проблема:** При quota exceeded → confidence=50 → +10 boost. Тендеры без AI-проверки получают завышенный score.

**Решение:** confidence=None, ai_boost=0 при quota exceeded. Тендеры продолжают приходить по smart_matcher score, но без ложного boost.

---

#### [P1-4] Enrichment без таймаутов

**Проблема:** `requests.get()` без timeout. Медленная страница блокирует весь pipeline. 20 тендеров × 5 сек = 100 сек.

**Решение:** timeout=10, try/except, partial enriched data при ошибке.

---

#### [P1-5] In-memory кэши теряются при рестарте

**Проблема:** AI кэш (24ч TTL) и enrichment кэш (500 тендеров) — в dict(). Railway restart → всё потеряно → повторные API-вызовы.

**Решение:** PostgreSQL-таблица `cache_entries` (key, value JSONB, cache_type, expires_at).

---

#### [P1-6] Feedback loop (кнопки "Интересно"/"Пропустить" не анализируются)

**Проблема:** 74+ уведомлений/день, нажатия не сохраняются. Нет метрик качества выдачи.

**Решение:** Таблица user_feedback + анализ паттернов skip → подсказки пользователю.

---

#### [P1-7] Баг delivery_status в админке

**Проблема:** Страница "Статус системы": `'SniperNotification' has no attribute 'delivery_status'`.

---

### P2 — Желательные

- **[P2-1]** Synonym explosion (180+ синонимов → false positive) → лимит 30, ранжирование
- **[P2-2]** Quiet hours без учёта часовых поясов (фиксированный UTC+3)
- **[P2-3]** Strict mode tuning (40% penalty может быть слишком агрессивно)
- **[P2-4]** Масштабирование для 100+ фильтров (последовательный цикл)
- **[P2-5]** Расширенная аналитика в админке (engagement rate, retention когорты)

---

# ЧАСТЬ 3: ПРОМПТ ДЛЯ CLAUDE CODE

Ниже — самодостаточный промпт для новой сессии Claude Code. Скопируй целиком.

---

```
# ЗАДАНИЕ: Tender Sniper — Техническая доработка

## Контекст проекта

Tender Sniper — Telegram-бот для мониторинга госзакупок (zakupki.gov.ru).
Стек: Python, aiogram 3, PostgreSQL (Railway), Alembic, GPT-4o-mini.
Рабочая директория: /Users/nikolaichizhik/Desktop/tender-ai-bot-fresh

## Pipeline скоринга (как работает сейчас)

RSS → Pre-scoring (keywords only, MIN_PRESCORE=1) → Enrichment (HTTP-парсинг
страницы: цена/регион/дедлайн) → Final Scoring (keywords+region+price+synonyms,
0-100) → AI Verification (gpt-4o-mini, confidence 0-100, composite boost +10/+15)
→ Notification (MIN_SCORE=35, dedup, квоты, тихие часы)

## Ключевые файлы

tender-ai-bot-fresh/
├── bot/handlers/
│   ├── start.py              — /start, главное меню, unmark bot_blocked
│   ├── sniper.py             — UI фильтров, trash bin, управление
│   ├── sniper_search.py      — FSM создания фильтра
│   └── admin_sniper.py       — Админ-панель, статистика
├── tender_sniper/
│   ├── service.py            — Главный цикл мониторинга (5 мин), notification sending
│   │                           MIN_SCORE_FOR_NOTIFICATION = 35
│   │                           Per-filter sending (не batch)
│   │                           Blocked user detection
│   ├── instant_search.py     — Pipeline: pre-score → enrich → final score → AI
│   │                           AI boost: confidence>=60 → +15, 40-59 → +10
│   │                           Enrichment cache: 500 тендеров (in-memory)
│   ├── matching/
│   │   └── smart_matcher.py  — Scoring: keywords, regions, price, synonyms
│   │                           STOP_WORDS: 17 слов (система, закупка, поставка...)
│   │                           Keywords < 3 chars → ignored
│   │                           Compound +35, Exact +25, Partial +18, Synonym +20
│   │                           Region: strict match IF filter has regions AND tender has region
│   │                           Region=None → tender PASSES (это дыра!)
│   │                           Federal districts → expanded to regions
│   │                           40+ region aliases
│   │                           68 negative patterns (-5 pts)
│   ├── ai_relevance_checker.py — gpt-4o-mini, ACCEPT>=40, REJECT<25
│   │                           Cache: in-memory 24h TTL
│   │                           Quota: trial=20, basic=100, premium=10000/day
│   │                           Quota exceeded → confidence=50 (BUG: gives +10 boost)
│   │                           ai_intent generated ONCE at filter creation (BUG: not updated on edit)
│   ├── notifications/
│   │   └── telegram_notifier.py — Telegram sending, blocked_chat_ids tracking
│   ├── database/
│   │   └── sqlalchemy_adapter.py — All DB ops, mark/unmark bot_blocked, soft delete
│   └── regions.py            — Federal districts → regions mapping (INCOMPLETE: 52/85)
├── src/parsers/
│   └── zakupki_rss_parser.py — RSS parsing, enrichment (HTML), type filter
│                               Region extraction: customer name (52 regions) + address regex
│                               NO timeout on HTTP requests (BUG)
│                               REGION_CODES: only 52 of 85 regions
├── database.py               — SQLAlchemy models (SniperUser, SniperFilter, SniperNotification...)
│                               SniperUser.data: JSON flexible storage (quiet_hours, bot_blocked...)
│                               SniperFilter.deleted_at: soft delete (trash bin)
├── bot/engagement_scheduler.py — Daily digest (9AM), follow-up (day1/3), reactivation
│                               Groups skipped in digest
└── alembic/                  — DB migrations

## Модель SniperFilter (поля для скоринга)

- keywords: JSON list — основные ключевые слова
- exclude_keywords: JSON list — исключения
- primary_keywords: JSON list — вес 2x
- secondary_keywords: JSON list — вес 1x
- regions: JSON list — регионы заказчика (для matching)
- price_min/price_max: Float — ценовой диапазон
- tender_types: JSON list — товары/услуги/работы
- law_type: String — 44-ФЗ / 223-ФЗ
- ai_intent: Text — AI-описание намерения (генерируется 1 раз)
- expanded_keywords: JSON list — AI-синонимы
- min_deadline_days: Integer
- is_active: Boolean, deleted_at: DateTime (soft delete)
- notify_chat_ids: JSON list — маршрутизация в группы

## Что уже решено (НЕ ТРОГАТЬ, работает)

1. Per-filter notification sending (не batch) — commit 4f634c9
2. MIN_SCORE = 35 (composite) — commit f6417e8
3. Composite score: SmartMatcher + AI boost (+10/+15) — commit bdbc0f5
4. Groups skipped in daily digest — commit b7c3c40
5. Blocked bot user detection (mark in DB, show in admin, auto-clear on /start) — commit 2313272
6. Deadline parsing fix — commit 4f634c9
7. Type filter: товары checks name only, skip "поставка" prefix
8. Region removed from pre-score (was causing 0-score for unknown regions)
9. Soft delete (trash bin) for filters
10. AI doc analysis upgraded to gpt-4o for items pass

## ЗАДАЧИ (в порядке приоритета)

### ЗАДАЧА 1: Фильтрация по регионам — полная переработка [P0]

ГЛАВНАЯ ПРОБЛЕМА: Тендеры из чужих регионов проскакивают. Три дыры:
1. Enrichment не извлёк регион (27% тендеров) → tender passes without region check
2. Справочник только 52 из 85 регионов
3. Мусорные регионы: "г. него", "ул Республики", "Бурятия Республика"

СДЕЛАЙ:

1. ПЕРЕПИШИ `tender_sniper/regions.py`:
   - REGION_CANONICAL: словарь ВСЕХ 85 регионов РФ с алиасами
   - Включить: все республики, области, края, АО, города фед. значения
   - Алиасы: сокращения, инверсии, варианты написания
   - Формат: {"Каноничное имя": ["алиас1", "алиас2", ...]}
   - СОХРАНИТЬ существующий mapping ФО→регионы (используется в UI)

2. Добавь normalize_region(raw_text: str) -> Optional[str]:
   - lower() для сравнения, возврат каноничного имени
   - Отсечение мусора: "ул.", "улица", "пер.", "проспект" → None
   - Отсечение районов: "ЧЕЛЯБИНСКАЯ ОБЛАСТЬ КОРКИНСКИЙ" → "Челябинская область"
   - Инвертированный порядок: "Бурятия Республика" → "Республика Бурятия"
   - Валидация: текст ДОЛЖЕН совпасть с алиасом, иначе None

3. Добавь region_from_inn(inn: str) -> Optional[str]:
   - Первые 2 цифры ИНН → регион (маппинг 01-99)
   - Только для ИНН длиной 10 или 12

4. В `src/parsers/zakupki_rss_parser.py`:
   - Enrichment: текущее извлечение → normalize_region()
   - Если None → попробовать region_from_inn(customer_inn)
   - Обновить REGION_CODES (52→85)

5. В `tender_sniper/matching/smart_matcher.py`:
   - Сравнение через каноничные имена (не raw text)

6. РЕШЕНИЕ для region=None тендеров (обсудить с пользователем):
   Вариант A: Пропускать с пониженным score (-20 баллов)
   Вариант B: Отклонять если фильтр ТРЕБУЕТ конкретный регион
   Вариант C: Пропускать + пометка "Регион не определён" в уведомлении

КРИТЕРИЙ:
- 0 мусорных регионов ("г. него", "г. а", "ул Республики")
- ≤ 15% "Не указан" (вместо 27%)
- Все 85 регионов в справочнике
- Тендеры из Москвы НЕ приходят в фильтр "только Казань"

НЕ ЛОМАЙ: Существующий UI выбора регионов через ФО, миграции Alembic,
существующие фильтры пользователей (JSON regions совместим).

---

### ЗАДАЧА 2: Архивные тендеры [P0]

КОНТЕКСТ: 30.6% тендеров — архивные (2014, 2021). Дедлайн давно прошёл.

СДЕЛАЙ:

1. В `instant_search.py` ПЕРЕД enrichment:
   - Парси pubDate из RSS
   - Отсечь тендеры с pubDate старше 90 дней

2. В `instant_search.py` ПОСЛЕ enrichment:
   - Если deadline < текущей даты → исключить
   - Если deadline = None → оставить

3. В `service.py` перед отправкой:
   - Проверка дедлайна (дополнительная страховка)

КРИТЕРИЙ: 0 тендеров с pubDate до 2026 в уведомлениях.

---

### ЗАДАЧА 3: Короткие ключевые слова [P1]

КОНТЕКСТ: "ПО", "IT", "ИБП", "ПК", "МФУ" игнорируются (< 3 символов).

СДЕЛАЙ в `smart_matcher.py`:
- SHORT_KEYWORDS_WHITELIST = {"ПО", "IT", "ИТ", "ИБП", "АС", "БД", "ОС",
                               "ПК", "СХД", "МФУ", "ЭВМ", "СИ"}
- Логика: if len(word) < 3 and word.upper() not in WHITELIST: skip
- Для whitelist слов: ONLY exact match (не partial!), чтобы "ПО" ≠ "поставка"

КРИТЕРИЙ: Фильтр "ПО" → находит тендеры. "ПО" НЕ матчится с "поставка".

---

### ЗАДАЧА 4: ai_intent обновление [P1]

КОНТЕКСТ: Intent генерируется при создании, не обновляется при редактировании.

СДЕЛАЙ:
1. В `bot/handlers/sniper.py`: после каждого update фильтра → regenerate intent
2. Также обновлять expanded_keywords при изменении keywords

---

### ЗАДАЧА 5: AI квота — убрать ложный boost [P1]

КОНТЕКСТ: Quota exceeded → confidence=50 → +10 boost (ложный).

СДЕЛАЙ в `ai_relevance_checker.py`:
- Quota exceeded → confidence=None, boost=0
- НЕ кэшировать (при upgrade тарифа → перепроверить)
- Тендеры продолжают приходить по smart_matcher score

---

### ЗАДАЧА 6: Enrichment таймауты [P1]

СДЕЛАЙ в `zakupki_rss_parser.py`:
- timeout=10 для всех requests.get()
- try/except: при timeout → partial data (region=None, price=RSS price)
- Логировать ошибки

---

### ЗАДАЧА 7: Персистентный кэш в PostgreSQL [P1]

СДЕЛАЙ:
1. Alembic миграция: таблица cache_entries (key, value JSONB, cache_type, expires_at)
2. Класс PersistentCache: get(), set(), cleanup()
3. Заменить in-memory кэши в ai_relevance_checker.py и instant_search.py

---

## ВАЖНЫЕ ПРАВИЛА

- ВСЕГДА компилируй изменённые файлы: python3 -m py_compile <file>
- НЕ ЛОМАЙ работающий функционал (см. список "Что уже решено")
- Используй существующие паттерны кода (DatabaseSession, logging format)
- Alembic миграции: проверь цепочку down_revision
- Тестируй на compilation, не только логику
```

---

*Документ создан: 19.02.2026 | Версия: 3.0*
*Обновления v3.0: Актуализирован статус задач, отражены все fix-коммиты за неделю, убраны выполненные задачи из промпта, добавлен детальный pipeline с дырами, обновлены ключевые файлы и константы*

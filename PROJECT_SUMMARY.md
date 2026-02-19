# Tender Sniper Bot — Актуальное саммари (февраль 2026)

## Что это
Telegram-бот для автоматического мониторинга госзакупок (zakupki.gov.ru). Пользователь создаёт фильтры (ключевые слова, регионы, цена, тип закупки), бот каждые 5 минут парсит RSS, скорит тендеры и отправляет уведомления.

## Стек
- **Bot**: aiogram 3, FSM, PostgreSQL (Railway), Alembic миграции
- **Parsing**: requests (sync) + feedparser, обогащение через HTML-парсинг страниц zakupki.gov.ru
- **Scoring**: SmartMatcher (keyword matching) + AI Relevance Checker (gpt-4o-mini)
- **Notifications**: Telegram inline messages с кнопками

---

## Архитектура скоринга (pipeline)

```
RSS Feed (zakupki.gov.ru)
    │
    ▼
┌─────────────────────────┐
│  1. RSS парсинг         │  zakupki_rss_parser.py
│  - Ключевые слова       │  Отправляет keywords + regions в RSS URL
│  - Тип закупки          │  Client-side фильтрация по типу (товары/услуги/работы)
│  - Цена из RSS          │  Извлекает: номер, название, цена, дата, ссылка
│  - Регион в URL         │  НО: RSS не возвращает регион в ответе!
└────────────┬────────────┘
             ▼
┌─────────────────────────┐
│  2. Pre-scoring         │  instant_search.py → smart_matcher.py
│  - Только keywords      │  MIN_PRESCORE = 1 (почти всё проходит)
│  - БЕЗ региона          │  Регион недоступен на этом этапе
│  - БЕЗ цены точной      │  Цена из RSS приблизительна
│  - STOP_WORDS фильтр    │  17 стоп-слов игнорируются
└────────────┬────────────┘
             ▼
┌─────────────────────────┐
│  3. Enrichment (HTTP)   │  zakupki_rss_parser.py → HTML parsing
│  - Загрузка страницы    │  Кэш: до 500 тендеров в сессии
│  - Точная цена          │  Regex: "Начальная цена", "НМЦК"
│  - Регион заказчика     │  Из названия заказчика + адреса
│  - Дедлайн подачи       │  Regex: "Окончание подачи заявок"
│  - Название объекта     │  Полное из карточки закупки
└────────────┬────────────┘
             ▼
┌─────────────────────────┐
│  4. Final scoring       │  instant_search.py → smart_matcher.py
│  - Keywords + вес       │  Compound +35, exact +25, partial +18, synonym +20
│  - Регион ✓             │  Strict: если в фильтре есть регионы — тендер ДОЛЖЕН совпасть
│  - Цена ✓               │  ±20 баллов от диапазона
│  - Negative patterns    │  -5 за военные, медицинские, строительные паттерны
│  - Strict mode          │  8+ keywords → нужно ≥10% совпадений
│  Score: 0-100           │
└────────────┬────────────┘
             ▼
┌─────────────────────────┐
│  5. AI Verification     │  ai_relevance_checker.py (gpt-4o-mini)
│  - Семантический анализ │  Промпт проверяет тип, тему, деятельность
│  - confidence 0-100     │  ACCEPT ≥ 40, REJECT < 25
│  - Composite boost:     │  confidence ≥ 60 → score +15
│    score = smart +ai    │  confidence 40-59 → score +10
│  - Кэш 24ч (in-memory) │  Квота: trial=20, basic=100, premium=10000/день
└────────────┬────────────┘
             ▼
┌─────────────────────────┐
│  6. Notification        │  service.py → telegram_notifier.py
│  - MIN_SCORE = 35       │  Composite score (smart_matcher + ai_boost)
│  - Dedup по БД          │  Unique: (user_id, filter_id, tender_number)
│  - Квота уведомлений    │  trial=20, basic=50, premium=100/день
│  - Тихие часы           │  Per-user настройка (МСК время)
│  - Per-filter sending   │  Отправка сразу после каждого фильтра (не batch)
│  - Blocked user detect  │  TelegramForbiddenError → mark + деактивация
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

## Как работает фильтрация по регионам (ТЕКУЩЕЕ СОСТОЯНИЕ)

### Проблема: тендеры из чужих регионов проскакивают

**Корень проблемы**: Регион определяется при enrichment (шаг 3), но извлечение ненадёжное.

#### Откуда берётся регион:

1. **RSS URL** — регион передаётся как код в запрос (`regionDelimitedIdList`), но:
   - Захардкожено только 52 из 85 регионов РФ
   - Сервер zakupki.gov.ru не всегда фильтрует точно

2. **Enrichment: из названия заказчика** — substring search по 52 регионам:
   ```
   "ООО Ромашка г. Екатеринбург" → Свердловская область ✅
   "ООО Ромашка" → None (регион неизвестен) ❌
   ```

3. **Enrichment: из адреса на странице** — regex "Почтовый адрес" / "Место нахождения":
   - Парсит город, область, республику
   - Частые сбои: HTML-структура меняется, адрес может быть юридическим (не факт. регион)

#### Фильтрация в SmartMatcher:
- Если фильтр указал регионы И регион тендера известен → strict match (reject если не совпал)
- Если регион тендера **не определён** (None/пусто) → **тендер ПРОХОДИТ** (не отклоняется)
- Поддержка: федеральные округа (раскрытие в регионы), 40+ алиасов, нормализация суффиксов

#### Итого — дыры:
1. Enrichment не извлёк регион → тендер проходит без проверки региона
2. RSS-сервер вернул тендер не из запрошенного региона → client-side проверка зависит от enrichment
3. Юр. адрес ≠ фактический регион → false match
4. Неполный список регионов (52 из 85)

---

## Известные проблемы и что уже сделано

### ✅ Решено
| Проблема | Решение | Коммит |
|----------|---------|--------|
| Уведомления терялись при рестарте | Per-filter sending вместо batch | `4f634c9` |
| Нерелевантные тендеры (score 22) | MIN_SCORE 20→35 | `f6417e8` |
| Мало тендеров после повышения порога | Composite score (smart + AI boost) | `bdbc0f5` |
| Дайджест "0 тендеров" в группах | Skip groups в digest | `b7c3c40` |
| Заблокированные пользователи не отражались | Detect + mark + show in admin | `2313272` |
| Deadline parsing ломался | Исправлен strptime | `4f634c9` |
| "Товары" фильтр ловил услуги | Type filter: check name only, skip "поставка" | ранее |
| Неизвестный регион → pre-score 0 | Убран регион из pre-score | ранее |

### ❗ Открытые проблемы
1. **Тендеры из чужих регионов** — enrichment не всегда извлекает регион; если регион не определён, тендер проходит
2. **STOP_WORDS фильтруют "ПО"** — ключевые слова < 3 символов игнорируются (ПО, IT, ИБП)
3. **AI квота soft-fail** — при исчерпании квоты тендеры проходят без AI-проверки (confidence=50)
4. **ai_intent не обновляется** при редактировании фильтра — генерируется только при создании
5. **Synonym explosion** — 180+ синонимов, возможны false positive
6. **Enrichment blocking** — нет таймаута, медленная страница блокирует весь поиск
7. **In-memory кэши** — AI кэш, enrichment кэш теряются при перезагрузке (Railway restarts)
8. **Quiet hours без DST** — фиксированный UTC+3, нет поддержки летнего времени
9. **Strict mode** — 40% penalty при 8+ keywords и < 2 совпадений (может быть слишком агрессивно)

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
| Negative pattern penalty | -5 | smart_matcher.py | Военные, мед. и т.д. |
| Strict mode penalty | -40% | smart_matcher.py | При 8+ keywords и < 10% match |
| Poll interval | 300s (5 мин) | service.py | Интервал мониторинга |
| Enrichment cache | 500 тендеров | instant_search.py | Max размер кэша |
| AI cache TTL | 24 часа | ai_relevance_checker.py | Время жизни кэша AI |

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
│   └── regions.py                — Федеральные округа → регионы mapping
│
├── src/parsers/
│   └── zakupki_rss_parser.py     — RSS парсинг, enrichment, type filter
│
├── database.py                   — SQLAlchemy модели (все таблицы)
└── alembic/                      — Миграции БД
```

---

## User journey

1. **/start** → Welcome screen → Онбординг (выбор сферы)
2. **Создание фильтра** → Ключевые слова → Регионы (по ФО) → Цена → Тип → AI intent
3. **Автомониторинг** → Каждые 5 мин → Уведомление в Telegram с кнопками
4. **Действия с тендером** → "Интересно" / "В таблицу" / "Пропустить" / "AI-резюме"
5. **Дайджест** → 09:00 МСК → Сводка за сутки
6. **Управление** → Пауза/Вкл, Редактирование фильтров, Корзина, Статистика

---

## Вопросы для брейншторма

1. **Регионы**: Как надёжнее определять регион тендера? Reject если регион не определён? Дополнительный API?
2. **Scoring**: Правильный ли баланс между recall (больше тендеров) и precision (меньше мусора)?
3. **AI**: Стоит ли использовать более мощную модель (gpt-4o) вместо mini? Или fine-tune?
4. **Short keywords**: Как обрабатывать "ПО", "IT", "ИБП" — сейчас они игнорируются как <3 символов
5. **Enrichment**: Как ускорить? Параллелизм? Другой источник данных?
6. **Масштабирование**: Что если 100+ фильтров? Текущий цикл последовательный
7. **Feedback loop**: Использовать ли "Интересно"/"Пропустить" для обучения scoring?

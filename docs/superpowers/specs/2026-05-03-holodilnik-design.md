# Holodilnik.ru — автопоиск артикулов под тендер — Design Spec

**Дата:** 2026-05-03
**Автор:** Николай Чижик (с агентом)
**Статус:** Draft

---

## 1. Цель и контекст

### Что строим

Кнопка «Найти на holodilnik.ru» в модалке карточки тендера в стадии RFQ (Запрос предложений) → подмодалка с найденными артикулами. Заменяет ручной workflow Николая по подбору позиций.

### Текущий ручной процесс

1. Получает уведомление о тендере на бытовую технику
2. Открывает документацию тендера → выписывает позиции с характеристиками
3. Идёт на holodilnik.ru, по каждой позиции вводит keyword в поиск
4. Сравнивает характеристики моделей с ТЗ, отбирает 1-3 модели
5. Копирует артикулы, отправляет менеджеру holodilnik
6. Менеджер отвечает с оптовыми ценами и наличием
7. Расчёт маржи → решение участвовать или нет

### Целевой автоматический процесс (этот спек)

Шаги 2-4 автоматизируются. Шаги 1, 5-7 остаются за юзером.
- Юзер открывает карточку тендера в Pipeline → жмёт «Найти на holodilnik.ru»
- Система: AI извлекает позиции из ТЗ → AI превращает каждую в keyword + фильтры → парсит holodilnik по каждой → возвращает 5-10 моделей на позицию
- Юзер видит сетку карточек с фото, чекбоксами выбирает нужное, копирует артикулы
- Дальше пересылает менеджеру вручную (как раньше)

### Цели MVP

1. Извлечение позиций из тендерной документации через существующий `tender_sniper/ai_document_extractor.py`
2. AI keyword-rewrite для каждой позиции (~$0.001 за позицию)
3. Парсинг публичного поиска `holodilnik.ru/search/?text=...`
4. UI: подмодалка с сеткой карточек по позициям ТЗ
5. Кэш результатов на 24 часа в `pipeline_card.data.suppliers`
6. Кнопка «📋 Артикулы» — копирует выбранные SKU в clipboard

### Non-goals (отложено)

- Email-отправка запроса менеджеру (зависит от починки SMTP)
- Учёт оптовых цен в расчёте маржи (нет API holodilnik для оптовых)
- Парсинг других поставщиков (M.Видео, DNS, ОЗОН) — этот спек только про holodilnik
- Полный детальный список характеристик в карточке (только название + цена + фото в MVP)
- Background-парсинг автоматически при попадании в стадию RFQ

---

## 2. Scope

### In-scope

- Подмодалка `_modal_holodilnik.html` поверх pipeline-модалки
- Backend: `cabinet/holodilnik_service.py` — парсинг + AI keyword
- API: 2 endpoint (start_search, get_status)
- Async polling — статус обновляется каждые 2 сек на UI
- Кэш в `pipeline_card.data.suppliers`
- Чекбоксы выбора + сохранение selected SKU в `data.suppliers[].selected`
- Кнопка «Запустить поиск заново» — игнорирует кэш

### Out-of-scope

- Email/Telegram отправка запросов
- Регистрационная авторизация на holodilnik (используем только публичный поиск)
- Расчёт маржи (юзер вводит вручную в полях `purchase_price` карточки)
- Хранение полной истории прошлых поисков (кэш перезаписывается)

---

## 3. Архитектура

### Подход: async + polling

```
[Browser]                       [aiohttp кабинет]                  [Background task]              [External]
   |                                  |                                   |                           |
   |-- POST /holodilnik-search  ----->|                                   |                           |
   |                                  |-- create task, store status ----->|                           |
   |<-- 202 Accepted, task_id --------|                                   |                           |
   |                                  |                                   |-- AI extract positions -->|
   |-- GET /holodilnik-status (loop)->|                                   |   tender_sniper.ai_*      |
   |<-- {progress: '3/10', ... } -----|                                   |                           |
   |                                  |                                   |-- AI keyword (per pos) -->|
   |                                  |                                   |   tender_sniper.ai_*      |
   |                                  |                                   |                           |
   |                                  |                                   |-- search holodilnik   -->|
   |                                  |                                   |   /search/?text=...      |
   |                                  |                                   |   (per keyword)           |
   |                                  |                                   |                           |
   |-- GET /holodilnik-status ------->|                                   |-- store result in        |
   |<-- {status: 'done', results} ----|<-- update card.data.suppliers ----|   card.data.suppliers     |
   |                                  |                                   |                           |
   |-- render grid                    |                                   |                           |
```

### Stack

- Backend: aiohttp + SQLAlchemy (как остальной кабинет)
- HTTP client для holodilnik: `aiohttp.ClientSession` с `User-Agent` like browser
- HTML parsing: `BeautifulSoup4` (уже в requirements.txt — используется в `src/parsers/`)
- AI: существующий `tender_sniper/ai_document_extractor.py` для extraction + новый легковесный `tender_sniper/ai_keyword_rewriter.py` для keyword-rewrite (или встроим в `cabinet/holodilnik_service.py`)
- Tasks: in-memory dict task_id → status. Не нужна Celery/Redis — задача короткая (10-60 сек), переживёт перезапуск через recovery с кэшем в БД

### Файлы

**Создаются:**
- `cabinet/holodilnik_service.py` — main domain logic
- `cabinet/templates/_modal_holodilnik.html` — подмодалка
- `cabinet/static/css/pages/holodilnik.css` (или дописать в `pipeline.css`)
- `cabinet/static/js/pages/holodilnik.js` (или дописать в `pipeline.js`)
- `tests/unit/test_holodilnik_service.py`

**Изменяются:**
- `cabinet/api.py` — `+2` endpoint
- `cabinet/routes.py` — регистрация
- `cabinet/templates/_modal_card.html` — кнопка «Найти на holodilnik.ru» (есть как заглушка)
- `cabinet/static/js/pages/pipeline.js` — wire-up button → подмодалка

---

## 4. Модель данных

### `pipeline_cards.data.suppliers` (JSON, существует, не было использовано)

```json
{
  "holodilnik": {
    "status": "done",
    "started_at": "2026-05-03T22:30:00Z",
    "finished_at": "2026-05-03T22:30:45Z",
    "expires_at": "2026-05-04T22:30:00Z",
    "positions": [
      {
        "tz_text": "Холодильник двухкамерный, ≥250 л, класс A, No Frost",
        "ai_query": "холодильник двухкамерный 250л",
        "ai_filters": { "class": ["A", "A+", "A++"], "frost": "no_frost" },
        "results": [
          {
            "sku": "HOLO-128342",
            "name": "LG GA-B509 SAQM",
            "price": 42990,
            "url": "https://www.holodilnik.ru/.../holodilnik-lg-ga-b509-saqm.html",
            "img": "https://...",
            "in_stock": true,
            "selected": true
          },
          ...
        ]
      },
      ...
    ]
  }
}
```

**Кэш:** при повторном открытии модалки JS читает `data.suppliers.holodilnik`. Если `expires_at > now` — рендерим как есть. Иначе показываем кнопку «Запустить поиск заново».

**Selected:** при клике чекбокса → `POST /api/.../holodilnik-toggle-select` обновляет флаг в JSON. Кнопка «Артикулы» собирает `[r for r in positions[*].results if r.selected]`.

### In-memory tasks

В `holodilnik_service.py`:
```python
_TASKS: Dict[str, Dict] = {}  # task_id → {status, progress, error}
```

Простой dict (не нужна Redis — single-instance Railway, задача 10-60 сек). При рестарте сервера — task пропадает, но юзер увидит результат через polling cache (если background task успел сохранить в `card.data` до падения).

---

## 5. Backend logic

### `cabinet/holodilnik_service.py` — основные функции

```python
async def start_holodilnik_search(card_id: int, company_id: int, by_user_id: int,
                                   force: bool = False) -> Dict:
    """Запускает background task. Возвращает {task_id, cached?}.
    Если есть свежий кэш и force=False — возвращаем сразу с cached=True."""

async def get_holodilnik_status(task_id: str) -> Dict:
    """Возвращает {status, progress, error?, results?}.
    status ∈ {pending, running, done, error}."""

async def _do_search(task_id: str, card_id: int, company_id: int, by_user_id: int):
    """Background task. По шагам:
    1. Загрузить card → tender data
    2. Если ai_summary пустой — extract_tender_documentation
    3. Извлечь positions из summary (AI или regex)
    4. Для каждой позиции: keyword_rewrite + search_holodilnik
    5. Сохранить в card.data.suppliers.holodilnik"""

async def _ai_keyword_rewrite(tz_position: str) -> Dict:
    """OpenAI prompt: 'Преобразуй ТЗ позицию в keyword + filters'.
    Возвращает {query: str, filters: dict}."""

async def _search_holodilnik(query: str, limit: int = 10) -> List[Dict]:
    """HTTP GET /search/?text=... → BeautifulSoup парсинг карточек.
    Возвращает [{sku, name, price, url, img, in_stock}]."""

async def toggle_selected(card_id: int, company_id: int,
                           position_idx: int, sku: str, selected: bool) -> Dict:
    """Обновляет флаг selected в card.data.suppliers.holodilnik.positions[idx].results[sku]."""

def get_selected_skus(card: Dict) -> List[str]:
    """Возвращает список SKU всех selected моделей."""
```

### AI keyword-rewrite (prompt)

```
System: Ты помощник по подбору товаров. Из строки ТЗ заказчика извлеки
максимально короткий поисковый запрос (3-6 слов) для интернет-магазина
бытовой техники, плюс структурированные фильтры по характеристикам.
Отвечай строго JSON.

User: «Холодильник двухкамерный, объём не менее 250 л, класс A, No Frost,
цвет белый»

Output JSON:
{
  "query": "холодильник двухкамерный 250л",
  "filters": {
    "min_volume_l": 250,
    "energy_class": ["A", "A+", "A++", "A+++"],
    "frost": "no_frost",
    "color": "white"
  }
}
```

Использует `OPENAI_API_KEY` (уже есть). Модель `gpt-4o-mini`. Один запрос на позицию (~$0.001).

### Holodilnik HTML парсинг

URL: `https://www.holodilnik.ru/search/?text=<urlencoded>`

Кодировка: `windows-1251` (явно в `<meta charset>`). Перед парсингом — `response.content.decode('cp1251', errors='replace')`.

Селекторы (примерные, на финальной реализации проверить через DevTools):
- `.product-card` — обёртка карточки товара
- `.product-card__name` — название
- `.product-card__price` — цена (текстом, парсим число)
- `.product-card__photo img[src]` — фото
- `.product-card a[href]` — ссылка
- `.product-card .out-of-stock` — флаг "не в наличии"
- SKU — извлекаем из URL (`.../holodilnik-lg-ga-b509-saqm.html` → последний segment) или из data-attribute

User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15. Холодильник может блокировать `python-requests/...` — нужен браузерный.

Лимит: top-10 моделей на позицию. Если меньше 5 — добавляем «фильтры не сработали, показываем все 10» в UI.

### Фильтрация результатов по AI filters

После HTML-парсинга — пробегаемся по карточкам и оставляем те, что подходят. Простые правила:
- `min_volume_l`: парсим объём из названия/спек если получится, иначе пропускаем фильтр
- `energy_class`: ищем pattern `[АA]\+*` в спеках
- `frost`: ищем «no frost» / «но фрост» в названии

Если фильтр не удалось проверить — оставляем модель (better recall than precision на MVP).

### API endpoints

```
POST /cabinet/api/pipeline/cards/{id}/holodilnik-search
  body: {force?: bool}
  response 202: {ok, task_id, cached?: false}
  response 200: {ok, cached: true, results: {...}}  ← если есть свежий кэш

GET /cabinet/api/pipeline/cards/{id}/holodilnik-status?task_id={tid}
  response: {
    status: 'pending'|'running'|'done'|'error',
    progress?: '3/10',
    current_step?: 'AI keyword for: холодильник...',
    error?: '...',
    results?: { ...полный suppliers.holodilnik объект... }
  }

POST /cabinet/api/pipeline/cards/{id}/holodilnik-toggle
  body: {position_idx: int, sku: str, selected: bool}
  response: {ok}
```

Все защищены `@require_team_member` и проверяют `card.company_id` совпадает с `request.company.id`.

---

## 6. UI

### Кнопка «Найти на holodilnik.ru»

В `_modal_card.html` уже есть `#cm-btn-supplier`. Сейчас она показывает Toast «Функция в разработке». Меняем на:
- Если `card.data.suppliers?.holodilnik?.expires_at > now` — открыть подмодалку, render из кэша
- Иначе — POST `/holodilnik-search` → откроется подмодалка с loading-состоянием

### Подмодалка `#holodilnik-modal`

Структура:
```
[Header]
  Поставщики · holodilnik.ru                                    [×]
  «Тендер X на сумму Y»

[Loading state — пока status != done]
  ⏳ Ищем подходящие модели...
  Шаг 3/10: «холодильник двухкамерный»

[Results state — status == done]
  Каждая позиция ТЗ — секция:
    [Position header: «Холодильник двухкамерный, ≥250 л...»]
    [Сетка 4×N карточек: фото / название / SKU / цена / чекбокс]

[Footer fixed]
  Выбрано: 5 · сумма по рознице: 247 950 ₽
  [📋 Скопировать артикулы] [🔄 Заново]
```

### Карточка модели

```html
<div class="hl-item" data-sku="HOLO-128342" data-position-idx="0">
  <input class="hl-check" type="checkbox" {% if selected %}checked{% endif %}>
  <a href="{{url}}" target="_blank"><img src="{{img}}" alt=""></a>
  <div class="hl-name">LG GA-B509 SAQM</div>
  <div class="hl-sku">№ HOLO-128342</div>
  <div class="hl-price">42 990 ₽</div>
</div>
```

### JS flow

```javascript
async function openHolodilnikModal(cardId) {
  const r = await fetch(`/.../holodilnik-search`, { method: 'POST', body: JSON.stringify({}) });
  if (r.status === 200) {
    const { results } = await r.json();
    renderResults(results);  // из кэша
    return;
  }
  if (r.status === 202) {
    const { task_id } = await r.json();
    startPolling(cardId, task_id);
  }
}

function startPolling(cardId, taskId) {
  const interval = setInterval(async () => {
    const r = await fetch(`/.../holodilnik-status?task_id=${taskId}`);
    const data = await r.json();
    updateLoadingUI(data.progress, data.current_step);
    if (data.status === 'done') {
      clearInterval(interval);
      renderResults(data.results);
    } else if (data.status === 'error') {
      clearInterval(interval);
      showError(data.error);
    }
  }, 2000);
  // safety timeout 5 минут
  setTimeout(() => clearInterval(interval), 5 * 60 * 1000);
}

async function toggleSelect(cardId, positionIdx, sku, selected) {
  await fetch(`/.../holodilnik-toggle`, {
    method: 'POST',
    body: JSON.stringify({ position_idx: positionIdx, sku, selected }),
  });
  updateFooterTotals();
}

async function copySkus() {
  const skus = collectSelectedSkus();  // из DOM
  await navigator.clipboard.writeText(skus.join('\n'));
  Toast.show('✓ Артикулы скопированы');
}
```

---

## 7. Тестирование

### Unit (`tests/unit/test_holodilnik_service.py`)

- `_ai_keyword_rewrite`: mock OpenAI, проверяет JSON-парсинг + fallback на raw query при ошибке AI
- `_search_holodilnik`: mock aiohttp response с готовым HTML fixture (сохранить пример из реального запроса) → проверяет правильность парсинга 5-10 карточек
- `start_holodilnik_search`: cached vs fresh — проверяем что cache hit возвращает 200 + results, miss → 202 + task_id
- `toggle_selected`: модифицирует JSON, не повреждает другие позиции

### Integration (manual smoke)

После деплоя:
- Открыть карточку тендера в стадии RFQ → жмём кнопку → подмодалка появляется в loading
- Через 30-60 сек видим 5-10 позиций с моделями
- Чекбокс — переключается, footer обновляется
- Закрыть-открыть модалку — результат из кэша мгновенно
- «🔄 Заново» — игнорирует кэш, новый search

---

## 8. Производительность и стоимость

### Время

- AI extraction: 10-30 сек (если карточка ещё не обогащена) — только первый раз
- AI keyword-rewrite: 1-2 сек × N позиций = 5-20 сек total
- Holodilnik HTTP × N: 0.5-1 сек × N = 2-10 сек
- **Итого: 30-60 сек** для тендера на 5-10 позиций

### Стоимость GPT

Per-search:
- Extraction: уже сделано (если `card.ai_summary` заполнен)
- Keyword rewrite: $0.001 × 10 позиций = $0.01

Per-tender ≈ $0.01-0.05. Дёшево.

### Лимит и rate-limiting

- 1 параллельный поиск на пользователя (если жмёт «Заново» когда уже идёт — 409 Conflict)
- Holodilnik HTTP: 1 запрос в секунду (интервал между keyword-ами) чтобы не получить ban
- Если holodilnik вернул 429/503 — задача `error`, юзер видит «Холодильник не отвечает, попробуйте позже»

---

## 9. Безопасность

- HTML холодильника — внешний контент. **Никогда** не вставляем raw HTML в DOM. Только `textContent` для названий, проверенный URL для `<img src>` и `<a href>` (только https://www.holodilnik.ru/).
- AI keyword: prompt вкладывает text из ТЗ → возможен prompt-injection. Парсим JSON-ответ через `json.loads` с try/except, на error — fallback на raw text query.
- `card_id` всегда проверяется на принадлежность `request.company.id` (RBAC из team_service).

---

## 10. Деплой

1. Push в `main` → Railway auto-deploy
2. Без миграций БД (используем существующее JSON-поле `pipeline_cards.data`)
3. Smoke-тест на одной карточке тендера

### Откат

Удалить кнопку из `_modal_card.html` (или превратить обратно в Toast «В разработке»). Backend код не вредит существующему функционалу.

---

## 11. Открытые вопросы / next steps

1. **Email-отправка запроса менеджеру** — следующая итерация. Зависит от починки SMTP или подключения Resend.
2. **Парсинг других поставщиков** (M.Видео, DNS, ОЗОН) — отдельный спек. Архитектура `holodilnik_service.py` должна быть extensible: переименуем в `supplier_service.py` с pluggable backends.
3. **Хранение истории поисков** — сейчас перезаписываем кэш. Если понадобится «когда я искал эту модель в прошлый раз» — нужна отдельная таблица `holodilnik_searches`.
4. **Оптовые цены** — не доступны через публичный сайт. Возможно через парсинг переписки в почте? Backlog.

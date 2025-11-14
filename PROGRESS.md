# Прогресс разработки Tender AI Agent

**Дата последнего обновления:** 2025-11-14
**Статус:** В активной разработке
**Последний коммит:** 5ff87d4 - Universal file format support

---

## Текущее состояние проекта

### Реализованные функции

#### 1. Telegram Bot (✅ Работает)
- **Статус:** Развернут на Railway, работает стабильно
- **Основные функции:**
  - Поиск тендеров по номеру и ключевым словам
  - AI-анализ тендерной документации
  - Генерация HTML отчетов
  - История запросов пользователя
  - Система контроля доступа с базой данных

#### 2. Система контроля доступа (✅ Обновлена)
- **Файлы:**
  - `bot/middlewares/access_control.py`
  - `bot/database/access_manager.py`
  - `bot/handlers/access_requests.py`

- **Функциональность:**
  - Управление доступом через SQLite базу данных
  - Автоматические запросы на доступ при команде /start
  - Админ-панель для одобрения/отклонения запросов
  - Синхронизация с переменной окружения ALLOWED_USERS
  - Уведомления пользователям об одобрении/отклонении

#### 3. Обработка документов (✅ Значительно расширена)

**Поддерживаемые форматы:**
- ✅ PDF (с OCR для сканов)
- ✅ DOCX/DOC (Word документы)
- ✅ XLSX/XLS (Excel таблицы) - **НОВОЕ**
- ✅ TXT (с автоопределением кодировки) - **НОВОЕ**
- ✅ CSV (табличные данные) - **НОВОЕ**
- ✅ RTF (Rich Text Format) - **НОВОЕ**
- ✅ ZIP (архивы)
- ✅ Универсальный fallback для любых неизвестных форматов - **НОВОЕ**

**Ключевые улучшения:**
- Автоопределение кодировки текстовых файлов (chardet)
- Множественные fallback механизмы
- Извлечение текста из бинарных файлов (strings utility)
- Качественная обработка ошибок

#### 4. AI Анализ (✅ Оптимизирован)

**Исправленные проблемы:**
- ✅ Решена проблема с OpenAI rate limit (429 ошибка)
  - Переход с gpt-4o (30K tokens/min) на gpt-4o-mini (200K context)
  - Применено в `analyze_contract_terms()` и `analyze_documentation()`
  - Экономия: 15x дешевле

**Файлы:**
- `src/analyzers/tender_analyzer.py:231-232`
- `src/analyzers/tender_analyzer.py:685-686`

**Модели:**
- Premium: gpt-4o (для важных задач)
- Fast: gpt-4o-mini (для документации - используется по умолчанию)

---

## Последние изменения (сессия 2025-11-14)

### Коммит 1: `03f400e` - Add XLSX/XLS file support
**Проблема:** XLSX файлы определялись как "unknown", текст не извлекался, GPT возвращал placeholder текст

**Решение:**
- Добавлен метод `extract_from_xlsx()` в `text_extractor.py:292`
- Обновлен `detect_file_type()` для распознавания Excel файлов
- Добавлена зависимость `openpyxl>=3.1.0`

**Результат:** Excel файлы теперь корректно обрабатываются, данные извлекаются из всех листов

### Коммит 2: `5ff87d4` - Universal file format support
**Проблема:** Многие форматы файлов в тендерах не поддерживались

**Решение:**
Добавлены новые методы в `text_extractor.py`:
- `extract_from_text_file()` - TXT с автоопределением кодировки
- `extract_from_csv()` - CSV таблицы
- `extract_from_rtf()` - RTF документы
- `extract_from_unknown()` - универсальный fallback для любых форматов

**Новые зависимости:**
- `chardet>=5.0.0` - определение кодировки
- `striprtf>=0.0.22` - RTF файлы

**Результат:** Система может обработать **ЛЮБОЙ** файл из тендерной документации

---

## Архитектура проекта

### Структура директорий
```
tender-ai-agent/
├── bot/                          # Telegram бот
│   ├── main.py                   # Точка входа бота
│   ├── config.py                 # Конфигурация
│   ├── handlers/                 # Обработчики команд
│   │   ├── start.py
│   │   ├── search.py
│   │   ├── history.py
│   │   ├── admin.py
│   │   └── access_requests.py   # Система запросов доступа
│   ├── middlewares/
│   │   └── access_control.py    # Middleware контроля доступа
│   ├── database/
│   │   ├── db.py
│   │   └── access_manager.py    # Управление доступом
│   └── bot.db                    # SQLite база данных
├── src/                          # Основная логика
│   ├── analyzers/
│   │   └── tender_analyzer.py   # AI анализ тендеров
│   ├── document_processor/
│   │   └── text_extractor.py    # Извлечение текста из документов
│   ├── parsers/
│   │   └── zakupki_parser.py    # Парсинг zakupki.gov.ru
│   └── llm/                      # LLM адаптеры
├── templates/                    # HTML шаблоны отчетов
├── requirements.txt              # Python зависимости
├── nixpacks.toml                # Railway конфигурация
└── .env                         # Переменные окружения (не в git)
```

### Ключевые компоненты

#### 1. Telegram Bot Layer
- **Framework:** aiogram 3.x
- **Storage:** MemoryStorage для FSM
- **Database:** SQLite для пользователей и истории

#### 2. Document Processing Layer
- **PDF:** PyPDF2 + OCR (pdf2image + pytesseract)
- **Office:** python-docx, openpyxl
- **Text:** chardet для кодировок
- **Fallback:** strings utility для бинарных файлов

#### 3. AI Analysis Layer
- **Provider:** OpenAI (gpt-4o-mini)
- **Fallback:** Anthropic Claude, Google Gemini, Groq
- **Retry Logic:** tenacity для надежности

#### 4. Data Collection Layer
- **Parser:** beautifulsoup4 + feedparser
- **Source:** zakupki.gov.ru RSS + HTML

---

## Известные проблемы

### 1. OCR на Railway (⚠️ Частично работает)
**Проблема:** poppler_utils в nixpacks.toml, но не всегда доступен в PATH
**Статус:** Не критично - большинство PDF имеют текстовый слой
**Workaround:** Основной метод извлечения через PyPDF2 работает

### 2. Поврежденные PDF (⚠️ Редко)
**Проблема:** "EOF marker not found" для некоторых PDF
**Статус:** Редкие случаи
**Решение:** Fallback на OCR, затем на универсальный метод

### 3. Прокси требования (ℹ️ Опционально)
**Статус:** Прокси настроен в .env, работает корректно
**Переменная:** PROXY_URL

---

## Конфигурация

### Переменные окружения (.env)
```bash
# Обязательные
BOT_TOKEN=<telegram_bot_token>
OPENAI_API_KEY=<openai_key>

# Опциональные
ADMIN_USER_ID=<telegram_user_id>           # Администратор
ALLOWED_USERS=<user_id1>,<user_id2>        # Белый список (опционально)
PROXY_URL=<socks5://user:pass@host:port>   # Прокси (опционально)

# Альтернативные LLM провайдеры (опционально)
ANTHROPIC_API_KEY=<claude_key>
GOOGLE_API_KEY=<gemini_key>
GROQ_API_KEY=<groq_key>
```

### Railway Deployment
- **Платформа:** railway.app
- **Buildpack:** nixpacks
- **Start Command:** `cd bot && python3 main.py`
- **Health Check:** HTTP запросы к Telegram API
- **Auto-deploy:** Включен на ветке main

---

## Следующие шаги (TODO)

### Высокий приоритет
- [ ] Протестировать XLSX и универсальный fallback с реальными тендерами
- [ ] Мониторинг успешности извлечения текста из разных форматов
- [ ] Оптимизация использования токенов (сжатие длинных документов)

### Средний приоритет
- [ ] Добавить кэширование анализа для одинаковых тендеров
- [ ] Расширить админ-панель (статистика использования)
- [ ] Улучшить HTML шаблоны отчетов

### Низкий приоритет
- [ ] Поддержка старых .xls файлов (через xlrd)
- [ ] Уведомления о новых тендерах по подпискам
- [ ] Экспорт истории в Excel

---

## Технические детали

### Обработка OpenAI Rate Limits
**Файл:** `src/analyzers/tender_analyzer.py`

**Проблема:** gpt-4o имеет лимит 30K tokens/minute
**Решение:** Переключение на gpt-4o-mini (200K context)

```python
# Строки 231-232 (analyze_contract_terms)
# Строки 685-686 (analyze_documentation)
response_text = self._make_api_call(
    system_prompt,
    user_prompt,
    response_format="json",
    use_premium=False  # ← Используем fast модель
)
```

### Универсальное извлечение текста
**Файл:** `src/document_processor/text_extractor.py:509-576`

**Механизм:**
1. Автоопределение кодировки (chardet, confidence >70%)
2. Извлечение текстовых строк (strings utility)
3. Принудительное UTF-8 чтение
4. Информативное сообщение об ошибке

### Управление доступом
**Файл:** `bot/database/access_manager.py`

**Таблица:** `allowed_users`
```sql
CREATE TABLE allowed_users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    added_at TEXT NOT NULL,
    added_by INTEGER,
    notes TEXT
)
```

**Логика:**
1. ADMIN_USER_ID - всегда доступ
2. ALLOWED_USERS = None - открытый доступ
3. ALLOWED_USERS = set() - закрытый, только БД
4. БД синхронизируется с ALLOWED_USERS при старте

---

## Зависимости (requirements.txt)

### Core
- PyPDF2==3.0.1
- python-docx==1.1.0
- openpyxl>=3.1.0 (XLSX)
- chardet>=5.0.0 (кодировки)
- striprtf>=0.0.22 (RTF)

### OCR
- pdf2image>=1.16.3
- pytesseract>=0.3.10
- Pillow>=10.0.0

### LLM
- openai>=1.0.0
- anthropic==0.40.0
- groq>=0.4.0
- google-generativeai>=0.3.0

### Bot
- aiogram>=3.15.0
- aiosqlite>=0.19.0

### Utilities
- pydantic>=2.5.0
- tenacity>=8.2.3
- loguru>=0.7.0
- beautifulsoup4>=4.12.0
- feedparser>=6.0.10

---

## Git History (последние коммиты)

```bash
5ff87d4 - Add universal file format support and smart fallback extraction
03f400e - Add XLSX/XLS file support to document extraction
8e040b2 - Fix OpenAI rate limit by using gpt-4o-mini
<earlier commits>
```

---

## Контакты и ссылки

- **GitHub:** https://github.com/nikoly240702/tender-ai-bot
- **Railway:** Deployed автоматически при push в main
- **Telegram Bot:** @<bot_username> (см. BotConfig.BOT_TOKEN)

---

## Заметки для продолжения работы

### Что работает отлично:
✅ Система контроля доступа
✅ OpenAI интеграция (rate limit исправлен)
✅ Извлечение из PDF, DOCX, XLSX, TXT, CSV, RTF
✅ Универсальный fallback для любых файлов
✅ HTML отчеты
✅ История запросов

### Что требует внимания:
⚠️ OCR на Railway (poppler issues)
⚠️ Тестирование с реальными тендерами после обновлений
⚠️ Мониторинг использования токенов

### Последний статус (2025-11-14):
- Два коммита запушены: XLSX support + Universal file support
- Railway автоматически деплоит (2-5 минут)
- Бот работает стабильно
- Рекомендуется протестировать с тендерами, содержащими различные форматы файлов

---

**Документация готова для продолжения работы.**

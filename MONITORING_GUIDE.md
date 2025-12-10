# 📊 Руководство по мониторингу и отладке Tender Sniper

## 🎯 Уровни мониторинга

### 1. 🔴 Sentry - Отслеживание багов (РЕКОМЕНДУЕТСЯ)

**Настройка:**
```bash
# Добавить в Railway environment variables:
SENTRY_DSN=https://your-key@o123456.ingest.sentry.io/7890123
```

**Как получить Sentry DSN:**
1. Зарегистрируйтесь: https://sentry.io (бесплатно до 5K events/месяц)
2. Create Project → Python
3. Copy DSN из Settings

**Что Sentry отслеживает автоматически:**
- ❌ Все необработанные ошибки
- 📊 Stacktraces с полным контекстом
- 👤 Информация о пользователе (telegram_id)
- 🌍 Environment (production/development)
- 📈 Частота ошибок и тренды
- 🔔 Алерты в Telegram/Email/Slack

**Пример события в Sentry:**
```python
Error: ValidationError in create_filter
User: telegram_id=123456789
Context:
  filter_name: "test"
  keywords: []

Stacktrace:
  File "bot/handlers/sniper_search.py", line 1189
    validated_data = FilterCreate(...)
  pydantic.ValidationError: Необходимо указать хотя бы одно ключевое слово
```

---

### 2. 📡 Railway Logs - Реальное время

**Просмотр логов:**

```bash
# Установить Railway CLI
npm install -g @railway/cli

# Логин
railway login

# Link к проекту (один раз)
railway link

# Смотреть логи в реальном времени
railway logs

# Последние 100 строк
railway logs --limit 100

# Фильтр по ошибкам
railway logs | grep ERROR
railway logs | grep "telegram_id=123456"
```

**Или через Web UI:**
https://railway.app/project/YOUR_PROJECT/logs

**Что логируется:**
```python
# Создание фильтра
2025-12-10 16:15:32 - INFO - Пользователь 123456789 создал фильтр: IT оборудование

# Ошибка валидации
2025-12-10 16:15:45 - ERROR - ❌ Ошибка валидации данных: keywords: Необходимо указать хотя бы одно ключевое слово

# Уведомления
2025-12-10 16:16:10 - INFO - ✅ Уведомление отправлено пользователю 123456789

# Автомониторинг
2025-12-10 16:20:00 - INFO - 🔍 Опрос #5: найдено 3 новых тендера
```

---

### 3. 📊 User Action Logging - Аналитика

**Новая система логирования действий пользователей!**

**Создал файлы:**
- `alembic/versions/20251210_create_user_actions.py` - миграция
- `bot/utils/analytics.py` - helper функции

**Как использовать:**

```python
from bot.utils.analytics import log_user_action

# Логировать действие
await log_user_action(
    user_id=user['id'],  # ID из БД, не telegram_id!
    action_type='filter_created',
    action_data={'filter_name': 'IT оборудование', 'keywords_count': 3}
)
```

**Типы событий:**
```python
# Фильтры
'filter_created'   # Создан фильтр
'filter_edited'    # Изменен фильтр
'filter_deleted'   # Удален фильтр

# Поиск
'search_executed'  # Выполнен мгновенный поиск

# Тендеры
'tender_viewed'    # Просмотрен тендер
'tender_favorited' # Добавлен в избранное
'tender_hidden'    # Скрыт тендер

# Уведомления
'notification_received'  # Получено уведомление
'notification_clicked'   # Кликнули на уведомление
```

**Получение статистики:**

```python
from bot.utils.analytics import get_user_stats, get_popular_actions

# Статистика пользователя
stats = await get_user_stats(user_id=123)
# {
#     'total_actions': 150,
#     'filters_created': 5,
#     'searches_executed': 45,
#     'tenders_viewed': 100
# }

# Популярные действия за неделю
popular = await get_popular_actions(days=7)
# [
#     {'action_type': 'filter_created', 'count': 45},
#     {'action_type': 'search_executed', 'count': 120}
# ]
```

**SQL запросы для анализа:**

```sql
-- Самые активные пользователи
SELECT
    u.telegram_id,
    u.username,
    COUNT(*) as actions_count
FROM user_actions ua
JOIN sniper_users u ON ua.user_id = u.id
WHERE ua.created_at > NOW() - INTERVAL '7 days'
GROUP BY u.id
ORDER BY actions_count DESC
LIMIT 10;

-- Проблемные пользователи (много создают фильтров, но не используют)
SELECT
    u.telegram_id,
    COUNT(*) FILTER (WHERE ua.action_type = 'filter_created') as filters_created,
    COUNT(*) FILTER (WHERE ua.action_type = 'search_executed') as searches
FROM user_actions ua
JOIN sniper_users u ON ua.user_id = u.id
GROUP BY u.id
HAVING COUNT(*) FILTER (WHERE ua.action_type = 'filter_created') > 3
   AND COUNT(*) FILTER (WHERE ua.action_type = 'search_executed') = 0;

-- Конверсия: просмотр → избранное
SELECT
    COUNT(*) FILTER (WHERE action_type = 'tender_viewed') as views,
    COUNT(*) FILTER (WHERE action_type = 'tender_favorited') as favorites,
    ROUND(
        COUNT(*) FILTER (WHERE action_type = 'tender_favorited')::numeric /
        COUNT(*) FILTER (WHERE action_type = 'tender_viewed') * 100,
        2
    ) as conversion_rate
FROM user_actions
WHERE created_at > NOW() - INTERVAL '7 days';
```

---

### 4. 🏥 Health Check - Статус системы

**Endpoints:**
```bash
# Полная проверка (database, bot, sniper_service)
curl https://your-app.railway.app/health

# Готовность к приему запросов
curl https://your-app.railway.app/ready

# Liveness probe (процесс жив?)
curl https://your-app.railway.app/live
```

**Пример ответа:**
```json
{
  "status": "healthy",
  "started_at": "2024-12-10T16:11:51Z",
  "timestamp": "2024-12-10T16:25:30Z",
  "checks": {
    "database": "ok",
    "bot": "running",
    "sniper_service": "ok",
    "sentry": "disabled",
    "config": "ok"
  }
}
```

**Railway автоматически:**
- Проверяет `/health` каждые 30 секунд
- Рестартит при 3+ неудачных проверках подряд
- Показывает uptime в UI

---

### 5. 🗄️ PostgreSQL - Прямой доступ к данным

**Подключение к Railway PostgreSQL:**

```bash
# Через Railway CLI
railway run psql $DATABASE_URL

# Или напрямую (найти URL в Railway Variables)
psql "postgresql://postgres:PASSWORD@postgres.railway.internal:5432/railway"
```

**Полезные запросы:**

```sql
-- Все пользователи
SELECT telegram_id, username, subscription_tier, created_at
FROM sniper_users
ORDER BY created_at DESC
LIMIT 10;

-- Все активные фильтры
SELECT
    u.telegram_id,
    sf.name,
    sf.keywords,
    sf.is_active,
    sf.last_check
FROM sniper_filters sf
JOIN sniper_users u ON sf.user_id = u.id
WHERE sf.is_active = true;

-- Статистика по уведомлениям
SELECT
    u.telegram_id,
    COUNT(*) as notifications_count,
    MAX(stn.sent_at) as last_notification
FROM sniper_tender_notifications stn
JOIN sniper_users u ON stn.user_id = u.id
GROUP BY u.id
ORDER BY notifications_count DESC;

-- Фильтры с ошибками
SELECT
    u.telegram_id,
    sf.name,
    sf.error_count,
    sf.last_check
FROM sniper_filters sf
JOIN sniper_users u ON sf.user_id = u.id
WHERE sf.error_count > 0
ORDER BY sf.error_count DESC;
```

---

## 🐛 Отладка багов

### Сценарий 1: Пользователь сообщает о баге

**Шаги:**

1. **Получите telegram_id пользователя:**
   ```python
   # Попросите пользователя отправить /start
   # В логах будет: "Пользователь 123456789 запустил бота"
   ```

2. **Проверьте Sentry:**
   - Откройте https://sentry.io/your-project
   - Поиск по `user:telegram_id=123456789`
   - Смотрите stacktrace и context

3. **Проверьте Railway Logs:**
   ```bash
   railway logs | grep "telegram_id=123456789"
   ```

4. **Проверьте данные в БД:**
   ```sql
   SELECT * FROM sniper_users WHERE telegram_id = 123456789;
   SELECT * FROM sniper_filters WHERE user_id = (
       SELECT id FROM sniper_users WHERE telegram_id = 123456789
   );
   ```

5. **Проверьте действия пользователя:**
   ```sql
   SELECT * FROM user_actions
   WHERE user_id = (SELECT id FROM sniper_users WHERE telegram_id = 123456789)
   ORDER BY created_at DESC
   LIMIT 20;
   ```

### Сценарий 2: Падают все деплои

1. **Проверьте Railway Build Logs:**
   - Railway UI → Deployments → Latest → Build Logs

2. **Проверьте миграции:**
   ```bash
   railway run alembic current
   railway run alembic history
   ```

3. **Проверьте environment variables:**
   ```bash
   railway variables
   ```

### Сценарий 3: Бот не отвечает

1. **Проверьте Health Check:**
   ```bash
   curl https://your-app.railway.app/health
   ```

2. **Проверьте логи:**
   ```bash
   railway logs --limit 50
   ```

3. **Рестарт:**
   ```bash
   railway up
   ```

---

## 📈 Dashboard для мониторинга

### Вариант 1: Railway UI (бесплатно)
- Логи в реальном времени
- CPU/Memory usage
- Health check status
- Deployment history

### Вариант 2: Sentry Dashboard (бесплатно до 5K events)
- Графики ошибок
- Top errors
- User impact
- Release tracking

### Вариант 3: Metabase + PostgreSQL (опционально)
- Подключить Metabase к Railway PostgreSQL
- Создать дашборды с метриками:
  - Новых пользователей/день
  - Активных фильтров
  - Отправленных уведомлений
  - Конверсии (поиск → избранное)

---

## 🚨 Алерты

### Sentry Alerts (рекомендуется)

**Настройка:**
1. Sentry → Settings → Alerts
2. Create Alert Rule
3. When: "An issue is first seen"
4. Then: Send notification to Telegram/Email

**Примеры алертов:**
- ❌ Новая ошибка в production
- 📈 >10 ошибок за 1 час
- 👤 Ошибка затронула >5 пользователей
- 🔥 Critical error (например, DB connection lost)

### Railway Health Check Alerts

Railway автоматически алертит при:
- 🔴 Service unhealthy (3+ неудачных health checks)
- 💀 Service crashed
- 🔄 Too many restarts

---

## 📝 Best Practices

1. **Всегда логируйте telegram_id:**
   ```python
   logger.info(f"Пользователь {telegram_id} создал фильтр")
   ```

2. **Используйте structured logging:**
   ```python
   logger.info("filter_created", extra={
       'telegram_id': telegram_id,
       'filter_name': filter_name,
       'keywords_count': len(keywords)
   })
   ```

3. **Логируйте действия пользователей:**
   ```python
   await log_user_action(user_id, 'filter_created', {'filter_name': name})
   ```

4. **Не логируйте чувствительные данные:**
   - ❌ Пароли, токены, полные URLs с credentials
   - ✅ Hashed IDs, sanitized data

5. **Мониторьте метрики:**
   - Response time (в логах Railway)
   - Error rate (Sentry)
   - User activity (PostgreSQL)
   - System health (Health check)

---

## 🎯 Что делать сейчас

1. **Обязательно:**
   - [ ] Настроить Sentry DSN в Railway
   - [ ] Запустить миграцию user_actions
   - [ ] Проверить health check endpoint

2. **Рекомендуется:**
   - [ ] Добавить log_user_action в ключевые handler'ы
   - [ ] Настроить Sentry alerts
   - [ ] Проверить Railway logs еженедельно

3. **Опционально:**
   - [ ] Настроить Metabase dashboard
   - [ ] Добавить custom metrics в Sentry
   - [ ] Интегрировать с Telegram для алертов

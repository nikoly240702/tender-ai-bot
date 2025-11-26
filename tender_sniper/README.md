# Tender Sniper 🎯

Real-time tender monitoring and instant notification system for zakupki.gov.ru

## Status

🚧 **PLACEHOLDER** - Module structure created, implementation pending

## Overview

Tender Sniper is a premium module for the Tender AI Bot that provides:
- ⚡ Real-time monitoring of new tenders
- 🎯 Smart matching based on your criteria
- 📱 Instant Telegram notifications
- 💰 Subscription-based monetization
- 🤖 Automated pre-analysis

## Architecture

```
tender_sniper/
├── bot/            # Enhanced Telegram bot with subscriptions
├── parser/         # Real-time parser for zakupki.gov.ru
├── matching/       # Smart matching engine
├── notifications/  # Instant notification service
├── payments/       # Payment processing (YooKassa)
├── database/       # Database models and migrations
├── admin/          # Admin dashboard (web)
└── api/            # REST API for integrations
```

## Development Phases

### Phase 1: Core Infrastructure (Week 1)
- [ ] Database schema design
- [ ] Basic Telegram bot with subscriptions
- [ ] Real-time parser prototype
- [ ] Simple matching engine

### Phase 2: MVP Release (Week 2)
- [ ] Payment integration (YooKassa)
- [ ] Notification system
- [ ] User onboarding flow
- [ ] Basic admin panel

### Phase 3: Premium Features (Week 3-4)
- [ ] Advanced matching algorithms
- [ ] AI pre-analysis
- [ ] API for external integrations
- [ ] Analytics dashboard

## Enabling Tender Sniper

1. Edit `config/features.yaml`:
```yaml
tender_sniper:
  enabled: true
  components:
    realtime_parser: true
    smart_matching: true
    instant_notifications: true
```

2. Install additional dependencies (when implemented):
```bash
pip install -r tender_sniper/requirements.txt
```

3. Run migrations (when implemented):
```bash
python -m tender_sniper.database.migrate
```

4. Start the service (when implemented):
```bash
python -m tender_sniper.start
```

## Subscription Tiers

| Feature | Free | Basic (15K₽/mo) | Premium (50K₽/mo) |
|---------|------|------------------|-------------------|
| Categories | 5 | 15 | Unlimited |
| Notifications/day | 10 | 50 | Unlimited |
| AI Analysis | ❌ | Limited | ✅ Full |
| API Access | ❌ | ❌ | ✅ |
| Priority Support | ❌ | Email | 24/7 |

## Configuration

All settings are managed via `config/features.yaml`:

```python
from tender_sniper.config import is_component_enabled

if is_component_enabled('realtime_parser'):
    # Start real-time parser
    pass
```

## API Usage (Future)

```python
from tender_sniper.parser import RealtimeParser
from tender_sniper.matching import SmartMatcher

# Initialize parser
parser = RealtimeParser()
parser.add_category("компьютерное оборудование")
parser.add_price_range(100_000, 5_000_000)

# Set up matching
matcher = SmartMatcher()
matcher.add_keywords(["ноутбук", "компьютер"])
matcher.add_regions(["Москва", "Санкт-Петербург"])

# Start monitoring
parser.start(callback=matcher.process)
```

## Database Schema (Planned)

```sql
-- Users table
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    subscription_tier VARCHAR(20),
    created_at TIMESTAMP,
    expires_at TIMESTAMP
);

-- Filters table
CREATE TABLE filters (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    keywords TEXT[],
    price_min DECIMAL,
    price_max DECIMAL,
    regions TEXT[],
    active BOOLEAN DEFAULT true
);

-- Notifications table
CREATE TABLE notifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    tender_id VARCHAR(50),
    sent_at TIMESTAMP,
    read BOOLEAN DEFAULT false
);
```

## Testing (When Implemented)

```bash
# Run unit tests
pytest tender_sniper/tests/

# Run integration tests
pytest tender_sniper/tests/ -m integration

# Test parser
python -m tender_sniper.parser.test

# Test matching engine
python -m tender_sniper.matching.test
```

## Deployment (Future)

```yaml
# docker-compose.yml addition
services:
  tender-sniper:
    build: ./tender_sniper
    environment:
      - DATABASE_URL=postgresql://...
      - REDIS_URL=redis://...
      - TELEGRAM_BOT_TOKEN=...
    depends_on:
      - postgres
      - redis
    restart: unless-stopped
```

## Monitoring (Planned)

- Grafana dashboard for metrics
- Sentry for error tracking
- Telegram admin notifications
- Health check endpoint: `/health`

## Revenue Projections

Based on market research:
- **Free users**: 1000+ (funnel top)
- **Basic subscribers**: 50-100 (15K₽/mo = 750K-1.5M₽/mo)
- **Premium subscribers**: 10-20 (50K₽/mo = 500K-1M₽/mo)
- **Total MRR**: 1.25-2.5M₽/month

## 🎉 Implementation Status

### ✅ Completed (Phase 1)

- [x] **Database Schema** - SQLite with full user/filter/notification tables
- [x] **Subscription Plans** - Free, Basic (15K₽), Premium (50K₽) tiers
- [x] **Real-time Parser** - Асинхронный мониторинг RSS-фидов zakupki.gov.ru
- [x] **Smart Matcher** - Scoring алгоритм (0-100) с поддержкой синонимов
- [x] **Telegram Notifier** - Красивые уведомления с inline кнопками
- [x] **Main Service** - Координатор всех компонентов

### 🚧 In Progress (Phase 2)

- [ ] **Payment Integration** - YooKassa/CloudPayments
- [ ] **Telegram Bot Handlers** - FSM для управления фильтрами
- [ ] **Admin Dashboard** - Web-интерфейс для управления

### 📋 Planned (Phase 3)

- [ ] **AI Pre-analysis** - Автоматическая оценка тендеров
- [ ] **API Endpoints** - REST API для интеграций
- [ ] **Analytics** - Расширенная статистика

## 🚀 Quick Start

### 1. Включение Tender Sniper

Отредактируйте `config/features.yaml`:

```yaml
tender_sniper:
  enabled: true
  components:
    realtime_parser: true
    smart_matching: true
    instant_notifications: true
```

### 2. Инициализация базы данных

```bash
python -m tender_sniper.database.init_plans
```

### 3. Запуск сервиса

```bash
python -m tender_sniper.service
```

Или программно:

```python
import asyncio
from tender_sniper.service import TenderSniperService

async def main():
    service = TenderSniperService(
        bot_token="YOUR_BOT_TOKEN",
        poll_interval=300  # 5 минут
    )

    await service.initialize()
    await service.start()

asyncio.run(main())
```

## 📊 Архитектура

```
┌─────────────────────────────────────────────────┐
│         Tender Sniper Service (service.py)      │
│                                                 │
│  ┌──────────┐  ┌─────────┐  ┌──────────────┐  │
│  │ Parser   │→ │ Matcher │→ │ Notifier     │  │
│  │ (RSS)    │  │(Scoring)│  │ (Telegram)   │  │
│  └──────────┘  └─────────┘  └──────────────┘  │
│                       ↓                         │
│                ┌────────────┐                   │
│                │  Database  │                   │
│                │  (SQLite)  │                   │
│                └────────────┘                   │
└─────────────────────────────────────────────────┘
```

### Workflow:

1. **Real-time Parser** опрашивает RSS каждые N минут
2. **Smart Matcher** проверяет новые тендеры против активных фильтров
3. **Database** сохраняет матчи и проверяет квоты уведомлений
4. **Telegram Notifier** отправляет уведомления пользователям

## 🔧 Компоненты

### Real-time Parser

```python
from tender_sniper.parser import RealtimeParser

parser = RealtimeParser(poll_interval=300)
parser.add_callback(on_new_tenders)
await parser.start(keywords="компьютеры", price_min=100_000)
```

### Smart Matcher

```python
from tender_sniper.matching import SmartMatcher

matcher = SmartMatcher()
match_result = matcher.match_tender(tender, filter_config)
# Returns: {'score': 85, 'matched_keywords': [...], ...}
```

### Database

```python
from tender_sniper.database import get_sniper_db

db = await get_sniper_db()

# Create filter
filter_id = await db.create_filter(
    user_id=1,
    name="IT Equipment",
    keywords=["компьютеры", "ноутбуки"],
    price_min=100_000,
    price_max=5_000_000
)

# Check quota
has_quota = await db.check_notification_quota(user_id=1, limit=50)
```

### Telegram Notifier

```python
from tender_sniper.notifications.telegram_notifier import TelegramNotifier

notifier = TelegramNotifier(bot_token="YOUR_TOKEN")

await notifier.send_tender_notification(
    telegram_id=123456789,
    tender=tender_data,
    match_info={'score': 85, 'matched_keywords': [...]},
    filter_name="IT оборудование"
)
```

## 📈 Тарифные планы

| Feature | Free | Basic (15K₽/mo) | Premium (50K₽/mo) |
|---------|------|------------------|-------------------|
| Фильтры | 5 | 15 | Unlimited |
| Уведомления/день | 10 | 50 | Unlimited |
| AI Анализ | ❌ | Limited | ✅ Full |
| API Access | ❌ | ❌ | ✅ |

Планы автоматически инициализируются при первом запуске.

## 🧪 Testing

```bash
# Test real-time parser
python tender_sniper/parser/realtime_parser.py

# Test smart matcher
python tender_sniper/matching/smart_matcher.py

# Test full service
python tender_sniper/service.py
```

## 📝 Logs

Логи сохраняются в:
- `tender_sniper/tender_sniper.log` - главный лог сервиса
- Console output - real-time статус

## Next Steps

1. ✅ ~~Implement database schema~~ DONE
2. ✅ ~~Create real-time parser~~ DONE
3. ✅ ~~Develop smart matcher~~ DONE
4. ✅ ~~Build notification service~~ DONE
5. 🚧 Add payment processing (YooKassa)
6. 🚧 Create Telegram bot handlers (FSM)
7. 📋 Launch MVP
8. 📋 Iterate based on user feedback

## Support

- Documentation: `/docs` (when implemented)
- Admin panel: `/admin` (when implemented)
- API docs: `/api/docs` (when implemented)

---

*This is a placeholder module. Implementation will begin in Phase 2 of the project.*
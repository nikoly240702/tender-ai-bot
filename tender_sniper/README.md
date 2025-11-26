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

## Next Steps

1. Implement database schema
2. Create basic Telegram bot with FSM
3. Develop real-time parser
4. Add payment processing
5. Launch MVP
6. Iterate based on user feedback

## Support

- Documentation: `/docs` (when implemented)
- Admin panel: `/admin` (when implemented)
- API docs: `/api/docs` (when implemented)

---

*This is a placeholder module. Implementation will begin in Phase 2 of the project.*
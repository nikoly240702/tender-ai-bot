# 🚀 Production Deployment Guide - Tender AI Bot

Complete production deployment guide для Tender AI Bot с PostgreSQL.

## 📋 Table of Contents

- [Prerequisites](#prerequisites)
- [Environment Variables](#environment-variables)
- [Local Development](#local-development)
- [Railway Deployment](#railway-deployment)
- [Docker Deployment](#docker-deployment)
- [Database Migrations](#database-migrations)
- [Monitoring & Health Checks](#monitoring--health-checks)
- [Backup & Recovery](#backup--recovery)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required

- **Python 3.11+**
- **PostgreSQL 16+** (or Railway/Heroku managed PostgreSQL)
- **Telegram Bot Token** from [@BotFather](https://t.me/BotFather)

### Recommended

- **Sentry account** for error monitoring
- **Docker & Docker Compose** for containerization
- **Railway CLI** for Railway deployments

---

## Environment Variables

### Required Variables

```bash
# Telegram Bot Token
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz

# PostgreSQL Database URL
DATABASE_URL=postgresql://user:password@host:port/dbname
```

### Recommended Variables

```bash
# Access Control (comma-separated Telegram user IDs)
ALLOWED_USERS=123456789,987654321

# Sentry DSN for error tracking
SENTRY_DSN=https://public@sentry.io/project-id
```

### Optional Variables

```bash
# Logging
LOG_LEVEL=INFO                    # DEBUG, INFO, WARNING, ERROR
LOG_FORMAT=json                   # json (production) or human (development)

# Health Check
HEALTH_CHECK_PORT=8080

# Proxy (if needed)
PROXY_URL=socks5://user:pass@host:port

# AI Provider API Keys
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GROQ_API_KEY=gsk_...
GOOGLE_API_KEY=...
```

See `.env.example` for complete configuration template.

---

## Local Development

### 1. Clone Repository

```bash
git clone <repository-url>
cd tender-ai-bot
```

### 2. Create Virtual Environment

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Setup Environment

```bash
cp .env.example .env
# Edit .env with your configuration
```

### 5. Run Database Migrations

```bash
# Initialize database (creates tables)
alembic upgrade head
```

### 6. Start Bot

```bash
python -m bot.main
```

### Using Docker Compose (Recommended)

```bash
# Start PostgreSQL + Bot + PgAdmin
docker-compose up -d

# View logs
docker-compose logs -f bot

# Stop services
docker-compose down
```

Access PgAdmin at http://localhost:5050 (only with `--profile dev`):

```bash
docker-compose --profile dev up
```

---

## Railway Deployment

### Quick Deploy

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new)

### Manual Deployment

#### 1. Install Railway CLI

```bash
npm install -g @railway/cli
```

#### 2. Login to Railway

```bash
railway login
```

#### 3. Initialize Project

```bash
railway init
```

#### 4. Add PostgreSQL

```bash
railway add postgresql
```

This automatically creates `DATABASE_URL` environment variable.

#### 5. Set Environment Variables

```bash
# Set required variables
railway variables set BOT_TOKEN="your-bot-token"
railway variables set ALLOWED_USERS="123456789,987654321"
railway variables set SENTRY_DSN="your-sentry-dsn"

# Optional: Set log format to JSON for production
railway variables set LOG_FORMAT="json"
railway variables set LOG_LEVEL="INFO"
```

#### 6. Deploy

```bash
# Push code to Railway
railway up

# Or link GitHub repository for automatic deployments
railway link
```

#### 7. Run Migrations

Migrations run automatically on startup via `railway.json`:

```json
{
  "deploy": {
    "startCommand": "alembic upgrade head && python -m bot.main"
  }
}
```

#### 8. View Logs

```bash
railway logs
```

### Railway Configuration Files

- **`railway.json`** - Railway deployment config
- **`railway.toml`** - Alternative config format
- **`Procfile`** - Heroku-style process definition

### Health Checks

Railway automatically uses `/health` endpoint for health checks:

```bash
curl https://your-app.railway.app/health
```

Response:
```json
{
  "status": "healthy",
  "started_at": "2024-11-24T12:00:00Z",
  "checks": {
    "database": "ok",
    "bot": "running",
    "sniper_service": "ok"
  }
}
```

---

## Docker Deployment

### Build Image

```bash
docker build -t tender-ai-bot .
```

### Run Container

```bash
docker run -d \
  --name tender-ai-bot \
  -e BOT_TOKEN="your-token" \
  -e DATABASE_URL="postgresql://..." \
  -e ALLOWED_USERS="123,456" \
  -p 8080:8080 \
  tender-ai-bot
```

### Using Docker Compose

```bash
# Production setup (bot + postgres)
docker-compose up -d

# Development setup (includes PgAdmin)
docker-compose --profile dev up -d
```

**Services:**
- **bot** - Telegram bot на порту 8080
- **postgres** - PostgreSQL database на порту 5432
- **pgadmin** - PgAdmin на порту 5050 (dev only)

---

## Database Migrations

### Create Migration

```bash
alembic revision --autogenerate -m "Description of changes"
```

### Apply Migrations

```bash
# Upgrade to latest
alembic upgrade head

# Upgrade one version
alembic upgrade +1

# Downgrade one version
alembic downgrade -1

# View current version
alembic current

# View migration history
alembic history
```

### Migration on Deployment

Migrations run automatically on Railway/Heroku via start command:

```bash
alembic upgrade head && python -m bot.main
```

---

## Monitoring & Health Checks

### Health Check Endpoints

| Endpoint | Purpose |
|----------|---------|
| `/health` | Full health check (database, services) |
| `/ready` | Readiness probe (is app ready?) |
| `/live` | Liveness probe (is app alive?) |

### Example Health Check

```bash
curl http://localhost:8080/health
```

### Sentry Integration

Error tracking via Sentry:

1. Create Sentry project at https://sentry.io
2. Copy DSN
3. Set `SENTRY_DSN` environment variable

```bash
export SENTRY_DSN="https://public@sentry.io/project-id"
```

Sentry captures:
- Unhandled exceptions
- Bot errors
- Performance traces (10% sampling)

### Structured Logging

Production uses JSON logs for parsing by ELK, DataDog, CloudWatch:

```json
{
  "timestamp": "2024-11-24T12:34:56.789Z",
  "level": "INFO",
  "logger": "bot.main",
  "message": "Bot started",
  "extra": {"user_id": 123}
}
```

Switch to human-readable format for development:

```bash
export LOG_FORMAT=human
```

---

## Backup & Recovery

### Automated Backups

#### Via Railway

Railway PostgreSQL includes automated backups. View in Railway dashboard.

#### Manual Backups

```bash
# Run backup script
./scripts/backup_db.sh

# Backups saved to: backups/backup_YYYY-MM-DD_HH-MM-SS.sql.gz
```

#### Automated Backups with Cron

```bash
# Add to crontab (daily at 3 AM)
crontab -e

# Add line:
0 3 * * * /path/to/scripts/backup_cron.sh
```

### Restore from Backup

```bash
# Restore from backup file
./scripts/restore_db.sh backups/backup_2024-11-24_12-00-00.sql.gz
```

⚠️ **Warning:** Restore will overwrite all existing data!

### Upload Backups to Cloud

Uncomment cloud upload in `scripts/backup_cron.sh`:

```bash
# AWS S3
aws s3 cp "$BACKUP_FILE" "s3://your-bucket/backups/"

# Google Cloud Storage
gsutil cp "$BACKUP_FILE" "gs://your-bucket/backups/"
```

---

## Troubleshooting

### Bot Not Starting

**Check logs:**

```bash
# Railway
railway logs

# Docker
docker logs tender_ai_bot

# Local
tail -f bot.log
```

**Common issues:**

1. **Missing BOT_TOKEN**
   ```
   ❌ Missing required: BOT_TOKEN
   ```
   → Set `BOT_TOKEN` environment variable

2. **Database connection failed**
   ```
   ❌ Error: connection to server failed
   ```
   → Check `DATABASE_URL` format
   → Verify PostgreSQL is running

3. **Module import errors**
   ```
   ModuleNotFoundError: No module named 'X'
   ```
   → Run `pip install -r requirements.txt`

### Migration Errors

**Alembic can't find database**

```bash
# Check DATABASE_URL
echo $DATABASE_URL

# Test connection
psql $DATABASE_URL
```

**Migration conflicts**

```bash
# Reset to specific version
alembic downgrade <revision>

# Or recreate from scratch (⚠️ DESTRUCTIVE)
alembic downgrade base
alembic upgrade head
```

### Health Check Failing

**Check health endpoint:**

```bash
curl http://localhost:8080/health
```

**Common causes:**
- Database connection issues
- Tender Sniper service crashed
- Bot not fully initialized

### Performance Issues

**Enable debug logging:**

```bash
export LOG_LEVEL=DEBUG
```

**Check database performance:**

```bash
# Connect to PostgreSQL
psql $DATABASE_URL

# Check slow queries
SELECT * FROM pg_stat_statements ORDER BY total_time DESC LIMIT 10;
```

### Out of Memory

**Reduce connection pool size** in `database.py`:

```python
pool_size=10,      # Reduce from 20
max_overflow=20,   # Reduce from 40
```

---

## Production Checklist

Before deploying to production:

- [ ] Set `BOT_TOKEN` environment variable
- [ ] Set `DATABASE_URL` to PostgreSQL
- [ ] Set `ALLOWED_USERS` for access control
- [ ] Set `SENTRY_DSN` for error tracking
- [ ] Set `LOG_FORMAT=json` for structured logs
- [ ] Run `alembic upgrade head` to apply migrations
- [ ] Configure automated backups
- [ ] Set up health check monitoring
- [ ] Test `/health` endpoint
- [ ] Verify bot responds to `/start`
- [ ] Check logs for errors

---

## Architecture Overview

```
┌─────────────────┐
│  Telegram API   │
└────────┬────────┘
         │
         v
┌─────────────────┐      ┌──────────────┐
│   Tender Bot    │─────>│  PostgreSQL  │
│   (aiogram)     │      │   Database   │
└────────┬────────┘      └──────────────┘
         │
         ├─────────> Health Check (:8080)
         ├─────────> Sentry Monitoring
         └─────────> Structured Logs (JSON)
```

---

## Support

For issues or questions:

- Check logs first: `railway logs` or `docker logs`
- Review configuration in `.env`
- Test health endpoint: `curl http://localhost:8080/health`

---

**Production Ready ✅**

Generated: 2024-11-24

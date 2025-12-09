#!/bin/bash
set -e

echo "=========================================="
echo "🚀 DOCKER ENTRYPOINT: Starting Tender AI Bot"
echo "=========================================="

# Проверяем DATABASE_URL
if [ -z "$DATABASE_URL" ]; then
    echo "❌ ERROR: DATABASE_URL not set!"
    exit 1
fi

echo "✅ DATABASE_URL is set"

# Запускаем миграции Alembic
echo "=========================================="
echo "🔄 Running Alembic migrations..."
echo "=========================================="

alembic upgrade head

MIGRATION_STATUS=$?
if [ $MIGRATION_STATUS -eq 0 ]; then
    echo "✅ Migrations completed successfully"
else
    echo "❌ Migrations failed with status $MIGRATION_STATUS"
    exit 1
fi

# Запускаем приложение
echo "=========================================="
echo "🤖 Starting bot application..."
echo "=========================================="

exec python -m bot.main

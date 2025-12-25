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

# Устанавливаем флаг, что админ-панель обрабатывает health check
export ADMIN_PANEL_ENABLED=1

# Запускаем Admin Panel в фоне (для webhook и управления)
echo "=========================================="
echo "🌐 Starting Admin Panel on port 8080..."
echo "=========================================="

python -m uvicorn tender_sniper.admin.app:app --host 0.0.0.0 --port 8080 &
ADMIN_PID=$!
echo "✅ Admin Panel started (PID: $ADMIN_PID)"

# Даём время админке запуститься
sleep 2

# Запускаем бота
echo "=========================================="
echo "🤖 Starting bot application..."
echo "=========================================="

python -m bot.main &
BOT_PID=$!
echo "✅ Bot started (PID: $BOT_PID)"

# Ждём оба процесса
wait $ADMIN_PID $BOT_PID

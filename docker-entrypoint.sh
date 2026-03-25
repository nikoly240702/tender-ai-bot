#!/bin/bash
# NO set -e — we handle errors manually

echo "=========================================="
echo "🚀 DOCKER ENTRYPOINT: Starting Tender AI Bot"
echo "=========================================="
echo "Python version: $(python --version 2>&1)"
echo "Working dir: $(pwd)"

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

alembic upgrade head || echo "⚠️ Alembic migration failed (non-fatal, tables may already exist)"

echo "✅ Migration step completed"

# Устанавливаем флаг, что админ-панель обрабатывает health check
export ADMIN_PANEL_ENABLED=1

# Запускаем Admin Panel в фоне (для webhook и управления)
echo "=========================================="
echo "🌐 Starting Admin Panel on port 8080..."
echo "=========================================="

echo "Testing imports..."
python -c "from tender_sniper.admin.app import app; print('✅ Admin app import OK')" || echo "❌ Admin app import FAILED"
python -c "from bot.main import main; print('✅ Bot main import OK')" || echo "❌ Bot main import FAILED"

python -m uvicorn tender_sniper.admin.app:app --host 0.0.0.0 --port 8080 &
ADMIN_PID=$!
echo "✅ Admin Panel started (PID: $ADMIN_PID)"

# Даём время админке запуститься
sleep 3

# Проверяем что админка жива
curl -sf http://localhost:8080/health && echo " ✅ Admin health OK" || echo " ⚠️ Admin health not responding yet"

# Запускаем бота
echo "=========================================="
echo "🤖 Starting bot application..."
echo "=========================================="

python -m bot.main &
BOT_PID=$!
echo "✅ Bot started (PID: $BOT_PID)"

# Ждём оба процесса
wait $ADMIN_PID $BOT_PID

#!/bin/bash
set -ex  # Exit on error, print commands

echo "========================================"
echo "🚀 Starting Tender AI Bot..."
echo "========================================"
echo "Working directory: $(pwd)"
echo "Files in current dir: $(ls -la)"

# Проверяем DATABASE_URL
if [ -z "$DATABASE_URL" ]; then
    echo "❌ ERROR: DATABASE_URL is not set!"
    exit 1
fi

echo "📊 DATABASE_URL is set: ${DATABASE_URL:0:50}..."

# Проверяем наличие alembic
which alembic || echo "⚠️ alembic not found in PATH"
echo "Alembic version: $(alembic --version 2>&1 || echo 'not available')"

# Запускаем миграции Alembic
echo "========================================"
echo "🔄 Running database migrations..."
echo "========================================"
alembic upgrade head

if [ $? -eq 0 ]; then
    echo "✅ Migrations completed successfully"
else
    echo "❌ ERROR: Migrations failed!"
    exit 1
fi

# Запускаем бота
echo "========================================"
echo "🤖 Starting bot..."
echo "========================================"
python -m bot.main

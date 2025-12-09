#!/bin/bash
set -e

echo "🚀 Starting Tender AI Bot..."

# Проверяем DATABASE_URL
if [ -z "$DATABASE_URL" ]; then
    echo "❌ ERROR: DATABASE_URL is not set!"
    exit 1
fi

echo "📊 DATABASE_URL is set"

# Запускаем миграции Alembic
echo "🔄 Running database migrations..."
alembic upgrade head

if [ $? -eq 0 ]; then
    echo "✅ Migrations completed successfully"
else
    echo "❌ ERROR: Migrations failed!"
    exit 1
fi

# Запускаем бота
echo "🤖 Starting bot..."
python -m bot.main

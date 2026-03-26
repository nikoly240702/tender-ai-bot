#!/bin/bash

echo "=== ENTRYPOINT START ==="
echo "Python: $(python --version 2>&1)"

if [ -z "$DATABASE_URL" ]; then
    echo "ERROR: DATABASE_URL not set"
    exit 1
fi
echo "DATABASE_URL is set"

# Alembic migrations (non-fatal)
alembic upgrade head 2>&1 || true
echo "Alembic done"

# Start admin panel (port 8080 for healthcheck)
python -m uvicorn tender_sniper.admin.app:app --host 0.0.0.0 --port 8080 &
ADMIN_PID=$!
echo "Admin started PID=$ADMIN_PID"

sleep 3

# Start bot
python -m bot.main &
BOT_PID=$!
echo "Bot started PID=$BOT_PID"

wait $ADMIN_PID $BOT_PID

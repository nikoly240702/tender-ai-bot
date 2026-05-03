#!/bin/bash
set -ex

echo "=== ENTRYPOINT START ==="
echo "Python: $(python --version 2>&1)"
echo "Pip list (top):"
pip list 2>&1 | head -30

if [ -z "$DATABASE_URL" ]; then
    echo "ERROR: DATABASE_URL not set"
    exit 1
fi
echo "DATABASE_URL is set"

# Alembic migrations run inside bot.main via run_migrations()

# Admin panel — слушает 127.0.0.1:8081 (только loopback, не публично).
# Снаружи доступна через aiohttp reverse-proxy на /admin/* (см. bot/health_check.py).
# --root-path /admin: FastAPI генерирует ссылки с этим префиксом, поэтому
# admin-шаблоны корректно работают за прокси.
python -u -m uvicorn tender_sniper.admin.app:app \
    --host 127.0.0.1 --port 8081 \
    --root-path /admin \
    --log-level info 2>&1 &
ADMIN_PID=$!
echo "Admin started PID=$ADMIN_PID on 127.0.0.1:8081 (proxied via /admin)"

sleep 3

# Check if admin is still alive
if ! kill -0 $ADMIN_PID 2>/dev/null; then
    echo "FATAL: admin (uvicorn) died before healthcheck; see logs above"
fi

# Bot слушает публичный 8080 — aiohttp кабинет + healthcheck + reverse-proxy /admin.
# ADMIN_PANEL_ENABLED не выставляем — bot.main должен запустить свой health_check_server.
python -u -m bot.main 2>&1 &
BOT_PID=$!
echo "Bot started PID=$BOT_PID on public 8080"

wait $ADMIN_PID $BOT_PID

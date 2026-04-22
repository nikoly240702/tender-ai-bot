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

# Start admin panel (port 8080 for healthcheck + admin UI).
# Redirect stderr to stdout so Python tracebacks land in Railway logs.
python -u -m uvicorn tender_sniper.admin.app:app --host 0.0.0.0 --port 8080 --log-level info 2>&1 &
ADMIN_PID=$!
echo "Admin started PID=$ADMIN_PID"

sleep 3

# Check if admin is still alive
if ! kill -0 $ADMIN_PID 2>/dev/null; then
    echo "FATAL: admin (uvicorn) died before healthcheck; see logs above"
fi

# Start bot (ADMIN_PANEL_ENABLED=1 tells bot.main to skip its own health server on 8080)
ADMIN_PANEL_ENABLED=1 python -u -m bot.main 2>&1 &
BOT_PID=$!
echo "Bot started PID=$BOT_PID"

wait $ADMIN_PID $BOT_PID

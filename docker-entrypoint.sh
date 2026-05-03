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

# Bot слушает публичный 8080 — aiohttp кабинет + healthcheck + reverse-proxy /admin.
# Admin uvicorn запускается изнутри bot/main.py как async subprocess
# (так его логи попадают в общий поток).
python -u -m bot.main 2>&1

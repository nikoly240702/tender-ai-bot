#!/bin/bash
set -e

echo "=== ENTRYPOINT START ==="
echo "Python: $(python --version 2>&1)"

if [ -z "$DATABASE_URL" ]; then
    echo "ERROR: DATABASE_URL not set"
    exit 1
fi

# 1. Запускаем uvicorn admin в фоне (loopback 127.0.0.1:8081).
#    Снаружи проксируется через aiohttp /admin/* (bot/health_check.py).
#    --root-path /admin: FastAPI генерирует ссылки с префиксом, шаблоны
#    работают за прокси корректно.
mkdir -p /tmp
echo "[entrypoint] Starting admin uvicorn on 127.0.0.1:8081..."
python -u -m uvicorn tender_sniper.admin.app:app \
    --host 127.0.0.1 --port 8081 \
    --root-path /admin \
    --log-level info \
    > /tmp/admin.log 2>&1 &
ADMIN_PID=$!
echo "[entrypoint] Admin PID=$ADMIN_PID"

# Стримим логи admin в stdout (попадут в Railway logs).
tail -F /tmp/admin.log &
TAIL_PID=$!

# Даём admin 5с чтобы поднялся
sleep 5
if kill -0 $ADMIN_PID 2>/dev/null; then
    echo "[entrypoint] Admin uvicorn alive after startup"
else
    echo "[entrypoint] FATAL: Admin uvicorn DIED. Logs above 👆"
fi

# 2. Запускаем bot в foreground — его падение завершает контейнер.
#    Bot слушает 0.0.0.0:8080 — публичный (Railway healthcheck + кабинет
#    + admin reverse-proxy).
echo "[entrypoint] Starting bot on 0.0.0.0:8080..."
exec python -u -m bot.main 2>&1

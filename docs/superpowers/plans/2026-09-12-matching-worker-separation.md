# Matching Worker Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `TenderSniperService` (the tender-matching/scraping engine) out of the existing `tender-ai-bot` Railway service into its own separate service (`worker`), so its CPU-bound background work can no longer contend for the same GIL/event loop as the cabinet HTTP server — fixing the intermittent multi-second cabinet stalls confirmed in production.

**Architecture:** Two Railway services sharing one repo/Docker image and one Postgres DB, coordinated only through that DB (no new inter-service RPC). `web` (existing service) keeps the cabinet HTTP server and both Telegram/Max bot polling loops. `worker` (new service) runs only `TenderSniperService`, which is already fully self-contained (own DB connection via `tender_sniper.database.get_sniper_db()`, own lightweight `aiogram.Bot` for sending notifications, zero dependency on the aiogram `Dispatcher` — confirmed by grep, zero hits for `Dispatcher`/`dp.include_router` anywhere under `tender_sniper/`). A `SERVICE_ROLE` env var lets the single shared `docker-entrypoint.sh` pick which entry point to run.

**Tech Stack:** Python 3.11, aiogram 3.x, aiohttp, Railway (Dockerfile-based deploy, two services sharing one image).

**Spec:** `docs/superpowers/specs/2026-09-11-matching-worker-separation-design.md`

## Global Constraints

- No behavior change for end users — this is a pure process-topology change (spec: Non-goals).
- No task queue / Redis — DB is the only coordination mechanism (spec: Design).
- `web` remains the *only* place that runs `alembic upgrade head` on startup — `worker` must never run migrations (spec: Design, Risks).
- Single production database, no staging environment — verify locally/in-worktree first (both entry points importable, no circular imports), then verify against production directly after deploy using the same `curl` timing technique that originally surfaced the bug (spec: Testing).
- `worker` needs its own liveness signal so a silently-dead matching engine doesn't go unnoticed (spec: Risks).

---

### Task 1: Worker health check server

**Files:**
- Modify: `bot/health_check.py` (add one new function after `start_health_check_server`, currently ending at line 576, before `update_health_status` at line 578)

**Interfaces:**
- Produces: `start_worker_health_check_server(port: int = 8080) -> web.AppRunner`, reusing the existing `health_check_handler`, `readiness_handler`, `liveness_handler` functions (lines 116, 172, 184 today) — same handlers as `web`'s health check, just mounted alone with no cabinet/admin-proxy/webhook routes. Task 2 imports and calls this.

- [ ] **Step 1: Add the new function**

In `bot/health_check.py`, immediately after the `start_health_check_server` function's closing `return runner` (currently line 576) and before `def update_health_status` (currently line 578), insert:

```python
async def start_worker_health_check_server(port: int = 8080) -> web.AppRunner:
    """
    Health check HTTP-сервер для worker-сервиса (только матчинг тендеров,
    без кабинета/админки/вебхуков — тем достаточно /health, /ready, /live).
    """
    app = web.Application()
    app.router.add_get('/health', health_check_handler)
    app.router.add_get('/ready', readiness_handler)
    app.router.add_get('/live', liveness_handler)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

    _health_status["status"] = "healthy"

    logger.info(f"✅ Worker health check server started on port {port}")
    return runner
```

- [ ] **Step 2: Verify it imports and runs**

```bash
python3 -c "
import ast
ast.parse(open('bot/health_check.py').read())
print('syntax OK')
"
```

Then a live smoke test (starts the server, hits `/health`, tears down):

```bash
python3 -c "
import asyncio
from bot.health_check import start_worker_health_check_server
import aiohttp

async def main():
    runner = await start_worker_health_check_server(port=8091)
    async with aiohttp.ClientSession() as session:
        async with session.get('http://127.0.0.1:8091/health') as resp:
            print('status:', resp.status)
            print(await resp.json())
    await runner.cleanup()

asyncio.run(main())
"
```

Expected: `status: 200` (or `503` if DB env vars aren't set in this shell — either is fine, it proves the server itself came up and responded; a connection error would not be fine).

- [ ] **Step 3: Commit**

```bash
git add bot/health_check.py
git commit -m "feat: add minimal health check server for the worker service

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Worker entry point

**Files:**
- Create: `tender_sniper/worker_main.py`

**Interfaces:**
- Consumes: `tender_sniper.service.TenderSniperService`, `tender_sniper.config.is_tender_sniper_enabled`, `bot.health_check.start_worker_health_check_server` (Task 1), `bot.env_validator.EnvValidator`, `tender_sniper.monitoring.{init_sentry, init_telegram_error_alerts, flush_events, send_error_to_telegram}`, `bot.config.BotConfig`.
- Produces: a runnable module (`python -u -m tender_sniper.worker_main`) that Task 4's entrypoint script invokes.

- [ ] **Step 1: Write the module**

Create `tender_sniper/worker_main.py`:

```python
"""
Worker-сервис: только TenderSniperService (мэтчинг/скрапинг тендеров).

Вынесен из bot/main.py в отдельный Railway-сервис, чтобы CPU-тяжёлая
работа мэтчинга не конкурировала за GIL/event loop с HTTP-сервером
кабинета (см. docs/superpowers/specs/2026-09-11-matching-worker-separation-design.md).

Никогда не запускает aiogram Dispatcher/polling — TenderSniperService
уже полностью самодостаточен (свой Bot для отправки уведомлений, своё
подключение к БД), поэтому здесь нет и не должно быть роутеров/polling.
"""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.config import BotConfig
from bot.env_validator import EnvValidator
from bot.health_check import start_worker_health_check_server, update_health_status
from tender_sniper.config import is_tender_sniper_enabled
from tender_sniper.monitoring import (
    init_sentry, capture_exception, flush_events,
    init_telegram_error_alerts, send_error_to_telegram,
)
from tender_sniper.service import TenderSniperService

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


class GracefulShutdown:
    """Тот же паттерн, что в bot/main.py — не импортируем оттуда напрямую,
    чтобы worker не тянул за собой весь модуль bot.main (aiogram Dispatcher,
    все роутеры и т.д.) только ради одного вспомогательного класса."""

    def __init__(self):
        self.shutdown_timeout = 30

    async def shutdown(self, signal_type, loop):
        logger.info(f"⚠️  Получен сигнал {signal_type.name}, начинаем graceful shutdown...")
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if tasks:
            logger.info(f"⏳ Ожидаем завершения {len(tasks)} задач (макс {self.shutdown_timeout}с)...")
            done, pending = await asyncio.wait(
                tasks, timeout=self.shutdown_timeout, return_when=asyncio.ALL_COMPLETED
            )
            if pending:
                logger.warning(f"⚠️  {len(pending)} задач не успели завершиться, отменяем")
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
        logger.info("✅ Graceful shutdown завершён")
        loop.stop()


async def main():
    logger.info("🔍 Проверка переменных окружения...")
    EnvValidator.validate_and_exit_if_invalid(strict=False)

    health_check_runner = None
    health_check_port = int(os.getenv('HEALTH_CHECK_PORT', '8080'))
    logger.info(f"🏥 Запуск worker health check сервера на порту {health_check_port}...")
    health_check_runner = await start_worker_health_check_server(port=health_check_port)

    sentry_enabled = init_sentry(
        environment="production",
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
    )
    if sentry_enabled:
        logger.info("✅ Sentry мониторинг активирован")
        update_health_status("sentry", "ok")
    else:
        logger.info("ℹ️  Sentry мониторинг отключен (SENTRY_DSN не указан)")
        update_health_status("sentry", "disabled")

    admin_id = int(os.getenv('ADMIN_TELEGRAM_ID', '0'))
    if admin_id:
        init_telegram_error_alerts(admin_chat_id=admin_id)
        logger.info(f"✅ Telegram error alerts настроены для админа {admin_id}")

    try:
        BotConfig.validate()
        logger.info("✅ Конфигурация валидна")
    except ValueError as e:
        logger.error(f"❌ Ошибка конфигурации: {e}")
        capture_exception(e, level="fatal", tags={"component": "worker_config"})
        return

    shutdown_handler = GracefulShutdown()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig, lambda s=sig: asyncio.create_task(shutdown_handler.shutdown(s, loop))
        )
    logger.info("✅ Graceful shutdown handler зарегистрирован")

    sniper_service = None
    sniper_task = None
    if not is_tender_sniper_enabled():
        logger.warning("⚠️  Tender Sniper отключен в конфигурации — worker простаивает")
        update_health_status("sniper_service", "disabled")
    else:
        try:
            logger.info("🎯 Инициализация Tender Sniper Service...")
            sniper_service = TenderSniperService(
                bot_token=BotConfig.BOT_TOKEN,
                poll_interval=120,
                max_tenders_per_poll=100,
            )
            await sniper_service.initialize()

            async def run_sniper():
                try:
                    await sniper_service.start()
                except Exception as e:
                    logger.error(f"❌ Ошибка Tender Sniper: {e}", exc_info=True)

            sniper_task = asyncio.create_task(run_sniper())
            logger.info("✅ Tender Sniper Service запущен")
            update_health_status("sniper_service", "ok")
        except Exception as e:
            logger.error(f"❌ Не удалось запустить Tender Sniper: {e}", exc_info=True)
            update_health_status("sniper_service", f"error: {e}")
            capture_exception(e, level="fatal", tags={"component": "worker_main"})
            await send_error_to_telegram(e, context="Запуск worker (tender_sniper.worker_main)")

    try:
        if sniper_task:
            await sniper_task
        else:
            # Ничего не запущено (feature flag выключен) — держим процесс
            # живым, чтобы health check продолжал отвечать, а не крашился
            # в рестарт-луп.
            await asyncio.Event().wait()
    finally:
        if sniper_service:
            logger.info("🛑 Остановка Tender Sniper Service...")
            await sniper_service.stop()
        if health_check_runner:
            logger.info("🛑 Остановка health check сервера...")
            await health_check_runner.cleanup()
        flush_events(timeout=2)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Worker остановлен пользователем")
```

- [ ] **Step 2: Verify it imports cleanly**

```bash
python3 -c "
import ast
ast.parse(open('tender_sniper/worker_main.py').read())
print('syntax OK')
"
python3 -c "
import sys
sys.path.insert(0, '.')
import tender_sniper.worker_main
print('module import OK')
"
```

Expected: both print their `OK` line with no traceback. (The second command only checks import-time — it does not run `main()`, since that needs a real `DATABASE_URL`/`BOT_TOKEN` and would try to bind a port.)

- [ ] **Step 3: Commit**

```bash
git add tender_sniper/worker_main.py
git commit -m "feat: add worker_main entry point for the standalone matching service

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Remove the matching service from `bot/main.py`

**Files:**
- Modify: `bot/main.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `bot/main.py` with `TenderSniperService` fully removed — `web`'s `main()` no longer starts, monitors, or stops it. Task 5 (deployment) relies on `web` no longer doing any matching work at all, so this task's correctness is load-bearing for the whole plan's goal.

- [ ] **Step 1: Remove the two now-unused imports**

Remove these two lines (currently lines 45-47):

```python
# Импортируем Tender Sniper Service
from tender_sniper.service import TenderSniperService
from tender_sniper.config import is_tender_sniper_enabled
```

Confirm nothing else in `bot/main.py` still references `TenderSniperService` or `is_tender_sniper_enabled` after Step 2 below (a plain text search for those two names in the file should return zero hits once Step 2 is done).

- [ ] **Step 2: Remove the service startup block**

Remove this entire block (currently lines 560-588, "Инициализируем Tender Sniper Service (если включен)" through the closing `else` branch):

```python
    # Инициализируем Tender Sniper Service (если включен)
    sniper_service = None
    sniper_task = None
    if is_tender_sniper_enabled():
        try:
            logger.info("🎯 Инициализация Tender Sniper Service...")
            sniper_service = TenderSniperService(
                bot_token=BotConfig.BOT_TOKEN,
                poll_interval=120,  # 2 минуты
                max_tenders_per_poll=100
            )
            await sniper_service.initialize()

            # Запускаем мониторинг в фоновом режиме
            async def run_sniper():
                try:
                    await sniper_service.start()
                except Exception as e:
                    logger.error(f"❌ Ошибка Tender Sniper: {e}", exc_info=True)

            sniper_task = asyncio.create_task(run_sniper())
            logger.info("✅ Tender Sniper Service запущен в фоновом режиме")
            update_health_status("sniper_service", "ok")
        except Exception as e:
            logger.error(f"❌ Не удалось запустить Tender Sniper: {e}", exc_info=True)
            update_health_status("sniper_service", f"error: {e}")
    else:
        logger.info("ℹ️  Tender Sniper отключен в конфигурации")
        update_health_status("sniper_service", "disabled")
```

- [ ] **Step 3: Remove the shutdown block**

In the `finally:` block, remove this section (currently lines 649-658, "Останавливаем Tender Sniper если запущен"):

```python
        # Останавливаем Tender Sniper если запущен
        if sniper_service:
            logger.info("🛑 Остановка Tender Sniper Service...")
            await sniper_service.stop()
        if sniper_task and not sniper_task.done():
            sniper_task.cancel()
            try:
                await sniper_task
            except asyncio.CancelledError:
                pass
```

- [ ] **Step 4: Verify**

```bash
grep -n "TenderSniperService\|is_tender_sniper_enabled\|sniper_service\|sniper_task" bot/main.py
```

Expected: no output (zero matches) — confirms every reference was removed, not just the import.

```bash
python3 -c "
import ast
ast.parse(open('bot/main.py').read())
print('syntax OK')
"
python3 -c "
import sys
sys.path.insert(0, '.')
import bot.main
print('module import OK')
"
```

Expected: both print their `OK` line. (This only checks import-time side effects — `bot.main`'s module-level code is just imports and logging config, so this is a real, meaningful check, not a no-op.)

- [ ] **Step 5: Commit**

```bash
git add bot/main.py
git commit -m "refactor: remove TenderSniperService from the web service

Matching now runs exclusively in the new worker service
(tender_sniper/worker_main.py) — the web service (cabinet + bot
polling) no longer starts, monitors, or stops it.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Entry point branching in the shared Docker image

**Files:**
- Modify: `docker-entrypoint.sh`

**Interfaces:**
- Consumes: a new `SERVICE_ROLE` env var (`web` default, or `worker`).
- Produces: one shared image where `web`'s Railway service (unchanged env, defaults to `web`) and the new `worker` Railway service (Task 5 sets `SERVICE_ROLE=worker` on it) boot into the right entry point.

- [ ] **Step 1: Rewrite the file**

Replace the full contents of `docker-entrypoint.sh` with:

```bash
#!/bin/bash
set -e

echo "=== ENTRYPOINT START ==="
echo "Python: $(python --version 2>&1)"
echo "SERVICE_ROLE: ${SERVICE_ROLE:-web}"

if [ -z "$DATABASE_URL" ]; then
    echo "ERROR: DATABASE_URL not set"
    exit 1
fi

if [ "${SERVICE_ROLE:-web}" = "worker" ]; then
    echo "[entrypoint] Starting worker (tender matching) on 0.0.0.0:8080..."
    exec python -u -m tender_sniper.worker_main 2>&1
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
```

(The only changes from the current file: the new `SERVICE_ROLE` echo line near the top, and the new `if` block right after the `DATABASE_URL` check that `exec`s the worker entry point and returns before touching the admin-uvicorn/bot.main logic at all when `SERVICE_ROLE=worker`. Everything else — including every comment — is byte-for-byte the existing file.)

- [ ] **Step 2: Verify**

```bash
bash -n docker-entrypoint.sh
```

Expected: no output, exit code 0 (this is bash's syntax-check-only mode — confirms the script parses without actually running it, since running it needs a real container environment).

```bash
diff <(git show HEAD:docker-entrypoint.sh) docker-entrypoint.sh
```

Expected: a diff showing only the `SERVICE_ROLE` echo line and the new worker `if` block as additions — nothing else changed.

- [ ] **Step 3: Commit**

```bash
git add docker-entrypoint.sh
git commit -m "feat: branch docker-entrypoint.sh on SERVICE_ROLE for the worker service

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Deploy the worker service and verify the fix

**Files:** none (Railway service configuration + production verification only — no further code changes).

**Interfaces:**
- Consumes: Tasks 1-4's code (must be on `main` before this task, per the constraint that both services share one image/commit).

This task is deployment/verification, not code — steps are commands to run and things to check, not a diff.

- [ ] **Step 1: Merge and deploy Tasks 1-4 to `main` first**

Tasks 1-4 must already be pushed to `main` (triggering `web`'s normal redeploy) before starting this task — `worker` is a second service built from the same commit, so it needs that commit to exist on `main` first. Confirm:

```bash
railway deployment list
```

Expected: the latest deployment (from Task 4's push) shows `SUCCESS`, and `web` is healthy — before creating `worker`, confirm nothing broke: `TenderSniperService` should no longer be running inside `web` at all (it doesn't exist there anymore), so tender matching will be silently paused between this point and Step 3 below. This gap is expected and acceptable — do Steps 2-4 promptly to keep it short.

- [ ] **Step 2: Create the `worker` Railway service**

```bash
railway add --service worker --repo <the same GitHub repo web is linked to>
```

Then, in the Railway dashboard (or via `railway variables --service worker --set KEY=VALUE` for each), configure the new `worker` service:
- Copy every environment variable `web` has (`DATABASE_URL`, `BOT_TOKEN`, `PROXY_URL` through `PROXY_URL_5`, `SENTRY_DSN`, `ADMIN_TELEGRAM_ID`, `ANTHROPIC_API_KEY`, and any other var `web` currently has set — check `railway variables --service web` for the authoritative list to copy from, since this list can drift from what's in `.env` locally).
- Add one new variable specific to `worker`: `SERVICE_ROLE=worker`.
- Confirm `worker`'s build settings point at the same Dockerfile (`railway.toml`'s `[build]` section applies repo-wide by default — verify `worker` picked it up, or set it explicitly to match `web`'s).
- Set `worker`'s healthcheck path to `/health` (matching `web`'s `railway.toml` `healthcheckPath`) via the dashboard, since `railway.toml`/`railway.json`'s `[deploy]` section is currently written for a single implicit service — if it doesn't apply automatically to the second service, configure it explicitly in the dashboard for `worker`.

- [ ] **Step 3: Verify `worker` boots cleanly**

```bash
railway logs --service worker -n 100
```

Expected: `SERVICE_ROLE: worker`, `[entrypoint] Starting worker (tender matching) on 0.0.0.0:8080...`, then the same `🚀 ИНИЦИАЛИЗАЦИЯ TENDER SNIPER SERVICE` / `✅ ВСЕ КОМПОНЕНТЫ ИНИЦИАЛИЗИРОВАНЫ` sequence previously seen inside `web`'s logs — now appearing in `worker`'s logs instead. No `TelegramConflictError` (worker never polls).

```bash
curl -s -o /dev/null -w "HTTP %{http_code}\n" https://<worker's public URL>/health
```

Expected: `HTTP 200`.

- [ ] **Step 4: Verify `web` no longer runs the matching engine**

```bash
railway logs --service web -n 200 | grep -c "ИНИЦИАЛИЗАЦИЯ TENDER SNIPER SERVICE\|MATCH! Score"
```

Expected: `0` — this log line should no longer appear in `web`'s logs at all after its post-Task-4 redeploy, since the code that produced it no longer exists there.

- [ ] **Step 5: Confirm the original bug is fixed**

Repeat the exact diagnostic that originally found this bug — several back-to-back timed requests against a real authenticated cabinet endpoint:

```bash
for i in 1 2 3 4 5 6 7 8; do
  curl -s -o /dev/null -w "attempt $i: HTTP %{http_code} | total: %{time_total}s\n" \
    -H "Cookie: cabinet_session=<a valid session token>" \
    --max-time 25 "https://cabinet.tendersniper.ru/cabinet/api/filters?active_only=false"
done
```

Expected: all 8 attempts complete in well under a second each, consistently — no more of the 0.4s-to-25s variance seen before this plan (that variance was the entire reason this plan exists; if it's still there, `worker` and `web` are not actually decoupled and something in Tasks 1-4 needs revisiting before calling this done).

- [ ] **Step 6: Confirm notifications still flow**

```bash
railway logs --service worker -n 200 | grep -c "MATCH! Score"
```

Expected: nonzero (matching is actively happening in `worker`) — and check `sniper_notifications.sent_at` for rows newer than `worker`'s deploy time, e.g.:

```sql
SELECT count(*) FROM sniper_notifications WHERE sent_at > now() - interval '10 minutes';
```

(Run this once ЕИС/zakupki.gov.ru is reachable again, if it's still down at deploy time — a zero count while ЕИС is down is not evidence of a problem; re-check once it's back.)

---

## Self-Review Notes

- **Spec coverage:** every Design-section item has a task — worker health check (Task 1), worker entry point (Task 2), removing the engine from `web` (Task 3), shared-image branching (Task 4), and the two-service deploy + the exact verification technique the spec names (Task 5). The Risks section's two items (enumerate every `main()` side effect before splitting; worker needs its own liveness signal) are directly addressed: Task 2's entry point was built by enumerating every top-level call in `bot/main.py`'s `main()` and deciding explicitly what worker needs (env validation, health check, Sentry, Telegram error alerts, config validation, graceful shutdown) versus what it doesn't (health_check's cabinet mount, aiogram Bot/Dispatcher/middlewares/routers, VK Max bot, SubscriptionChecker, EngagementScheduler, data cleanup, the one-shot admin scripts, the pipeline archive/bitrix-pull background loops — all of these are cabinet/bot-user-facing concerns unrelated to matching, and stay in `web` unchanged); Task 1 is the liveness signal.
- **Type/name consistency checked:** `start_worker_health_check_server` (Task 1) is the exact name imported in Task 2. `SERVICE_ROLE` (Task 4) is the exact env var name `worker_main.py` implicitly relies on being absent-or-"worker" only at the shell level (the Python module itself doesn't read `SERVICE_ROLE` — only the entrypoint script branches on it, which is correct: the module doesn't need to know which service it's in, it only ever does one thing).
- **No placeholders:** every task has literal, complete code except Task 5, which is inherently a deployment/ops task (Railway service creation, env var copying, dashboard configuration) — its steps are concrete commands and concrete expected output, not vague instructions, which is the correct level of concreteness for a task that isn't a code diff.

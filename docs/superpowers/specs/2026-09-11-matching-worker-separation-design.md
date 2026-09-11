# Separate the tender-matching engine into its own Railway service

## Context

The entire app — cabinet HTTP server, Telegram bot polling, VK Max bot
polling, and the tender-matching/scraping engine (`TenderSniperService`) —
runs today as one Python process, one asyncio event loop, one Railway
service (`tender-ai-bot`, `bot/main.py`). The matching engine's CPU-bound
work (HTML parsing, keyword/regex scoring in `tender_sniper/matching/`)
already runs in background threads via `run_in_executor`, correctly
avoiding directly blocking the event loop — but Python's GIL still
serializes CPU time between those threads and the thread serving HTTP
requests, so the cabinet intermittently stalls for seconds at a time
whenever the matching engine is mid-cycle.

This was confirmed directly against production (2026-09-11): identical
`GET /cabinet/api/filters` requests, back to back, ranged from 0.4s to 25s
with zero DB locks, zero server errors — pure scheduling contention. It got
sharply worse after a second company workspace temporarily doubled total
active filters being matched per tender (30 → 59); cutting back to 29
(consolidating notification routing instead of keeping two duplicate filter
sets — see project memory) restored normal responsiveness, but that's a
capacity ceiling, not a fix — the same problem returns and worsens as more
people/filters are added, because the root cause (one process, one GIL,
shared between HTTP serving and CPU-bound background work) doesn't go away.

Full comparison of approaches, and why this one was chosen over
`multiprocessing`-in-one-container or a Celery/Arq+Redis task queue, is in
project memory (`architecture_matching_separation.md`) — not repeated here.

## Goal

Move `TenderSniperService` (the matching/scraping engine) to its own
Railway service, so its CPU-bound work can no longer contend for the same
GIL/event loop as the cabinet HTTP server. No behavior change for end
users other than a more consistently responsive cabinet under load.

## Non-goals

- No task queue / Redis / horizontal worker scaling (see rejected
  alternative C in project memory — revisit only if this proves
  insufient at a much larger scale).
- No change to matching logic, scraping logic, or notification content —
  purely a process-topology change.
- No change to the Telegram/Max bot's own command handling (wizard,
  filters, `/bitrix24`, the new `take_work` button, etc.) — this stays in
  the existing "web" service.

## Design

**Two Railway services, one repo, one Postgres DB (already provisioned):**

- **`web`** (the existing `tender-ai-bot` service, unchanged in spirit):
  aiohttp cabinet HTTP server (`bot/health_check.py`), Telegram bot
  polling (`dp.start_polling(bot)`, `bot/main.py:618`), VK Max bot
  polling. Stays I/O-bound, low CPU. Keeps running
  `alembic upgrade head` on startup (`bot/main.py:132`) — this remains
  the *only* place migrations run, so a simultaneous first deploy of both
  services can't race on schema changes.
- **`worker`** (new service, same repo/image): only
  `TenderSniperService` (`bot/main.py:566-581` today) — the realtime
  parser, instant search, smart matcher, AI relevance checking. No
  aiohttp server, no `dp.start_polling`. Does **not** run migrations —
  assumes `web` already has, consistent with Railway's typical
  same-commit rolling deploy of both services.

**Coordination is DB-only.** `worker` writes to `sniper_notifications`
and (via the existing pipeline/kanban code paths) `pipeline_cards`
exactly as today; `web` reads/writes those same tables for the cabinet
and bot commands. No new inter-service RPC, no shared in-memory state —
this was already true structurally (the two subsystems only ever talked
through the DB), so splitting them doesn't introduce a new integration
surface, just moves an existing one across a process boundary.

**Sending Telegram notifications from `worker`.** Today, notification
delivery happens inside the same process that's already polling for
bot updates, using the same `Bot` instance. In the split, `worker` needs
*only* the ability to send messages (never poll for updates — polling
must stay singular in `web`, since this repo already guards against
`TelegramConflictError` from more than one polling instance running at
once). Concretely: `worker` constructs its own lightweight
`aiogram.Bot` instance from `BOT_TOKEN` purely for `bot.send_message(...)`
calls, and never calls `dp.start_polling`.

**Deployment mechanics** (exact shape decided during planning, not
finalized here): both services can share the same Dockerfile/image;
Railway lets a service override the container's start command, so
`worker` doesn't need its own Dockerfile — just a different entry point
module (e.g. `python -u -m tender_sniper.worker_main`) extracted from the
relevant slice of `bot/main.py`'s `async def main()` (lines ~560-581,
~651-652 today) instead of the full startup sequence.

## Testing

Same posture as the multi-workspace work: no staging environment, single
production DB. Verify in a worktree first (both entry points importable,
no circular-import regressions from splitting `bot/main.py`), then a
careful, verified production rollout — direct `curl` timing against
production (the technique that originally surfaced this bug) is the way
to confirm the fix actually landed: repeated `/cabinet/api/*` requests
should stay consistently fast even while `worker`'s matching cycle is
running, with no more of the 0.4s-to-25s variance seen before the split.

## Risks

- Splitting `bot/main.py`'s single `async def main()` into two entry
  points risks accidentally duplicating startup logic that should only
  run once (the migration runner being the sharpest example — see
  Design above) or dropping something that both need (env validation,
  Sentry init, logging setup) — enumerate every side effect of the
  current single `main()` before splitting, not just the two obviously
  separable subsystems.
- Two services means two things that can independently fail to boot;
  Railway's health checks currently target `web`'s `/health` — `worker`
  needs its own liveness signal (even a trivial one) so a silently-dead
  matching engine doesn't go unnoticed the way a dead HTTP server
  wouldn't.

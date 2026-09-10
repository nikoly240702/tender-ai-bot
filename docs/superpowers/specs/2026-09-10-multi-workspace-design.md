# Multi-workspace: company-scoped filters + second (isolated) workspace

## Context

Two goals, one root cause:

1. Finish the team cabinet so several people can actually work in it (currently
   only the account owner can see/manage filters and the notification feed).
2. Stand up a second, fully isolated workspace with its own filters, its own
   people, its own notification channel, and no visibility for the current
   partner — to run a separate tender business without disclosure.

Root cause: `SniperFilter` and `SniperNotification` are scoped to `user_id`
(one personal Telegram/Max account), not to `Company`. `PipelineCard` (the
kanban) is already `company_id`-scoped and Bitrix sync already resolves its
webhook per-company (`cabinet/bitrix_sync.py::_get_company_webhook`), so the
kanban/Bitrix layer is already multi-tenant. The gap is one layer up: filters
and the raw notification feed that feeds the kanban.

## Goals

- Any member of a company can create/edit/view filters and the notification
  feed for that company, not just the owner's personal account.
- A second `Company` can be created with its own filter set (seeded as a copy
  of today's filters, then diverged independently) and its own group-chat
  notification target, with zero data path into the existing Bitrix account
  or the existing team's view.
- The account owner can be a member of both companies and switch between them
  in the cabinet.
- Zero behavior change for every existing single-company user (the partner
  included) — no switcher UI, no new fields visible, nothing to notice.

## Non-goals

- Granular per-role permissions beyond the existing owner/member split (backlog).
- Changing how the Telegram/Max **bot** commands list filters — those stay
  personal to the account they're sent from; only the **cabinet** becomes
  company-scoped.
- Any of the parked feature ideas (RFQ auto-request, buyer/bidder kanban
  split, etc. — see project memory `backlog_features_2026_09.md`).

## Data model changes

**`sniper_filters`**: add `company_id` (FK → `companies.id`, indexed,
nullable during migration then backfilled NOT NULL). Keep `user_id` as-is —
it becomes "creator", used only as the DM fallback when `notify_chat_ids`
is empty.

**`sniper_notifications`**: add `company_id` (FK → `companies.id`, indexed),
stamped from `filter.company_id` at creation time. `user_id` stays as the
creator/dedup key — the existing `UniqueConstraint('user_id', 'tender_number')`
is untouched since a filter still has exactly one creator.

**`company_members`**: drop `UniqueConstraint('user_id', name='uq_company_members_user')`,
replace with `UniqueConstraint('user_id', 'company_id')`. This is the change
that lets the account owner belong to both companies. Every other place that
currently assumes "one company per user" needs to move to an explicit
active-company resolution (next section) rather than "the" company.

**`web_sessions`**: add nullable `active_company_id` (FK → `companies.id`).
Holds which company a multi-company user is currently viewing; unused/ignored
for single-company users.

## Cabinet RBAC changes

Replace `team_service.get_company_for_user(user_id)` call sites in
`cabinet/auth.py::require_team_member` with a new
`get_active_company(user_id, session_token)`:

- Look up all `CompanyMember` rows for the user.
- 0 companies → today's behavior (403 on API, auto-create on page load).
- 1 company → return it. **No behavior change for single-company users.**
- >1 companies → read `web_sessions.active_company_id`; if it's set and still
  a valid membership, use it; otherwise default to the oldest membership
  (i.e. company A) and persist that as the default.

New endpoint `POST /api/company/switch` (`require_team_member`, no
`require_owner`): body `{company_id}`, validates the caller is a member of
that company, writes `web_sessions.active_company_id`, returns the new
company. Cabinet header shows a workspace switcher **only when
`len(memberships) > 1`** — so it's invisible to everyone except an account
that's deliberately been added to two companies.

All filter-related routes/queries in `cabinet/api.py` switch from
`SniperFilter.user_id == user['user_id']` to
`SniperFilter.company_id == company['id']` (company already on `request`
via `require_team_member`). Same for the notification-feed query. Filter
create/edit endpoints stamp `company_id = company['id']`, `user_id = creator`.

## Matching engine

No structural change. `service.py`'s matching loop iterates `SniperFilter`
rows same as today; the one edit is at the `SniperNotification(...)`
construction site(s) — copy `company_id` from the matched filter onto the
new notification row. (Locate via the existing `SniperNotification(` call
sites in `service.py` when implementing — not enumerated here.)

## Bitrix / kanban

No change needed — already company-scoped and already a safe no-op when a
company has no webhook configured (`bitrix_sync.py` docstring: "ошибки
Bitrix не должны валить работу с карточкой").

## Second workspace bootstrap (Company B)

1. Create `Company` B (owner_user_id = the account owner's `SniperUser.id` —
   the same account, now also a member of A).
2. Add a `CompanyMember` row for the owner in B (role `owner`).
3. Seed script (new, `scripts/`, following the existing pattern used for all
   prior filter seeders: idempotent by name, `--dry` flag) copies today's
   active filters into `company_id = B`, `notify_chat_ids = [<new group
   chat id>]`. Source filters (company A) are untouched.
4. No Bitrix webhook is written to `Company.data` for B → sync no-ops.
5. Generate a `TeamInvite` for B; new people log in with their own
   Telegram/Max accounts and redeem it → `CompanyMember(company_id=B,
   role='member')`. They only ever see B — `get_active_company` returns B
   for them since they have exactly one membership.
6. From here on, filters for B are edited directly in the cabinet — no more
   manual seed scripts needed for day-to-day changes.

## Testing

- Unit tests for `get_active_company` (0/1/>1 membership cases, invalid
  `active_company_id` fallback).
- Unit tests for filter CRUD/feed endpoints scoped by `company_id` instead
  of `user_id`.
- Migration: run `alembic upgrade head` against a local Postgres copy of
  production data before touching prod (single Railway environment, no
  staging) — verify backfill assigns every existing filter/notification to
  the correct (existing) company and the row counts match pre-migration.
- Manual smoke test in cabinet: existing single-company account sees no
  switcher and no behavior change; owner account (added to both) sees a
  switcher and correct per-company filter/feed isolation.

## Risks

- `CompanyMember` unique-constraint change happens on the one production DB
  with no staging environment — take a DB snapshot immediately before
  running the migration.
- Backfill must correctly map every current filter/notification to the
  right existing company (should be a single company today, but verify row
  count before/after).

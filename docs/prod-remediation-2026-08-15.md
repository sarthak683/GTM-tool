# Production Remediation Plan — 2026-08-15 audit

Produced by the full-stack audit (prod infra, prod data, backend, Celery,
frontend). **Nothing in this file has been executed against production.**
Items are grouped by how they get fixed. SQL was verified read-only against
prod data shapes; re-verify row counts immediately before running anything.

## A. Fixed by deploying the current branch (no manual action)

Migration 113 runs automatically on deploy (initContainer) and:

- Adds the missing FKs on `call_recordings.deal_id` / `deleted_by_id`.
- Adds `uq_meetings_external_source_id` (calendar/tl;dv duplicate backstop).
- Adds `uq_tasks_open_system_key` after dismissing the one duplicate pair.
- Remaps the 500 `tasks.priority = 'normal'` rows to `'medium'`.
- Recomputes `deals.stakeholder_count` from `deal_contacts` (151 wrong rows).

Code fixes that stop ongoing damage the moment they're live: Instantly sync
TypeError (71+ failures/day), pre-meeting brief event-loop bug (~49% failure)
plus its duplicate-send race, tl;dv cursor loss + poison-record wedging, email
sync poison wedging, report resend-on-partial-failure, settings lost-update
races, Aircall 3-5x call counting, per-call dedup on every metric surface,
sequence-status regressions, orphaned-task creation on deletes, close-date
off-by-one in the UI.

Deploy also flips the worker to prefork (time limits enforceable) — and the
chart now pins postgres to its current digest and preforks the priority worker.

## B. One-time data cleanup (SELECT-verified; needs explicit approval to run)

Run in a transaction; check counts with the SELECTs first.

### B1. Orphaned tasks pointing at deleted entities (1,925 rows; 13 open)

```sql
-- verify
SELECT COUNT(*) FROM tasks t LEFT JOIN deals d ON d.id = t.entity_id
WHERE t.entity_type = 'deal' AND d.id IS NULL;
SELECT COUNT(*) FROM tasks t LEFT JOIN contacts c ON c.id = t.entity_id
WHERE t.entity_type = 'contact' AND c.id IS NULL;

-- remediate (keeps audit history: dismiss instead of delete)
UPDATE tasks t SET status = 'dismissed', updated_at = NOW()
FROM (
  SELECT t2.id FROM tasks t2
  LEFT JOIN deals d ON d.id = t2.entity_id AND t2.entity_type = 'deal'
  LEFT JOIN contacts c ON c.id = t2.entity_id AND t2.entity_type = 'contact'
  LEFT JOIN companies co ON co.id = t2.entity_id AND t2.entity_type = 'company'
  WHERE t2.status = 'open'
    AND ((t2.entity_type = 'deal' AND d.id IS NULL)
      OR (t2.entity_type = 'contact' AND c.id IS NULL)
      OR (t2.entity_type = 'company' AND co.id IS NULL))
) ghosts
WHERE t.id = ghosts.id;
```

Leave the dismissed/completed ghosts in place unless task analytics should
exclude them; if so, delete task_comments first (FK).

### B2. Stuck call recordings (25 rows >24h in non-terminal states)

The deployed reaper (`reap_stuck_call_recordings`, 2h cutoff) will fail these
automatically after deploy — **no manual SQL needed**. Verify afterwards:

```sql
SELECT status, COUNT(*) FROM call_recordings
WHERE status IN ('uploaded','transcribing','classifying')
  AND created_at < NOW() - INTERVAL '24 hours' AND deleted_at IS NULL
GROUP BY status;  -- expect 0 rows a day after deploy
```

### B3. Off-enum user roles (4 users)

Fix via the admin UI (roles drive permissions; a human should pick):
ids `85e237eb-…`, `ed22ca5f-…`, `da06e7db-…` (role=''), `c73b02b0-…`
(role='agency').

### B4. `new_stage_18` deals (3 rows)

Human decision — deal ids `f6d8ee4f-…`, `e2dede4b-…`, `b008a7ca-…`. Either
rename the stage in stage settings or move the deals to a real stage in the
UI (which also writes stage history correctly now).

### B5. Per-rep duplicate email activities (1,731 surplus rows)

**Decision needed before touching data.** The write path deliberately keys
dedup on (message, contact, deal, **created_by**) so each rep gets a copy;
timelines and some counts read them raw. Options:
1. Keep per-rep rows, dedupe at read time everywhere (dashboards already do;
   timelines/perf metrics don't) — code-only fix, no data change.
2. Collapse to one row per (message, contact, deal) keeping the earliest, and
   drop created_by from the dedup key going forward.
Recommendation: option 1 first (no data loss), revisit 2 later.

### B6. Dead Gmail connections (6 reps, oldest since May 22)

Operational: annie@, mahesh@, pulkit@, rakesh@, sarthak@, zippy@ must
re-connect Gmail in Settings → Email Sync. Email history for these reps has
gaps back to their `last_sync` dates; the backfill will fill what Gmail still
serves once reconnected.

## C. Deploy sequence (requires explicit go-ahead)

1. Push branch + build images (CI): backend + frontend tags from this commit.
2. Staging first per `.claude/skills/crm-deployment/SKILL.md`: helm diff, then
   upgrade `gtm` in ns `gtm`; verify System Health panel (new Idle states),
   run one tl;dv/gmail sync cycle, send a test pod report.
3. Prod: helm diff FIRST (expect: image tags, redis `noeviction`,
   worker/priority-worker prefork, postgres digest pin — the digest pin is a
   no-op restart since it matches the running image). Note the redis change
   rolls the broker: schedule off-hours, in-flight beat messages redeliver
   via acks_late.
4. Post-deploy checks: `alembic_version` = 113; job_health panel shows
   Idle/skip reasons; no new ERROR signatures in worker logs; call counts on
   the dashboard drop to per-call numbers (expect the "calls" tile to shrink
   ~3x for Aircall-heavy reps — communicate this to the team BEFORE deploy).

## D. Known-remaining items (documented, not yet fixed)

- **Aircall reconciliation poller** (missed webhooks are still permanently
  uncounted calls until built) — highest-value next backend feature.
- Instantly webhook idempotency ordering (counter commit before dedup row);
  counter semantics unification across webhook/poller (backend audit #3/#11).
- Campaign launch: external writes before DB commit (#12); `company_name`
  always empty in pushed leads (broken personalization) — one-line fix worth
  batching with #12.
- Stage-history two-transaction windows in deals.py update/move_stage (the
  four missing writers ARE fixed; the ordering hazard remains).
- Multi-commit endpoint flows (PUT /contacts, account-sourcing upload rows).
- Frontend: Pipeline 500-prospect cap, filterless AccountSourcing exports,
  client-side sort over one page, SalesAnalytics drilldown param drift
  (needs backend params: from/to, multi-geo, multi-rep), Companies 1000-cap,
  Meetings race guard, silent mutation failures (toasts), timeline caps.
- personal email sync memory profile (full-table loads per user per cycle).
- tl;dv transcript stored 4x per meeting (row bloat).
- Cancelled calendar events never tombstone their Meeting rows.
- `days_in_stage` staleness (51% of deals) — the daily recalc task needs to
  refresh it for all open deals; currently only health-scored ones.

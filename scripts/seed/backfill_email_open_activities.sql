-- Backfill missing email_opened activity rows so each contact's
-- `email_open_count` matches the number of `email_opened` Activity rows.
--
-- Why this exists:
--   The Instantly webhook (app/api/v1/endpoints/webhooks.py:689) does the
--   right thing in production — every "email_opened" event increments the
--   counter AND writes one Activity row. But pre-existing seed/test data
--   was authored by bumping the counter without exploding into per-event
--   rows. That makes the lifecycle drawer (which reads Activity rows) look
--   thin compared to the prospect-row counter — confusing for demos.
--
--   This script reconciles the gap by synthesizing the missing rows. It is
--   idempotent: re-running it after the next real webhook delivery will be
--   a no-op (the counter and rows will already match).
--
-- Run:
--   docker compose exec -T postgres psql -U beacon -d beacon_crm \
--     < scripts/seed/backfill_email_open_activities.sql

BEGIN;

-- For each contact whose counter exceeds its open-activity row count,
-- compute the gap and insert that many synthetic Activity rows. The
-- synthetic rows are timestamped just after `email_last_opened_at` (or
-- `updated_at` if missing), with one-minute stagger so the lifecycle
-- assembler sees them as distinct events ordered after the original.
WITH per_contact AS (
  SELECT
    c.id                                AS contact_id,
    c.email_open_count                  AS counter,
    c.email_last_opened_at              AS last_opened_at,
    c.updated_at                        AS contact_updated_at,
    COALESCE((
      SELECT COUNT(*)
        FROM activities a
       WHERE a.contact_id = c.id
         AND (a.metadata->>'event_type') = 'email_opened'
    ), 0)                               AS existing_rows
  FROM contacts c
  WHERE COALESCE(c.email_open_count, 0) > 0
),
gap AS (
  SELECT
    contact_id,
    counter - existing_rows AS missing,
    COALESCE(last_opened_at, contact_updated_at) AS anchor_ts
  FROM per_contact
  WHERE counter > existing_rows
)
INSERT INTO activities (
  id, type, source, content, metadata,
  contact_id, external_source, external_source_id, created_at
)
SELECT
  gen_random_uuid(),
  'email',
  'instantly',
  'Email opened (backfilled)',
  jsonb_build_object(
    'event_type',   'email_opened',
    'backfilled',   true,
    'backfill_run', NOW()::text
  ),
  g.contact_id,
  'instantly',
  -- External-source id is unique-constrained per (external_source, external_source_id),
  -- so embed the contact id + offset to avoid collisions across reruns.
  'backfill:open:' || g.contact_id::text || ':' || s.n::text,
  COALESCE(g.anchor_ts, NOW()) - INTERVAL '1 second' * s.n
FROM gap g
CROSS JOIN LATERAL generate_series(1, g.missing) AS s(n)
-- Re-run safety: skip if a backfill row with this synthetic external_source_id
-- already exists (older runs landed it via the same convention).
WHERE NOT EXISTS (
  SELECT 1 FROM activities a
   WHERE a.external_source    = 'instantly'
     AND a.external_source_id = 'backfill:open:' || g.contact_id::text || ':' || s.n::text
);

-- Same idea for clicks: `email_click_count` vs `email_link_clicked` rows.
-- Click events are rarer than opens; usually the counter is 0 so this is a
-- no-op for most contacts.
WITH per_contact AS (
  SELECT
    c.id                                AS contact_id,
    c.email_click_count                 AS counter,
    c.email_last_opened_at              AS last_opened_at,
    c.updated_at                        AS contact_updated_at,
    COALESCE((
      SELECT COUNT(*)
        FROM activities a
       WHERE a.contact_id = c.id
         AND (a.metadata->>'event_type') = 'email_link_clicked'
    ), 0)                               AS existing_rows
  FROM contacts c
  WHERE COALESCE(c.email_click_count, 0) > 0
),
gap AS (
  SELECT
    contact_id,
    counter - existing_rows AS missing,
    COALESCE(last_opened_at, contact_updated_at) AS anchor_ts
  FROM per_contact
  WHERE counter > existing_rows
)
INSERT INTO activities (
  id, type, source, content, metadata,
  contact_id, external_source, external_source_id, created_at
)
SELECT
  gen_random_uuid(),
  'email',
  'instantly',
  'Link clicked in email (backfilled)',
  jsonb_build_object(
    'event_type',   'email_link_clicked',
    'backfilled',   true,
    'backfill_run', NOW()::text
  ),
  g.contact_id,
  'instantly',
  'backfill:click:' || g.contact_id::text || ':' || s.n::text,
  COALESCE(g.anchor_ts, NOW()) - INTERVAL '1 second' * s.n
FROM gap g
CROSS JOIN LATERAL generate_series(1, g.missing) AS s(n)
WHERE NOT EXISTS (
  SELECT 1 FROM activities a
   WHERE a.external_source    = 'instantly'
     AND a.external_source_id = 'backfill:click:' || g.contact_id::text || ':' || s.n::text
);

COMMIT;

-- ── Sanity report: confirm counters now match row counts ────────────────────
SELECT
  c.first_name || ' ' || c.last_name                 AS name,
  c.email_open_count                                 AS counter_opens,
  (SELECT COUNT(*) FROM activities a
    WHERE a.contact_id = c.id
      AND (a.metadata->>'event_type') = 'email_opened') AS row_opens,
  c.email_click_count                                AS counter_clicks,
  (SELECT COUNT(*) FROM activities a
    WHERE a.contact_id = c.id
      AND (a.metadata->>'event_type') = 'email_link_clicked') AS row_clicks
FROM contacts c
WHERE COALESCE(c.email_open_count, 0) > 0 OR COALESCE(c.email_click_count, 0) > 0
ORDER BY c.email_open_count DESC NULLS LAST
LIMIT 20;

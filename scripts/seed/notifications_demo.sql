-- Demo data for the in-app NotificationBell.
--
-- Creates 6 notifications across every state the bell can render
-- (unread / read-but-actionable / accepted / dismissed) so you can
-- click around and see the visual treatments without waiting for a
-- real Instantly reply to come in.
--
-- Idempotent: each row is keyed by a deterministic `dedup_key` and
-- guarded with `ON CONFLICT (user_id, dedup_key) DO UPDATE`, so
-- re-running this script wipes the demo notifications back to the
-- canonical demo state. Real production notifications are untouched
-- (their dedup_key prefix is `meeting_booked:...`, not `demo:...`).
--
-- Run:
--   docker compose exec -T postgres psql -U beacon -d beacon_crm \
--     < scripts/seed/notifications_demo.sql

BEGIN;

WITH me AS (
  -- Target the workspace owner. Change the email here if you want to
  -- seed for a different rep.
  SELECT id FROM users WHERE email = 'sarthak@beacon.li' LIMIT 1
),
picks AS (
  -- Pick six real demo contacts so the "Accept → Create deal" flow has
  -- a real prospect to materialize against.
  SELECT
    c.id              AS contact_id,
    c.first_name || ' ' || c.last_name AS contact_name,
    c.company_id      AS company_id,
    co.name           AS company_name,
    ROW_NUMBER() OVER (ORDER BY c.created_at DESC) AS rn
  FROM contacts c
  LEFT JOIN companies co ON co.id = c.company_id
  -- These are the demo contacts the existing test data set up. If they
  -- don't exist, the seed gracefully degrades to zero rows.
  WHERE c.first_name IN (
    'Avery','Blake','Casey','Devon','Emery','Finley','Gray','Harper'
  )
  LIMIT 6
),
to_insert AS (
  SELECT
    p.contact_id,
    p.contact_name,
    p.company_id,
    p.company_name,
    p.rn,
    CASE p.rn
      WHEN 1 THEN 'Positive reply from ' || p.contact_name
      WHEN 2 THEN 'Meeting interest from ' || p.contact_name
      WHEN 3 THEN 'Hot reply from ' || p.contact_name
      WHEN 4 THEN p.contact_name || ' wants to meet'
      WHEN 5 THEN 'Booked: intro call with ' || p.contact_name
      WHEN 6 THEN 'Inbound interest — ' || p.contact_name
    END AS title,
    CASE p.rn
      WHEN 1 THEN 'Replied "Yes, let''s set up time next week." — strong buy signal.'
      WHEN 2 THEN 'Asked for a 20-min intro Tue/Wed PM Pacific. Wants to loop in Controller.'
      WHEN 3 THEN 'Said "send a calendar invite, I''m in."'
      WHEN 4 THEN 'Confirmed availability Thursday 10am ET for a discovery call.'
      WHEN 5 THEN 'Calendly slot confirmed. Auto-suggested deal pre-filled.'
      WHEN 6 THEN 'Replied to step 1: "would love to chat about this."'
    END AS body,
    -- Mix of states so the bell shows the full visual range:
    --   rn 1,2,3 → fresh unread (badge counts them)
    --   rn 4    → already read but still actionable (Create deal visible)
    --   rn 5    → already accepted (greyed out, no Accept button)
    --   rn 6    → dismissed (filtered out of the popover but recoverable)
    CASE
      WHEN p.rn IN (4) THEN NOW() - INTERVAL '20 minutes'
      WHEN p.rn IN (5) THEN NOW() - INTERVAL '2 hours'
      WHEN p.rn IN (6) THEN NOW() - INTERVAL '1 day'
      ELSE NULL
    END AS read_at,
    CASE p.rn WHEN 5 THEN NOW() - INTERVAL '2 hours' ELSE NULL END AS accepted_at,
    CASE p.rn WHEN 6 THEN NOW() - INTERVAL '1 day' ELSE NULL END AS dismissed_at,
    -- Stagger created_at so the popover order matches the narrative.
    CASE p.rn
      WHEN 1 THEN NOW() - INTERVAL '3 minutes'
      WHEN 2 THEN NOW() - INTERVAL '11 minutes'
      WHEN 3 THEN NOW() - INTERVAL '38 minutes'
      WHEN 4 THEN NOW() - INTERVAL '1 hour'
      WHEN 5 THEN NOW() - INTERVAL '2 hours'
      WHEN 6 THEN NOW() - INTERVAL '1 day'
    END AS created_at
  FROM picks p
)
INSERT INTO notifications (
  id, user_id, type, title, body, action_payload, dedup_key,
  read_at, dismissed_at, accepted_at, created_at, updated_at
)
SELECT
  gen_random_uuid(),
  (SELECT id FROM me),
  'meeting_booked_suggest_deal',
  t.title,
  t.body,
  jsonb_build_object(
    'contact_id', t.contact_id::text,
    'contact_name', t.contact_name,
    'company_id', CASE WHEN t.company_id IS NULL THEN NULL ELSE t.company_id::text END,
    'company_name', t.company_name,
    'reply_summary', t.body,
    'reply_intent', 'interested',
    'reply_sentiment', 'positive',
    'demo', true
  ),
  'demo:notification:' || t.contact_id::text,
  t.read_at,
  t.dismissed_at,
  t.accepted_at,
  t.created_at,
  NOW()
FROM to_insert t
ON CONFLICT (user_id, dedup_key) DO UPDATE SET
  title          = EXCLUDED.title,
  body           = EXCLUDED.body,
  action_payload = EXCLUDED.action_payload,
  read_at        = EXCLUDED.read_at,
  dismissed_at   = EXCLUDED.dismissed_at,
  accepted_at    = EXCLUDED.accepted_at,
  created_at     = EXCLUDED.created_at,
  updated_at     = NOW();

COMMIT;

-- ── Sanity report ───────────────────────────────────────────────────────────
SELECT
  to_char(created_at, 'MM-DD HH24:MI') AS when_,
  title,
  CASE
    WHEN dismissed_at IS NOT NULL THEN 'dismissed'
    WHEN accepted_at  IS NOT NULL THEN 'accepted'
    WHEN read_at      IS NOT NULL THEN 'read'
    ELSE 'UNREAD'
  END AS state
FROM notifications
WHERE dedup_key LIKE 'demo:notification:%'
ORDER BY created_at DESC;

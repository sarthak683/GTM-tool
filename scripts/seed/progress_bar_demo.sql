-- Demo data for the prospect-row progress bar (Yellow/Blue/Green/Red/White dots).
--
-- Attaches 9 demo contacts to the first existing company, each one spanning
-- a canonical dot-state combination ProgressCell can render. Idempotent:
-- re-running wipes only the demo contacts (matched by their
-- progress-bar.test email) + their activities, then re-inserts fresh ones.
-- Existing real data is untouched.
--
-- A DB trigger prevents direct INSERTs into `companies` outside the Account
-- Sourcing flow, so this script piggybacks on the first company already in
-- the table.
--
-- Run:
--   docker compose exec -T postgres psql -U beacon -d beacon_crm \
--     -f /tmp/progress_bar_demo.sql

BEGIN;

-- ── 1. Clear out any previous demo contacts (+ their activities) ────────────
DELETE FROM activities
WHERE contact_id IN (
  SELECT id FROM contacts WHERE email LIKE 'demo+%@progress-bar.test'
);
DELETE FROM contacts WHERE email LIKE 'demo+%@progress-bar.test';

-- ── 2. Insert the 9 demo contacts ───────────────────────────────────────────
-- Each row maps to one canonical state the rep should see in ProgressCell.
WITH co AS (
  SELECT id FROM companies ORDER BY created_at ASC LIMIT 1
),
new_contacts AS (
  INSERT INTO contacts (
    id, company_id, first_name, last_name, email, email_verified, phone,
    title, sequence_status, email_open_count, email_click_count,
    email_last_opened_at, call_status, call_disposition, call_last_at,
    next_followup_at, created_at, updated_at
  )
  VALUES
    -- 1. Cold prospect — no activity at all. Expect: empty rail in both lanes.
    (gen_random_uuid(), (SELECT id FROM co), 'Alex', 'Cold',
     'demo+01@progress-bar.test', true, '+15551110001',
     'VP Engineering', NULL, 0, 0, NULL,
     'none', NULL, NULL, NULL, NOW(), NOW()),

    -- 2. Email sent, no opens. Expect: 1 yellow.
    (gen_random_uuid(), (SELECT id FROM co), 'Brenda', 'Sent',
     'demo+02@progress-bar.test', true, '+15551110002',
     'Director of Sales', 'sent', 0, 0, NULL,
     'none', NULL, NULL, NULL, NOW(), NOW()),

    -- 3. A few opens, no reply. Expect: 1 yellow + 3 blue.
    (gen_random_uuid(), (SELECT id FROM co), 'Carlos', 'Curious',
     'demo+03@progress-bar.test', true, '+15551110003',
     'Head of RevOps', 'sent', 3, 0, NOW() - INTERVAL '2 hours',
     'none', NULL, NULL, NULL, NOW(), NOW()),

    -- 4. Hot reader, overflow case. 12 opens → 1 yellow + 6 blue + "+6".
    (gen_random_uuid(), (SELECT id FROM co), 'Dani', 'Devoted',
     'demo+04@progress-bar.test', true, '+15551110004',
     'CTO', 'sent', 12, 4, NOW() - INTERVAL '30 minutes',
     'none', NULL, NULL, NULL, NOW(), NOW()),

    -- 5. Positive email reply. Expect: 1 yellow + 5 blue + 1 green.
    (gen_random_uuid(), (SELECT id FROM co), 'Eshan', 'Engaged',
     'demo+05@progress-bar.test', true, '+15551110005',
     'Chief of Staff', 'replied', 5, 1, NOW() - INTERVAL '1 day',
     'none', NULL, NULL, NULL, NOW(), NOW()),

    -- 6. Negative email reply. Expect: 1 yellow + 1 blue + 1 red.
    (gen_random_uuid(), (SELECT id FROM co), 'Farah', 'Frosty',
     'demo+06@progress-bar.test', true, '+15551110006',
     'Procurement Lead', 'not_interested', 1, 0, NOW() - INTERVAL '3 days',
     'none', NULL, NULL, NULL, NOW(), NOW()),

    -- 7. Called 3 times, callback scheduled for 4 days from now.
    --    Expect: 3 yellow + blue + white + "MMM DD".
    (gen_random_uuid(), (SELECT id FROM co), 'Greta', 'Gonna-callback',
     'demo+07@progress-bar.test', true, '+15551110007',
     'VP Operations', NULL, 0, 0, NULL,
     'connected', 'call_back_later_rescheduled', NOW() - INTERVAL '1 day',
     NOW() + INTERVAL '4 days', NOW(), NOW()),

    -- 8. Called 5 times, demo booked. Expect: 5 yellow + 1 green.
    (gen_random_uuid(), (SELECT id FROM co), 'Hiro', 'Hooked',
     'demo+08@progress-bar.test', true, '+15551110008',
     'CRO', NULL, 0, 0, NULL,
     'connected', 'demo_scheduled_booked', NOW() - INTERVAL '2 hours',
     NULL, NOW(), NOW()),

    -- 9. Called 10 times, burned out. Expect: 8 yellow + "+2" + red.
    (gen_random_uuid(), (SELECT id FROM co), 'Ivy', 'Iced',
     'demo+09@progress-bar.test', true, '+15551110009',
     'Founder', NULL, 0, 0, NULL,
     'connected', 'connected_not_interested', NOW() - INTERVAL '5 hours',
     NULL, NOW(), NOW())
  RETURNING id, email
)
-- ── 4. Insert call activities to populate call_attempt_count ────────────────
-- ProgressCell counts via COUNT(activities WHERE type='call') per contact.
-- Each helper row below inserts the right number of dummy call activities.
INSERT INTO activities (id, contact_id, type, created_at)
SELECT gen_random_uuid(), nc.id, 'call', NOW() - (g.n || ' hours')::interval
FROM new_contacts nc
CROSS JOIN LATERAL generate_series(1,
  CASE nc.email
    WHEN 'demo+07@progress-bar.test' THEN 3
    WHEN 'demo+08@progress-bar.test' THEN 5
    WHEN 'demo+09@progress-bar.test' THEN 10
    ELSE 0
  END
) AS g(n);

COMMIT;

-- ── Sanity report ───────────────────────────────────────────────────────────
SELECT
  c.first_name || ' ' || c.last_name        AS name,
  c.email,
  c.sequence_status,
  c.email_open_count                        AS opens,
  c.call_disposition,
  (SELECT COUNT(*) FROM activities a
    WHERE a.contact_id = c.id AND a.type = 'call') AS call_attempts,
  c.next_followup_at::date                  AS followup_date
FROM contacts c
WHERE c.email LIKE 'demo+%@progress-bar.test'
ORDER BY c.email;

# CRM deep audit — October 1, 2026

## Release already completed

The existing reliability changes, commit `2317d7c`, are deployed to staging
(`gtm`, Helm revision 264) and production (`gtm-prod`, revision 235), with tag
`v0.261001-2317d7c-reliability` for both images. Image-only drift gates passed,
all five application workloads were preserved, and migration head remains 143.
Staging passed 21 disposable workflow checks; production passed nine authenticated
read checks. All new pods have zero restarts. Post-release logs show no Python
tracebacks or HTTP 5xx responses.

Production inspection after this release is read-only. Follow-up code commit
`ffa223b` is deployed to staging only (Helm revision 265), using backend and
frontend tag `v0.261001-ffa223b-deep-audit`. The image-only drift check found six
image fields and no resource additions or removals. Migration head remains 143.
Draft PR #16 is based on #15; neither PR was merged during this work.

## Follow-up fixes

### Async SDK lifecycle

Request-owned Anthropic and OpenAI clients were not explicitly closed on many
paths. Celery runs each async task on a fresh event loop, so SDK destructor cleanup
could occur after its owning loop was closed. Pre-release worker logs contain
repeated `RuntimeError: Event loop is closed` cleanup exceptions.

`app/clients/lifecycle.py` closes owned clients on success, API failure, timeout,
or cancellation while the owning loop is running. Applied to Claude completions
and enrichment, OpenAI embeddings, personal/shared email classification, meeting
summaries, ICP intelligence, webhook classification, demo generation/repair, and
Whisper transcription. Demo retries keep their client open for the entire operation.
Existing cached clients used within the web process are not closed per request.

Regression tests cover successive fresh worker loops, failures, cancellation,
existing fallback behavior, and actual Anthropic SDK calls through an in-memory
HTTP transport. No paid AI requests were made. This corrects confirmed resource
leaks; old log stacks do not establish that every historical cleanup exception
had the same cause.

### Tasks badge and queue

The badge counted open assigned tasks without checking whether the associated
record exists or is visible. The Tasks page applied entity visibility later,
so its rows could disagree with the badge after deletion or reassignment.
The badge and workspace query now use the existing company/contact/deal visibility
rules through ID-only EXISTS predicates. The badge remains read-only, and orphan
rows are excluded before entity hydration. SQLite execution tests cover admin,
AE and SDR scopes, missing entities, soft deletion, closed tasks and other users.

Production has 1,925 historical tasks with missing entities (1,788 deal tasks,
137 contact tasks). None is presently an open task assigned to a user. Current badge differences caused by hidden or deleted entities are confirmed for
two users: Dyuthith (2 counted, 1 visible) and Sipra (6 counted, 4 visible). Existing
cascade delete handlers already clean related tasks; these historical rows were
not deleted or changed during this audit.

### Clearing rich-text notes

Clearing an editor saved `<p></p>` as a nonempty string. Qualification criteria
then appeared present even though the editor was blank. Empty editors now emit
an empty value, preserving the existing null handling of optional note fields.
Tests verify empty saves and retained formatting for nonempty notes.

### Dependency maintenance and integration guidance

Updated frontend editor, sanitizer, router, build and test dependencies. Removed
obsolete React Router future flags because their behavior is now the default.
`npm audit` went from 44 affected entries to zero, including development tooling.
The TypeScript production build and all frontend tests pass.

Updated FastAPI/Starlette together with compatible instrumentation, multipart
parsing, JWT, dotenv, PDF extraction, OAuth and test packages. Pinned the Anthropic
SDK tested by the rebuilt runtime. The runtime image now upgrades its own pip;
upgrading only the builder did not update the runtime base's copy. Installed-package
`pip-audit` went from 69 advisory entries in eight packages to zero in the rebuilt
Docker runtime. Advisory counts include tooling and do not by themselves prove
an exploitable production path.

Settings no longer promises that personal-email sync automatically creates new
prospects: the implementation only matches existing CRM contacts and deals.

## Production and staging inspection

- Inspected every public-schema table (50), migration state, table counts,
  database connections, long transactions, blocking locks and invalid indexes.
- No duplicate nonblank prospect emails, dangling contact/company, deal/company
  or activity/contact references, inactive contact AE owners, or active mail
  connections belonging to inactive users were found.
- All 19 tracked scheduled jobs most recently succeeded or skipped for a configured
  reason. Historical failures are retained and are not treated as current failures.
- No sourcing batch or recording is stuck in a processing state. Production has
  eight historical failed sourcing batches and 50 old failed recordings; the
  latest recording failure is August 16. Indexed Drive files: 51 successful and
  one intentionally skipped unsupported/empty file, zero current failures.
- Seven personal mailboxes have `invalid_grant` and are inactive. Their owners
  must reconnect; application code cannot renew revoked Google authorization.
  Pravalika's connection is active and its historical backfill completed.
- 357 contact/account AE differences exist: 220 contacts have no AE, 30 accounts
  have no AE, and 107 have explicit different AEs. Divergent ownership is supported
  by the product; these were measured, not automatically reassigned.
- One production backend OOM kill occurred before the release. New pods have not
  restarted. The cause is unresolved; a healthy rollout does not establish that
  the earlier memory pressure is permanently fixed.
- Staging uses development configuration and intentionally lacks some external
  integrations. Checks use synthetic `.invalid` data and avoid real email, calls,
  push notifications and paid AI.

Raw observations and browser results are kept locally under
`artifacts/deep-audit-2026-10-01/`; they are not committed as customer data.

## Verification and limitations

- Full rebuilt Docker backend suite: 667 passed, 19 subtests, 366 existing
  deprecation warnings. Includes lifecycle, badge and auth surface regressions.
  Compile and Ruff gates passed.
- Frontend: 52 tests passed in 14 files; TypeScript/Vite production build and
  dependency audit passed.
- Desktop navigation, deal drawer/editor, and mobile layout smoke checks.
- All 163 protected GET routes reject anonymous requests in both environments.
  A full route auth regression covers the nested-router framework representation.
- Authenticated disposable staging CRUD with cleanup.
- Exact deployed amd64 staging image: all 667 tests passed in an isolated test
  pod, and installed-package pip-audit reported zero known advisories.
- Staging: 23 authenticated workflow checks with cleanup; six synthetic parser
  checks passed (PDF, text, CSV, DOCX, XLSX and PPTX).
- Browser: ten main routes, deal drawer, formatted notes, clearing notes and the
  returned qualification warning; no console errors. Local mobile Settings
  controls fit a 390px viewport.
- All four PR CI checks passed. Deployment gates preserve configuration, storage
  and existing workloads.

Running the full unit suite inside the serving staging backend initially
exceeded its memory limit, restarted that pod once and briefly caused 503s in an
overlapping workflow test. Staging recovered. The interrupted synthetic account
was cleaned up, the suite was moved to a separate 2Gi test pod without integration
credentials, and all suite and workflow checks then passed. The isolated pod was
deleted afterward. This test-induced restart is recorded separately from the
earlier production OOM; production was unaffected.

A native macOS virtualenv run stalled while loading a protobuf extension in a
fresh subprocess. The authoritative Linux Docker suite completes successfully;
no production runtime workaround was introduced for that local tool issue.

Actual iPhone notification display still awaits the user's deferred device test.
No audit can establish that all possible application paths are bug-free. Historical
orphan data, revoked Google authorization, and the earlier OOM remain operational
findings, separate from the code fixes verified here.

## Changed files

- Backend routes: `app/api/v1/endpoints/tasks.py`, `webhooks.py`.
- SDK clients: `app/clients/lifecycle.py`, `claude.py`, `claude_enrichment.py`,
  `demo_ai.py`, `openai_embeddings.py`.
- Services: `app/services/icp_intelligence.py`, `personal_email_sync.py`,
  `tldv_sync.py`. Tasks: `app/tasks/_runner.py`, `email_sync.py`, `transcribe_call.py`.
- Frontend: `frontend/src/App.tsx`, `pages/Settings.tsx`,
  `components/RichTextEditor.tsx`, `components/RichTextEditor.test.tsx`,
  `frontend/package.json`, `package-lock.json`, `yarn.lock`.
- Runtime: `Dockerfile`, `requirements.txt`.
- Backend tests: `tests/test_async_client_lifecycle.py`, `test_claude_client.py`,
  `test_task_badge_visibility.py`, `test_task_count_readonly.py`,
  `test_get_route_auth_surface.py`.
- Reports: this file and `docs/CRM_RELIABILITY_AUDIT_2026-10-01.md`.

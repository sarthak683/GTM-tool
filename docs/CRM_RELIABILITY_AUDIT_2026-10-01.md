# CRM reliability audit — October 1, 2026

## Live incident results

Pravalika's summit upload contains 59 distinct prospect emails. All were already
present. BriteCore and Medallia ownership was corrected using the deployed
assignment handlers, with existing SDR assignments preserved. The deployed
prospect repository confirmed that all 59 are visible with Pravalika's actual
permissions and Mine filter.

After her Gmail reconnect, the historical backfill completed: 1,710 emails
processed, 212 activities created, and four calendar meetings found. A subsequent
incremental sync succeeded. There were 72 personal email activities for the week
beginning September 28 at the time of inspection.

Her registered iPhone subscription received successful push-provider responses.
That confirms provider acceptance, not delivery or display on the phone. She will
test from the installed Home Screen app later. No test push was sent on her behalf.

## Confirmed defects fixed in this branch

- Both call entry points now send the number the rep selected. The API validates
  it against the prospect's saved primary and alternate numbers and normalizes
  it for the dialer. Legacy requests without a body still use the primary number.
- Settings verifies that the browser subscription is registered to the current
  CRM user. A stale browser subscription no longer falsely appears enabled.
- iPhone installation guidance survives unsupported-browser detection. Safari
  permission is requested before awaiting service-worker readiness so the click
  remains available as user activation.
- Notification taps fall back to opening the dial bridge when navigation returns
  null or navigation/focus rejects. Non-object push payloads no longer crash.
- Push HTTP work is moved off the async event loop and has a five-second HTTP
  timeout. Failure logs omit push endpoints and exception payloads.
- An inactive personal Gmail connection retains its last error and sync history
  so the reconnect warning remains visible. Shared inbox errors no longer coexist
  with a green Connected / Auto-sync active claim.
- The duplicate-deal lookup applies the caller's visibility scope while retaining
  the current main branch's lightweight field projection.
- Prospect detail content and mobile navigation fit narrow screens; long contact
  details wrap, and the navigation strip scrolls within its own boundary.
  The closed Zippy panel is hidden from layout rather than extending beyond the
  viewport.
- `make test-backend` uses the smoke runner that copies tests into the container
  and rejects empty collection. The historical roster expectation was corrected
  to match the team's intentional removal of Awinja, already on main.

## Verification

- Merged latest `origin/main` (886b3ee) before final verification.
- Full backend container suite: 654 passed; 367 existing deprecation warnings.
- Frontend: 50 tests passed across 14 files, including notification registration,
  Safari permission order, and service-worker navigation failure regression cases.
- Python compile and Ruff gates passed. TypeScript/Vite production build passed.
- Backend, worker, beat, and frontend rebuilt locally. Health, frontend, and API
  docs returned successfully. No startup exceptions found.
- Regenerated frontend OpenAPI types; the generated diff also catches up existing
  schema drift on main.
- Browser smoke covered pipeline, prospects, contact details, account sourcing,
  settings, tasks, meetings, analytics, team, Data Room, and sequences. Responsive
  checks used 1280px and 390px widths. Existing integrations in the local database
  include expired tokens; no external messages, AI runs, or calls were initiated.

## Release and limits

Deployed commit `2317d7c` to staging (`gtm`, Helm revision 264) and production
(`gtm-prod`, Helm revision 235), using tag `v0.261001-2317d7c-reliability` for
both backend and frontend. Both upgrades passed image-only drift and workload
preservation checks. All new pods are healthy with zero restarts. Staging passed
21 disposable workflow checks; production passed nine authenticated read-only
checks. Migration head remains `143`. Post-release logs contain no tracebacks or
HTTP 5xx responses. Actual iPhone display remains pending the user's device test. This is a broad regression and workflow smoke
audit; it does not establish that every possible application path is bug-free.

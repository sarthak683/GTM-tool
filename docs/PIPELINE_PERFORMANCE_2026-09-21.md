# Pipeline browser responsiveness

John reported a freeze immediately after opening Pipeline. His Chrome screenshots
show 28–38 second input delays and a Page Unresponsive dialog. These demonstrate
browser-thread starvation, but do not identify the blocking function or exclude
a browser extension or device-specific cause.

The authenticated read-only John preview has 719 deals and, before this patch,
41,578 DOM elements. No equivalent 38-second freeze was reproduced on the
developer machine. The previous company-picker optimization was insufficient.

## Changes

- Mount only the active desktop/mobile layout.
- Mount desktop column contents when first near the viewport. Keep visited
  columns mounted to preserve pagination and interaction state.
- Render 12 cards per page per deal column and mobile stage. Next/Previous
  expose remaining records; filters, totals, bulk selection state and CSV
  exports still operate on the full result set.
- Combine live-update bursts into one signal per 500 ms, with only one board
  request in flight and a trailing refresh if an event arrives during it.
  Background refreshes preserve the board instead of replacing it with skeletons.
- Retain remotely selected companies and resolve existing picker values by ID.
  Mobile deal cards use the company name already in the board response.
  Prospect company context loads only when opening the prospect tab.

## Verification before rollout

`make frontend-build`: 36 tests passed; TypeScript/Vite build passed.
Docker frontend rebuilt with `docker compose up -d --build --no-deps frontend`.
`make smoke` passed. Desktop 1440×900 and mobile 390×844 screenshots reviewed.

`scripts/smoke/pipeline-browser.cjs` runs Chromium with all API requests fulfilled
by synthetic fixtures (no real authentication or CRM writes). With 1,000 deals
and 6× CPU throttling:

| Metric | Previous production frontend | New local frontend |
| --- | ---: | ---: |
| Initially mounted cards | 1,000 | 48 |
| DOM elements | 58,427 | 3,231 |
| Longest observed main-thread task | 1,056 ms | 149 ms (154 ms repeat) |

These are controlled test observations, not John's laptop measurements or an SLA.
The smoke verifies pagination, searching a record beyond the initial page,
switching layouts, company labels, absence of mobile horizontal overflow,
no company-catalog request at entry, and no JavaScript page errors.

To run, install/provide Playwright on `NODE_PATH`, rebuild localhost, then run
`node scripts/smoke/pipeline-browser.cjs`. `PIPELINE_TEST_URL` selects the frontend
host; API requests are still intercepted. `PIPELINE_BASELINE=1` measures the old
implementation without expecting the new rendering bound.

John must reload to receive the deployed frontend. Resolution of the original
device-specific freeze requires his fresh-session confirmation; successful pod
readiness or an HTML HTTP 200 response alone is not that confirmation.

## Live verification and editor follow-up

The first performance release was verified in staging (revision 255) and
production (revision 226). John’s read-only production view mounted 3,883 DOM
elements and 59 cards while retaining all 719 deals. Search worked.

Opening a deal then exposed a separate rich-text editor lifecycle crash:
`Cannot read properties of null (reading 'cached')` in `getHTML()`. Tiptap clears
the schema on destruction, and the synchronization effect could read a stale
instance. The follow-up creates the editor after React commits, guards destroyed
instances in effects/toolbars/blur, and disables the duplicate StarterKit Link
extension. Strict lifecycle testing and the Chromium smoke now cover editor
mounting, changed note values and opening a deal. All 37 frontend tests pass.

The editor follow-up image is `v0.260921-bdc603c-browserperf`, promoted from
staging revision 256 to production revision 227. The subsequent source-only lint
cleanup removes an obsolete ESLint suppression; it does not change browser code.
Frontend lint exits successfully (existing warnings remain). CI also reports two
backend failures in unchanged code: pod roster consistency and the visibility
guard for `deals.py:310`; backend code and images were not changed by this fix.

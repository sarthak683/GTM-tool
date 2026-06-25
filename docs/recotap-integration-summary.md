

Recotap — Integration Summary 





## Endpoints we use

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/journey-stages` | Read the journey-stage labels (Unaware → Aware → Consideration → Opportunity → Customer) |
| `GET` | `/accounts` | Pull account signals (journey stage, account score, intent sub-scores), incrementally via `lastSync` |
| `POST` | `/accounts` | Create the accounts we track (insert-only today; upsert being added by Recotap) |
| `PUT` | `/accounts/{rtp_aid}` | Update an existing account |


## How we use the API now

**Pull (Recotap → Beacon).** We call `GET /accounts`, matching each account to our CRM company by **web domain**. Following your guidance we now pull **incrementally**: we store the `syncTimestamp` you return and pass it as `lastSync` on the next pull so we only fetch changed accounts, with an on-demand full re-pull as a safety net. We read the journey stage, account score, the four intent sub-scores, and last-activity date.

**Push (Beacon → Recotap).** We push the accounts we're actively working via `POST /accounts` (currently **insert-only** — we'll switch to a single **upsert** call once you add it). We're moving our CRM deal stage off the free-text `tags` workaround onto a structured **"CRM Stage" custom field**, and can optionally group pushed accounts into a **segment**.

## Status of the points from your last email

| Your point | What we did |
|---|---|
| 1. Response envelope (data vs data.data) | Handled — accounts read at data.data, stages at data. |
| 2. Pagination — stop on hasNextPage | Handled — we paginate on hasNextPage, not nextCursor. |
| 3. Partial success on POST (HTTP 200) | Handled — we read each item's status. |
| 4. Account score is unbounded (>100 valid) | Done — no longer treated as a 0–100 percentage. |
| 5. CRM deal stage → custom field / Deals API | Adopting the custom field now; evaluating the Deals API for ROI. |
| 6. segmentId (static, active/draft) | Supported — optional grouping on push. |
| lastSync incremental (no webhook) | Implemented — incremental pulls using your syncTimestamp. |
| Upsert on POST | Noted — POST is insert-only today; you're adding upsert (in progress). We'll use the single upsert call once it's live. |



1. **Lookup filter** — please confirm timing for the GET /accounts?domain= / ?externalId= filter you mentioned; it lets us fetch a single account directly.
2. **customFields in GET /accounts** — please confirm timing; we need to read back the custom-field values we set to verify them.

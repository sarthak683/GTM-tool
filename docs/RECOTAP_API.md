# Recotap API — what Beacon calls

Short reference of the Recotap ABM API calls we make. Code: `app/clients/recotap.py`.

## Auth & base URL
- Header: `X-Api-Key: <key>` + `Content-Type: application/json`
- Base URL switches on `RECOTAP_ENVIRONMENT`:
  - `sandbox` → `https://sandboxapi.reco-tap.com/api/v1` (note the hyphen)
  - `prod` → `https://eapi.recotap.com/api/v1`
- Empty key = client inert (no calls made), not an error.

## Endpoints we use

| Method | Path | We use it for |
|---|---|---|
| `GET` | `/journey-stages` | Fetch the journey-stage labels (Unaware→Customer) |
| `GET` | `/accounts` | Pull account signals into `recotap_accounts` |
| `POST` | `/accounts` | Push CRM accounts (upsert by domain) |
| `PUT` | `/accounts/{rtp_aid}` | Update an existing account (set tags) |
| `POST` | `/deals` | Push CRM deals (upsert by `externalDealId`) |

All of it runs on a schedule now — `app.tasks.recotap.sync_recotap`, daily at
04:00 UTC with a full re-pull on Sundays. Before that the integration had no
schedule at all: the pull fired only when someone pressed "Sync Recotap" in the
UI, and the account push had last run 52 days earlier.

### GET /accounts
Query params: `limit` (default 100), `cursor`, `lastSync` (optional incremental).
**Response envelope is double-nested** — rows are at `data.data[]`.
```
{ "data": { "data": [ {account}, ... ], "hasNextPage": true, "nextCursor": "..." } }
```
Pagination: loop while `hasNextPage` is true. **Do NOT loop on `nextCursor`** — it stays populated even on the last page.

Account fields we read: `rtp_aid`, `domain`, `name`, `externalId`,
`rtp_journey_stage`, `rtp_account_score` (0–100, can exceed 100),
`rtp_advertising_activity_score`, `rtp_website_intent_score`,
`rtp_g2_intent_score`, `rtp_bombora_intent_score`, `rtp_last_account_date`.

### POST /accounts
Body:
```
{ "accounts": [ { "domain": "...", "name": "...", "externalId": "<company_id>", "tags": ["CRM: POC"] } ],
  "segmentId": "<optional>" }
```
Send `upsert: true` (the client always does) so an account matched by domain is
UPDATED rather than rejected — without it Recotap answers `status=failed` with
*"Account with domain … already exists"*. HTTP is `200` even when items fail —
read per-item `status` (`created` / `updated` / `failed`) in `data.results[]`.
We never push placeholder/junk domains (guarded by `is_pushable_domain`).

### POST /deals
Body:
```
{ "deals": [ { "externalDealId": "<beacon deal uuid>", "name": "...", "amount": 75000,
               "stageId": "poc_wip", "stageLabel": "POC WIP",
               "pipelineId": "deal", "pipelineLabel": "Deal Pipeline",
               "startDate": "2026-01-15T00:00:00Z", "closedDate": "2026-06-30T00:00:00Z",
               "ownerName": "...", "ownerEmail": "...", "ownerId": "<user uuid>",
               "dealCurrencyCode": "USD",
               "associatedAccounts": [ { "externalId": "<company uuid>", "domain": "acme.com", "name": "Acme Corp" } ] } ] }
```
Only `externalDealId` and `name` are required; **every other key is omitted, not
sent as null**, so a deal with no owner cannot blank an owner Recotap holds.

- **Upsert on `externalDealId`** — we send the Beacon deal UUID, which is stable
  across renames and stage moves as an upsert key must be.
- **Max 100 deals per request.** `RecotapClient.push_deals()` chunks; callers
  hand over the whole changed set. One failed chunk is recorded as failed items
  and the remaining chunks still go.
- HTTP is `200` regardless of per-item outcome. Read `results[]` /
  `summary` — `{total, upserted, failed}`.
- `associatedAccounts[].domain` is what Recotap matches on. A deal whose company
  has a placeholder domain is sent **without** the domain and is created
  unlinked, which beats attaching it to junk.
- Change detection lives in `recotap_deal_pushes` (payload hash per deal), so a
  nightly run sends only what moved. Anything that previously failed is retried
  even when its payload is unchanged.

Code: `app/services/recotap_deals.py`. Upstream reference:
<https://docs.recotap.com/api-reference/deals/push-deals>.

### PUT /accounts/{rtp_aid}
Body: the fields to update, e.g. `{ "name": "...", "tags": ["CRM: Customer"] }`.
Used to set tags on an account that already exists (the POST-conflict path).

## Notes
- Recotap **does not** let us set its computed `journey_stage` or undefined
  custom fields — that's why CRM status is pushed as **tags** (`CRM: Customer/POC/Demo/...`).
- ⚠️ The prod API key leaked once and **must be rotated** (see `RECOTAP_USAGE.md`).
- Full integration guide: `docs/RECOTAP_USAGE.md`.

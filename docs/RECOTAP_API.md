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
| `POST` | `/accounts` | Push CRM accounts (insert-only) |
| `PUT` | `/accounts/{rtp_aid}` | Update an existing account (set tags) |

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
**Insert-only.** HTTP is `200` even when items fail — read per-item `status`
(`created` / `failed`) in `data.results[]`. On `"already exists"` the error
carries the `rtp_aid`; we then `PUT` to update (see below). We never push
placeholder/junk domains (guarded by `is_pushable_domain`).

### PUT /accounts/{rtp_aid}
Body: the fields to update, e.g. `{ "name": "...", "tags": ["CRM: Customer"] }`.
Used to set tags on an account that already exists (the POST-conflict path).

## Notes
- Recotap **does not** let us set its computed `journey_stage` or undefined
  custom fields — that's why CRM status is pushed as **tags** (`CRM: Customer/POC/Demo/...`).
- ⚠️ The prod API key leaked once and **must be rotated** (see `RECOTAP_USAGE.md`).
- Full integration guide: `docs/RECOTAP_USAGE.md`.

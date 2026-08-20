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
| `GET` | `/accounts/custom-fields` | Read field keys (**plural** path) |
| `POST` | `/accounts/custom-field` | Define the CRM-stage field (**singular** path) |
| `POST` | `/deal-stages` | Register our pipeline + stage taxonomy (one-time) |
| `POST` | `/sales-activities` | Push calls + emails (batch 50) |

Endpoints Recotap documents that we still do NOT use: `GET /deal-stages`,
`GET /segments`, `GET /accounts/missing-external-ids`, and the map-external-IDs
call. ⚠️ Their published OpenAPI spec at `/api-reference/openapi.json` is still
the Mintlify **plant-store template**, not their API — the prose pages and
`llms.txt` index are the only usable reference.

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

**Scope:** every live company with a real domain, whether or not it has a deal.
It used to be only companies whose deals mapped to a CRM stage, which left 412
prod accounts unknown to Recotap — and an account Recotap has never been told
about cannot be scored, which is backwards for the accounts we have not worked
yet.

**Which domain we send:** if Recotap already holds an account for this company
(`recotap_accounts.rtp_aid IS NOT NULL`), we push to **Recotap's** domain, not
the CRM's. `POST /accounts` upserts on domain, so sending our own spelling for an
account they hold as `manh.com` creates a *second* account rather than updating
the first. Prod carried exactly that: `manhattanassociates.com` (score 0, no
stage) beside `manh.com` (score 52, Aware), one company's signal split in two.

### Account identity — how a Recotap account finds its Beacon company

`recotap_accounts.company_id` is the join, and `link_recotap_accounts()` resolves
it on every sync using three keys, most authoritative first:

| # | Key | Why |
|---|-----|-----|
| 1 | `externalId` → Beacon company UUID | We send it on every push and read it back on every pull. Domain-independent, so the link survives either side correcting a domain. **This is the key the integration should settle on.** |
| 2 | Normalized domain | Scheme/`www.` stripped, lowercased. |
| 3 | Exact normalized company name | Only when exactly ONE live company answers to it. Recovers accounts the first two cannot — Recotap's `ironcladapp.com` against the CRM's `ironclad.com`. |

The name pass refuses ambiguity by design: three prod rows are all named
"Northstar Technologies", and a wrong link puts one account's buying intent on a
different account.

A company may legitimately own more than one row. `_merge_rows()` collapses them
for display — max score, furthest stage, engagement re-derived from the merged
score — and `sync_crm_journey` keeps the CRM-derived stage on exactly one of
them so the funnel cannot count a company twice.

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

### POST /accounts/custom-field  ·  GET /accounts/custom-fields
Note the paths differ: **create is singular, list is plural.**

Create body: `{ "label": "CRM Stage", "labelType": "singleSelection",
"options": [...], "description": "..." }`. `labelType` is one of
`singleLineText | multiLineText | singleSelection | multiSelection | number |
date`; selection types **require** at least one option.

The **key is generated by Recotap from the label** — spaces become underscores,
the whole thing is uppercased, and `_C` is appended (`CRM Stage` →
`CRM_STAGE_C`). Only spaces are transformed, so other punctuation survives; read
the key back rather than computing it. Creation is **not idempotent**: a `409`
means the key already exists, which is the normal steady state, so treat it as
success and re-list to recover the key. That key is then what `customFields` is
keyed by on POST/PUT `/accounts`.

### POST /deal-stages
Body: `{ "pipelines": [ { "pipelineId", "pipelineLabel", "stages": [ { "stageId",
"stageLabel" } ] } ] }` — 1–20 pipelines.

**Create-only and all-or-nothing.** If any `pipelineId` already exists Recotap
returns `409` and creates *nothing* — there are no partial creates. Registration
is therefore a one-time action; a scheduled caller must read the 409 as
"already registered" and never retry. Empirically, deals referencing an
unregistered `stageId` are still accepted (our first 689-deal push succeeded
before any registration), so this improves how stages render on their side
rather than being a prerequisite.

### POST /sales-activities
Body: `{ "activities": [...] }` — **max 50** (deals allow 100; different ceiling).
Required per activity: `externalActivityId` (dedup key — a repeat returns
`failed`), `activityType` (**only** `call` or `email`; anything else returns
`skipped`), `occurredAt` (ISO 8601), `domain`, `ownerEmail`, and `contacts[]`
with at least one entry carrying an `email`.
Call extras: `callTitle`, `durationMinutes`, `outcome`, `direction`
(`inbound|outbound`). Email extras: `openCount`, `clickCount`.
HTTP is `200` regardless — read `results[]` / `summary`
(`total/created/failed/skipped`).

## Notes
- Recotap **does not** let us set its computed `journey_stage`. Custom fields
  are rejected unless pre-defined — but they can be pre-defined via
  `POST /accounts/custom-field`, which is what `resolve_crm_stage_field_key()`
  now does. The `CRM: Customer/POC/...` **tag remains only as a fallback** for
  when the field cannot be resolved.
- ⚠️ The prod API key leaked once and **must be rotated** (see `RECOTAP_USAGE.md`).
- Full integration guide: `docs/RECOTAP_USAGE.md`.

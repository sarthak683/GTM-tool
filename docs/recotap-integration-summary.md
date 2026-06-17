# Beacon × Recotap — Integration Summary

*Shared by the Beacon team for the Recotap team's reference.*

**Beacon** is a GTM execution CRM. We integrate **Recotap** account intelligence
so our reps see each account's journey stage, account score, and intent
sub-scores directly inside our Account Sourcing view. This summarizes how we use
the Recotap API today.

## Authentication & environments
- Auth header: `X-Api-Key: <key>` (plus `Content-Type: application/json`).
- We use both environments:
  - Sandbox — `https://sandboxapi.reco-tap.com/api/v1`
  - Production — `https://eapi.recotap.com/api/v1`

## Endpoints we use

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/journey-stages` | Read the journey-stage labels (Unaware → Aware → Consideration → Opportunity → Customer) |
| `GET` | `/accounts` | Pull account signals (journey stage, account score, intent sub-scores) |
| `POST` | `/accounts` | Create accounts for domains we track |
| `PUT` | `/accounts/{rtp_aid}` | Update an existing account (e.g. set tags) |

## How we use them

**Pull (Recotap → Beacon).** We call `GET /accounts` (paginated) and match each
account to our CRM company by **web domain**. We surface `rtp_journey_stage`,
`rtp_account_score`, and the intent sub-scores (advertising / website / G2 /
Bombora) in our UI, and derive a Hot/Warm/Cold engagement label from the score.
We pass `lastSync` for incremental pulls.

**Push (Beacon → Recotap).** We push the accounts we're actively working via
`POST /accounts` (and `PUT /accounts/{rtp_aid}` on conflict). Because the
computed Journey Stage isn't settable and custom-field keys are rejected, we
convey our CRM deal status as **account tags** (e.g. `CRM: Customer`, `CRM: POC`,
`CRM: Demo`, `CRM: Qualified`). We only push real public domains.

## Fields & examples

**Fields we exchange**

| Direction | Fields |
|---|---|
| Read (`GET /accounts`) | `rtp_aid`, `domain`, `name`, `externalId`, `rtp_journey_stage`, `rtp_account_score`, the four intent sub-scores (advertising / website / G2 / Bombora), `rtp_last_account_date` |
| Write (`POST` / `PUT`) | `domain`, `name`, `externalId` (our CRM company id), `tags` |

**`GET /accounts`** — trimmed response
```json
{ "data": { "data": [
  { "rtp_aid": "abc123", "domain": "acme.com", "name": "Acme",
    "rtp_journey_stage": "Consideration", "rtp_account_score": 72 }
], "hasNextPage": false, "nextCursor": "..." } }
```

**`POST /accounts`** — request → response
```json
{ "accounts": [ { "domain": "acme.com", "externalId": "<crm-company-id>", "tags": ["CRM: POC"] } ] }
→ { "data": { "results": [ { "domain": "acme.com", "status": "created", "rtp_aid": "abc123" } ] } }
```

## Observations & questions for the Recotap team

These are minor things we worked around — flagging in case they're easy wins or
worth documenting:

1. **Response envelopes vary.** `GET /journey-stages` has returned both a bare
   array and `{ "data": [...] }`; `GET /accounts` is double-nested
   (`{ "data": { "data": [...] } }`). Could these be made consistent?
2. **Pagination signal.** On `GET /accounts`, `nextCursor` stays populated even
   on the last page, so we paginate on `hasNextPage`. Is `hasNextPage` the
   intended stop signal?
3. **Partial success on POST.** `POST /accounts` returns HTTP `200` even when
   individual items fail (status is per-item in `data.results[]`). We read
   per-item status — please confirm that's the intended contract.
4. **Account score range.** `rtp_account_score` is documented as 0–100 but we've
   observed values above 100. Is that expected?
5. **Setting CRM stage.** Is there a supported way to push our CRM deal stage to
   Recotap (a settable field, or a webhook), instead of encoding it as tags?

## What would help us next
- A webhook (or incremental endpoint) for account-score / journey-stage updates,
  so we don't have to poll.
- Confirmation of `lastSync` semantics for incremental pulls.

*Contact: Beacon engineering.*

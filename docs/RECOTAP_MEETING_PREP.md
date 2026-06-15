# Recotap Integration — Meeting Prep

> Internal prep for the kickoff call. Source: https://docs.recotap.com/llms.txt
> Beacon-side codebase context: this file is a snapshot, not the system of truth.

---

## 1. What Recotap is (in one paragraph)

AI-powered ABM platform for SaaS. Aggregates intent signals from **website visits, G2,
Bombora, and LinkedIn ads** to produce an **account score** and place each account into a
**buying-journey stage**. They're an official LinkedIn Marketing Partner — that's their
differentiation. Bidirectional integration: you push your CRM accounts/deals/activities;
you pull back their score + journey stage + intent signals. **No webhooks documented** —
you poll their `List Accounts` endpoint on a schedule with a `lastSync` cursor.

Brand: write as **Recotap** in docs/emails.

---

## 2. Their API at a glance

- **Base URL:** `http://eapi.recotap.com/api/v1/` (note: HTTP — *ask them to confirm if this is meant to be HTTPS*)
- **Auth:** `X-Api-Key: <key>` header. No OAuth.
- **Format:** JSON. Standard REST.
- **Batch limits:** 100 accounts/req · 100 deals/req · 50 activities/req
- **Pagination:** keyset `cursor` + `limit`. Response carries `nextCursor`, `hasNextPage`, `syncTimestamp`.
- **Delta sync:** pass `lastSync=<ISO 8601>` to pull only modified-since-then.

### Endpoint inventory

| Direction | Endpoint | Purpose |
|---|---|---|
| Push | `POST /accounts` | Create or upsert accounts (100/req) |
| Push | `PUT /accounts/{id}` | Update single account |
| Push | `POST /accounts/map-external-ids` | Backfill our IDs onto their existing accounts |
| Push | `POST /deals` | Upsert deals via `externalDealId` (100/req) |
| Push | `POST /sales-activities` | Push call/email activities (50/req) |
| Push | `POST /deal-stages` | Push our deal-stage taxonomy |
| Push | `POST /custom-fields/account` | Define custom fields on accounts |
| Pull | `GET /accounts` | List accounts with scores/journey/intent |
| Pull | `GET /accounts/unmapped` | Accounts on their side without our externalId |
| Pull | `GET /deal-stages` | List configured deal stages |
| Pull | `GET /journey-stages` | List journey stage definitions |
| Pull | `GET /segments` | List segments (their grouping concept) |
| Pull | `GET /custom-fields/account` | List custom-field definitions |
| Spec | `GET /api-reference/openapi.json` | OpenAPI 3.x — autogen the client from this |

---

## 3. Their data model (push side)

### Account (POST /accounts)

```json
{
  "accounts": [{
    "domain": "acme.com",          // REQUIRED, dedup key, lowercased + www-stripped
    "name": "Acme Corp",           // REQUIRED
    "externalId": "crm-001",       // OUR Beacon UUID
    "shortName": "Acme",
    "linkedinUrl": "https://www.linkedin.com/company/acme-corp",
    "tags": ["enterprise", "q2-target"],
    "customFields": { "CONTRACT_VALUE_C": 75000 }
  }],
  "segmentId": "663xyz..."          // optional — assign all to a segment
}
```

- **Primary key on their side:** `rtp_aid` (Recotap-generated).
- **Dedup key:** `domain`. If duplicate, item returned as `failed`.
- `externalId` is stored for reference only — *not* a dedup key.

### Deal (POST /deals)

Required: `externalDealId`, `name`. Everything else optional.

```json
{
  "deals": [{
    "externalDealId": "deal-uuid",
    "name": "Acme - Beacon rollout",
    "amount": 180000,
    "dealCurrencyCode": "USD",
    "stageId": "...",            // their stage id
    "stageLabel": "Demo Done",   // human label
    "pipelineId": "...",
    "pipelineLabel": "Enterprise",
    "ownerEmail": "rep@beacon.li",
    "ownerName": "Mahesh Pothula",
    "ownerId": "...",
    "startDate": "2026-04-21T00:00:00Z",
    "closedDate": "2026-06-30T00:00:00Z",
    "associatedAccounts": [{ "externalId": "crm-001", "domain": "acme.com", "name": "Acme Corp" }]
  }]
}
```

- Linked-account match priority: `domain` (most reliable) → `externalId` → `name`.
- Always returns HTTP 200 with per-item status (`upserted`/`failed`).

### Sales Activity (POST /sales-activities)

**Only `call` and `email` are accepted. Everything else is skipped server-side.**

```json
{
  "activities": [{
    "externalActivityId": "activity-uuid",
    "activityType": "call",            // or "email" — nothing else processed
    "occurredAt": "2026-05-07T18:29:00Z",
    "domain": "acme.com",              // links to account
    "ownerEmail": "rep@beacon.li",
    "ownerName": "Mahesh Pothula",
    "ownerId": "...",
    "accountName": "Acme Corp",
    "subject": "Discovery call",
    "from": { "email": "...", "name": "..." },
    "contacts": [{ "email": "buyer@acme.com" }],   // min 1
    // call-only:
    "callTitle": "Discovery call",
    "durationMinutes": 24,
    "outcome": "connected",
    "direction": "outbound",
    // email-only:
    "openCount": 2,
    "clickCount": 1
  }]
}
```

- Dedup by `externalActivityId`. Duplicate → `failed`.

---

## 4. Their data model (pull side — what we get back)

### Account (GET /accounts)

```json
{
  "accounts": [{
    "externalId": "crm-001",
    "name": "Acme Corp",
    "domain": "acme.com",
    "rtp_aid": "...",                         // their internal id
    "rtp_account_score": 78,                  // 0..100
    "rtp_journey_stage": "Consideration",     // team-configured: Awareness / Consideration / Decision / …
    "rtp_advertising_activity_score": 24,     // LinkedIn ads
    "rtp_website_intent_score": 12,
    "rtp_g2_intent_score": 4,
    "rtp_bombora_intent_score": 0,
    "rtp_last_account_date": "2026-05-07T..."
  }],
  "nextCursor": "...",
  "hasNextPage": true,
  "syncTimestamp": "2026-05-07T19:30:00Z"
}
```

**Important:** "all `rtp_` fields are stored as `0` on a freshly pushed account. Scores
update on a schedule — *not* real-time." Confirm cadence in the meeting.

---

## 5. How Beacon's models map onto Recotap

| Beacon | Recotap | Notes |
|---|---|---|
| `companies.id` (UUID) | `externalId` | Our UUID is their `externalId` |
| `companies.domain` | `domain` | Their dedup key — already cleaned in our DB |
| `companies.name` | `name` | |
| `companies.icp_score` | `customFields.BEACON_ICP_SCORE` | Push as a custom field |
| `companies.icp_tier` | `customFields.BEACON_ICP_TIER` | |
| `companies.why_now` | `customFields.WHY_NOW` | |
| `companies.account_thesis` | `customFields.ACCOUNT_THESIS` | |
| `companies.beacon_angle` | `customFields.BEACON_ANGLE` | |
| `companies.priority_tag` | `tags: ["P0"/"P1"/"P2"]` | Or also a custom field |
| `companies.intent_signals` (JSONB) | ← `rtp_*_intent_score` | We *receive* these; merge into our intent_signals |
| **(new)** `companies.recotap_id` | `rtp_aid` | Persist their PK |
| **(new)** `companies.recotap_score` | `rtp_account_score` | Pulled |
| **(new)** `companies.recotap_journey_stage` | `rtp_journey_stage` | Pulled |
| `deals.id` | `externalDealId` | |
| `deals.name` | `name` | |
| `deals.value` | `amount` | Decimal → number |
| `deals.stage` (`demo_scheduled`, `poc_agreed`, …) | `stageLabel` (also `stageId` if we get mapping) | We'd push our stage taxonomy via `POST /deal-stages` first |
| `deals.close_date_est` | `closedDate` | ISO-8601 |
| `deals.assigned_rep_email` | `ownerEmail` | |
| `deals.company_id` → company.domain | `associatedAccounts[].domain` | Join in our SQL |
| `activities.type='call'` | `activityType: "call"` | + `durationMinutes` from `call_duration / 60` |
| `activities.type='email'` | `activityType: "email"` | + `openCount`/`clickCount` if we have them |
| `activities.type='meeting'` / `transcript` / `note` / `visit` | **NOT SUPPORTED** — skip | About ⅔ of our activity volume |
| `activities.created_at` | `occurredAt` | |
| `activities.call_outcome` | `outcome` | Map our enums (`attempted`/`voicemail`/`connected`/`no_answer`/`failed`) to theirs |

---

## 6. Proposed Beacon-side build

```
app/
├── clients/recotap.py            # Auth, pagination, batched upserts
├── tasks/recotap_sync.py         # Push & pull Celery tasks
├── services/recotap_sync.py      # Mapping logic Beacon ↔ Recotap shapes
└── models/settings.py            # +recotap_api_key, +recotap_enabled, +recotap_last_pull_at

alembic/versions/0XX_recotap_columns.py
  - companies.recotap_id (str, unique nullable)
  - companies.recotap_score (int nullable)
  - companies.recotap_journey_stage (str nullable)
  - companies.recotap_synced_at (datetime nullable)
  - deals.recotap_synced_at (datetime nullable)
  - activities.recotap_synced_at (datetime nullable)
  - activities.recotap_skipped (bool default false)   -- for meeting/note rows
  - workspace_settings.recotap_*

celery beat:
  - push every 5 min (rows where updated_at > recotap_synced_at)
  - pull every 15 min via lastSync = workspace_settings.recotap_last_pull_at
```

**Estimated effort:** ~2 dev days end-to-end once spec is locked, +1 day for QA and initial backfill.

---

## 7. Sync flow we'd implement

### Initial backfill (one-time)
1. Push all `companies` with `domain` not null in batches of 100 via `POST /accounts`.
2. Recotap returns `failed` for any duplicate domains (already in their system from a prior signup or trial). For those: call `POST /accounts/map-external-ids` with our UUID + domain to claim them.
3. Push all `deals` with valid `company_id` via `POST /deals`.
4. Push all `activities` where `type IN ('call', 'email')` via `POST /sales-activities`.
5. First pull: `GET /accounts?limit=100` without `lastSync`, paginate to completion. Store `syncTimestamp` as `workspace_settings.recotap_last_pull_at`.

### Incremental (Celery beat)
- **Push (every 5 min):** rows where `updated_at > recotap_synced_at`. Stamp `recotap_synced_at = now()` on successful upsert.
- **Pull (every 15 min):** `GET /accounts?lastSync={recotap_last_pull_at}&cursor={...}`, paginate. Update our `companies.recotap_score` / `recotap_journey_stage` / merge intent signals. Save new `syncTimestamp`.

---

## 8. Open questions to ask in the meeting

Top priority — get answers before they leave:

1. **Is `http://eapi.recotap.com` correct, or should it be `https://`?** Looks like a docs typo — HTTPS is industry standard for API key auth.
2. **Webhooks for score updates?** Docs don't mention any. If pull-only, what's your recommended polling cadence?
3. **How long after we push an account before scores are populated?** Docs say "on a schedule, not real-time." Hours? Days?
4. **Activity type roadmap.** Only `call` and `email` today. Will you add `meeting`? We have ~⅔ of our activity volume in meetings/transcripts (tl;dv + Google Calendar) and dropping that signal weakens what you can score on.
5. **Deal stage taxonomy.** Should we push our stage list via `POST /deal-stages` and you reflect them back as `stageId`s? Or do you have a master list we should map to? - it seems i need to add existing stage id if its there to avoid mismatch
6. **Sandbox / test workspace?** We don't want our first integration QA to write into production scoring.
7. **Rate limits?** Docs list batch size but not requests-per-minute.
8. **Pricing model.** Is the API call-quota-bound or volume-bound? Does Beacon's plan include the API or is it an add-on?
9. **Segments — what are they exactly?** Push-only? Pull-only? Both? How does our `companies.priority_tag` (P0/P1/P2) relate?
10. **Custom-field schema versioning.** If we change a custom field type, what happens to existing values?
11. **SLA / status page.** Where do we monitor your uptime?
12. **Error semantics.** Docs say "always 200 with per-item status." What about platform errors (5xx, 401, 429)? Retry guidance?
13. **PII / data residency.** Where are accounts stored? Any GDPR concerns for EU companies in our pipeline?
14. **OpenAPI spec at `/api-reference/openapi.json` — confirm it's current** and we can use it to autogen our Python client.

Secondary (nice-to-have if time):

15. Can we set our journey-stage definitions to mirror our deal stages, or are they configured independently in Recotap?
16. Do you score *contacts* / persons separately, or only accounts?
17. Is there a UI on the Recotap side we'd log into?

---

## 9. Beacon codebase quick reference (so you can explain on the call)

### The three core tables

- **`companies`** (`app/models/company.py`) — our Account. Has `domain` (matching key), `icp_score`, `intent_signals` JSONB, ownership (`assigned_to_id` AE + `sdr_id` SDR), thesis/why-now/angle text fields.
- **`deals`** (`app/models/deal.py`) — pipeline opportunity tied to a `company_id`. Stage enum (`reprospect`, `demo_scheduled`, `demo_done`, `qualified_lead`, `poc_agreed`, `poc_wip`, `poc_done`, `commercial_negotiation`, `msa_review`, `workshop`, `closed_won`, `closed_lost`, `not_a_fit`, `on_hold`, `nurture`, `churned`). `value` (Decimal), `close_date_est`, `qualification` JSONB with MEDDPICC.
- **`activities`** (`app/models/activity.py`) — every customer-facing touch. `type ∈ {email, call, meeting, note, transcript, visit}`, `medium`, `source` (`manual`, `aircall`, `tldv`, `gmail_sync`, `instantly`, …). Call-specific (`call_duration`, `call_outcome`) and email-specific (`email_message_id`, `email_subject`) sub-fields.

### How our data gets created today

- **Accounts**: bulk CSV upload or sourcing batch via Account Sourcing flow. Enriched async via ICP + Hunter + intent providers.
- **Deals**: manual create from a contact or from an account; auto-created from booked meetings.
- **Activities**:
  - Email: Gmail inbox sync (every 3 min) + personal-inbox sync (every 10 min) + Instantly webhooks.
  - Call: Aircall webhook *(not currently writing in prod)* + manual call-log button on contact list.
  - Meeting: tl;dv every 5 min + Google Calendar via personal-inbox sync.
  - Note: manual.

### Daily volume (prod, last 30 days)
- Companies: hundreds total; tens active in pipeline
- Deals: ~50 active
- Activities: ~12 calls/day (manual), variable email volume, ~10 meetings/week
- Intent signals: ICP enrichment cache (Hunter + custom GPT-4o web research)

### Existing intent-signal source today
We use **enrichment_cache.intent_signals** populated by our own scrape: hiring, funding, product launches via DuckDuckGo + GPT. Recotap would augment with G2/Bombora/LinkedIn ad data — *complementary, not replacement*.

---

## 10. Talking points / pitch you can use

- "We're a sales CRM purpose-built for Beacon's GTM motion. Our `Company` is your `Account`,
   our `Deal` is your `Deal`, our `Activity` is your `Sales Activity` — model alignment is good."
- "We already have first-party intent signals from enrichment scrapes. Recotap's value is the
   second/third-party layer — LinkedIn ad engagement, G2, Bombora — that we *don't* have."
- "Sync direction we expect: we own the source of truth for accounts/deals/activities;
   Recotap is the source of truth for scores/journey-stage/segments."
- "We're a small team — would prefer pull-based polling (~every 15 min) over webhook
   infrastructure on day one, unless you have webhooks already."

---

## 11. Risks to flag

- **Activity coverage gap** — Recotap drops everything that isn't `call` or `email`. If
   their scoring weights activity volume heavily, our meeting/transcript-heavy reps will look
   under-engaged. Mitigation: map meetings → email-shaped synthetic activities? Risky.
- **No webhooks** — score changes propagate at their internal cadence + our 15-min poll =
   up to ~30 min lag. Probably fine for ABM use cases (not transactional).
- **HTTP base URL** — non-blocking but ask them to fix or confirm.
- **Stage taxonomy ambiguity** — until we know whether they have a fixed master list or
   accept our 18-stage enum verbatim, we can't finalize the deal-mapping code.

---

## 12. Action items for after the meeting

- [ ] Receive sandbox API key from Recotap
- [ ] Confirm HTTPS vs HTTP base URL
- [ ] Get the OpenAPI spec, autogen `app/clients/recotap.py` skeleton
- [ ] Decide custom-field schema for our ICP fields (icp_score, why_now, beacon_angle, account_thesis, priority_tag)
- [ ] Push deal-stage taxonomy via `POST /deal-stages`, capture stage IDs
- [ ] Migration for `recotap_*` columns
- [ ] Build push + pull Celery tasks behind a `recotap_enabled` flag (off in prod until QA passes)
- [ ] Backfill plan: which companies/deals/activities go first; estimate volume vs their batch limits
- [ ] Settings UI: API key input + last-sync timestamp display + enable toggle

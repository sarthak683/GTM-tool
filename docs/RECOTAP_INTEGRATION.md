# Recotap Integration

**Status:** Design + verified API contract · **Rewritten:** 2026-06-04
**Supersedes:** the coupled-with-sales design previously in this file and in
[`RECOTAP_MEETING_PREP.md`](./RECOTAP_MEETING_PREP.md) (now historical kickoff prep).

> **What changed (2026-06-04).** Recotap is now scoped as a **standalone marketing /
> ABM module** — its own tables, its own API router, its own marketing-facing pages.
> It does **not** write `rtp_*` columns onto `companies`/`deals`/`activities` and does
> **not** feed the sales dashboard. The earlier bi-directional "merge into sales" design
> is retired. Sections 4–6 below are the new design; sections 7–8 are the **verified**
> sandbox contract (tested 2026-06-04, not vendor docs — the vendor reference has errors,
> see §8.5).

---

## 1. What Recotap is

Recotap is an AI ABM platform and LinkedIn Marketing Partner. It scores accounts and
places each in a buying-journey stage using LinkedIn-ad, G2, Bombora, and website intent
signals. We push a **target-account list**; Recotap returns an **account score**, a
**journey stage**, and **intent sub-scores**.

For Beacon it is a **marketing tool**, complementary to (and separate from) our first-party
sales intent (ICP enrichment, scrapes). Marketing acts on Recotap's scores from dedicated
ABM pages; the sales pipeline is untouched.

---

## 2. Design principle: decoupled from sales

This is the core constraint driving every decision below.

| Concern | Decision |
|---|---|
| **Storage** | New `recotap_*` tables. **No** `rtp_*` columns on `companies`/`deals`/`activities`. |
| **API** | Dedicated router under `/api/v1/marketing/recotap/*`. Not mixed into `performance`/sales endpoints. |
| **UI** | New marketing/ABM section. **Not** in `SalesAnalytics.tsx` or the sales nav. |
| **Sync** | Own Celery tasks (`recotap_sync`), own beat schedule, own enable flag. |
| **Account source** | A marketing-curated ABM list — **not** a live mirror of the sales `companies` table (see §4.2 + Open Decision D1). |
| **Sales data push** | Deal/activity push is **out of MVP scope** — it's the part that re-couples to sales. The endpoints work (§8.4) and can be a phase-2 opt-in, but the default build pushes accounts only. |

The litmus test for any future change: *if a sales engineer deletes the entire sales
analytics surface, the Recotap module should keep working unchanged.*

---

## 3. End-to-end picture

```
Marketing UI (new "ABM" section)
   │  REST
   ▼
/api/v1/marketing/recotap/*   ──►  app/services/recotap.py  ──►  app/clients/recotap.py  ──►  Recotap API
   │                                      │                              (X-Api-Key)
   │                                      ▼
   └──────────── reads ◄────────  recotap_accounts / recotap_segments / recotap_sync_state
                                          ▲
                                          │
                          app/tasks/recotap_sync.py (Celery beat: pull ~15m, push on change)
```

- **Beacon → Recotap:** push the marketing ABM list (`POST /accounts`, `PUT /accounts/{id}`).
- **Recotap → Beacon:** pull scores/journey/intent (`GET /accounts?lastSync=…`) into `recotap_accounts`.
- The marketing pages read **only** `recotap_*` tables — never the live vendor API on the request path.

---

## 4. Data model (new tables)

All new, all namespaced. Nothing here touches the sales schema.

### 4.1 `recotap_accounts`
The ABM account list **and** the pulled scores — one row per account.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | our id |
| `rtp_aid` | str, unique nullable | Recotap's PK; null until first push succeeds |
| `domain` | str, indexed | dedup key (lowercased, www-stripped) — mirrors Recotap's dedup |
| `name` | str | |
| `external_id` | str nullable | our reference id we send as `externalId` |
| `company_id` | uuid FK → companies, **nullable** | optional read-only cross-reference; **no** behavioral coupling (Open Decision D5) |
| `tags` | jsonb | |
| `segment_id` | str nullable | Recotap segment assignment |
| `score` | int nullable | ← `rtp_account_score` (0–100) |
| `journey_stage` | str nullable | ← `rtp_journey_stage` |
| `advertising_activity_score` | int nullable | ← `rtp_advertising_activity_score` |
| `website_intent_score` | int nullable | ← `rtp_website_intent_score` |
| `g2_intent_score` | int nullable | ← `rtp_g2_intent_score` |
| `bombora_intent_score` | int nullable | ← `rtp_bombora_intent_score` |
| `last_account_date` | datetime nullable | ← `rtp_last_account_date` |
| `raw` | jsonb nullable | full last-pulled payload (forward-compat for new `rtp_*` fields) |
| `pushed_at` | datetime nullable | last successful push |
| `pulled_at` | datetime nullable | last successful pull |
| `push_status` | str nullable | `created` / `pending` / `failed` + last error |
| `created_at` / `updated_at` | datetime | standard |

### 4.2 `recotap_segments`
Pulled segment definitions (Recotap's grouping concept).

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `rtp_segment_id` | str, unique | |
| `name` | str | |
| `raw` | jsonb | |
| `synced_at` | datetime | |

### 4.3 `recotap_sync_state`
Single keyed row for the module's sync bookkeeping (or store under
`workspace_settings` JSON — see D4). A dedicated table is cleaner for a standalone module.

| Column | Type | Notes |
|---|---|---|
| `id` | int PK (singleton) | |
| `enabled` | bool, default false | master flag — off in prod until QA passes |
| `environment` | str | `sandbox` / `prod` (selects base URL + which key to read) |
| `last_pull_cursor` | str nullable | the `syncTimestamp` to pass as next `lastSync` |
| `last_pull_at` / `last_push_at` | datetime nullable | |
| `last_error` | text nullable | for the Settings UI + admin System Health |
| `accounts_synced` | int | rolling counter for the UI |

> Journey stages are a tiny fixed list (§8.3). Fetch live and cache in memory or in
> `recotap_sync_state.raw` — no dedicated table needed.

**Migration:** one new Alembic revision that `CREATE TABLE`s the three above. **No**
`ALTER TABLE` on `companies`/`deals`/`activities`. (Per repo policy, use
`CREATE TABLE IF NOT EXISTS` patterns where the init container may re-run — see the
team's "index migrations must be idempotent" rule.)

---

## 5. Backend

```
app/
├── models/recotap.py            # RecotapAccount, RecotapSegment, RecotapSyncState
├── clients/recotap.py           # HTTP wrapper: auth, base-URL select, envelope normalize, pagination, retries
├── services/recotap.py          # Beacon ↔ Recotap shape mapping; push/pull orchestration
├── tasks/recotap_sync.py        # Celery: pull beat (~15m), push-on-change + safety sweep
└── api/v1/endpoints/recotap.py  # marketing-facing REST (see §6)
```

### 5.1 Client responsibilities (`app/clients/recotap.py`)
- **Base-URL selection** by environment — **mind the hyphen** (§8.1).
- `X-Api-Key` header from the env/settings key for the active environment.
- **Per-endpoint envelope normalization** (§8.2) — this is mandatory, not optional; the
  API returns four different response shapes.
- **Keyset pagination** — loop on `hasNextPage`, never on `nextCursor` (§8.5).
- **Batch limits:** 100 accounts/req, 100 deals/req, 50 activities/req.
- **Write semantics differ per endpoint** (§8.5): `POST /accounts` is insert-only →
  `PUT /accounts/{rtp_aid}` on "already exists"; `POST /deals` upserts.
- **Retry/backoff** on 5xx/429 (rate-limit policy unconfirmed — treat conservatively).

### 5.2 Sync tasks (`app/tasks/recotap_sync.py`)
- **Pull (beat, ~15 min):** `GET /accounts?lastSync={last_pull_cursor}`, paginate to
  `hasNextPage:false`, upsert into `recotap_accounts` by `domain`/`rtp_aid`, store the new
  `syncTimestamp` as `last_pull_cursor`. Treat `score:0 / journey_stage:""` as
  *not-yet-scored*, never as a real zero (§8.5).
- **Push (on marketing write + low-frequency safety sweep):** for new rows
  `POST /accounts`; on `failed: already exists` capture the returned `rtp_aid` and
  `PUT /accounts/{rtp_aid}`; for changed rows with a known `rtp_aid` go straight to `PUT`.
  Marketing list changes are infrequent — no need for a 5-min push cadence.
- Everything gated on `recotap_sync_state.enabled`.

---

## 6. API (marketing-facing router)

Mount a dedicated router; do **not** add these to the sales/performance endpoints.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/marketing/recotap/accounts` | List ABM accounts (our stored copy) — filter by score/journey/segment/tag, sort, paginate |
| GET | `/api/v1/marketing/recotap/accounts/{id}` | Account detail incl. intent sub-scores |
| POST | `/api/v1/marketing/recotap/accounts` | Add account(s) to the ABM list → push to Recotap + store |
| PUT | `/api/v1/marketing/recotap/accounts/{id}` | Update name/tags/segment → `PUT` to Recotap |
| POST | `/api/v1/marketing/recotap/accounts/import` | *(optional)* one-time copy from `companies` (Open Decision D1) |
| GET | `/api/v1/marketing/recotap/segments` | List segments |
| GET | `/api/v1/marketing/recotap/journey-stages` | Journey-stage labels (proxy/cached) |
| GET | `/api/v1/marketing/recotap/sync-status` | enabled flag, last pull/push, counts, last error |
| POST | `/api/v1/marketing/recotap/sync` | Trigger a manual pull (admin/marketing) |
| GET / PUT | `/api/v1/marketing/recotap/settings` | API key (sandbox/prod), enable toggle, environment |

These are **Beacon's** endpoints (backend ↔ our frontend). The vendor's API is called only
inside the client/tasks, never proxied straight through.

---

## 7. Frontend (new marketing pages)

A new role-gated **"ABM"** (or "Marketing") nav section — separate route tree (e.g.
`/abm`), not under the sales analytics pages.

- **ABM Accounts** — table from `recotap_accounts`: score, journey stage, intent
  sub-scores (advertising / website / G2 / Bombora), segment, tags. Filter/sort. "Add
  account" + optional "Import from companies". Sync-status banner ("last synced 12m ago").
- **Account detail drawer** — score breakdown, journey stage, `last_account_date`, edit
  tags/segment. Show "Not yet scored" when `score` is null/0 and `last_account_date` is
  recent.
- **Settings** — API key entry (sandbox/prod), environment selector, enable toggle, manual
  "Sync now", last-sync timestamp + last error.

Permissions: a marketing role or feature flag (Open Decision D2).

---

## 8. Verified API contract (tested 2026-06-04, sandbox)

> Everything in this section was exercised against the live sandbox with our key. Where it
> disagrees with the vendor docs or the old `RECOTAP_MEETING_PREP.md`, **this section wins.**

### 8.1 Base URLs + auth
| | |
|---|---|
| **Sandbox** | `https://sandboxapi.reco-tap.com/api/v1/` — note the **hyphen**: `reco-tap` |
| **Prod** | `https://eapi.recotap.com/api/v1/` — **no** hyphen: `recotap` (HTTPS; confirm with vendor — sandbox HTTPS verified) |
| **Auth** | `X-Api-Key: <key>` header on every request. No OAuth, no token exchange, no expiry. |
| **Key storage** | `.env` → `RECOTAP_SANDBOX_API_KEY` (verified present, 64 chars). Add `RECOTAP_API_KEY` for prod. |
| **Auth failures** | `401 Unauthorized` (`message: "Unauthorized access"`) for both invalid **and** missing key — verified. |

> ⚠️ **The hyphen is a real footgun:** sandbox and prod are *different domains*
> (`reco-tap.com` vs `recotap.com`). Select by environment, never hand-edit.

### 8.2 Response envelopes are NOT uniform
The client must normalize **per endpoint** — there are four shapes:

| Shape | Endpoints | Structure |
|---|---|---|
| **Double-nested + pagination** | `GET /accounts`, `GET /segments` | `{statusCode, timestamp, path, data:{ data:[…], nextCursor, hasNextPage, syncTimestamp? }}` — the account array is `data.data`, *not* `data` |
| **Bare array** | `GET /deal-stages`, `GET /journey-stages` | top-level JSON array, **no envelope at all** |
| **Write result** | `POST /accounts`, `POST /deals`, `POST /sales-activities`, `PUT /accounts/{id}` | `{statusCode, timestamp, path, data:{ results:[…], summary:{…} }}` (PUT: `data:{message}`) |
| **Error** | any failing request | `{statusCode, timestamp, path, message, customMessage}` — flat, **no `data`** |

### 8.3 Verified endpoint matrix
| Method | Path | Status | Behavior (verified) |
|---|---|---|---|
| GET | `/accounts` | ✅ 200 | Paginated list of *our* pushed accounts + scores. `limit`, `cursor`, `lastSync` params. |
| POST | `/accounts` | ✅ 200 | **Insert-only.** Per-item `status: created`/`failed`. Dedup key = `domain`. Returns `rtp_aid`. |
| PUT | `/accounts/{rtp_aid}` | ✅ 200 | Update. Returns `{message:"Account updated successfully"}`; bumps `last_account_date`. *(field propagation to the read model can lag a scoring cycle.)* |
| POST | `/deals` | ✅ 200 | **Upserts** by `externalDealId`. Per-item `status: upserted`/`failed`. |
| POST | `/sales-activities` | ✅ 200 | Insert by `externalActivityId`. `status: created`; non-`call`/`email` → `skipped`. |
| GET | `/journey-stages` | ✅ 200 | Bare array. **Actual values:** `["Unaware","Aware","Consideration","Opportunity","Customer"]`. |
| GET | `/segments` | ✅ 200 | Paginated (empty in sandbox). |
| GET | `/deal-stages` | ✅ 200 | Bare array (empty in sandbox). |
| GET | `/accounts/unmapped` | ❌ **404** | **Does not exist.** (Old docs listed it.) |
| GET | `/custom-fields/account` | ❌ **404** | **Does not exist** (at least not via GET). See §8.5 on custom fields. |
| GET | `/api-reference/openapi.json` | ⚠️ | Returns Mintlify's **default "Plant Store" placeholder**, not Recotap's spec — do **not** autogen a client from it. |

### 8.4 Account object
**Push (`POST /accounts`)** — `{ "accounts": [ … ], "segmentId"?: "…" }`, each account:
```json
{
  "domain": "acme.com",        // REQUIRED — dedup key (lowercased, www-stripped)
  "name": "Acme Corp",         // REQUIRED
  "externalId": "beacon-uuid", // optional — reference only, NOT a dedup key
  "shortName": "Acme",         // optional
  "linkedinUrl": "https://…",  // optional
  "tags": ["q2-target"],       // optional
  "customFields": { … }        // optional — but every key must be PRE-DEFINED (see §8.5)
}
```
Success item: `{ "rtp_aid": "…", "domain": "…", "status": "created" }`.
Duplicate item: `{ "status": "failed", "error": "Account with domain '…' already exists. Use PUT /accounts/{rtp_aid} to update it." }`.
Summary: `{ "total", "created", "failed" }`.

**Pull (`GET /accounts`)** — each account in `data.data[]`:
```json
{
  "externalId": "…", "name": "…", "domain": "…",
  "rtp_aid": "…",
  "rtp_account_score": 0,            // 0–100; 0 == not-yet-scored on fresh push
  "rtp_journey_stage": "",           // "" until scored; one of the §8.3 labels
  "rtp_advertising_activity_score": 0,
  "rtp_website_intent_score": 0,
  "rtp_g2_intent_score": 0,
  "rtp_bombora_intent_score": 0,
  "rtp_last_account_date": "2026-06-04T17:40:21Z"
}
```

### 8.5 Gotchas (all verified — these break naive clients)
1. **`POST /accounts` is insert-only, not upsert.** Duplicate `domain` → `failed`, and the
   error hands you the `PUT /accounts/{rtp_aid}` path. Push logic = POST-then-PUT-on-conflict
   (or map `externalId`→`rtp_aid` first). *(Old docs said "upsert" — wrong.)*
2. **`POST /deals` *does* upsert** by `externalDealId`. So write semantics differ between
   accounts and deals — don't share one code path blindly.
3. **`customFields` keys must be pre-defined** or the **entire account** is rejected
   (`"Custom field key(s) not found: X"`) — not just the field. And `GET /custom-fields/account`
   404s, so definition is likely manual in the Recotap UI / a different path. **MVP: push
   `domain`/`name`/`tags` only, no custom fields** (this also drops the old plan to push
   `icp_score`/`why_now` as custom fields — that was the coupled design).
4. **Fresh accounts come back with all scores `0` and empty `journey_stage`.** Scoring is
   async/scheduled, not real-time; a fake/non-real domain may *never* score. Treat
   `0`/`""` as *not-yet-scored*, not as a real low score.
5. **`nextCursor` is non-null even on the last page** (`hasNextPage:false` with a populated
   `nextCursor`). Paginate on `hasNextPage` only, or you infinite-loop.
6. **Status vocab + summary keys differ per endpoint:** accounts `{created,failed}`,
   deals `{upserted,failed}`, activities `{created,failed,skipped}`. HTTP is always `200`
   even when every item fails — you must read `summary`/`results`.
7. **`name` is normalized (lowercased) server-side** on read-back. Don't rely on casing
   round-tripping.

---

## 9. Build plan

1. **Migration** — create `recotap_accounts`, `recotap_segments`, `recotap_sync_state`. No sales-table changes.
2. **Client** (`app/clients/recotap.py`) — auth, env base-URL, envelope normalization, pagination, retries.
3. **Service + mapping** (`app/services/recotap.py`) — Beacon shapes ↔ Recotap shapes.
4. **Endpoints** (`app/api/v1/endpoints/recotap.py`) — the §6 router, behind marketing/admin auth.
5. **Sync tasks** (`app/tasks/recotap_sync.py`) + beat (pull ~15m; push on change). Gated on `enabled`.
6. **Frontend** — the §7 ABM section + Settings.
7. **Rollout** — sandbox first (key already wired). Enable in prod only after QA passes; surface in the admin **System Health** tab (reuse the scheduled-job monitor).

**Estimated effort:** ~2.5–3 dev days (the extra half-day vs the old estimate is the
separate UI surface, offset by dropping custom fields + deal/activity push from MVP).

---

## 10. Open decisions

| # | Decision | Recommendation |
|---|---|---|
| **D1** | **Account source** — marketing-curated independent list, vs live mirror of `companies` | **Marketing-curated** (true decoupling). Offer a one-time "Import from companies" convenience, not a live sync. |
| **D2** | **Permissions** — dedicated marketing role, admin-only, or feature flag | Start with **admin + feature flag**; add a marketing role if/when RBAC supports it. |
| **D3** | **Deal/activity push** in scope? | **No for MVP** — it re-couples to sales. Phase-2 opt-in if marketing wants engagement to factor into scoring. |
| **D4** | `recotap_sync_state` **table vs `workspace_settings` JSON** | Dedicated **table** (cleaner for a standalone module; easier System-Health surfacing). |
| **D5** | `recotap_accounts.company_id` **soft-link** to `companies`? | **Yes, nullable, read-only** cross-reference only — never drive sales behavior off it. |
| **D6** | **Prod base-URL HTTPS** | Confirm with vendor (sandbox HTTPS verified; prod assumed HTTPS). |
| **D7** | **Rate limits** | Unknown — vendor lists batch sizes only. Build conservative backoff; ask vendor. |

---

## 11. Testing status (2026-06-04)

Sandbox verified end-to-end with `RECOTAP_SANDBOX_API_KEY`:
- ✅ Auth (200 valid / 401 invalid / 401 missing)
- ✅ `GET /accounts` read + keyset pagination
- ✅ `POST /accounts` create + `domain` dedup (insert-only)
- ✅ `PUT /accounts/{rtp_aid}` update
- ✅ `POST /deals` (upsert) and `POST /sales-activities` (create/skip) — *available; out of MVP scope per D3*
- ✅ `GET /journey-stages`, `/segments`, `/deal-stages`
- ❌ `/accounts/unmapped`, `/custom-fields/account` — phantom (404)

**Sandbox test data left behind** (harmless; no documented `DELETE /accounts`):
- account `rtp_aid 6a21b8855a81c58936372c2d`, domain `beacon-sbx-1780594820.example`, tag `beacon-sandbox-test`
- one test deal + one test activity linked to that domain

---

## 12. Related docs
- [`RECOTAP_MEETING_PREP.md`](./RECOTAP_MEETING_PREP.md) — historical kickoff prep + Beacon-codebase reference (§9 there is still useful). **API claims there are superseded by §8 here.**
- [`RECOTAP_CRM_INTEGRATION_BRIEF.md`](./RECOTAP_CRM_INTEGRATION_BRIEF.md) / [`RECOTAP_INTEGRATION_PROPOSAL.md`](./RECOTAP_INTEGRATION_PROPOSAL.md) — external-facing collateral; describe the older coupled model. Reconcile before re-sharing.

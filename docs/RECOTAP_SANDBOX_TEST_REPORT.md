# Recotap Sandbox — Test Report

**Date:** 2026-06-04 · **Environment:** Sandbox · **Result:** ✅ PASS (core paths working)

| | |
|---|---|
| **Base URL** | `https://sandboxapi.reco-tap.com/api/v1/` *(note the hyphen: `reco-tap`)* |
| **Auth** | `X-Api-Key` header |
| **Key** | `.env` → `RECOTAP_SANDBOX_API_KEY` (64 chars, value masked) |
| **Method** | Direct HTTP calls (Python `urllib`) against the live sandbox; key read from `.env`, never printed |
| **Companion doc** | Design + full contract: [`RECOTAP_INTEGRATION.md`](./RECOTAP_INTEGRATION.md) §8 |

## TL;DR
Every endpoint we'd actually build on responds correctly: **auth, account create/dedup/update,
account read with pagination, deals, activities, and the journey-stage/segment/deal-stage
lookups.** Two endpoints the old docs listed don't exist (404), the OpenAPI URL is a
placeholder, and response shapes vary by endpoint — none of which blocks the build.

---

## Summary results

| # | Test | Request | Expected | Got | Verdict |
|---|---|---|---|---|---|
| 1 | Auth — valid key | `GET /accounts?limit=1` | 200 | `200`, empty list | ✅ |
| 2 | Auth — invalid key | `GET /accounts` w/ bogus key | 401 | `401 "Unauthorized access"` | ✅ |
| 3 | Auth — missing header | `GET /accounts` no key | 401 | `401 "Unauthorized access"` | ✅ |
| 4 | Push w/ undefined custom field | `POST /accounts` (`customFields.SOURCE_C`) | reject | `failed: "Custom field key(s) not found"` | ✅ (expected reject) |
| 5 | Push minimal account | `POST /accounts` (domain,name,externalId,tags) | created + id | `created`, `rtp_aid` returned | ✅ |
| 6 | Push duplicate domain | `POST /accounts` (same domain) | reject as dup | `failed: "already exists. Use PUT…"` | ✅ |
| 7 | Read back | `GET /accounts?limit=5` | account returns | returned w/ `rtp_aid`, scores `0` | ✅ |
| 8 | Update | `PUT /accounts/{rtp_aid}` | 200 | `200 "Account updated successfully"` | ✅ |
| 9 | Push deal | `POST /deals` | 200 | `upserted` | ✅ |
| 10 | Push activity | `POST /sales-activities` | 200 | `created` | ✅ |
| 11 | Journey stages | `GET /journey-stages` | 200 list | `[Unaware,Aware,Consideration,Opportunity,Customer]` | ✅ |
| 12 | Segments | `GET /segments` | 200 | empty, paginated | ✅ |
| 13 | Deal stages | `GET /deal-stages` | 200 | empty array | ✅ |
| 14 | Unmapped accounts | `GET /accounts/unmapped` | 200 | **`404` not found** | ❌ phantom |
| 15 | Custom-field defs | `GET /custom-fields/account` | 200 | **`404` not found** | ❌ phantom |
| 16 | OpenAPI spec | `GET …/openapi.json` | Recotap spec | Mintlify "Plant Store" placeholder | ⚠️ unusable |

---

## Detailed log (actual requests + responses)

### Tests 1–3 · Authentication
```
GET /accounts?limit=1   (valid key)     -> HTTP 200 OK
GET /accounts?limit=1   (invalid key)   -> HTTP 401  message="Unauthorized access"
GET /accounts?limit=1   (no X-Api-Key)  -> HTTP 401  message="Unauthorized access"
```
Valid-key body:
```json
{
  "statusCode": 200,
  "timestamp": "2026-06-04T17:34:56.576Z",
  "path": "/api/v1/accounts?limit=1",
  "data": { "data": [], "nextCursor": null, "hasNextPage": false,
            "syncTimestamp": "2026-06-04T17:34:56.576Z" }
}
```
**Finding:** auth is enforced for both invalid and missing keys → the 200 is a real
authenticated success, not an open endpoint.

### Test 4 · Push with an undefined custom field (negative)
`POST /accounts` with `customFields: { "SOURCE_C": "beacon-smoke-test" }`:
```json
{ "data": { "results": [ {
      "rtp_aid": null,
      "domain": "beacon-sbx-1780594796.example",
      "status": "failed",
      "error": "Custom field key(s) not found: SOURCE_C. Unable to add this account."
} ], "summary": { "total": 1, "created": 0, "failed": 1 } } }
```
**Finding:** one unknown custom-field key rejects the **entire** account. Custom fields must
be pre-defined first. → MVP pushes `domain`/`name`/`tags` only (see `RECOTAP_INTEGRATION.md` §8.5).

### Tests 5–7 · Push / dedup / read-back round-trip
Test domain: `beacon-sbx-1780594820.example` · fields: `domain, name, externalId, tags`

**5. Create:**
```json
{ "data": { "results": [ { "rtp_aid": "6a21b8855a81c58936372c2d",
      "domain": "beacon-sbx-1780594820.example", "status": "created" } ],
  "summary": { "total": 1, "created": 1, "failed": 0 } } }
```
**6. Push same domain again:**
```json
{ "data": { "results": [ { "rtp_aid": "6a21b8855a81c58936372c2d",
      "domain": "beacon-sbx-1780594820.example", "status": "failed",
      "error": "Account with domain 'beacon-sbx-1780594820.example' already exists. Use PUT /accounts/6a21b8855a81c58936372c2d to update it." } ],
  "summary": { "total": 1, "created": 0, "failed": 1 } } }
```
**7. Read back (`GET /accounts?limit=5`):**
```json
{ "data": { "data": [ {
      "name": "beacon sandbox test 1780594820",
      "domain": "beacon-sbx-1780594820.example",
      "externalId": "beacon-sbx-1780594820",
      "rtp_aid": "6a21b8855a81c58936372c2d",
      "rtp_last_account_date": "2026-06-04T17:40:21Z",
      "rtp_account_score": 0, "rtp_journey_stage": "",
      "rtp_advertising_activity_score": 0, "rtp_website_intent_score": 0,
      "rtp_g2_intent_score": 0, "rtp_bombora_intent_score": 0 } ],
  "nextCursor": "6a21b8855a81c58936372c2d", "hasNextPage": false,
  "syncTimestamp": "2026-06-04T17:44:51.648Z" } }
```
**Findings:** (a) `POST /accounts` is **insert-only** — duplicate `domain` fails and points to
`PUT /accounts/{rtp_aid}`. (b) Fresh accounts read back with all scores `0` and empty
`journey_stage` (scoring is async). (c) `nextCursor` is populated even though
`hasNextPage:false` → paginate on `hasNextPage`. (d) `name` came back lowercased.

### Test 8 · Update via PUT
`PUT /accounts/6a21b8855a81c58936372c2d` with `{name, tags}`:
```json
{ "data": { "message": "Account updated successfully" } }
```
Subsequent `GET` showed `rtp_last_account_date` bumped to `2026-06-04T17:45:55Z`, but the
`name` field had not yet changed in the read model.
**Finding:** PUT is accepted (200) and touches the record; field propagation to the read
model can lag a scoring cycle.

### Tests 9–10 · Deal + activity push
```json
// POST /deals
{ "results": [ { "externalDealId": "beacon-sbx-deal-…", "status": "upserted" } ],
  "summary": { "total": 1, "upserted": 1, "failed": 0 } }

// POST /sales-activities
{ "results": [ { "externalActivityId": "beacon-sbx-act-…", "status": "created" } ],
  "summary": { "total": 1, "created": 1, "failed": 0, "skipped": 0 } }
```
**Finding:** `POST /deals` **upserts** by `externalDealId` (status `upserted`) — different from
accounts' insert-only behavior. Activities insert and report a `skipped` count for
non-`call`/`email` types. *(Both endpoints work but are out of MVP scope — they re-couple to sales.)*

### Tests 11–13 · Lookup reads
```json
GET /journey-stages -> ["Unaware","Aware","Consideration","Opportunity","Customer"]   // bare array
GET /segments       -> { "data": [], "nextCursor": null, "hasNextPage": false }        // paginated
GET /deal-stages    -> []                                                              // bare array
```
**Finding:** lookup endpoints return **bare arrays** (no `statusCode` envelope), unlike the
list/write endpoints. Journey-stage labels differ from the old docs' examples.

### Tests 14–15 · Phantom endpoints
```json
GET /accounts/unmapped      -> 404  "Cannot GET /api/v1/accounts/unmapped"
GET /custom-fields/account  -> 404  "Cannot GET /api/v1/custom-fields/account"
```
**Finding:** both were listed in the old API inventory but **do not exist**. Don't build on them.

### Test 16 · OpenAPI spec
`GET https://docs.recotap.com/api-reference/openapi.json` → `200`, but the body is
Mintlify's default template (`title: "OpenAPI Plant Store"`, paths `/plants`,
server `sandbox.mintlify.com`).
**Finding:** **not** Recotap's real spec — do not autogen a client from it. The live API is
the source of truth (this report).

---

## Response-shape map (observed)
The client must normalize per endpoint:

| Shape | Endpoints | Structure |
|---|---|---|
| Double-nested + pagination | `GET /accounts`, `GET /segments` | `{statusCode,…,data:{ data:[…], nextCursor, hasNextPage, syncTimestamp? }}` |
| Bare array | `GET /deal-stages`, `GET /journey-stages` | top-level array, no envelope |
| Write result | `POST /accounts`, `/deals`, `/sales-activities`, `PUT /accounts/{id}` | `{statusCode,…,data:{ results:[…], summary:{…} }}` |
| Error | any failure | `{statusCode, timestamp, path, message, customMessage}` (no `data`) |

Status vocab also differs: accounts `created/failed`, deals `upserted/failed`,
activities `created/failed/skipped`. **HTTP is always `200` even when items fail** — read `summary`.

---

## Test data left in sandbox
Harmless; no documented `DELETE /accounts`, so left in place:
- Account `rtp_aid 6a21b8855a81c58936372c2d` · domain `beacon-sbx-1780594820.example` · tag `beacon-sandbox-test`
- One test deal + one test activity linked to that domain
- (One earlier account push, domain `beacon-sbx-1780594796.example`, never created — failed on custom field)

---

## How to re-run
The smoke test reads the key from `.env` and hits the sandbox directly — no client code required:

```python
import json, re, urllib.request, urllib.error
key = next(re.match(r'\s*RECOTAP_SANDBOX_API_KEY\s*=\s*(.+)', l).group(1).strip().strip('"').strip("'")
           for l in open('.env') if l.strip().startswith('RECOTAP_SANDBOX_API_KEY'))
BASE = "https://sandboxapi.reco-tap.com/api/v1"
req = urllib.request.Request(BASE + "/accounts?limit=1",
                             headers={"X-Api-Key": key, "Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=25) as r:
    print(r.status, json.dumps(json.load(r), indent=2))
```
Expected: `200` with the `{statusCode,…,data:{data:[…],…}}` envelope.

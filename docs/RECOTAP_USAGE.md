# How Beacon Uses Recotap

> Practical, current-state guide to the Recotap ABM integration as it actually
> runs (not the original decoupled-module proposal in `RECOTAP_INTEGRATION.md`,
> which was superseded — we surface Recotap signals **inside Account Sourcing**).

## 1. What Recotap gives us

Recotap is an ABM (account-based marketing) intelligence provider. For an
account (keyed by **web domain**) it returns intent-derived signals:

- **Journey stage** — `Unaware → Aware → Consideration → Opportunity → Customer`
  (low → high intent). Computed by Recotap from *advertising / website / G2 /
  Bombora* intent — it does **not** know about our CRM deals.
- **Account score** (`rtp_account_score`, nominally 0–100; observed values can
  exceed 100).
- **Intent sub-scores** — advertising activity, website intent, G2 intent,
  Bombora intent.
- We derive **engagement** (`Hot ≥72 / Warm ≥45 / Cold`) from the score, and a
  seed path adds an **ICP fit** label.

## 2. Storage (decoupled by design)

One row per account in **`recotap_accounts`** (migration `088`), joined to
`companies` by **domain** — there are deliberately **no `rtp_*` columns on
`companies`**. Model: `app/models/recotap.py` (`RecotapAccount`).

- `domain` (lowercased) — join + dedup key. Recotap can return duplicate-domain
  rows; we upsert by domain (last-write-wins).
- `company_id` — FK to `companies` with `ON DELETE SET NULL` (migration `091`);
  re-links by domain on the next pull.
- `journey_stage`, `score`, `engagement`, `icp_fit`, the four intent sub-scores,
  `hq_location`, `tags`, `raw` (full payload).
- `source`: `recotap` (live pull) / `seed` (local mock) / `pending`.
- Push bookkeeping: `rtp_aid` (Recotap PK), `pushed_at`, `push_status`.

## 3. Configuration

| Setting | Purpose |
|---|---|
| `RECOTAP_ENVIRONMENT` | `sandbox` \| `prod` — selects key **and** base URL |
| `RECOTAP_SANDBOX_API_KEY` | sandbox key; base `https://sandboxapi.reco-tap.com/api/v1` (note the hyphen) |
| `RECOTAP_PROD_API_KEY` | prod key; base `https://eapi.recotap.com/...`. Legacy name `RECOTAP_API_KEY` still accepted via `AliasChoices` |

- Client: `app/clients/recotap.py` (`X-Api-Key` header; `.strip()`s the key so
  stray whitespace in `.env`/secret is tolerated). Empty key = **inert/no-op**,
  not fatal.
- Helm: key → Secret (`secrets.recotapProdApiKey`); `RECOTAP_ENVIRONMENT` →
  ConfigMap (defaults to `prod`). **Cluster reality:** the live deployments read
  env only from `gtm-backend-secret` (no ConfigMap), so set env via
  `kubectl patch secret gtm-backend-secret`.
- ⚠️ **Security:** the prod key was once leaked into a session transcript and
  **must be rotated** with Recotap. Never print the key; the client tolerates
  whatever whitespace, so rotation just needs a clean re-inject.

## 4. Data flows

### 4.1 Pull (Recotap → Beacon) — `recotap.pull_into_db`
`GET /accounts` → upsert `recotap_accounts` by domain, mapping
`rtp_journey_stage`/`rtp_account_score`/intent sub-scores; engagement derived
from score. Marks `source="recotap"`, sets `pulled_at`. N+1 on company lookup
(~1 SELECT/account) — fine at current scale.

### 4.2 Seed (mock) — `recotap.seed_mock_signals`
Deterministic mock signals per sourced company (md5-stable), **sandbox only**
(prod has real data; seeding would pollute it). Never overwrites live-pulled
rows unless `overwrite=True`.

### 4.3 Push (Beacon → Recotap) — `recotap.push_crm_status`
Recotap **won't let us set its computed Journey Stage**, and rejects undefined
custom-field keys — so we surface CRM status as account **tags**. The most
advanced deal stage maps to one tag:

`closed_won → CRM: Customer · msa_review/commercial_negotiation → CRM: Negotiation · poc_* → CRM: POC · demo_* → CRM: Demo · qualified_lead → CRM: Qualified`

`POST /accounts` is insert-only; on "already exists" we capture the `rtp_aid`
and `PUT` the tags. **Domain guard** (`is_pushable_domain`): placeholder/import
domains (`*.unknown`, bare numeric IDs) are skipped so we never create junk
accounts in Recotap's tenant. No scheduler yet — push is on-demand.

## 5. Surfacing in Account Sourcing

Signals live where the team works (not a separate module):
- List/detail endpoints enrich `CompanyRead.recotap` by domain
  (`recotap.signals_by_domain`).
- **Buying Journey** band headlines the accounts view (stage tiles with counts +
  engagement chips + a Sync button).
- Filter bar: **Journey Stage** multi-select (`?journey_stage=` — comma-sep, or
  `not_scored`).
- Rows show journey/score/engagement badges; the detail page has a Recotap
  signals panel.
- Refresh/seed: `POST /api/v1/account-sourcing/recotap/refresh` (seed defaults
  ON for sandbox, OFF for prod). Counts: `GET .../recotap/summary`.

## 6. Current state & gotchas (2026-06)

- **Prod `recotap_accounts` is currently EMPTY** — no live pull has been
  persisted on prod, so the Buying Journey band has no Recotap data there. (A
  prior localhost/staging pull saw 659 rows → 437 distinct domains, mostly
  `Unaware`; ~5% joined to Beacon companies by domain.)
- Domain overlap between Beacon CRM accounts and Recotap-tracked domains is low
  (~5% on staging) — signals only appear on accounts whose domain Recotap tracks.
- No Celery auto-sync; pull and push are manual/on-demand.
- `_STAGE_ORDER` in `recotap.py` historically mis-ranked `qualified_lead` (see §7).

## 7. CRM-derived Journey Stage (shipped)

**Why:** Recotap's journey stage is intent-only and (on prod) empty. The CRM
knows the *real* position once a deal exists. So `recotap.sync_crm_journey`
derives a journey stage from each company's **most-advanced deal stage** and
writes it onto the `recotap_accounts` row's `journey_stage` (marking
`source="crm"`), creating a row by domain when none exists. It runs **last** in
`POST /recotap/refresh` (after pull/seed) so it wins over Recotap's intent stage
for any account with a live deal. The band, badges, and `?journey_stage=` filter
all read `journey_stage`, so they reflect it with no other changes; the mock
seeder and a later pull won't clobber a `source="crm"` row. A company that drops
to a terminal stage has its CRM-derived stage cleared.

Canonical pipeline order (most-advanced wins): `reprospect → demo_scheduled →
demo_done → qualified_lead → poc_agreed → poc_wip → poc_done →
commercial_negotiation → workshop → msa_review → closed_won`.

**Confirmed mapping** (`crm_journey_stage`):

| Beacon deal stage | Journey stage |
|---|---|
| demo_scheduled, demo_done, qualified_lead | **Aware** |
| poc_agreed, poc_wip, poc_done | **Consideration** |
| commercial_negotiation, workshop, msa_review | **Opportunity** |
| closed_won | **Customer** |
| reprospect / cold / nurture / closed_lost / not_a_fit / churned / on_hold / no deal | *(no override — Recotap's stage, if any, stays)* |

Projected on current prod deals: **Aware 33, Consideration 16, Opportunity 8,
Customer 21** — 78 accounts gain a deal-derived journey stage the empty Recotap
pull never provided.

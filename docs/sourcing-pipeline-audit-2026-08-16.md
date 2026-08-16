# Sourcing / Prospecting / Pipeline / Analytics audit — 2026-08-16

Follow-up to `prod-remediation-2026-08-15.md`, focused on Account Sourcing,
Prospecting, Deal Pipeline, and Sales Analytics. Prod data was measured first
(read-only), then every defect was traced to its code path and fixed.

## What prod data showed (before repair)

| Problem | Scale |
|---|---|
| Prospects visible under disabled (`not_a_fit`/`dnd`) accounts | 743 (608 with active sequences, 126 touched in last 30d) |
| Prospect↔account SDR divergence | 95 never inherited + 128 reverse + 206 hard conflicts |
| Accounts with fabricated `*.unknown` domains | 194 (40 with contacts) |
| Accounts whose domain contradicts their own contacts | 105 |
| True misattached-contact candidates | 193 |
| Prospects with no account (all actively worked) | 166 (74 auto-relinkable) |
| Deals with stale `days_in_stage` | 360 (turned out to be the 361 closed-stage deals the nightly refresher skips) |
| `agency@beacon.li` pseudo-user | owns 99 accounts + 845 prospects (needs a human decision) |

`new_stage_18` from the 08-15 report is a **valid configured stage**
("Marketing Lead (MQL)") — B4 was a false alarm, no deal repair needed.

## The one architectural rule behind the fixes

Every defect was a fact stored in two places with no owner. The fixes give each
fact one home: account-disable state lives on `companies.account_status` and is
ENFORCED BY QUERY everywhere (`INACTIVE_ACCOUNT_STATUSES` = not_a_fit + dnd);
assignment flows now cascade/report instead of silently diverging; deal-stage
validity lives in workspace settings and every write path validates against it.

## Backend fixes (all deployed together)

**Disable semantics** — prospects of not_a_fit/dnd accounts leave the
prospecting list/count/export/KPIs (one shared gate in
`ContactRepository.list_with_company_name`), reminder jobs skip them, all four
outreach-launch paths refuse them with explicit counts, and flipping the status
pauses the account's Instantly campaigns (shared-campaign-safe,
`apply_account_disable_effects`). Rows are never deleted; re-enabling restores
everything. Owners can now OPEN and FIND their parked accounts (list filter,
detail guard, global search) — previously re-enabling was admin-only by
accident.

**Assignment** — cascades return `CascadeResult` and every caller surfaces
"N prospects kept their individual owner" instead of silently skipping;
AE cascade is a shared helper (was copy-pasted, drifting); bulk contact
assignment now applies the same SDR progress-reset as single (parity bug);
contact-level assignment backfills an EMPTY company slot (25 prod accounts were
invisible to the SDR working their prospects); the import "mirror" rule that
fabricated AE ownership is removed; claim-on-edit inherits the account's owner
instead of stealing the slot.

**Mapping** — `_clean_domain` fully normalizes and refuses aggregator/free-mail
domains as company keys; the orphan auto-mapper refuses those domains and
name-only shadow absorption now requires domain agreement;
`get_by_normalized_name` returns None on ambiguity and rejects candidates whose
real domain contradicts the incoming one; a real domain can never be replaced
by a different real domain (`merge_company_from_upload`); importers re-link
unmapped rows and REPORT cross-account conflicts (workbook → batch error_log,
CSV → `conflict_count`/`conflict_details` in the response + UI panel); name
dedupe is case-insensitive and refuses to merge rows with contradicting emails;
seven case-sensitive email/domain lookups fixed. The always-on hygiene filter
no longer hides domain-mismatched prospects — they get an
`account_domain_mismatch` badge instead (hiding them was why SDRs "missed"
prospects).

**Pipeline** — every `deals.stage` write validates against configured stages
via `resolve_valid_deal_stage` (zippy, system tasks, t_stage_apply, ClickUp,
convert); `workshop` aliases to `msa_review`; settings refuses to delete a
stage that still holds deals (409 with counts); new settings-created stages get
readable label-derived ids instead of `new_stage_NN`; stage writes and their
history/activity rows commit in ONE transaction (previously the deal row
committed first); ClickUp re-import writes stage history and stops resetting
`stage_entered_at` for unchanged stages; the stale-deal exclusion in
notifications and the health/reconcile tasks use the settings closed group (3
disagreeing hardcoded sets removed); deals list has an id tiebreaker;
`stakeholder_count` is maintained by all three link writers; dead
`Deal.owner_id` removed from API schemas.

**Analytics** — scorecard `reply_rate` counts what the system actually writes
(`event_type=reply_received`; the old filter matched a value nothing ever
wrote, so it was structurally 0); `emails_sent` includes rep-attributed Gmail
sends and excludes webhook replies/opens (wrong in both directions before);
`closed_won_value` dedupes re-won deals; `stuck_deals` measures from
`stage_entered_at` like the board; dashboard "days in stage" computed live;
deal scans filter `pipeline_type='deal'` like every other surface; "Last N
days" is midnight-aligned (matches explicit ranges); forecast horizon capped at
365d (was 36,500 on "All time"); `today` is UTC; Account Sourcing summary
counts contacts live instead of stale JSON.

**Latent NULL-JSONB trap (4 sites)** — `NOT (jsonb_col @> x)` is NULL when the
column is NULL, silently dropping rows from prospect and account lists. Prod
currently has 0 NULL rows, but the fixture-based smoke test hit it immediately.
All negated `contains` filters are now NULL-safe.

**Frontend** — account-status + "Check mapping" badges on prospect rows
(desktop + mobile); Account-status filter (URL-persisted, `acct=`); explicit
disable/re-enable toasts; import-conflict panel; Pipeline forecast panel reads
the FILTERED board; drag-drop and drawer writes surface errors (were
try/finally with no catch); drawer stage menu offers only configured stages;
close-date commits on blur (was PATCH-per-keystroke); stage-settings fallback
list matches real backend stages (was discovery/evaluation/proposal/
negotiation — none exist).

## One-time prod repair (user-run)

`scripts/prod-repair/sourcing-repair-2026-08-16.sh` — review by default,
`APPLY=yes` to write. Applies: ghost-task dismissal (B1, ~13), placeholder
domain adoption (unanimous evidence only), orphan relink (~74+), SDR/AE
gap-fill from accounts (~95+), ownerless-company backfill (~25). Exports CSVs
for the decisions that stay manual: 206 SDR conflicts, 193 misattached
candidates, real-domain corrections, same-name/different-domain merges.

## Deliberately NOT automated

- `agency@beacon.li` (99 accounts / 845 prospects) — team-queue vs real-rep
  decision.
- Real-domain corrections and company merges (IRIS-style same-name different
  companies) — human judgment, CSVs provided.
- Re-enabling an account does NOT auto-resume paused Instantly campaigns.

## Roadmap — updated after the second release (same day)

DONE in v0.260816b (see the follow-up sections in the release commit):
metric-definition unification (shared `app/services/metric_definitions.py`
module both engines consume — emails, replies, call units, meeting dedupe/
attribution/happened-inference), workspace timezone driving every analytics/
scorecard window (`workspace_timezone` setting, IANA, DST-correct), soft-delete
for companies + deals (migration 114 `deleted_at`; history and past scorecards
never rewrite; delete = leave current-state surfaces), company merge + alias
domains (migration 114 `additional_domains`; merge endpoint + admin UI; every
domain matcher and the mismatch badge honor aliases), and filter-wide bulk
assign on Prospecting ("Assign all N matching", stale-count 409 guard, same
handoff/backfill semantics as id-based assignment).

Remaining:

1. Aircall reconciliation poller (webhook gaps still silently lower call
   counts) — carried from 08-15; now the top item.
2. Literal single-engine merge: definitions are unified, but the scorecard and
   dashboard still run separate query engines (per-rep vs bulk). Collapse when
   convenient; the shared dictionary keeps them agreeing meanwhile.
3. Point-in-time deal ownership (attribution currently follows the CURRENT
   assignee in BOTH engines — consistent, but reassignment moves history).
4. Trash/restore UI for soft-deleted companies/deals (restore is a manual
   UPDATE today).
5. Business-day stuck thresholds (currently calendar days, ~40% over-flagging).

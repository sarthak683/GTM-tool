# Beacon CRM Data Models

Prepared for Recotap  
Share by: April 22, 2026

## Purpose

This document describes the core Beacon CRM data models that matter for integration:

- Accounts
- Deals / Pipeline
- Sales Activities

These models map closely to a standard CRM structure and should be enough for Recotap to plan the first version of the bi-directional sync APIs.

## 1. Accounts

In Beacon, the account object is `Company`.

An account represents the customer or target company being worked by the sales team.

### Account model intent

This object stores:

- the company identity
- firmographic context
- ownership and assignment
- fit / ICP context
- account-level enrichment and intent signals

### Primary account fields

| Field | Type | Required | Description |
|---|---|---:|---|
| `id` | UUID | Yes | Internal Beacon account ID |
| `name` | string | Yes | Company name |
| `domain` | string | Yes | Company domain / website |
| `industry` | string \| null | No | Industry label |
| `vertical` | string \| null | No | More specific segment / vertical |
| `employee_count` | integer \| null | No | Estimated employee count |
| `arr_estimate` | float \| null | No | Estimated annual revenue when available |
| `funding_stage` | string \| null | No | Funding or maturity stage |
| `region` | string \| null | No | Region, e.g. `US`, `EU`, `APAC` |
| `headquarters` | string \| null | No | HQ location |
| `has_dap` | boolean | Yes | Whether they use a digital adoption platform |
| `dap_tool` | string \| null | No | DAP vendor if known |
| `tech_stack` | JSON \| null | No | Structured technology / tools context |
| `icp_score` | integer \| null | No | Beacon fit score |
| `icp_tier` | string \| null | No | Beacon ICP tier |
| `assigned_to_id` | UUID \| null | No | Primary owner in Beacon |
| `assigned_rep` | string \| null | No | Rep identifier from imported/source data |
| `assigned_rep_email` | string \| null | No | Rep email |
| `assigned_rep_name` | string \| null | No | Rep full name |
| `sdr_id` | UUID \| null | No | SDR owner if applicable |
| `sdr_email` | string \| null | No | SDR email |
| `sdr_name` | string \| null | No | SDR name |
| `outreach_status` | string \| null | No | Account-level outreach state |
| `disposition` | string \| null | No | Current disposition |
| `description` | text \| null | No | Company description |
| `intent_signals` | JSON \| null | No | Stored intent / buying signals |
| `why_now` | text \| null | No | Why timing matters now |
| `beacon_angle` | text \| null | No | Suggested Beacon value angle |
| `account_thesis` | text \| null | No | Account strategy / thesis |
| `recommended_outreach_lane` | string \| null | No | Suggested outreach motion |
| `enrichment_sources` | JSON \| null | No | Sources used for enrichment |
| `enrichment_cache` | JSON \| null | No | Cached external or AI-enriched payloads |
| `enriched_at` | datetime \| null | No | Last enrichment timestamp |
| `created_at` | datetime | Yes | Record creation time |
| `updated_at` | datetime | Yes | Record last update time |

### Notes for integration

- `id` should be treated as the Beacon primary key.
- `domain` is the strongest natural matching key outside Beacon.
- `tech_stack`, `intent_signals`, `enrichment_sources`, and `enrichment_cache` are JSON fields and may contain nested structures.
- ownership can appear in both internal Beacon IDs and imported rep labels.

### Example account payload

```json
{
  "id": "8d58b2d8-6b70-44fc-9b4c-9643af2bc2b3",
  "name": "Procore",
  "domain": "procore.com",
  "industry": "Construction Software",
  "vertical": "Construction",
  "employee_count": 14000,
  "region": "US",
  "icp_score": 81,
  "icp_tier": "hot",
  "assigned_to_id": "0bc93244-fb6b-4c2c-8332-b1e90cc5d44f",
  "assigned_rep_email": "rep@beacon.li",
  "outreach_status": "active",
  "why_now": "Expansion and implementation complexity are rising.",
  "beacon_angle": "Reduce rollout drag and improve time-to-value.",
  "created_at": "2026-04-21T12:00:00Z",
  "updated_at": "2026-04-21T12:00:00Z"
}
```

## 2. Deals / Pipeline

In Beacon, the deal / pipeline object is `Deal`.

A deal represents an active or closed revenue opportunity linked to an account.

### Deal model intent

This object stores:

- pipeline stage
- owner
- commercial value
- timeline
- health
- qualification context
- next-step context

### Primary deal fields

| Field | Type | Required | Description |
|---|---|---:|---|
| `id` | UUID | Yes | Internal Beacon deal ID |
| `company_id` | UUID \| null | No | Parent account ID |
| `name` | string | Yes | Deal name |
| `pipeline_type` | string | Yes | Usually `deal` |
| `stage` | string | Yes | Current pipeline stage |
| `priority` | string | Yes | Priority label |
| `department` | string \| null | No | Department / business unit |
| `geography` | string \| null | No | Geography |
| `source` | string \| null | No | Deal source |
| `close_date_est` | date \| null | No | Estimated close date |
| `health` | string | Yes | Health label |
| `health_score` | integer \| null | No | Numeric health score |
| `days_in_stage` | integer | Yes | Number of days in current stage |
| `stage_entered_at` | datetime \| null | No | When the current stage began |
| `last_activity_at` | datetime \| null | No | Last related activity time |
| `stakeholder_count` | integer | Yes | Number of linked stakeholders |
| `owner_id` | string \| null | No | Imported owner identifier |
| `assigned_to_id` | UUID \| null | No | Beacon owner user ID |
| `email_cc_alias` | string \| null | No | Alias used for email threading |
| `external_source` | string \| null | No | Source system label |
| `external_source_id` | string \| null | No | Source-system record ID |
| `value` | decimal \| null | No | Deal value |
| `qualification` | JSON \| null | No | Qualification payload, including MEDDPICC |
| `tags` | JSON array | Yes | Flexible labels |
| `description` | text \| null | No | Freeform description |
| `next_step` | text \| null | No | Current next step |
| `commit_to_deal` | boolean | Yes | Internal commit flag |
| `created_at` | datetime | Yes | Record creation time |
| `updated_at` | datetime | Yes | Record last update time |

### Current Beacon deal stages

Beacon uses the following pipeline stages:

- `reprospect`
- `demo_scheduled`
- `demo_done`
- `qualified_lead`
- `poc_agreed`
- `poc_wip`
- `poc_done`
- `commercial_negotiation`
- `msa_review`
- `workshop`
- `closed_won`
- `closed_lost`
- `not_a_fit`
- `cold`
- `on_hold`
- `nurture`
- `churned`
- `closed`

### Qualification payload

`qualification` is a JSON field. It may include MEDDPICC data and other qualification detail.

Examples of nested qualification data:

- metrics
- economic buyer
- decision criteria
- paper process
- identified pain
- champion
- competition

### Notes for integration

- `stage` is the most important workflow field.
- `value`, `close_date_est`, `health`, and `qualification` are key reporting and prioritization fields.
- `external_source` and `external_source_id` are important for deduplication / source traceability.
- `company_id` links the deal to the account object.

### Example deal payload

```json
{
  "id": "f295741e-41be-40cc-a84e-836af54b1d71",
  "company_id": "8d58b2d8-6b70-44fc-9b4c-9643af2bc2b3",
  "name": "Procore - Beacon rollout motion",
  "pipeline_type": "deal",
  "stage": "qualified_lead",
  "priority": "high",
  "close_date_est": "2026-06-30",
  "health": "green",
  "health_score": 78,
  "days_in_stage": 12,
  "assigned_to_id": "0bc93244-fb6b-4c2c-8332-b1e90cc5d44f",
  "value": 180000,
  "next_step": "ROI review with economic buyer",
  "qualification": {
    "meddpicc": {
      "metrics": 2,
      "economic_buyer": 1,
      "decision_criteria": 2
    }
  },
  "created_at": "2026-04-21T12:00:00Z",
  "updated_at": "2026-04-21T12:00:00Z"
}
```

## 3. Sales Activities

In Beacon, the sales activity object is `Activity`.

An activity represents an email, call, meeting, note, transcript, or other logged customer-facing interaction.

### Activity model intent

This object stores:

- interaction type
- related deal / contact
- source system
- content or AI summary
- channel-specific metadata for calls and emails

### Primary activity fields

| Field | Type | Required | Description |
|---|---|---:|---|
| `id` | UUID | Yes | Internal Beacon activity ID |
| `deal_id` | UUID \| null | No | Related deal |
| `contact_id` | UUID \| null | No | Related contact |
| `type` | string | Yes | Activity type |
| `source` | string \| null | No | Source system or producer |
| `medium` | string \| null | No | Communication channel |
| `content` | text \| null | No | Raw content or note |
| `ai_summary` | text \| null | No | AI summary of the activity |
| `event_metadata` | JSON \| null | No | Source-specific metadata |
| `external_source` | string \| null | No | External system name |
| `external_source_id` | string \| null | No | External record ID |
| `created_at` | datetime | Yes | Activity timestamp |
| `created_by_id` | UUID \| null | No | Beacon user who created it |
| `call_id` | string \| null | No | Call identifier |
| `call_duration` | integer \| null | No | Call duration in seconds |
| `call_outcome` | string \| null | No | Call result |
| `recording_url` | string \| null | No | Call recording link |
| `aircall_user_name` | string \| null | No | Aircall agent name |
| `email_message_id` | string \| null | No | Email dedupe key |
| `email_subject` | string \| null | No | Email subject |
| `email_from` | string \| null | No | Email sender |
| `email_to` | string \| null | No | Email recipient list |
| `email_cc` | string \| null | No | Email CC list |

### Current Beacon activity types

Observed / intended activity types include:

- `email`
- `call`
- `meeting`
- `note`
- `transcript`
- `visit`

### Common mediums

Observed / intended mediums include:

- `email`
- `call`
- `linkedin`
- `whatsapp`
- `in_person`
- `sms`
- `other`

### Notes for integration

- `type` is the primary activity classification.
- `medium` provides the channel.
- `deal_id` and `contact_id` may be null depending on how the activity entered Beacon.
- `external_source` and `external_source_id` are important for dedupe and sync replay.
- calls and emails each have additional field sets beyond the common activity envelope.

### Example activity payload

```json
{
  "id": "537ff8a7-e73f-48db-9761-b1f9d2f3b9d0",
  "deal_id": "f295741e-41be-40cc-a84e-836af54b1d71",
  "contact_id": "4ba54d61-71dc-4500-a4fe-68d4724ef11a",
  "type": "email",
  "source": "instantly",
  "medium": "email",
  "content": "Following up on the rollout pain we discussed.",
  "ai_summary": "Rep anchored on implementation drag and asked for ROI review.",
  "email_subject": "Reducing rollout time for Procore",
  "email_from": "rep@beacon.li",
  "email_to": "buyer@company.com",
  "created_at": "2026-04-21T12:00:00Z"
}
```

## Relationship Summary

The core relationship model is:

- One account can have many deals
- One deal can have many activities
- One activity can optionally be linked to a contact

In simplified form:

```text
Company (Account)
  └─ Deal
       └─ Activity
            └─ Contact (optional link)
```

## Integration Guidance

For planning the sync APIs, the recommended object priority is:

1. Accounts
2. Deals / Pipeline
3. Sales Activities

Recommended matching keys:

- Beacon primary key: `id`
- external system key when available: `external_source_id`
- natural account matching helper: `domain`

Recommended timestamp for incremental sync:

- `updated_at` for accounts and deals
- `created_at` for activities

## Summary

Beacon’s CRM model is already structured in a standard way:

- `Company` for accounts
- `Deal` for opportunities / pipeline
- `Activity` for sales interactions

This should make it straightforward for Recotap to design sync APIs for:

- pushing Beacon CRM records into Recotap
- returning engagement and intent data back into Beacon later


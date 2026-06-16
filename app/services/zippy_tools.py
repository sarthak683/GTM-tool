"""Zippy's tool catalog — the schemas we hand Claude + the local executors.

Keeping the tool-use contract centralized means the agent loop in
``zippy_agent.py`` stays small: it just feeds this catalog to Claude and
dispatches ``tool_use`` blocks to the ``execute_tool`` entry point here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.knowledge_search import KnowledgeSnippet, search_knowledge
from app.services.zippy_docs import GeneratedDocument
from app.services.zippy_docs.generic import GenericDocInput
from app.services.zippy_docs.generic import generate as generate_generic
from app.services.zippy_docs.mom import MOMInput, MOMTemplateUnavailable
from app.services.zippy_docs.mom import generate as generate_mom
from app.services.zippy_docs.mom import inspect_mom_template
from app.services.zippy_docs.nda import NDAInput
from app.services.zippy_docs.nda import generate as generate_nda
from app.services.zippy_docs.nda import inspect_template as inspect_nda_template
from app.services.zippy_docs.proposal import ProposalInput
from app.services.zippy_docs.proposal import generate as generate_proposal
from app.services.zippy_docs.proposal import inspect_proposal_template

logger = logging.getLogger(__name__)


# Anthropic tool-use schemas. Keep descriptions tight — they're Claude's only
# signal for when to call each tool.
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "search_knowledge_base",
        "description": (
            "Search the user's Google Drive + Beacon shared drive for snippets relevant "
            "to a question. Use whenever the user asks about a concept, prior client, "
            "playbook, number, or doc that might live in their files. Returns a list of "
            "snippets with source names and Drive links — always cite them in your answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query (natural language).",
                },
                "top_k": {
                    "type": "integer",
                    "description": "How many snippets to return. Default 6, max 12.",
                    "default": 6,
                },
                "source_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional: restrict the search to specific Drive file IDs. "
                        "Useful when the user references '@file' in the UI."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "inspect_mom_template",
        "description": (
            "Open Beacon's official MOM template from Drive and report how "
            "many rewritable content sections it contains. ALWAYS call this "
            "FIRST before `generate_mom` to confirm the template is reachable. "
            "Do NOT search other docs for MOM content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "generate_mom",
        "description": (
            "Rewrite Beacon's official MOM template in place using the user's "
            "transcript and produce a .docx file. The tool extracts every "
            "paragraph from the template and has Claude rewrite the non-"
            "structural content — there are no placeholders to fill. ONLY call "
            "this after `inspect_mom_template`. Never add sections not in the "
            "template."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "client_name": {"type": "string"},
                "meeting_date": {
                    "type": "string",
                    "description": "Date as a human string, e.g. '19 April 2026'. Optional.",
                },
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of attendee names, optional.",
                },
                "transcript": {
                    "type": "string",
                    "description": "Meeting transcript if available.",
                },
                "context_notes": {
                    "type": "string",
                    "description": (
                        "Free-text notes from the user — agenda bullets, takeaways, "
                        "anything to include if no transcript is available."
                    ),
                },
                "format_type": {
                    "type": "string",
                    "enum": ["long", "short"],
                    "description": (
                        "'long' = detailed MOM with all sub-sections (default). "
                        "'short' = key highlights only, concise."
                    ),
                },
                "collateral": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Collateral items to include, each as "
                        "'Label : Name | url'. You determine these from the "
                        "collateral selection rules in your system prompt."
                    ),
                },
            },
            "required": ["client_name"],
        },
    },
    {
        "name": "inspect_nda_template",
        "description": (
            "Open Beacon's official NDA template for the given jurisdiction "
            "and report how many rewritable content sections it contains. "
            "ALWAYS call this FIRST before `generate_nda` to confirm the "
            "template is reachable. Do not search other docs or invent "
            "clauses — use ONLY this template."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "jurisdiction": {
                    "type": "string",
                    "enum": ["india", "us", "singapore"],
                },
            },
            "required": ["jurisdiction"],
        },
    },
    {
        "name": "generate_nda",
        "description": (
            "Draft a Non-Disclosure Agreement by rewriting Beacon's official NDA "
            "template (stored in the workspace Drive folder) with the "
            "counterparty name and jurisdiction-specific details. The template "
            "is rewritten in place — structural headings are preserved, "
            "content blocks are rewritten from the user's inputs. "
            "Only `jurisdiction` is REQUIRED (it picks which template to load). "
            "Every other field is OPTIONAL — pass ONLY the values the user "
            "explicitly supplied. Do NOT invent or default any value. Missing "
            "fields are left blank by the rewriter so reviewers can see what "
            "still needs filling."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "jurisdiction": {
                    "type": "string",
                    "enum": ["india", "us", "singapore"],
                },
                "fills": {
                    "type": "object",
                    "description": (
                        "Free-form dict of extra details to pass to the "
                        "rewriter (e.g. registered office, PAN, authorised "
                        "signatory). The rewriter will use these verbatim "
                        "where appropriate. Optional."
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "disclosing_party": {
                    "type": "string",
                    "description": (
                        "Full legal name of the disclosing party. Pass only if "
                        "the user named it — no default."
                    ),
                },
                "receiving_party": {
                    "type": "string",
                    "description": (
                        "Full legal name of the counterparty. Pass only if the "
                        "user provided it — never invent a name."
                    ),
                },
                "effective_date": {
                    "type": "string",
                    "description": (
                        "Effective date as a human-readable string, e.g. "
                        "'21 April 2026'. Pass only if the user provided it."
                    ),
                },
                "term_years": {
                    "type": "integer",
                    "description": "Term in years. Pass only if the user provided it.",
                },
                "governing_city": {
                    "type": "string",
                    "description": (
                        "Governing-law / venue city, e.g. 'Mumbai'. Pass only "
                        "if the user provided it — no default."
                    ),
                },
                "purpose": {
                    "type": "string",
                    "description": "Purpose of the exchange. Pass only if the user provided it.",
                },
                "mutual": {
                    "type": "boolean",
                    "description": "True for mutual, False for one-way. Pass only if the user specified.",
                },
                "extra_clauses": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Additional clauses to append verbatim. Optional.",
                },
            },
            "required": ["jurisdiction"],
        },
    },
    {
        "name": "inspect_proposal_template",
        "description": (
            "Open Beacon's Business Proposal template from Drive and confirm "
            "it's reachable. Returns block count and available variants (lite/main). "
            "ALWAYS call this FIRST before generate_proposal."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "generate_proposal",
        "description": (
            "Generate a Business Proposal for a prospect by rewriting Beacon's "
            "Drive template with client context from Gmail threads, transcripts, "
            "and user inputs. Uploads the result as an editable Google Doc and "
            "returns the link. Also handles update requests - pass change_request "
            "to regenerate with specific changes applied."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "client_name": {"type": "string"},
                "variant": {
                    "type": "string",
                    "enum": ["lite", "main"],
                    "description": "'lite' = 7-section concise version. 'main' = full 9-section version. Default: main.",
                },
                "email_thread_content": {
                    "type": "string",
                    "description": "Combined text of all email threads for this prospect from search_email + read_email_thread.",
                },
                "transcript": {
                    "type": "string",
                    "description": "Meeting transcript, POC outcomes, or free-form notes.",
                },
                "prepared_by": {"type": "string", "description": "AE full name."},
                "prepared_by_title": {"type": "string", "description": "AE title, e.g. 'Account Executive'."},
                "prepared_by_phone": {"type": "string"},
                "prepared_by_email": {"type": "string"},
                "date": {"type": "string", "description": "e.g. '4 May 2026'"},
                "platform": {"type": "string", "description": "e.g. 'Darwinbox', 'SAP S/4HANA'"},
                "domain": {"type": "string", "description": "e.g. 'Order-to-Cash', 'HR/payroll'"},
                "client_description": {"type": "string", "description": "One-line description of what the client does."},
                "use_cases": {"type": "array", "items": {"type": "string"}, "description": "2-3 key use cases."},
                "effort_reduction_pct": {"type": "string", "description": "e.g. '50-60'"},
                "timeline_reduction_pct": {"type": "string", "description": "e.g. '40-60'"},
                "hypercare_reduction_pct": {"type": "string", "description": "e.g. '70-80'"},
                "annual_platform_fee": {"type": "string", "description": "e.g. '250000'"},
                "per_client_fee": {"type": "string", "description": "e.g. '1000'"},
                "implementations_per_year": {"type": "string"},
                "avg_hours_per_impl": {"type": "string"},
                "hourly_rate": {"type": "string"},
                "change_request": {
                    "type": "string",
                    "description": "For updates: what the user wants changed. Leave empty for first-time generation.",
                },
            },
            "required": ["client_name"],
        },
    },
    {
        "name": "inspect_roi_template",
        "description": (
            "Check that the Beacon ROI Excel template is available in Drive "
            "and return the list of survey questions it expects. Call FIRST "
            "before generate_roi."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "generate_roi",
        "description": (
            "Fill the Beacon ROI Analysis template with client survey "
            "response data and produce a live Google Sheet. The tool fills "
            "the Survey Input and Inputs sheets — all ROI calculations are "
            "formula-driven and auto-update when the Sheet is opened. Call "
            "after collecting Q2-Q14 answers from the AE."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "client_name": {"type": "string"},
                "prepared_by": {"type": "string", "description": "AE name"},
                "report_date": {
                    "type": "string",
                    "description": "e.g. 'April 2026'",
                },
                "q1_reason": {"type": "string"},
                "q2_impls_per_year": {
                    "type": "string",
                    "description": (
                        "Raw answer e.g. '700 total, 400 full module'"
                    ),
                },
                "q3_team_size": {"type": "string", "description": "e.g. '24'"},
                "q4_ftes_per_impl": {"type": "string", "description": "e.g. '3'"},
                "q5_duration_range": {
                    "type": "string",
                    "description": (
                        "REQUIRED. Overall min-max project duration "
                        "(start to go-live). Always ask the AE for this. "
                        "e.g. '3-6 months' or '10-16 weeks'. This is the "
                        "TOTAL project range, not per-phase."
                    ),
                },
                "q6_inception_weeks": {
                    "type": "string",
                    "description": (
                        "PHASE 1 — Inception / Discovery duration "
                        "(the AE's answer to Q6 only). e.g. '1-4 weeks' or '2 days'."
                    ),
                },
                "q7_solutioning_weeks": {
                    "type": "string",
                    "description": (
                        "PHASE 2 — Solutioning / BRD duration "
                        "(the AE's answer to Q7 only). e.g. '2 weeks' or '0 if ongoing'."
                    ),
                },
                "q8_config_weeks": {
                    "type": "string",
                    "description": (
                        "PHASE 3 — Configuration & Workflow Setup duration "
                        "(the AE's answer to Q8 only). e.g. '3-5 weeks'."
                    ),
                },
                "q9_data_migration_weeks": {
                    "type": "string",
                    "description": (
                        "PHASE 4 — Data Migration / Preparation duration "
                        "(the AE's answer to Q9 only). e.g. '2-4 weeks'."
                    ),
                },
                "q10_testing_weeks": {
                    "type": "string",
                    "description": (
                        "PHASE 5 — Testing / UAT duration "
                        "(the AE's answer to Q10 only). e.g. '1-3 weeks'."
                    ),
                },
                "q11_cutover_weeks": {
                    "type": "string",
                    "description": (
                        "PHASE 6 — Cutover & Production Go-Live duration "
                        "(the AE's answer to Q11 only). e.g. '1 week'."
                    ),
                },
                "q12_fte_cost_usd": {
                    "type": "string",
                    "description": "e.g. '$40,000'",
                },
                "q13_ramp_up": {
                    "type": "string",
                    "description": (
                        "REQUIRED. Ramp-up period for a new implementation "
                        "team member to handle a full implementation "
                        "independently. Always ask the AE for this. "
                        "e.g. '3 months', '6 weeks', '1 quarter'."
                    ),
                },
                "q14_new_headcount": {
                    "type": "string",
                    "description": "e.g. 'Net 0' or '+3'",
                },
            },
            "required": ["client_name"],
        },
    },
    {
        "name": "search_email",
        "description": (
            "Search the AE's Gmail inbox. Returns thread summaries with "
            "IDs. Use to find company emails, meeting notes, or any "
            "relevant thread."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "e.g. 'zywave meeting notes' or "
                        "'poc kickoff gainsight'"
                    ),
                },
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_email_thread",
        "description": (
            "Read full content of a Gmail thread by thread ID. Returns "
            "all messages with full body text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"thread_id": {"type": "string"}},
            "required": ["thread_id"],
        },
    },
    {
        "name": "inspect_poc_kickoff_template",
        "description": (
            "Confirm the Beacon PoC Kickoff template is in Drive. Call "
            "FIRST before generate_poc_kickoff."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "generate_poc_kickoff",
        "description": (
            "Fill the Beacon PoC Kickoff template with data extracted "
            "from email threads and produce a Google Doc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "client_name": {"type": "string"},
                "email_thread_content": {
                    "type": "string",
                    "description": (
                        "Full text from all relevant email threads "
                        "concatenated"
                    ),
                },
                "meeting_date": {"type": "string"},
                "prepared_by": {
                    "type": "string",
                    "description": "AE name",
                },
                "extra_context": {"type": "string"},
            },
            "required": ["client_name", "email_thread_content"],
        },
    },
    {
        "name": "inspect_poc_ppt_template",
        "description": (
            "Confirm the Beacon PoC Demo PPT template (originally built "
            "for Zellis) is reachable in Drive. Call FIRST before "
            "generate_poc_ppt. Reports slide_count and which slides are "
            "rewritable (slides 3, 4, 5)."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "generate_poc_ppt",
        "description": (
            "Fill the Beacon PoC Demo deck (Zellis-template-based) with "
            "client-specific content for slides 3, 4, 5. Combines the "
            "PoC Kickoff document text and the email thread content as "
            "source material. Slides 1, 2, 6, 7 stay structurally "
            "identical to the Zellis original — only the literal "
            "'Zellis' string is swapped to client_name. Produces an "
            "editable Google Slides deck. Call AFTER "
            "inspect_poc_ppt_template and AFTER you have the PoC "
            "Kickoff document text + email content gathered."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "client_name": {"type": "string"},
                "poc_kickoff_content": {
                    "type": "string",
                    "description": (
                        "Full text of the PoC Kickoff document for this "
                        "client (use the doc you just generated, or "
                        "fetch it). Required — drives slides 4 and 5."
                    ),
                },
                "email_thread_content": {
                    "type": "string",
                    "description": (
                        "Concatenated email thread text for additional "
                        "context (slide 3 pain points). Optional but "
                        "recommended."
                    ),
                },
                "prepared_by": {
                    "type": "string",
                    "description": "AE name. Optional.",
                },
            },
            "required": ["client_name", "poc_kickoff_content"],
        },
    },
    {
        "name": "draft_linkedin_message",
        "description": (
            "Draft LinkedIn outreach messages for a prospect. "
            "Always generates 3 tone variants (Challenge-First, Consultative, "
            "Direct/Numbers-Driven). Call this after identifying the prospect's "
            "vertical and buyer role — either from the user's text input or "
            "from a LinkedIn profile screenshot. Never ask which tone the user "
            "prefers — always return all three."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prospect_name": {
                    "type": "string",
                    "description": "Full name of the prospect.",
                },
                "title": {
                    "type": "string",
                    "description": "Current job title.",
                },
                "company": {
                    "type": "string",
                    "description": "Company name.",
                },
                "vertical": {
                    "type": "string",
                    "description": (
                        "Classified vertical: hrms, finops, erp, insurtech, "
                        "retail_tech, logistics, ecommerce, cs_platform, or other."
                    ),
                },
                "buyer_role": {
                    "type": "string",
                    "enum": [
                        "ps_implementation",
                        "cs_support",
                        "finance",
                        "product_engineering",
                        "founder_ceo",
                        "other",
                    ],
                    "description": (
                        "Which buyer seat this person occupies — determines "
                        "which product line to lead with."
                    ),
                },
                "outreach_type": {
                    "type": "string",
                    "enum": [
                        "cold",
                        "connection_request",
                        "inmail",
                        "follow_up",
                        "multi_thread",
                    ],
                    "description": "Type of outreach.",
                },
                "personalization_hook": {
                    "type": "string",
                    "description": (
                        "The specific personalization signal found — e.g. "
                        "'3 roles at same company over 7 years', "
                        "'spoke at SaaStr 2026 about AI in CS', "
                        "'posted about manual config pain last week'. "
                        "From screenshot: extract this from image content."
                    ),
                },
                "career_arc": {
                    "type": "string",
                    "description": (
                        "Prospect's role progression if visible (e.g. "
                        "'Support Manager → Head of CS → VP CX at same "
                        "company'). Extract from screenshot if available."
                    ),
                },
                "recent_activity": {
                    "type": "string",
                    "description": (
                        "Recent LinkedIn posts, talks, or public moments. "
                        "Extract from screenshot if visible."
                    ),
                },
                "ae_name": {
                    "type": "string",
                    "description": (
                        "Name of the AE sending the message (from the AE "
                        "roster or user input)."
                    ),
                },
            },
            "required": ["prospect_name", "title", "company"],
        },
    },
    {
        "name": "generate_document",
        "description": (
            "Create a free-form Word document from markdown content. Use for one-pagers, "
            "follow-up emails as docx, briefs, or any deliverable that isn't MOM/NDA."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "markdown": {
                    "type": "string",
                    "description": "Document body in light markdown (#, ##, ###, -, numbered).",
                },
                "client_name": {
                    "type": "string",
                    "description": "Optional client/prospect name for the subtitle.",
                },
            },
            "required": ["title", "markdown"],
        },
    },
    {
        "name": "lookup_prospect_phone",
        "description": (
            "Look up a prospect's phone number from the CRM database by name "
            "and/or company. Returns the phone number, full name, and company "
            "so the frontend can trigger an Aircall dial. Use when the user "
            "says 'call [name]', 'dial [person]', 'connect with [prospect]', "
            "'call [name] at [company]', or similar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prospect_name": {
                    "type": "string",
                    "description": "Full or partial name of the prospect.",
                },
                "company": {
                    "type": "string",
                    "description": "Company name to narrow the search. Optional.",
                },
            },
            "required": ["prospect_name"],
        },
    },
    {
        "name": "find_deal",
        "description": (
            "Search for a deal by company name or deal name. Returns the deal's "
            "current field values so Zippy can show a confirmation before updating. "
            "Always call this before update_deal to confirm the deal exists and "
            "show the user what will change."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Company name or deal name to search for.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "update_deal",
        "description": (
            "Update one or more fields on a deal after the user has confirmed. "
            "NEVER call this without first calling find_deal AND receiving explicit "
            "user confirmation (yes / looks good / confirmed). "
            "Only pass fields the user explicitly asked to change. "
            "Do NOT pass health — health is auto-calculated."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "deal_id": {
                    "type": "string",
                    "description": "UUID of the deal to update. Get this from find_deal.",
                },
                "next_step": {
                    "type": "string",
                    "description": "Updated next step text.",
                },
                "next_step_due_at": {
                    "type": "string",
                    "description": "Next step due date/time as ISO string e.g. '2026-06-08T00:00:00'.",
                },
                "stage": {
                    "type": "string",
                    "description": (
                        "New deal stage. Must be one of: reprospect, demo_scheduled, "
                        "demo_done, qualified_lead, poc_agreed, poc_wip, poc_done, "
                        "commercial_negotiation, msa_review, workshop, closed_won, "
                        "closed_lost, not_a_fit, cold, on_hold, nurture, churned."
                    ),
                },
                "value": {
                    "type": "number",
                    "description": "Deal value / amount in USD.",
                },
                "close_date_est": {
                    "type": "string",
                    "description": "Estimated close date as YYYY-MM-DD.",
                },
                "description": {
                    "type": "string",
                    "description": "Deal description / notes.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Replace deal tags with this list.",
                },
            },
            "required": ["deal_id"],
        },
    },
    {
        "name": "explain_deal_health",
        "description": (
            "Explain why a deal has its current health score and what the AE "
            "can do to improve it. Call this when the user asks about deal health, "
            "why a deal is red/yellow, or how to improve health. "
            "Health is auto-calculated — never set it directly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "deal_id": {
                    "type": "string",
                    "description": "UUID of the deal. Get from find_deal first.",
                },
            },
            "required": ["deal_id"],
        },
    },
    {
        "name": "find_entity_for_task",
        "description": (
            "Search for a deal, contact, or company by name to get its ID "
            "before creating a task linked to it. Always call this first "
            "when the user mentions a company or person name in a task request."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Company, deal, or contact name to search for.",
                },
                "entity_type": {
                    "type": "string",
                    "enum": ["deal", "contact", "company"],
                    "description": "Which type to search. Default to 'deal' if unsure.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "create_task",
        "description": (
            "Create a manual task in the Tasks queue after the user has confirmed. "
            "NEVER call this without first calling find_entity_for_task AND "
            "receiving explicit user confirmation (yes / looks good / confirmed). "
            "Only pass fields the user explicitly provided."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Task title — short, action-oriented.",
                },
                "entity_type": {
                    "type": "string",
                    "enum": ["deal", "contact", "company"],
                    "description": "Type of record this task is linked to.",
                },
                "entity_id": {
                    "type": "string",
                    "description": "UUID of the deal/contact/company. Get from find_entity_for_task.",
                },
                "due_at": {
                    "type": "string",
                    "description": "Due date/time as ISO string e.g. '2026-06-04T18:00:00'.",
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Task priority. Default: medium.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional context or notes for the task.",
                },
            },
            "required": ["title", "entity_type", "entity_id"],
        },
    },
]


@dataclass
class ToolOutcome:
    """What the agent loop uses to build the tool_result message + side effects."""

    result_text: str                       # what Claude sees
    citations: list[dict] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    is_error: bool = False


async def execute_tool(
    name: str,
    args: dict,
    *,
    session: AsyncSession,
    user_id: Optional[UUID],
) -> ToolOutcome:
    """Dispatch a single Claude tool call. Never raises — errors come back
    via ``ToolOutcome.is_error`` so the agent can relay them to Claude."""
    try:
        if name == "search_knowledge_base":
            return await _execute_search(args, user_id=user_id)
        if name == "inspect_mom_template":
            return await _execute_inspect_mom(user_id=user_id)
        if name == "generate_mom":
            return await _execute_mom(args, user_id=user_id)
        if name == "inspect_nda_template":
            return await _execute_inspect_nda(args, user_id=user_id)
        if name == "generate_nda":
            return await _execute_nda(args, user_id=user_id)
        if name == "inspect_proposal_template":
            return await _execute_inspect_proposal(user_id=user_id)
        if name == "generate_proposal":
            return await _execute_proposal(args, user_id=user_id)
        if name == "inspect_roi_template":
            return await _execute_inspect_roi(user_id=user_id)
        if name == "generate_roi":
            return await _execute_roi(args, user_id=user_id)
        if name == "search_email":
            return await _execute_search_email(args, user_id=user_id)
        if name == "read_email_thread":
            return await _execute_read_email(args, user_id=user_id)
        if name == "inspect_poc_kickoff_template":
            return await _execute_inspect_poc_kickoff(user_id=user_id)
        if name == "generate_poc_kickoff":
            return await _execute_poc_kickoff(args, user_id=user_id)
        if name == "inspect_poc_ppt_template":
            return await _execute_inspect_poc_ppt(user_id=user_id)
        if name == "generate_poc_ppt":
            return await _execute_poc_ppt(args, user_id=user_id)
        if name == "draft_linkedin_message":
            return await _execute_linkedin(args, user_id=user_id)
        if name == "generate_document":
            return await _execute_generic(args, user_id=user_id)
        if name == "lookup_prospect_phone":
            return await _execute_lookup_phone(args, session=session)
        if name == "find_deal":
            return await _execute_find_deal(args)
        if name == "update_deal":
            return await _execute_update_deal(args, user_id=user_id)
        if name == "explain_deal_health":
            return await _execute_explain_deal_health(args)
        if name == "find_entity_for_task":
            return await _execute_find_entity_for_task(args)
        if name == "create_task":
            return await _execute_create_task(args, user_id=user_id)
    except Exception as exc:
        logger.exception("Tool %s failed", name)
        return ToolOutcome(
            result_text=f"Tool '{name}' failed with error: {exc}",
            is_error=True,
        )

    return ToolOutcome(
        result_text=f"Unknown tool: {name}",
        is_error=True,
    )


# ── Individual executors ─────────────────────────────────────────────────────


async def _execute_search(args: dict, *, user_id: Optional[UUID]) -> ToolOutcome:
    query = (args.get("query") or "").strip()
    if not query:
        return ToolOutcome(
            result_text="No query was provided to search_knowledge_base.",
            is_error=True,
        )
    top_k = min(int(args.get("top_k") or 6), 12)
    source_ids = args.get("source_ids") or None

    snippets: list[KnowledgeSnippet] = await search_knowledge(
        query,
        user_id=user_id,
        include_admin=True,
        top_k=top_k,
        source_ids=source_ids,
    )

    if not snippets:
        return ToolOutcome(
            result_text=(
                "No matching content found in the user's Drive folder or the Beacon "
                "shared folder. Ask the user if they'd like to index more files or "
                "phrase the question differently."
            ),
        )

    # Format as a compact text block — Claude handles structured markdown well.
    lines = [f"Found {len(snippets)} snippet(s):", ""]
    citations: list[dict] = []
    for idx, snippet in enumerate(snippets, start=1):
        lines.append(f"[{idx}] {snippet.source_name} (score {snippet.score:.2f})")
        body = snippet.text.strip().replace("\n", " ")
        if len(body) > 600:
            body = body[:600].rstrip() + "…"
        lines.append(body)
        if snippet.drive_url:
            lines.append(f"Link: {snippet.drive_url}")
        lines.append("")
        citations.append(snippet.as_citation())
    return ToolOutcome(result_text="\n".join(lines), citations=citations)


def _doc_to_artifact(doc: GeneratedDocument) -> dict:
    """Frontend artifact.

    NOTE on `url` vs `drive_url`: the frontend chip in
    ZippyMessageBubble.tsx prepends API_BASE to `url`, so `url` MUST
    stay a relative path (e.g. /zippy_outputs/foo.docx) — putting an
    absolute Google Docs URL there produces a mangled
    `http://localhost:8000https://docs.google.com/...` href that
    Chrome rejects as about:blank. The chip reads `drive_url` directly
    when present and uses `url` only as a download fallback.
    """
    return {
        "type": doc.kind,
        "filename": doc.filename,
        "url": doc.url,
        "drive_url": doc.drive_url or "",
        "drive_file_id": doc.drive_file_id or "",
        "summary": doc.summary,
        "created_at": doc.created_at.isoformat(),
        # Backend-only field. The frontend ignores unknown keys; the
        # agent's _to_api_messages re-injects this body into the next
        # turn's context so Claude can pass it as poc_kickoff_content
        # when asked for a follow-up PoC Demo PPT (otherwise the body
        # is invisible across turns — only assistant text survives).
        "body_text": doc.body_text or "",
    }


def _doc_link_text(doc: GeneratedDocument) -> str:
    """Return a single line containing the Google Docs/Sheets link.

    The string is shaped so the agent can quote it verbatim into chat —
    the 'Open and edit in Google Docs:' prefix gives Claude an anchor it
    is unlikely to paraphrase away. If the upload failed we surface a
    visible warning instead of a useless local /zippy_outputs/ path that
    isn't editable in-browser.
    """
    if doc.drive_url:
        return f"Open and edit in Google Docs: {doc.drive_url}"
    return (
        "⚠️ Google Docs upload failed. "
        "Check your Drive connection in Settings and try again."
    )


async def _execute_inspect_mom(*, user_id: Optional[UUID]) -> ToolOutcome:
    result = await inspect_mom_template(user_id=user_id)
    if not result.get("found"):
        return ToolOutcome(
            result_text=(
                f"MOM template not available: {result.get('error', 'unknown error')}. "
                "You can still call `generate_mom` — it will produce a fallback draft."
            ),
        )
    return ToolOutcome(
        result_text=(
            f"Template found: {result['template_name']}. "
            f"Has {result['section_count']} content sections. "
            "Ready to generate MOM once transcript is provided."
        ),
    )


async def _execute_mom(args: dict, *, user_id: Optional[UUID]) -> ToolOutcome:
    data = MOMInput(
        client_name=args.get("client_name", "Client"),
        meeting_date=args.get("meeting_date"),
        attendees=args.get("attendees"),
        transcript=args.get("transcript"),
        context_notes=args.get("context_notes"),
        format_type=args.get("format_type", "long"),
        collateral=args.get("collateral") or [],
    )
    try:
        doc = await generate_mom(data, user_id=user_id)
    except MOMTemplateUnavailable as exc:
        # Kept for back-compat — the new generator falls back internally and
        # shouldn't raise this, but some callers may still import the class.
        return ToolOutcome(
            result_text=(
                f"Cannot generate MOM: {exc}. "
                "Ask the user to verify MOM Template.docx is in their indexed "
                "Drive folder and that the Drive OAuth account has access."
            ),
            is_error=True,
        )
    return ToolOutcome(
        result_text=(
            f"✅ MOM generated for {data.client_name}.\n"
            f"{_doc_link_text(doc)}\n"
            f"Summary: {doc.summary}"
        ),
        artifacts=[_doc_to_artifact(doc)],
    )


async def _execute_inspect_nda(args: dict, *, user_id: Optional[UUID] = None) -> ToolOutcome:
    result = await inspect_nda_template(
        args["jurisdiction"],
        user_id=str(user_id) if user_id else None,
    )
    if not result.get("found"):
        return ToolOutcome(
            result_text=(
                f"{result.get('error') or 'NDA template not available.'} "
                "You can still call `generate_nda` — it will produce a "
                "fallback draft from the user-provided details."
            ),
        )
    return ToolOutcome(
        result_text=(
            f"NDA template found for {result['jurisdiction']}. "
            f"Has {result['section_count']} content sections. "
            "Ask the user for party details."
        ),
    )


async def _execute_nda(args: dict, *, user_id: Optional[UUID] = None) -> ToolOutcome:
    data = NDAInput(
        jurisdiction=args["jurisdiction"],
        fills={str(k): str(v) for k, v in (args.get("fills") or {}).items()},
        receiving_party=args.get("receiving_party"),
        disclosing_party=args.get("disclosing_party"),
        mutual=args.get("mutual"),
        purpose=args.get("purpose"),
        term_years=int(args["term_years"]) if args.get("term_years") is not None else None,
        effective_date=args.get("effective_date"),
        governing_city=args.get("governing_city"),
        extra_clauses=list(args.get("extra_clauses") or []),
    )
    doc = await generate_nda(data, user_id=str(user_id) if user_id else None)
    return ToolOutcome(
        result_text=(
            f"✅ NDA generated ({data.jurisdiction.upper()}).\n"
            f"{_doc_link_text(doc)}\n"
            f"Summary: {doc.summary}"
        ),
        artifacts=[_doc_to_artifact(doc)],
    )


async def _execute_inspect_proposal(*, user_id: Optional[UUID] = None) -> ToolOutcome:
    meta = await inspect_proposal_template(
        user_id=str(user_id) if user_id else None
    )
    return ToolOutcome(result_text=meta.get("message", str(meta)))


async def _execute_proposal(args: dict, *, user_id: Optional[UUID] = None) -> ToolOutcome:
    inp = ProposalInput(
        client_name=args.get("client_name", ""),
        variant=args.get("variant", "main"),
        email_thread_content=args.get("email_thread_content", ""),
        transcript=args.get("transcript", ""),
        prepared_by=args.get("prepared_by", ""),
        prepared_by_title=args.get("prepared_by_title", ""),
        prepared_by_phone=args.get("prepared_by_phone", ""),
        prepared_by_email=args.get("prepared_by_email", ""),
        date=args.get("date", ""),
        platform=args.get("platform", ""),
        domain=args.get("domain", ""),
        client_description=args.get("client_description", ""),
        use_cases=args.get("use_cases", []),
        effort_reduction_pct=args.get("effort_reduction_pct", ""),
        timeline_reduction_pct=args.get("timeline_reduction_pct", ""),
        hypercare_reduction_pct=args.get("hypercare_reduction_pct", ""),
        annual_platform_fee=args.get("annual_platform_fee", ""),
        per_client_fee=args.get("per_client_fee", ""),
        implementations_per_year=args.get("implementations_per_year", ""),
        avg_hours_per_impl=args.get("avg_hours_per_impl", ""),
        hourly_rate=args.get("hourly_rate", ""),
        change_request=args.get("change_request", ""),
    )
    doc = await generate_proposal(
        inp, user_id=str(user_id) if user_id else None
    )
    link_line = _doc_link_text(doc)
    action = "updated" if inp.change_request else "generated"
    return ToolOutcome(
        result_text=(
            f"{link_line}\n\n"
            f"Business Proposal {action} for **{inp.client_name}**.\n"
            f"Variant: {inp.variant} | File: {doc.filename}"
        ),
        artifacts=[_doc_to_artifact(doc)],
    )


async def _execute_inspect_roi(*, user_id: Optional[UUID] = None) -> ToolOutcome:
    from app.services.zippy_docs.roi import inspect_roi_template
    result = await inspect_roi_template(
        user_id=str(user_id) if user_id else None
    )
    if not result.get("found"):
        return ToolOutcome(
            result_text=(
                f"ROI template not found: {result.get('error')}. "
                "Proceeding with generate_roi will produce a basic "
                "fallback sheet."
            )
        )
    fields = "\n".join(f"  - {f}" for f in result.get("input_fields", []))
    return ToolOutcome(
        result_text=(
            f"ROI template found: {result['template_name']}.\n"
            f"Input fields needed from the AE's form response:\n{fields}\n"
            f"{result.get('note', '')}"
        )
    )


async def _execute_roi(args: dict, *, user_id: Optional[UUID] = None) -> ToolOutcome:
    from app.services.zippy_docs.roi import ROIInput, generate as generate_roi
    data = ROIInput(
        client_name=args.get("client_name", "Client"),
        prepared_by=args.get("prepared_by", "Beacon"),
        report_date=args.get("report_date"),
        q1_reason=args.get("q1_reason"),
        q2_impls_per_year=args.get("q2_impls_per_year"),
        q3_team_size=args.get("q3_team_size"),
        q4_ftes_per_impl=args.get("q4_ftes_per_impl"),
        q5_duration_range=args.get("q5_duration_range"),
        q6_inception_weeks=args.get("q6_inception_weeks"),
        q7_solutioning_weeks=args.get("q7_solutioning_weeks"),
        q8_config_weeks=args.get("q8_config_weeks"),
        q9_data_migration_weeks=args.get("q9_data_migration_weeks"),
        q10_testing_weeks=args.get("q10_testing_weeks"),
        q11_cutover_weeks=args.get("q11_cutover_weeks"),
        q12_fte_cost_usd=args.get("q12_fte_cost_usd"),
        q13_ramp_up=args.get("q13_ramp_up"),
        q14_new_headcount=args.get("q14_new_headcount"),
    )
    doc = await generate_roi(data, user_id=str(user_id) if user_id else None)
    return ToolOutcome(
        result_text=(
            f"✅ ROI Analysis generated for {data.client_name}.\n"
            f"{_doc_link_text(doc)}\n"
            f"Summary: {doc.summary}"
        ),
        artifacts=[_doc_to_artifact(doc)],
    )


async def _execute_search_email(
    args: dict, *, user_id: Optional[UUID] = None
) -> ToolOutcome:
    from app.clients.gmail_client import search_threads
    query = args.get("query", "")
    # Cap at 10 — large lists balloon the prompt and the AE only needs
    # enough threads to disambiguate which conversation to read.
    limit = min(int(args.get("limit", 5)), 10)
    try:
        threads = await search_threads(
            query=query,
            page_size=limit,
            user_id=str(user_id) if user_id else None,
        )
        if not threads:
            return ToolOutcome(result_text=f"No emails found for: {query}")
        lines = []
        for t in threads:
            lines.append(
                f"Thread ID: {t['id']}\n"
                f"  Subject: {t.get('subject', '(no subject)')}\n"
                f"  From: {t.get('sender', '')}\n"
                f"  Date: {t.get('date', '')}\n"
                f"  Preview: {t.get('snippet', '')[:200]}"
            )
        return ToolOutcome(result_text="\n\n".join(lines))
    except Exception as exc:
        import traceback
        tb = traceback.format_exc(limit=3)
        return ToolOutcome(
            result_text=(
                f"Gmail call raised: {type(exc).__name__}: {exc}\n\n"
                f"Traceback (last 3 frames):\n{tb}\n\n"
                "Show this exception text to the user verbatim — they are "
                "the developer and need the diagnostic. Then STOP — do not "
                "retry, do not call search_knowledge_base."
            ),
            is_error=True,
        )


async def _execute_read_email(
    args: dict, *, user_id: Optional[UUID] = None
) -> ToolOutcome:
    from app.clients.gmail_client import get_thread_content
    thread_id = args.get("thread_id", "")
    try:
        thread = await get_thread_content(
            thread_id=thread_id,
            user_id=str(user_id) if user_id else None,
        )
        if not thread:
            return ToolOutcome(
                result_text=f"Thread {thread_id} not found.", is_error=True
            )
        return ToolOutcome(
            result_text=thread.get("full_text", "Empty thread.")
        )
    except Exception as exc:
        return ToolOutcome(
            result_text=f"Failed to read thread: {exc}", is_error=True
        )


async def _execute_inspect_poc_kickoff(
    *, user_id: Optional[UUID] = None
) -> ToolOutcome:
    from app.services.zippy_docs.poc_kickoff import inspect_poc_kickoff_template
    result = await inspect_poc_kickoff_template(
        user_id=str(user_id) if user_id else None
    )
    if not result.get("found"):
        return ToolOutcome(
            result_text=(
                f"PoC Kickoff template not found: {result.get('error')}. "
                "Will produce fallback doc if generate_poc_kickoff is called."
            )
        )
    return ToolOutcome(
        result_text=(
            f"Template found: {result['template_name']}. "
            f"Has {result['section_count']} content sections. Ready."
        )
    )


async def _execute_poc_kickoff(
    args: dict, *, user_id: Optional[UUID] = None
) -> ToolOutcome:
    from app.services.zippy_docs.poc_kickoff import (
        PoCKickoffInput,
        generate as generate_poc,
    )
    email_content = args.get("email_thread_content", "") or ""
    # Guard against the agent skipping the read step. Without real email
    # content the generator can only fill TBDs — better to refuse than
    # produce a hollow doc the AE will mistake for real output.
    if len(email_content.strip()) < 200:
        return ToolOutcome(
            result_text=(
                "REFUSED: email_thread_content is empty or too short "
                f"({len(email_content.strip())} chars). A useful PoC "
                "Kickoff requires real email content. Required next "
                "steps:\n"
                "  1. Call `search_email` with the company name "
                "(e.g. 'zywave poc kickoff', 'zywave next steps').\n"
                "  2. Call `read_email_thread` for each relevant "
                "thread ID returned.\n"
                "  3. Concatenate all `full_text` values from those "
                "calls into email_thread_content.\n"
                "  4. THEN call generate_poc_kickoff again.\n"
                "Do NOT retry generate_poc_kickoff with the same empty "
                "input. Do NOT pass placeholder text like 'TBD' to "
                "satisfy this guard — that defeats the purpose."
            ),
            is_error=True,
        )
    data = PoCKickoffInput(
        client_name=args.get("client_name", "Client"),
        email_thread_content=email_content,
        meeting_date=args.get("meeting_date"),
        prepared_by=args.get("prepared_by"),
        extra_context=args.get("extra_context"),
    )
    doc = await generate_poc(
        data, user_id=str(user_id) if user_id else None
    )
    # Pass the rewritten body back to Claude so a follow-up
    # generate_poc_ppt call can reuse it as poc_kickoff_content
    # without re-fetching anything. Cap at 18k chars to stay well
    # under the model's context budget.
    body_block = ""
    if doc.body_text:
        body = doc.body_text[:18000]
        body_block = (
            "\n\n=== FULL KICKOFF BODY (verbatim) ===\n"
            "If the user next asks for a PoC Demo PPT for this "
            "client, pass THIS exact block as the "
            "poc_kickoff_content argument to generate_poc_ppt — "
            "do NOT summarise, do NOT shorten.\n"
            "------------------------------------\n"
            f"{body}\n"
            "------------------------------------"
        )
    return ToolOutcome(
        result_text=(
            f"✅ PoC Kickoff document generated for {data.client_name}.\n"
            f"{_doc_link_text(doc)}\n"
            f"Summary: {doc.summary}"
            f"{body_block}"
        ),
        artifacts=[_doc_to_artifact(doc)],
    )


async def _execute_inspect_poc_ppt(
    *, user_id: Optional[UUID] = None
) -> ToolOutcome:
    from app.services.zippy_docs.poc_ppt import inspect_poc_ppt_template
    result = await inspect_poc_ppt_template(
        user_id=str(user_id) if user_id else None
    )
    if not result.get("found"):
        return ToolOutcome(
            result_text=(
                f"PoC Demo PPT template not found: {result.get('error')}. "
                "generate_poc_ppt will produce a fallback deck if called."
            )
        )
    fillable = result.get("fillable_slides", [3, 4, 5])
    return ToolOutcome(
        result_text=(
            f"Template found: {result['template_name']}. "
            f"Slide count: {result['slide_count']}. "
            f"Fillable slides: {fillable}. Ready."
        )
    )


async def _execute_poc_ppt(
    args: dict, *, user_id: Optional[UUID] = None
) -> ToolOutcome:
    from app.services.zippy_docs.poc_ppt import (
        PoCPPTInput,
        generate as generate_poc_ppt,
    )
    kickoff_content = args.get("poc_kickoff_content", "") or ""
    if len(kickoff_content.strip()) < 200:
        return ToolOutcome(
            result_text=(
                "REFUSED: poc_kickoff_content is empty or too short "
                f"({len(kickoff_content.strip())} chars). The PoC Demo "
                "deck pulls slide 4 (use cases) and slide 5 "
                "(deliverables, timeline) directly from the kickoff "
                "doc — without it slides will be hollow. Required next "
                "steps:\n"
                "  1. If you just generated a PoC Kickoff for this "
                "client, pass that document's full text as "
                "poc_kickoff_content.\n"
                "  2. Otherwise call search_knowledge_base / "
                "search_email to retrieve the kickoff text first.\n"
                "  3. THEN call generate_poc_ppt again.\n"
                "Do NOT pass placeholder text to satisfy this guard."
            ),
            is_error=True,
        )
    data = PoCPPTInput(
        client_name=args.get("client_name", "Client"),
        poc_kickoff_content=kickoff_content,
        email_thread_content=args.get("email_thread_content") or "",
        prepared_by=args.get("prepared_by"),
    )
    doc = await generate_poc_ppt(
        data, user_id=str(user_id) if user_id else None
    )
    return ToolOutcome(
        result_text=(
            f"✅ PoC Demo deck generated for {data.client_name}.\n"
            f"{_doc_link_text(doc)}\n"
            f"Summary: {doc.summary}"
        ),
        artifacts=[_doc_to_artifact(doc)],
    )


async def _execute_linkedin(
    args: dict, *, user_id: Optional[UUID] = None
) -> ToolOutcome:
    """Structure the prospect brief and hand it back to Claude.

    No side effects — Claude writes the three tone variants in its reply
    using the LINKEDIN OUTREACH rules baked into the system prompt. The
    tool call is purely a structured signal that the LinkedIn skill is
    the active mode for this turn (and that all 3 tones must be output).
    """
    name = args.get("prospect_name", "")
    title = args.get("title", "")
    company = args.get("company", "")
    vertical = args.get("vertical", "not classified")
    buyer_role = args.get("buyer_role", "other")
    outreach_type = args.get("outreach_type", "cold")
    hook = args.get("personalization_hook", "")
    arc = args.get("career_arc", "")
    activity = args.get("recent_activity", "")
    ae = args.get("ae_name", "")

    brief = (
        "PROSPECT BRIEF\n"
        f"Name: {name}\n"
        f"Title: {title}\n"
        f"Company: {company}\n"
        f"Vertical: {vertical}\n"
        f"Buyer role: {buyer_role}\n"
        f"Outreach type: {outreach_type}\n"
        f"Personalization hook: {hook or 'none identified'}\n"
        f"Career arc: {arc or 'not available'}\n"
        f"Recent activity: {activity or 'not available'}\n"
        f"AE: {ae or 'not specified'}\n\n"
        "Now draft all 3 tone variants following the LinkedIn outreach "
        "rules in your system prompt. Apply the correct proof points for "
        f"the '{vertical}' vertical and '{buyer_role}' buyer seat. "
        "Use before→after format. Keep each option under 100 words."
    )
    return ToolOutcome(result_text=brief, artifacts=[])


async def _execute_lookup_phone(
    args: dict, *, session: AsyncSession
) -> ToolOutcome:
    """Resolve a prospect's phone number from the CRM and return it as an
    ``aircall_dial`` artifact so the frontend can trigger the dial directly.

    The Contact model has no ``company`` string column — it links to a
    Company via ``company_id`` — so company narrowing joins the companies
    table on ``Company.name``. No Aircall API call happens here; dialing is
    frontend-only via ``window.__aircallDial``.
    """
    from sqlalchemy import or_
    from sqlmodel import select as sm_select

    from app.models.company import Company
    from app.models.contact import Contact

    prospect_name = (args.get("prospect_name") or "").strip()
    company_filter = (args.get("company") or "").strip()

    if not prospect_name:
        return ToolOutcome(
            result_text="Please provide a prospect name to look up.",
            artifacts=[],
        )

    name_conditions = []
    for part in prospect_name.split():
        name_conditions.append(Contact.first_name.ilike(f"%{part}%"))
        name_conditions.append(Contact.last_name.ilike(f"%{part}%"))

    stmt = (
        sm_select(Contact, Company)
        .join(Company, Contact.company_id == Company.id, isouter=True)
        .where(or_(*name_conditions))
    )
    if company_filter:
        stmt = stmt.where(Company.name.ilike(f"%{company_filter}%"))
    stmt = stmt.limit(5)

    result = await session.execute(stmt)
    rows = result.all()  # list of (Contact, Company | None)

    if not rows:
        return ToolOutcome(
            result_text=(
                f"No prospect found matching '{prospect_name}'"
                + (f" at {company_filter}" if company_filter else "")
                + ". Please check the name or search in Prospecting."
            ),
            artifacts=[],
        )

    if len(rows) > 1:
        options = "\n".join(
            f"- {c.first_name} {c.last_name}"
            + (f" at {co.name}" if co else "")
            + (f" ({c.phone})" if c.phone else " (no phone)")
            for c, co in rows
        )
        return ToolOutcome(
            result_text=(
                f"Found {len(rows)} matching prospects. Which one?\n"
                f"{options}\n\nReply with the full name to confirm."
            ),
            artifacts=[],
        )

    contact, company_obj = rows[0]
    full_name = f"{contact.first_name} {contact.last_name}"
    company_name = company_obj.name if company_obj else ""

    if not contact.phone:
        return ToolOutcome(
            result_text=(
                f"Found {full_name}"
                + (f" at {company_name}" if company_name else "")
                + " but they have no phone number on record. "
                "Please add one in the Prospecting page first."
            ),
            artifacts=[],
        )

    return ToolOutcome(
        result_text=(
            f"📞 Initiating call to **{full_name}**"
            + (f" at {company_name}" if company_name else "")
            + f" — {contact.phone}"
        ),
        artifacts=[{
            "type": "aircall_dial",
            "phone": contact.phone,
            "contact_name": full_name,
            "company": company_name,
        }],
    )


async def _execute_find_deal(args: dict) -> ToolOutcome:
    """Read-only deal lookup — returns current field values + a deal_found
    artifact so Zippy can build a confirmation before any write."""
    from sqlalchemy import or_
    from sqlmodel import select as sm_select

    from app.database import AsyncSessionLocal as async_session
    from app.models.company import Company
    from app.models.deal import Deal

    query = (args.get("query") or "").strip()
    if not query:
        return ToolOutcome(result_text="Please provide a deal or company name.", artifacts=[])

    async with async_session() as session:
        stmt = (
            sm_select(Deal, Company)
            .join(Company, Deal.company_id == Company.id, isouter=True)
            .where(
                or_(
                    Company.name.ilike(f"%{query}%"),
                    Deal.name.ilike(f"%{query}%"),
                )
            )
            .limit(5)
        )
        result = await session.execute(stmt)
        rows = result.all()

    if not rows:
        return ToolOutcome(
            result_text=f"No deal found matching '{query}'. Check the company name and try again.",
            artifacts=[],
        )

    if len(rows) > 1:
        options = "\n".join(
            f"- {d.name} | Stage: {d.stage} | Health: {d.health}" for d, c in rows
        )
        return ToolOutcome(
            result_text=f"Found {len(rows)} matching deals:\n{options}\n\nWhich one?",
            artifacts=[],
        )

    deal, company = rows[0]
    return ToolOutcome(
        result_text=(
            f"Found deal: {deal.name}\n"
            f"Stage: {deal.stage} | Health: {deal.health}\n"
            f"Next Step: {deal.next_step or 'not set'}\n"
            f"Next Step Due: {deal.next_step_due_at or 'not set'}\n"
            f"Value: {deal.value or 'not set'}\n"
            f"Close Date: {deal.close_date_est or 'not set'}\n"
            f"Deal ID: {deal.id}"
        ),
        artifacts=[{
            "type": "deal_found",
            "deal_id": str(deal.id),
            "deal_name": deal.name,
            "company_name": company.name if company else "",
            "stage": deal.stage,
            "health": deal.health,
            "next_step": deal.next_step or "",
            "next_step_due_at": str(deal.next_step_due_at) if deal.next_step_due_at else "",
            "value": str(deal.value) if deal.value else "",
            "close_date_est": str(deal.close_date_est) if deal.close_date_est else "",
        }],
    )


async def _execute_update_deal(args: dict, *, user_id: Optional[UUID] = None) -> ToolOutcome:
    """Write confirmed field changes to a deal. Health is intentionally NOT
    touched here — it's auto-calculated elsewhere."""
    import uuid
    from datetime import date, datetime
    from decimal import Decimal

    from sqlmodel import select as sm_select

    from app.database import AsyncSessionLocal as async_session
    from app.models.deal import DEAL_STAGES, Deal

    deal_id = args.get("deal_id", "")
    if not deal_id:
        return ToolOutcome(result_text="deal_id is required.", artifacts=[])

    changes: list[str] = []
    async with async_session() as session:
        result = await session.execute(
            sm_select(Deal).where(Deal.id == uuid.UUID(deal_id))
        )
        deal = result.scalar_one_or_none()
        if not deal:
            return ToolOutcome(result_text=f"Deal {deal_id} not found.", artifacts=[])

        if args.get("next_step") is not None:
            deal.next_step = args["next_step"]
            deal.next_step_updated_at = datetime.utcnow()
            changes.append(f"Next Step → {args['next_step']}")

        if args.get("next_step_due_at"):
            deal.next_step_due_at = datetime.fromisoformat(args["next_step_due_at"])
            changes.append(f"Next Step Due → {args['next_step_due_at']}")

        if args.get("stage") in DEAL_STAGES:
            deal.stage = args["stage"]
            deal.stage_entered_at = datetime.utcnow()
            changes.append(f"Stage → {args['stage']}")

        if args.get("value") is not None:
            deal.value = Decimal(str(args["value"]))
            changes.append(f"Value → ${args['value']:,.0f}")

        if args.get("close_date_est"):
            deal.close_date_est = date.fromisoformat(args["close_date_est"])
            changes.append(f"Close Date → {args['close_date_est']}")

        if args.get("description") is not None:
            deal.description = args["description"]
            changes.append("Description updated")

        if args.get("tags") is not None:
            deal.tags = args["tags"]
            changes.append(f"Tags → {', '.join(args['tags'])}")

        if not changes:
            return ToolOutcome(result_text="No fields were updated.", artifacts=[])

        deal.updated_at = datetime.utcnow()
        session.add(deal)
        await session.commit()

    # Log a deal activity note so the change shows on the deal timeline.
    # Non-fatal: the deal update already committed above, so a failure here
    # must never surface as an error. Use a fresh session, not the deal's.
    try:
        from uuid import uuid4

        from app.models.activity import Activity

        changes_text = "\n".join(f"• {c}" for c in changes)
        activity_content = f"Deal updated by Zippy:\n{changes_text}"

        activity = Activity(
            id=uuid4(),
            deal_id=uuid.UUID(deal_id),
            type="note",
            source="zippy",
            medium="other",
            content=activity_content,
            ai_summary=f"Zippy updated: {', '.join(changes)}",
            created_by_id=user_id if user_id else None,
            created_at=datetime.utcnow(),
        )
        async with async_session() as act_session:
            act_session.add(activity)
            await act_session.commit()
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning(
            "Failed to log activity for deal update: %s", exc
        )

    return ToolOutcome(
        result_text=(
            "✅ Deal updated successfully:\n" + "\n".join(f"  • {c}" for c in changes)
        ),
        artifacts=[{
            "type": "deal_updated",
            "deal_id": deal_id,
            "deal_name": deal.name,
            "changes": changes,
            "deal_link": f"/pipeline?deal={deal_id}",
        }],
    )


async def _execute_explain_deal_health(args: dict) -> ToolOutcome:
    """Read-only health explainer — mirrors deal_health.compute_health's
    dimension weights so the AE sees exactly where points come from."""
    import uuid
    from datetime import datetime

    from sqlalchemy import desc
    from sqlmodel import select as sm_select

    from app.database import AsyncSessionLocal as async_session
    from app.models.activity import Activity
    from app.models.deal import Deal
    from app.services.deal_health import compute_health

    deal_id = args.get("deal_id", "")
    if not deal_id:
        return ToolOutcome(result_text="deal_id is required.", artifacts=[])

    async with async_session() as session:
        deal_result = await session.execute(
            sm_select(Deal).where(Deal.id == uuid.UUID(deal_id))
        )
        deal = deal_result.scalar_one_or_none()
        if not deal:
            return ToolOutcome(result_text="Deal not found.", artifacts=[])

        activities_result = await session.execute(
            sm_select(Activity)
            .where(Activity.deal_id == uuid.UUID(deal_id))
            .order_by(desc(Activity.created_at))
            .limit(1)
        )
        activities = list(activities_result.scalars().all())

    score, health = compute_health(deal, activities)

    days_since_activity = None
    engagement_score = 0
    if activities:
        days_since_activity = (datetime.utcnow() - activities[0].created_at).days
        if days_since_activity <= 3:
            engagement_score = 40
        elif days_since_activity <= 7:
            engagement_score = 33
        elif days_since_activity <= 14:
            engagement_score = 22
        elif days_since_activity <= 30:
            engagement_score = 10

    stakeholders = deal.stakeholder_count or 0
    stakeholder_score = 30 if stakeholders >= 3 else (20 if stakeholders == 2 else (10 if stakeholders == 1 else 0))

    days_in_stage = deal.days_in_stage or 0
    if days_in_stage <= 7:
        velocity_score = 30
    elif days_in_stage <= 14:
        velocity_score = 24
    elif days_in_stage <= 30:
        velocity_score = 14
    elif days_in_stage <= 60:
        velocity_score = 5
    else:
        velocity_score = 0

    suggestions: list[str] = []
    if engagement_score < 40:
        suggestions.append(
            "Log a call or email — last touch was "
            f"{f'{days_since_activity} days ago' if days_since_activity is not None else 'never'}. "
            "Logging activity within 3 days adds 40 engagement points."
        )
    if stakeholder_score < 30:
        suggestions.append(
            f"Link more contacts — {stakeholders} stakeholder(s) linked. "
            "3+ gives full 30 stakeholder points."
        )
    if velocity_score < 30:
        suggestions.append(
            f"Deal has been in '{deal.stage}' for {days_in_stage} days. "
            "Moving to the next stage resets the velocity clock."
        )

    return ToolOutcome(
        result_text=(
            f"Health for {deal.name}: {health.upper()} (score: {score}/100)\n\n"
            "Breakdown:\n"
            f"  • Engagement recency: {engagement_score}/40"
            + (f" (last touch {days_since_activity}d ago)" if days_since_activity is not None else " (no activity logged)")
            + f"\n  • Stakeholder coverage: {stakeholder_score}/30 ({stakeholders} contact(s) linked)\n"
            f"  • Stage velocity: {velocity_score}/30 ({days_in_stage} days in '{deal.stage}')\n\n"
            + (
                "To improve health:\n" + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(suggestions))
                if suggestions else "Health is strong across all dimensions."
            )
        ),
        artifacts=[],
    )


async def _execute_find_entity_for_task(args: dict) -> ToolOutcome:
    """Read-only lookup of a deal / contact / company to get its ID before
    creating a task linked to it."""
    from sqlalchemy import or_
    from sqlmodel import select as sm_select

    from app.database import AsyncSessionLocal as async_session
    from app.models.company import Company
    from app.models.contact import Contact
    from app.models.deal import Deal

    query = (args.get("query") or "").strip()
    entity_type = args.get("entity_type", "deal")

    if not query:
        return ToolOutcome(result_text="Please provide a name to search for.", artifacts=[])

    async with async_session() as session:
        if entity_type == "deal":
            stmt = (
                sm_select(Deal, Company)
                .join(Company, Deal.company_id == Company.id, isouter=True)
                .where(
                    or_(
                        Deal.name.ilike(f"%{query}%"),
                        Company.name.ilike(f"%{query}%"),
                    )
                )
                .limit(5)
            )
            result = await session.execute(stmt)
            rows = result.all()
            if not rows:
                return ToolOutcome(result_text=f"No deal found matching '{query}'.", artifacts=[])
            if len(rows) > 1:
                options = "\n".join(f"- {d.name} (Stage: {d.stage})" for d, c in rows)
                return ToolOutcome(
                    result_text=f"Found {len(rows)} deals:\n{options}\n\nWhich one?",
                    artifacts=[],
                )
            deal, _company = rows[0]
            return ToolOutcome(
                result_text=f"Found deal: {deal.name} | Stage: {deal.stage} | ID: {deal.id}",
                artifacts=[{
                    "type": "entity_found",
                    "entity_type": "deal",
                    "entity_id": str(deal.id),
                    "entity_name": deal.name,
                }],
            )

        if entity_type == "company":
            stmt = sm_select(Company).where(Company.name.ilike(f"%{query}%")).limit(5)
            result = await session.execute(stmt)
            companies = list(result.scalars().all())
            if not companies:
                return ToolOutcome(result_text=f"No company found matching '{query}'.", artifacts=[])
            if len(companies) > 1:
                options = "\n".join(f"- {c.name}" for c in companies)
                return ToolOutcome(
                    result_text=f"Found {len(companies)} companies:\n{options}\n\nWhich one?",
                    artifacts=[],
                )
            c = companies[0]
            return ToolOutcome(
                result_text=f"Found company: {c.name} | ID: {c.id}",
                artifacts=[{
                    "type": "entity_found",
                    "entity_type": "company",
                    "entity_id": str(c.id),
                    "entity_name": c.name,
                }],
            )

        # contact
        stmt = (
            sm_select(Contact)
            .where(
                or_(
                    Contact.first_name.ilike(f"%{query}%"),
                    Contact.last_name.ilike(f"%{query}%"),
                )
            )
            .limit(5)
        )
        result = await session.execute(stmt)
        contacts = list(result.scalars().all())
        if not contacts:
            return ToolOutcome(result_text=f"No contact found matching '{query}'.", artifacts=[])
        if len(contacts) > 1:
            options = "\n".join(
                f"- {c.first_name} {c.last_name} ({c.email or 'no email'})" for c in contacts
            )
            return ToolOutcome(
                result_text=f"Found {len(contacts)} contacts:\n{options}\n\nWhich one?",
                artifacts=[],
            )
        c = contacts[0]
        return ToolOutcome(
            result_text=f"Found contact: {c.first_name} {c.last_name} | ID: {c.id}",
            artifacts=[{
                "type": "entity_found",
                "entity_type": "contact",
                "entity_id": str(c.id),
                "entity_name": f"{c.first_name} {c.last_name}",
            }],
        )


async def _execute_create_task(args: dict, *, user_id: Optional[UUID] = None) -> ToolOutcome:
    """Create a manual, AE-assigned task linked to a deal/contact/company.
    Always task_type='manual' and task_track='manual'."""
    import uuid
    from datetime import datetime

    from app.database import AsyncSessionLocal as async_session
    from app.models.task import Task

    title = (args.get("title") or "").strip()
    entity_type = args.get("entity_type", "")
    entity_id = args.get("entity_id", "")

    if not title:
        return ToolOutcome(result_text="Task title is required.", artifacts=[])
    if entity_type not in ("deal", "contact", "company"):
        return ToolOutcome(result_text="entity_type must be deal, contact, or company.", artifacts=[])
    if not entity_id:
        return ToolOutcome(result_text="entity_id is required.", artifacts=[])

    due_at = None
    if args.get("due_at"):
        try:
            due_at = datetime.fromisoformat(args["due_at"])
        except ValueError:
            pass

    # Auto-calculate priority from due_at: within 24h→high, 24-48h→medium, beyond→low
    if due_at:
        hours_until_due = (due_at - datetime.utcnow()).total_seconds() / 3600
        if hours_until_due <= 24:
            priority = "high"
        elif hours_until_due <= 48:
            priority = "medium"
        else:
            priority = "low"
    else:
        priority = args.get("priority", "medium")
        if priority not in ("low", "medium", "high"):
            priority = "medium"

    async with async_session() as session:
        task = Task(
            entity_type=entity_type,
            entity_id=uuid.UUID(entity_id),
            task_type="manual",
            task_track="manual",
            title=title,
            description=args.get("description") or None,
            status="open",
            priority=priority,
            due_at=due_at,
            created_by_id=user_id,
            assigned_to_id=user_id,
            assigned_role="ae",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(task)
        await session.commit()

    due_label = due_at.strftime("%d %b %Y %H:%M") if due_at else "no due date"
    return ToolOutcome(
        result_text=(
            f"✅ Task created: {title}\n"
            f"Due {due_label} · Priority: {priority}\n"
            f"Linked to: {entity_type} — open it in Pipeline to view."
        ),
        artifacts=[{
            "type": "task_created",
            "title": title,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "entity_name": args.get("entity_name", ""),
            "due_at": args.get("due_at", ""),
            "priority": priority,
            "deal_link": f"/pipeline?deal={entity_id}" if entity_type == "deal" else "",
        }],
    )


async def _execute_generic(args: dict, *, user_id: Optional[UUID] = None) -> ToolOutcome:
    data = GenericDocInput(
        title=args.get("title", "Draft"),
        markdown=args.get("markdown", ""),
        client_name=args.get("client_name"),
    )
    doc = await generate_generic(data, user_id=str(user_id) if user_id else None)
    return ToolOutcome(
        result_text=(
            f"✅ Document generated: {data.title}.\n"
            f"{_doc_link_text(doc)}"
        ),
        artifacts=[_doc_to_artifact(doc)],
    )

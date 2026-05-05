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
        if name == "generate_document":
            return await _execute_generic(args, user_id=user_id)
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

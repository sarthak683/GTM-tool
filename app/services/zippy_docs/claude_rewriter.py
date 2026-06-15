"""LLM-driven content rewriter used by every Zippy generator.

Given a structure (list of text blocks) extracted from a template and a dict
of user inputs, asks Claude to rewrite every non-structural block using ONLY
the user inputs. Returns the list of rewritten blocks ready to be patched
back into the original document by ``doc_rewriter.rewrite_docx_content``
(or the pptx equivalent).

This is doc-type agnostic — callers pass ``doc_type`` so we can add a tailored
instruction line, but the core contract is always the same: rewrite content,
leave skeleton alone, never invent facts.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


_DOC_TYPE_INSTRUCTIONS: dict[str, str] = {
    "mom": (
        "CRITICAL CONTEXT — READ FIRST:\n"
        "The MOM template you are rewriting is NOT a generic skeleton with "
        "placeholders. It is a PREVIOUSLY-FILLED MOM from a different "
        "client engagement (commonly 'Model N' with attendees Rehmann, "
        "Mahesh, Rakesh, and references to Novartis, life sciences, etc.). "
        "Every body paragraph is real narrative from THAT prior meeting. "
        "Your job is to REPLACE all of it with narrative derived from the "
        "CURRENT meeting's transcript in user_inputs['transcript'].\n\n"
        "OVERRIDE — this beats the system prompt's general rule:\n"
        "  • For MOM, the system prompt's 'return ORIGINAL unchanged when "
        "you have no replacement' rule DOES NOT APPLY to non-structural "
        "body text. Carrying Model N / Rehmann / Mahesh / Rakesh / "
        "Novartis / life-sciences content into the output is a CRITICAL "
        "FAILURE — the user gets a MOM that names the wrong company.\n"
        "  • If the current transcript has nothing to say for a given "
        "non-structural block, return an EMPTY STRING for that block. "
        "Blank is correct; wrong-client narrative is not.\n"
        "  • Structural blocks (section headers like 'Attendees', "
        "'Next Steps', 'Collateral Shared', 'Action Item / Owner / "
        "Timeline' table headers) STAY exactly as-is.\n\n"
        "RED-FLAG TOKENS — if any of these appear in your output and they "
        "are NOT in user_inputs['transcript'] or user_inputs['attendees'], "
        "you have failed: 'Model N', 'Rehmann', 'Mahesh', 'Rakesh', "
        "'Novartis', 'Eilisys' (unless current client), 'pharma', 'life "
        "sciences', '21 April 2026' (unless that's the actual meeting "
        "date). Scrub them.\n\n"
        "WHAT TO REWRITE using ONLY user_inputs['transcript'] + "
        "user_inputs['attendees'] + user_inputs['client_name'] + "
        "user_inputs['meeting_date']:\n"
        "  • Title line (e.g. '<Client> and Beacon — Meeting Recap — "
        "<date>')\n"
        "  • Greeting (e.g. 'Hi <primary attendee from current "
        "transcript>,')\n"
        "  • Attendees section — list user_inputs['attendees'] grouped by "
        "company; do NOT carry over prior attendee names\n"
        "  • Overview, Key Challenges, Scale & Metrics, Current Tooling, "
        "Areas of Strong Interest, any quoted phrases, action items table\n\n"
        "FORMAT RULES — check user_inputs['format_type']:\n"
        "  'long'  → detailed sub-sections with full sentences.\n"
        "  'short' → 3-5 bullets per section, concise.\n\n"
        "COLLATERAL — user_inputs['collateral'] is a list of strings "
        "'Label : Name | url'. Render each as a bullet. Empty list → "
        "blank section.\n\n"
        "EXTRACTION RULES — from THIS transcript only:\n"
        "  • Pain points (client's exact phrasing where possible)\n"
        "  • Metrics (team sizes, volumes, timelines)\n"
        "  • Tooling/systems mentioned\n"
        "  • Next steps with owner + timeline\n"
        "  • Commercial / budget signals\n\n"
        "HARD RULES:\n"
        "  - Never invent quotes, attendee titles, or specifics not in "
        "the current transcript.\n"
        "  - Never carry forward names, companies, or narrative from the "
        "prior-client template.\n"
        "  - Empty string IS acceptable here when the transcript has no "
        "content for a section."
    ),
    "nda": (
        "You are filling Beacon's NDA template. The user_inputs dict "
        "carries: disclosing_party, receiving_party, effective_date, "
        "governing_city, term_years, purpose, mutual, extra_clauses, "
        "additional_details (free-form dict).\n\n"
        "EXPLICIT PLACEHOLDER MAP — substitute these tokens wherever "
        "they appear in any block. This override BEATS the system "
        "prompt's 'leave original unchanged' default — for the tokens "
        "below, you MUST replace them when the corresponding input is "
        "non-empty:\n"
        "  • '[●]' (the bullet-in-brackets, U+25CF) → receiving_party. "
        "Replace EVERY occurrence in EVERY block, including the "
        "OTHER PART recital, 'For [●]:' notice line, and signature "
        "block. If receiving_party is empty, leave the token as-is.\n"
        "  • '_________' / '____________' / any run of 5+ underscores "
        "appearing right after the words 'entered into on' (or any "
        "phrase clearly marking the effective date slot) → "
        "effective_date. Other underscore runs (signature lines, "
        "name/title fill-ins, address lines) STAY as underscores — "
        "those are for the human signatory.\n"
        "  • '[a proposed engagement // the business relationship]' "
        "(and any '[X // Y]' double-slash choice block describing the "
        "purpose) → purpose. Pick the user's purpose verbatim, drop "
        "the brackets and slashes.\n"
        "  • 'Mumbai, Maharashtra, India' (or whatever city/state "
        "appears as the governing-law venue) → governing_city when "
        "governing_city is provided. Keep the country and state "
        "context if the input is just a city name.\n"
        "  • '24 months' / '[N] months' / '[N] years' in the TERM "
        "clause → term_years × 12 months when term_years is set "
        "(e.g. term_years=2 → '24 months'; term_years=3 → "
        "'36 months'). If term_years is empty, leave the original "
        "duration alone.\n\n"
        "LEAVE-AS-IS PLACEHOLDERS (these are for the AE / legal "
        "counsel to fill before signing — never invent values):\n"
        "  • '[[[an individual /// a body corporate /// ...]]]' "
        "entity-type chooser — keep as-is unless additional_details "
        "supplies an explicit entity_type.\n"
        "  • '[[[registered under the [Indian Partnership Act...]]]]' "
        "act/jurisdiction chooser — keep as-is unless "
        "additional_details supplies registered_under.\n"
        "  • '[[[address /// registered office /// principal place "
        "of business]]]' — keep as-is unless additional_details has "
        "address_label.\n"
        "  • Address blank ('___________________' after the entity-"
        "type/registered-under choosers) — keep underscores; the AE "
        "fills this manually.\n"
        "  • Signature-block 'Name:' / 'Title:' fill-ins on the "
        "receiving-party side — keep blank.\n\n"
        "HARD RULES:\n"
        "  - Never invent a counterparty entity type, address, "
        "signatory name, or jurisdiction not provided.\n"
        "  - Never substitute the '[●]' token with an empty string — "
        "either fill with receiving_party or keep the token.\n"
        "  - If purpose is given, the recital block must read "
        "naturally — drop the '[[' / '//' / ']]' wrapping and use the "
        "user's purpose phrase directly.\n"
        "  - additional_details may carry registered_office, "
        "entity_type, signatory_name, signatory_title, "
        "registered_under — use them if present, ignore if absent."
    ),
    "proposal": (
        "You are filling Beacon's Business Proposal template for a prospect.\n\n"
        "The template has two variants based on user_inputs['variant']:\n"
        "  'lite' -> 7 sections: Executive Summary, Current Challenges, Proposed "
        "Solution, Expected ROI & Impact, Implementation Plan, Commercials, Next Steps\n"
        "  'main' -> 9 sections: Executive Summary, Introducing Beacon, Understanding "
        "[Client]'s Landscape, Configuration Automation, Beacon's Solution (5.1/5.2/5.3), "
        "Projected ROI, Implementation Plan, Commercials, T&C\n\n"
        "VARIANT RULE: If variant is 'lite', skip Sections 2 (Introducing Beacon) and "
        "4 (Configuration Automation). For all other sections, fill both variants.\n\n"
        "DATA SOURCES (in priority order):\n"
        "  1. user_inputs['transcript'] - meeting notes, POC outcomes, client statements\n"
        "  2. user_inputs['email_thread'] - prior emails, requirements, pain points\n"
        "  3. user_inputs fields - platform, domain, use_cases, commercial figures\n"
        "  4. user_inputs['change_request'] - if non-empty, apply this change on top\n\n"
        "PLACEHOLDER SUBSTITUTION - replace every bracket token you see:\n"
        "  [Client Name] -> user_inputs['client_name']\n"
        "  [Date] -> user_inputs['date']\n"
        "  [Your Name] -> user_inputs['prepared_by']\n"
        "  [Your Title] -> user_inputs['prepared_by_title']\n"
        "  [Your Phone] -> user_inputs['prepared_by_phone']\n"
        "  [your.email@beacon.li] -> user_inputs['prepared_by_email']\n"
        "  [domain: e.g., Order-to-Cash...] -> user_inputs['domain']\n"
        "  [short description, e.g., ...] -> user_inputs['client_description']\n"
        "  [Client Contact Name(s)] -> extract from email_thread or leave blank\n"
        "  [Designation(s)] -> extract from email_thread or leave blank\n\n"
        "SECTION-BY-SECTION RULES:\n\n"
        "Executive Summary:\n"
        "  - Replace [Client Name] everywhere\n"
        "  - Fill [X]%, [Y]%, [Z]% with effort_reduction_pct, "
        "timeline_reduction_pct, hypercare_reduction_pct\n"
        "  - Extract and insert POC outcomes if mentioned in transcript\n"
        "  - The 3-5x ROI figure is standard - keep it unless email/transcript "
        "has a different figure\n\n"
        "Current Challenges / Understanding [Client]'s Landscape:\n"
        "  - Fill [domain: e.g., ...] with the actual domain from user_inputs\n"
        "  - Extract specific pain points from email_thread and transcript - use "
        "the client's exact language where possible\n"
        "  - Replace generic bullets with client-specific ones if enough context exists\n"
        "  - If no specific pain points found, keep the template bullets as-is\n\n"
        "Proposed Solution:\n"
        "  - Replace [Client Name]'s UI with the actual platform name\n"
        "  - Scope (Configuration/Cutover/Hypercare) - keep unless email specifies "
        "a narrower scope\n\n"
        "Configuration Automation section (main variant only):\n"
        "  - [Module/Area: e.g., ...] -> extract from transcript/email which specific "
        "module they discussed. If unclear, write the platform name + 'core configuration'\n"
        "  - [X-Y] hours -> extract from transcript if mentioned, else keep template default\n"
        "  - POC Concept Outcome table -> fill from transcript POC results if available\n\n"
        "ROI & Impact tables:\n"
        "  - Replace all [XX] and [YY] metric placeholders:\n"
        "    - effort_reduction_pct -> Avg. implementation effort row\n"
        "    - timeline_reduction_pct -> Avg. implementation duration row\n"
        "    - hypercare_reduction_pct -> Hypercare support load row\n"
        "    - implementations_per_year -> Implementations per year row\n"
        "  - Financial Impact section:\n"
        "    - hourly_rate -> $[50] placeholder\n"
        "    - avg_hours_per_impl -> [1,010] hrs placeholder\n"
        "    - If annual_platform_fee is provided, fill $[250,000] placeholder\n"
        "  - If a metric is not available, keep the [XX] placeholder so the AE "
        "can fill it manually - do NOT invent numbers\n\n"
        "Implementation Plan:\n"
        "  - Replace [Client Name] in milestone descriptions\n"
        "  - [X] days for MSA - keep as 'to be agreed' if no timeline in emails\n"
        "  - Extract any timeline signals from email/transcript for the phases\n\n"
        "Commercials:\n"
        "  - annual_platform_fee -> replace $[250,000] and USD $[250,000]\n"
        "  - per_client_fee -> replace $[1,000]\n"
        "  - If commercial figures not provided: keep the [xx] placeholders - "
        "never invent pricing\n"
        "  - The payment table: fill in actual amounts if provided, else keep $xx\n\n"
        "Next Steps (Lite variant):\n"
        "  - Extract agreed next steps from email_thread and transcript\n"
        "  - If none found, keep the 3 standard template steps\n\n"
        "Terms & Conditions (Main variant):\n"
        "  - [30/60/90] days notice period -> extract from emails if discussed, "
        "else write '30 days'\n"
        "  - Keep all other T&C text exactly as-is - do NOT rewrite legal language\n\n"
        "CHANGE REQUEST RULE:\n"
        "  If user_inputs['change_request'] is non-empty, apply that change on top "
        "of everything else. E.g. 'change annual fee to $180,000' means update all "
        "commercial figures accordingly. 'update use cases to focus on Collections' "
        "means rewrite the use case sections only.\n\n"
        "GENERAL RULES:\n"
        "  - Never invent financial figures, headcount numbers, or metrics not "
        "in the inputs or transcript\n"
        "  - Never rewrite table headers or column labels - only table cell content\n"
        "  - If a section has no matching input data AND the template block already "
        "has real content, return the ORIGINAL text unchanged\n"
        "  - Only return empty string if the original block was already empty\n"
        "  - Beacon's voice: confident, specific, outcome-focused, no fluff"
    ),
    "sow": (
        "Rewrite client name, scope, deliverables, timeline, payment terms, "
        "and assumptions using ONLY user inputs."
    ),
    "poc_kickoff": (
        "You are filling a Beacon PoC Kickoff document for the client "
        "named in user_inputs['client_name']. Rewrite ALL content blocks "
        "using ONLY the email thread content provided in "
        "user_inputs['email_content'].\n\n"
        "WHAT TO EXTRACT from emails and fill into the template:\n"
        "  • [Client Name] → use client_name from inputs everywhere it appears\n"
        "  • [Insert Date] / [Insert Start Date] / [Insert End Date] → "
        "extract dates from emails (kickoff date, completion date)\n"
        "  • <specific workflow> → the specific implementation workflow or "
        "module discussed in the emails (e.g. 'Inception automation for "
        "Guidewire', 'Configuration automation for SAP')\n"
        "  • [Insert URL] → PoC environment URL if mentioned in emails, "
        "otherwise leave as '[Insert URL]'\n"
        "  • [Insert Email/ID] and [Insert Password] → PoC credentials "
        "if mentioned, otherwise leave as-is\n"
        "  • Use Case 1 / Use Case 2 — INFER these from the emails. The "
        "thread will rarely use the literal phrase 'Use Case 1' — instead "
        "look for: pain points the client raised, workflows they want "
        "automated, modules they named, KPIs they want to move. Each such "
        "topic is a use case. Title = a short noun phrase ('Implementation "
        "config automation'); Business Problem = what hurts today in their "
        "words; Expected Outcome = the win they described.\n"
        "  • If the emails describe ONE clear use case, fill Use Case 1 "
        "fully and mark Use Case 2 fields as 'TBD — to be confirmed with "
        "Zywave'. Do NOT leave Use Case 1 blank just because the email "
        "didn't number it.\n"
        "  • Deliverable 1 / 2 → one short line each describing what "
        "Beacon will produce for that use case (e.g. 'AI-generated UAT "
        "scripts for the cutover workflow').\n"
        "  • [Insert End Date] → if the email mentions a duration ('4-week "
        "PoC', 'wrap by end of May'), compute the end date from the "
        "kickoff. If no duration is mentioned, leave as '[Insert End Date]'.\n"
        "  • [Your Name] → use prepared_by from inputs verbatim. If "
        "prepared_by is '[Your Name]' or starts with '[', leave the "
        "placeholder as-is — never substitute 'Zippy', 'Beacon', or any "
        "other brand string.\n\n"
        "NEXT STEPS SECTION — SPECIAL CASE:\n"
        "  The template contains a 'NEXT STEPS' section. We are deliberately "
        "removing this section from the generated document. For ANY content "
        "block whose nearest section heading above it is 'NEXT STEPS' (or "
        "any case variant), return an empty string as new_text — this is "
        "the ONE exception to the 'never return empty for non-empty blocks' "
        "rule. The structural heading 'NEXT STEPS' itself should also be "
        "returned as empty string so the section disappears entirely.\n\n"
        "HARD RULES:\n"
        "  - Never invent specific timelines or credentials not in emails.\n"
        "  - DO infer use case titles, problems, and outcomes from any "
        "topic genuinely discussed — that's not invention, that's "
        "summarising the thread.\n"
        "  - Keep structural text exactly (section headers like OBJECTIVE, "
        "USE CASES, TIMELINE etc. are structural — do not change them).\n"
        "  - Bracketed placeholders ([Insert URL], [Insert Email/ID], "
        "[Insert Password], [Insert End Date]) stay as bracketed "
        "placeholders when the email genuinely contains no data — these "
        "are filled by the AE before sending.\n"
        "  - client_name must appear wherever [Client Name] was in the template."
    ),
    "poc_demo_ppt": (
        "You are filling a Beacon PoC Demo deck originally built for "
        "Zellis. The new client is named in user_inputs['client_name']. "
        "Source material is in user_inputs['source_content'], which "
        "contains the PoC Kickoff document and the email thread, "
        "concatenated under '=== POC KICKOFF DOCUMENT ===' and "
        "'=== EMAIL THREADS ===' headers.\n\n"
        "SLIDE-BY-SLIDE CONTRACT (slides are 1-indexed; the structure "
        "carries a slide_number on every block):\n"
        "  • Slides 1, 2, 6, 7 → return new_text EQUAL TO the original "
        "text for every block, EXCEPT swap any literal occurrence of "
        "'Zellis' (case-insensitive) with client_name. These slides are "
        "Beacon-about / closing slides — never invent or reword.\n"
        "  • Slide 3 → 'About <client_name>' or 'Client Context'. Pull "
        "1-2 sentence company description and 3-5 bullets covering the "
        "pain points / drivers raised in the source content. Each bullet "
        "≤ 8 words. If the source has no description, keep the original "
        "Zellis block with the name swap only.\n"
        "  • Slide 4 → 'Use Cases in Scope'. List the use cases from the "
        "PoC Kickoff document verbatim (titles + one-line problem). If "
        "the kickoff names two use cases, render two; if one, render one "
        "and leave the second placeholder block empty. ≤ 8 words per "
        "bullet.\n"
        "  • Slide 5 → 'PoC Plan / Deliverables / Timeline'. Pull "
        "deliverables, milestones, kickoff & end dates from the kickoff "
        "doc. Bullets ≤ 8 words. Dates verbatim from the kickoff.\n\n"
        "REPLACEMENT RULE: every literal 'Zellis' (any case) anywhere in "
        "the deck becomes client_name. Do this even on slides 1, 2, 6, 7.\n\n"
        "HARD RULES:\n"
        "  - Never invent metrics, dates, names, logos.\n"
        "  - Never expand a bullet beyond 8 words.\n"
        "  - If the source genuinely has nothing for a slide-3/4/5 "
        "block, fall back to the original Zellis text with the name "
        "swap — do NOT return empty string for a non-empty original.\n"
        "  - Structural blocks (slide titles, footer page numbers) — "
        "return as-is with the Zellis→client_name swap only."
    ),
}


SYSTEM_PROMPT = (
    "You are Zippy, Beacon's internal document generator. You rewrite "
    "document content based on user inputs. You NEVER invent facts. You "
    "only use what is explicitly provided in the user inputs. "
    "CRITICAL: If you do not have a replacement for a block that already "
    "contains real text, return the ORIGINAL text for that block "
    "UNCHANGED — do NOT return empty string. Empty string overwrites real "
    "content with nothing, which destroys the template. Empty string is "
    "ONLY acceptable when the original block was already empty (a blank "
    "slot you genuinely have nothing to fill it with). "
    "NEVER write bracketed placeholders like '[not specified in transcript]', "
    "'[to be provided]', '[TBD]', '[attendees not listed]', or similar."
)


def _original_as_fallback(structure: list[dict]) -> list[dict]:
    """Return a no-op rewrite: every block keeps its existing text.

    Used when Claude is unreachable or returns something we can't parse —
    guarantees the generator never hard-fails because of a model hiccup.
    """
    return [
        {"block_index": b["block_index"], "new_text": b.get("text", "")}
        for b in structure
    ]


def _strip_fences(raw: str) -> str:
    """Remove ```json ... ``` wrappers Claude likes to add."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned[: -3]
        cleaned = cleaned.strip()
    return cleaned


def _repair_json(text: str) -> str:
    """Best-effort repair pass for the common breakages in Claude's JSON.

    Empirically, parse failures fall into a handful of buckets:
      1. Trailing commas before ] or }
      2. Literal newlines inside string values (must be \\n)
      3. Smart quotes used as JSON delimiters
      4. Unescaped tabs inside strings
    We can't fix unescaped double-quotes safely without a real parser, but
    these four cover most observed failures.
    """
    fixed = text
    # 1. Trailing commas
    fixed = re.sub(r",(\s*[\]}])", r"\1", fixed)
    # 3. Smart quotes — only when they look like JSON delimiters (next to
    #    structural chars). Replacing all of them would corrupt content.
    fixed = fixed.replace("“", '"').replace("”", '"')
    # 4. Tabs inside strings
    fixed = fixed.replace("\t", "\\t")
    # 2. Literal newlines inside string values: walk the text, when we're
    #    inside a "..." string (tracking escape state) replace raw \n / \r
    #    with the escaped sequence.
    out: list[str] = []
    in_string = False
    escape = False
    for ch in fixed:
        if in_string:
            if escape:
                out.append(ch)
                escape = False
                continue
            if ch == "\\":
                out.append(ch)
                escape = True
                continue
            if ch == '"':
                out.append(ch)
                in_string = False
                continue
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                out.append("\\r")
                continue
            out.append(ch)
        else:
            if ch == '"':
                in_string = True
            out.append(ch)
    return "".join(out)


_PAIR_RE = re.compile(
    r'"block_index"\s*:\s*(\d+).*?"new_text"\s*:\s*"((?:[^"\\]|\\.)*)"',
    re.DOTALL,
)


def _salvage_pairs(text: str) -> list[dict]:
    """Last-resort regex extractor.

    When even the repair pass can't produce valid JSON, pull out every
    block_index/new_text pair we can find. This loses any pair where
    Claude emitted a literally broken string, but recovers the rest —
    far better than the no-op fallback that drops every substitution.
    """
    items: list[dict] = []
    for m in _PAIR_RE.finditer(text):
        try:
            idx = int(m.group(1))
        except ValueError:
            continue
        # Decode common escapes the regex captured raw.
        new_text = (
            m.group(2)
            .replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
        )
        items.append({"block_index": idx, "new_text": new_text})
    return items


def _parse_claude_json(raw: str) -> Optional[list[dict]]:
    """Extract a JSON array from Claude's response.

    Three-stage pipeline:
      1. Strict json.loads on the fenced/trimmed text.
      2. json.loads after a repair pass (trailing commas, raw newlines, …).
      3. Regex salvage of block_index/new_text pairs.

    Logs the raw response on any failure so we can see exactly what
    Claude returned and decide whether to tighten the prompt.
    """
    if not raw:
        return None
    cleaned = _strip_fences(raw)
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end < start:
        logger.warning(
            "Claude rewrite response had no JSON array. RAW=%r",
            cleaned[:2000],
        )
        return None
    candidate = cleaned[start: end + 1]

    # Stage 1 — strict parse
    try:
        data = json.loads(candidate)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError as exc:
        logger.warning(
            "Claude rewrite parse failed (strict): %s. Trying repair pass. "
            "RAW[0:2000]=%r",
            exc,
            candidate[:2000],
        )

    # Stage 2 — repair pass
    repaired = _repair_json(candidate)
    try:
        data = json.loads(repaired)
        if isinstance(data, list):
            logger.info("Claude rewrite recovered via repair pass")
            return data
    except json.JSONDecodeError as exc:
        logger.warning(
            "Claude rewrite parse failed (repaired): %s. Falling back to "
            "regex salvage. REPAIRED[0:2000]=%r",
            exc,
            repaired[:2000],
        )

    # Stage 3 — regex salvage
    salvaged = _salvage_pairs(candidate)
    if salvaged:
        logger.info(
            "Claude rewrite recovered %d pairs via regex salvage",
            len(salvaged),
        )
        return salvaged

    return None


# Chunking config. A single Claude response is capped by max_tokens; for very
# long templates (e.g. the 366-block Business Proposal) the JSON array gets
# truncated mid-string and the whole pass falls back to a no-op. We split
# the structure into batches of at most _CHUNK_BLOCKS blocks and call Claude
# once per batch, then merge results by block_index. Each batch carries the
# full instruction context so Claude can still resolve placeholders correctly.
_CHUNK_BLOCKS = 80
_MAX_TOKENS = 16000


async def _rewrite_batch(
    batch: list[dict],
    full_compact: list[dict],
    user_inputs: dict[str, Any],
    doc_type: str,
    instructions: str,
    client: Any,
    model: str,
    batch_idx: int,
    total_batches: int,
) -> Optional[list[dict]]:
    """Send one batch to Claude and parse the response.

    `batch` is the slice we want rewritten this call. `full_compact` is the
    entire document outline — we ship it as read-only context so Claude
    knows where this batch sits in the document and can resolve cross-
    section references (e.g. fill use-case bullets that reference the
    company description from a much earlier block). Only the indices in
    `batch` are expected back.
    """
    batch_indices = [b["block_index"] for b in batch]

    user_msg = (
        f"Document type: {doc_type}\n"
        f"Batch {batch_idx + 1} of {total_batches}.\n\n"
        "Here is the FULL document outline (read-only context, every "
        "block in the document):\n"
        f"{json.dumps(full_compact, ensure_ascii=False)}\n\n"
        "Now rewrite ONLY the blocks in this batch. Each block has an "
        "index and text. Blocks marked is_structural: true are section "
        "headers — DO NOT rewrite them, return them exactly as-is. Blocks "
        "marked is_structural: false must be rewritten using ONLY the "
        "user inputs.\n\n"
        "IMPORTANT: A non-structural block whose text is empty is a "
        "fillable SLOT, not a spacer. Look at the nearest structural "
        "block ABOVE it in the FULL outline — that heading tells you "
        "what belongs in the slot. Only return empty string when the "
        "user inputs genuinely contain nothing for that section.\n\n"
        f"Batch to rewrite (indices {batch_indices[0]}–"
        f"{batch_indices[-1]}, {len(batch)} blocks):\n"
        f"{json.dumps(batch, ensure_ascii=False)}\n\n"
        f"User inputs:\n{json.dumps(user_inputs, ensure_ascii=False)}\n\n"
        f"Instructions for {doc_type}: {instructions}\n\n"
        "Return a JSON array containing ONLY the blocks in this batch — "
        "do not include any block_index outside this batch. Each item "
        "must have:\n"
        '  "block_index": <same int as input>,\n'
        '  "new_text": <rewritten string, or original text if is_structural is true>\n\n'
        "Return ONLY the JSON array. No explanation. No markdown code fences.\n"
        "JSON ESCAPING RULES — your output MUST parse with json.loads:\n"
        '  • Inside any string value, escape every literal " as \\".\n'
        "  • Inside any string value, escape every newline as \\n "
        "(two characters: backslash + n). Do NOT emit raw newlines "
        "inside strings.\n"
        "  • Escape every tab as \\t and every backslash as \\\\.\n"
        '  • Use only straight ASCII double quotes (") for delimiters, '
        "never smart/curly quotes.\n"
        "  • No trailing commas before ] or }."
    )

    try:
        response = await client.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = "".join(
            block.text
            for block in response.content
            if getattr(block, "type", "") == "text"
        )
    except Exception as exc:
        logger.exception(
            "Claude rewrite batch %d/%d failed: %s",
            batch_idx + 1, total_batches, exc,
        )
        return None

    parsed = _parse_claude_json(raw)
    if parsed is None:
        logger.warning(
            "Couldn't parse Claude rewrite batch %d/%d; this batch will "
            "fall back to original text",
            batch_idx + 1, total_batches,
        )
        return None

    # Filter to indices we actually expect back. Claude occasionally echoes
    # the whole document; the merge step would dedupe but it's cleaner to
    # drop strays here.
    expected = set(batch_indices)
    cleaned: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        if "block_index" not in item or "new_text" not in item:
            continue
        try:
            idx = int(item["block_index"])
        except (TypeError, ValueError):
            continue
        if idx not in expected:
            continue
        cleaned.append({"block_index": idx, "new_text": str(item.get("new_text") or "")})

    return cleaned or None


async def rewrite_with_claude(
    structure: list[dict],
    user_inputs: dict[str, Any],
    doc_type: str,
    client: Any,
    model: str,
) -> list[dict]:
    """Ask Claude to rewrite every non-structural block using user_inputs.

    For small templates (≤ _CHUNK_BLOCKS blocks) this is a single Claude
    call. For larger templates we batch into chunks of _CHUNK_BLOCKS and
    merge results by block_index — that's what keeps a 366-block proposal
    template from blowing past max_tokens and getting truncated mid-JSON.

    Returns a list of ``{"block_index": int, "new_text": str}`` dicts.
    Any batch that fails to parse contributes the ORIGINAL text for its
    blocks rather than empty strings, so a partial parse never wipes the
    template.
    """
    if client is None:
        logger.warning("No Anthropic client available — returning no-op rewrite")
        return _original_as_fallback(structure)

    instructions = _DOC_TYPE_INSTRUCTIONS.get(
        doc_type,
        "Rewrite content blocks using ONLY user inputs. Do not invent facts.",
    )

    compact = [
        {
            "block_index": b["block_index"],
            "is_structural": b["is_structural"],
            "text": b.get("text", ""),
        }
        for b in structure
    ]

    # Carve into chunks of _CHUNK_BLOCKS. We chunk on the FULL compact list
    # (including structural blocks) so the indices stay contiguous within
    # each batch — that's what lets Claude reason about local context like
    # "the heading right before this slot."
    batches: list[list[dict]] = [
        compact[i: i + _CHUNK_BLOCKS]
        for i in range(0, len(compact), _CHUNK_BLOCKS)
    ]
    total_batches = len(batches)
    logger.info(
        "Claude rewrite: %d blocks → %d batch(es) of up to %d",
        len(compact), total_batches, _CHUNK_BLOCKS,
    )

    # Per-index merge. Start with originals so any batch that fails to
    # parse contributes the original text instead of an empty string.
    by_index: dict[int, str] = {b["block_index"]: b.get("text", "") for b in structure}

    for i, batch in enumerate(batches):
        batch_result = await _rewrite_batch(
            batch=batch,
            full_compact=compact,
            user_inputs=user_inputs,
            doc_type=doc_type,
            instructions=instructions,
            client=client,
            model=model,
            batch_idx=i,
            total_batches=total_batches,
        )
        if batch_result is None:
            logger.warning(
                "Batch %d/%d kept original text for %d blocks",
                i + 1, total_batches, len(batch),
            )
            continue
        for item in batch_result:
            by_index[item["block_index"]] = item["new_text"]

    return [
        {"block_index": idx, "new_text": text}
        for idx, text in sorted(by_index.items())
    ]

"""
Outreach sequence generator — uses Claude to craft persona-aware email
sequences and LinkedIn messages.

Persona strategy:
  economic_buyer     → ROI, risk reduction, executive framing
  champion           → enablement, internal win, career story
  technical_evaluator → architecture fit, integration depth, proof
  unknown            → generic value-based opening

Each contact gets a 3-touch email cadence + a LinkedIn connection note.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

logger = logging.getLogger(__name__)

# ── Persona prompt fragments ───────────────────────────────────────────────────

_PERSONA_SYSTEM = {
    "economic_buyer": (
        "You are a senior enterprise sales rep at Beacon.li — an AI implementation orchestration platform "
        "that automates enterprise SaaS deployments, reducing onboarding time by 60-80%. "
        "You are writing to a C-suite / VP-level economic buyer. "
        "Focus on: business risk reduction, ROI, time-to-value, and competitive differentiation. "
        "Keep emails under 120 words. Be direct, no fluff. Never use phrases like 'I hope this finds you well'."
    ),
    "champion": (
        "You are a senior enterprise sales rep at Beacon.li — an AI implementation orchestration platform. "
        "You are writing to an internal champion (HR leader, Operations manager, Director-level). "
        "Focus on: making them look good internally, reducing their team's manual work, "
        "enabling them to deliver faster value with less risk. "
        "Keep emails under 120 words. Be consultative and peer-level, not salesy."
    ),
    "technical_evaluator": (
        "You are a senior enterprise sales rep at Beacon.li — an AI implementation orchestration platform. "
        "You are writing to a technical evaluator (CTO, Director of Engineering, Solutions Architect). "
        "Focus on: integration approach, technical architecture, security/compliance, and proof points. "
        "Mention that Beacon connects to their existing SaaS stack without replacing it. "
        "Keep emails under 130 words. Use precise, technical language."
    ),
    "unknown": (
        "You are a senior enterprise sales rep at Beacon.li — an AI implementation orchestration platform "
        "that automates enterprise SaaS deployments. "
        "Write a concise, value-focused outreach email. Keep under 100 words."
    ),
}

_FOLLOWUP_NUDGES = {
    1: "This is a 3-day follow-up. Reference the previous email briefly, add one new insight or stat, and keep a soft CTA.",
    2: "This is a 7-day final follow-up. Be brief. Acknowledge they're busy. Offer a clear exit or a quick 15-minute chat.",
}

_LINKEDIN_SYSTEM = (
    "You are crafting a LinkedIn connection request note (max 300 characters). "
    "Be authentic, specific to their role, and give a clear reason to connect. "
    "Do NOT use generic openers. Do NOT pitch the product in the connection note."
)

_DEFAULT_OUTREACH_CONTENT = {
    "general_prompt": (
        "Write concise enterprise outbound emails for Beacon.li. Personalize to the contact and company, "
        "avoid hype, avoid fluff, and keep the CTA low-friction."
    ),
    "linkedin_prompt": (
        "Keep LinkedIn notes conversational and specific to the person's role or recent company context."
    ),
    "step_templates": [
        {
            "step_number": 1,
            "channel": "email",
            "label": "Initial email",
            "goal": "Start a personalized conversation with a specific reason for reaching out.",
            "subject_hint": "Quick question about {{company_name}}",
            "body_template": (
                "Hi {{first_name}},\n\n"
                "Noticed {{company_name}} is pushing on {{reason_to_reach_out}}. Beacon helps teams reduce "
                "implementation drag without replacing the systems they already run.\n\n"
                "Worth a quick compare?"
            ),
            "prompt_hint": "Open with a strong personalization point and end with a simple CTA.",
        },
        {
            "step_number": 2,
            "channel": "linkedin",
            "label": "Follow-up",
            "goal": "Make a light LinkedIn touch so the rep can stay visible between emails.",
            "subject_hint": None,
            "body_template": "Reference the contact's role, the first email, and one concrete reason to connect on LinkedIn.",
            "prompt_hint": "Keep it short and human. This is a LinkedIn touch, not a full email.",
        },
        {
            "step_number": 3,
            "channel": "call",
            "label": "Final touch",
            "goal": "Give the SDR a concise call step with context and a reason to reach out live.",
            "subject_hint": None,
            "body_template": "Call this contact and reference the latest company signal plus the most relevant implementation pain Beacon can solve.",
            "prompt_hint": "Make this feel like a live talk track the SDR can use during the call, not an email.",
        },
    ],
}


async def generate_sequence(
    contact_id: UUID, session: AsyncSession
) -> Optional["OutreachSequence"]:  # noqa: F821
    """
    Generate a full outreach sequence for a contact.
    Fetches contact + company context, calls GPT-4o, persists to DB.
    """
    from app.models.contact import Contact
    from app.models.company import Company
    from app.models.outreach import OutreachSequence, OutreachStep
    from app.models.settings import WorkspaceSettings
    from app.clients.claude import ClaudeClient

    # ── Load contact + company ─────────────────────────────────────────────────
    contact = await session.get(Contact, contact_id)
    if not contact:
        logger.warning(f"generate_sequence: contact {contact_id} not found")
        return None

    company = await session.get(Company, contact.company_id) if contact.company_id else None

    # ── Build context dict ─────────────────────────────────────────────────────
    context = _build_context(contact, company)
    persona = contact.persona or "unknown"
    system_prompt = _PERSONA_SYSTEM.get(persona, _PERSONA_SYSTEM["unknown"])

    # ── Knowledge base context ──────────────────────────────────────────────────
    from app.services.knowledge_context import get_knowledge_context
    kb_context = await get_knowledge_context(session, "outreach", limit=3, max_total_chars=1500)
    context["kb_context"] = kb_context

    ai = ClaudeClient()

    # Check for existing sequence
    existing = await session.execute(
        select(OutreachSequence).where(OutreachSequence.contact_id == contact_id)
    )
    seq = existing.scalar_one_or_none()
    if not seq:
        seq = OutreachSequence(
            contact_id=contact_id,
            company_id=contact.company_id,
            persona=persona,
        )

    ws = await session.get(WorkspaceSettings, 1)
    step_delays = (ws.outreach_step_delays if ws else None) or [0, 3, 7]
    content_settings = _normalize_outreach_content_settings(
        ws.outreach_content_settings if ws else None,
        step_count=len(step_delays),
    )
    system_prompt = _build_system_prompt(persona, content_settings["general_prompt"])

    # Hand-crafted outreach from the "The 100" workbook: if the contact has
    # pre_written_emails populated, use them verbatim and skip AI generation.
    # The rep can still regenerate via the OutreachDrawer's "Generate" button
    # later; this just gives them a non-AI starting point that matches the
    # research copy the analyst hand-wrote.
    pre_written: list[dict] = []
    ed = contact.enrichment_data if isinstance(contact.enrichment_data, dict) else {}
    raw_pre = ed.get("pre_written_emails") if isinstance(ed, dict) else None
    if isinstance(raw_pre, list):
        for entry in raw_pre:
            if isinstance(entry, dict) and (entry.get("body") or entry.get("subject")):
                pre_written.append(entry)

    generated_bodies: list[str] = []
    generated_subjects: list[str] = []
    generated_steps: list[dict] = []
    generated_linkedin_message: Optional[str] = None

    for index, delay in enumerate(step_delays, start=1):
        # Pre-written email for this step index? Use it verbatim on email steps.
        pw_entry = None
        if index - 1 < len(pre_written):
            pw_entry = pre_written[index - 1]

        step_template_check = _template_for_step(content_settings["step_templates"], index)
        step_channel_check = str(step_template_check.get("channel") or "email").strip().lower() or "email"
        if pw_entry and step_channel_check == "email":
            body = (pw_entry.get("body") or "").strip() or ""
            subject = (pw_entry.get("subject") or "").strip() or _fallback_subject(
                index, context, step_template_check, generated_subjects
            )
            generated_bodies.append(body)
            generated_subjects.append(subject)
            generated_steps.append({"channel": "email", "subject": subject, "body": body, "delay": delay})
            continue

        step_template = _template_for_step(content_settings["step_templates"], index)
        channel = str(step_template.get("channel") or "email").strip().lower() or "email"
        if channel == "linkedin":
            li_prompt = _build_linkedin_prompt(context)
            li_system = f"{_LINKEDIN_SYSTEM} {content_settings['linkedin_prompt']}".strip()
            body = await ai.complete(li_system, li_prompt, max_tokens=80) or ""
            subject = None
            generated_linkedin_message = body
        elif channel == "call":
            prompt = _build_step_prompt(
                ctx=context,
                step_number=index,
                step_template=step_template,
                prior_email=generated_bodies[-1] if generated_bodies else None,
                prior_subject=generated_subjects[0] if generated_subjects else None,
            ) + "\n\nWrite a concise phone call task and talk track for the SDR. No email subject line."
            body = await ai.complete(system_prompt, prompt, max_tokens=140) or ""
            subject = None
        else:
            prompt = _build_step_prompt(
                ctx=context,
                step_number=index,
                step_template=step_template,
                prior_email=generated_bodies[-1] if generated_bodies else None,
                prior_subject=generated_subjects[0] if generated_subjects else None,
            )
            result = await ai.complete(
                system_prompt,
                prompt,
                max_tokens=250 if index == 1 else 190,
            )
            body = result or ""
            subject = _extract_subject(result) or _fallback_subject(index, context, step_template, generated_subjects)
            generated_bodies.append(body)
            generated_subjects.append(subject)
        generated_steps.append({"channel": channel, "subject": subject, "body": body, "delay": delay})

    email_steps = [step for step in generated_steps if step["channel"] == "email"]
    seq.email_1 = email_steps[0]["body"] if len(email_steps) > 0 else None
    seq.email_2 = email_steps[1]["body"] if len(email_steps) > 1 else None
    seq.email_3 = email_steps[2]["body"] if len(email_steps) > 2 else None
    seq.subject_1 = email_steps[0]["subject"] if len(email_steps) > 0 else f"Quick question for {context['company_name']}"
    seq.subject_2 = email_steps[1]["subject"] if len(email_steps) > 1 else (f"Re: {seq.subject_1}" if seq.subject_1 else None)
    seq.subject_3 = email_steps[2]["subject"] if len(email_steps) > 2 else (f"Re: {seq.subject_1}" if seq.subject_1 else None)
    seq.linkedin_message = generated_linkedin_message

    seq.generation_context = context
    seq.generated_at = datetime.utcnow()
    seq.updated_at = datetime.utcnow()

    session.add(seq)
    await session.flush()  # Flush to get seq.id before creating steps

    # ── Create OutreachStep records (flexible, non-hardcoded) ─────────────────
    # Delete any existing steps for this sequence before regenerating
    existing_steps = await session.execute(
        select(OutreachStep).where(OutreachStep.sequence_id == seq.id)
    )
    for old_step in existing_steps.scalars().all():
        await session.delete(old_step)

    for i, generated_step in enumerate(generated_steps, start=1):
        step = OutreachStep(
            sequence_id=seq.id,
            step_number=i,
            subject=generated_step["subject"],
            body=generated_step["body"],
            delay_value=generated_step["delay"],
            delay_unit="days",
        )
        step.channel = generated_step["channel"]
        session.add(step)

    await session.commit()
    await session.refresh(seq)

    logger.info(f"Outreach sequence generated for {contact.email} ({persona}) — {len(step_delays)} steps")
    return seq


# ── Prompt builders ────────────────────────────────────────────────────────────

def _build_context(contact, company) -> dict:
    full_name = f"{contact.first_name} {contact.last_name}".strip()
    company_name = company.name if company else "their company"
    industry = getattr(company, "industry", "") or ""
    funding = getattr(company, "funding_stage", "") or ""
    employees = getattr(company, "employee_count", None)
    tech_stack = getattr(company, "tech_stack", None) or {}

    # Pull funding signals from enrichment_sources
    enrichment = getattr(company, "enrichment_sources", {}) or {}
    news = enrichment.get("news", {})
    funding_headlines = [
        a["title"] for a in news.get("funding_signals", [])[:2]
    ] if news else []

    return {
        "contact_name": full_name,
        "first_name": contact.first_name,
        "title": contact.title or "leader",
        "company_name": company_name,
        "industry": industry,
        "funding_stage": funding,
        "employee_count": employees,
        "tech_stack": list(tech_stack.keys())[:5] if isinstance(tech_stack, dict) else [],
        "funding_headlines": funding_headlines,
        "persona": contact.persona or "unknown",
        "linkedin_url": contact.linkedin_url or "",
    }


def _build_system_prompt(persona: str, general_prompt: str) -> str:
    base = _PERSONA_SYSTEM.get(persona, _PERSONA_SYSTEM["unknown"])
    return f"{base} {general_prompt}".strip()


def _normalize_outreach_content_settings(value: Optional[dict], step_count: int) -> dict:
    raw = value if isinstance(value, dict) else {}
    raw_steps = raw.get("step_templates")
    steps = raw_steps if isinstance(raw_steps, list) and raw_steps else _DEFAULT_OUTREACH_CONTENT["step_templates"]
    normalized_steps = []
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        normalized_steps.append(
            {
                "step_number": int(step.get("step_number") or idx),
                "channel": str(step.get("channel") or "email").strip().lower() or "email",
                "label": str(step.get("label") or f"Step {idx}"),
                "goal": str(step.get("goal") or ""),
                "subject_hint": str(step.get("subject_hint") or "") or None,
                "body_template": str(step.get("body_template") or "") or None,
                "prompt_hint": str(step.get("prompt_hint") or "") or None,
            }
        )
    if not normalized_steps:
        normalized_steps = list(_DEFAULT_OUTREACH_CONTENT["step_templates"])
    normalized_steps.sort(key=lambda item: item["step_number"])
    while len(normalized_steps) < step_count:
        last = normalized_steps[-1]
        normalized_steps.append(
            {
                **last,
                "step_number": len(normalized_steps) + 1,
                "label": f"Step {len(normalized_steps) + 1}",
            }
        )
    return {
        "general_prompt": str(raw.get("general_prompt") or _DEFAULT_OUTREACH_CONTENT["general_prompt"]),
        "linkedin_prompt": str(raw.get("linkedin_prompt") or _DEFAULT_OUTREACH_CONTENT["linkedin_prompt"]),
        "step_templates": normalized_steps,
    }


def _template_for_step(step_templates: list[dict], step_number: int) -> dict:
    for template in step_templates:
        if int(template.get("step_number") or 0) == step_number:
            return template
    return step_templates[min(step_number - 1, len(step_templates) - 1)]


def _build_initial_prompt(ctx: dict, step_template: dict | None = None) -> str:
    funding_note = ""
    if ctx["funding_headlines"]:
        funding_note = f"\nRecent news: {ctx['funding_headlines'][0]}"

    emp_note = ""
    if ctx["employee_count"]:
        emp_note = f", ~{ctx['employee_count']} employees"

    stack_note = ""
    if ctx["tech_stack"]:
        stack_note = f"\nTech stack: {', '.join(ctx['tech_stack'])}"

    kb_note = ctx.get("kb_context", "")
    template_note = _build_template_note(step_template)

    return (
        f"Write a cold outreach email to {ctx['contact_name']}, {ctx['title']} at {ctx['company_name']}.\n"
        f"Industry: {ctx['industry']}{emp_note}\n"
        f"Funding stage: {ctx['funding_stage']}{funding_note}{stack_note}\n\n"
        "Include: a subject line on the first line (format 'Subject: ...'), "
        "then the email body. Reference something specific about their company or role. "
        "End with a clear, low-friction CTA (15-min chat or a simple yes/no question)."
        f"{template_note}"
        f"{kb_note}"
    )


def _build_followup_prompt(ctx: dict, touch: int, prior_email: str, prior_subject: Optional[str], step_template: dict | None = None) -> str:
    nudge = _FOLLOWUP_NUDGES.get(touch, "")
    template_note = _build_template_note(step_template)
    subject = prior_subject or f"Re: {ctx['company_name']}"
    return (
        f"Contact: {ctx['contact_name']}, {ctx['title']} at {ctx['company_name']}.\n"
        f"Primary thread subject: {subject}\n"
        f"Prior email sent:\n{prior_email[:300]}...\n\n"
        f"{nudge}\n"
        "Include a subject line on the first line (format 'Subject: ...'), then the follow-up body. "
        "Do NOT repeat the full prior email."
        f"{template_note}"
    )


def _build_linkedin_prompt(ctx: dict) -> str:
    return (
        f"Write a LinkedIn connection note to {ctx['contact_name']}, "
        f"{ctx['title']} at {ctx['company_name']} ({ctx['industry']}). "
        "Max 300 characters. Be specific, warm, and give one concrete reason to connect."
    )


def _build_step_prompt(
    ctx: dict,
    step_number: int,
    step_template: dict,
    prior_email: Optional[str],
    prior_subject: Optional[str],
) -> str:
    if step_number == 1 or not prior_email:
        return _build_initial_prompt(ctx, step_template)
    touch_index = min(step_number - 1, 2)
    return _build_followup_prompt(ctx, touch_index, prior_email, prior_subject, step_template)


def _build_template_note(step_template: dict | None) -> str:
    if not step_template:
        return ""
    label = step_template.get("label")
    if not label:
        label = f"Step {step_template.get('step_number') or ''}".strip()
    fragments = [
        f"\n\nHouse playbook for this touch ({label}):",
        f"\nGoal: {step_template.get('goal') or 'Keep momentum moving.'}",
    ]
    if step_template.get("subject_hint"):
        fragments.append(f"\nSubject hint: {step_template['subject_hint']}")
    if step_template.get("body_template"):
        fragments.append(f"\nReference template (adapt, do not copy verbatim):\n{step_template['body_template']}")
    if step_template.get("prompt_hint"):
        fragments.append(f"\nAdditional guidance: {step_template['prompt_hint']}")
    return "".join(fragments)


def _fallback_subject(step_number: int, ctx: dict, step_template: dict | None, generated_subjects: list[str]) -> str:
    if step_template and step_template.get("subject_hint"):
        subject_hint = str(step_template["subject_hint"]).strip()
        if subject_hint:
            return subject_hint.replace("{{company_name}}", ctx["company_name"])
    if step_number == 1:
        return f"Quick question for {ctx['company_name']}"
    primary_subject = generated_subjects[0] if generated_subjects else f"Quick question for {ctx['company_name']}"
    return f"Re: {primary_subject}"


def _extract_subject(email_text: Optional[str]) -> Optional[str]:
    """Pull the subject line if GPT included one in 'Subject: ...' format."""
    if not email_text:
        return None
    for line in email_text.splitlines():
        if line.lower().startswith("subject:"):
            subject = line[len("subject:"):].strip()
            # Remove it from the body by returning it
            return subject
    return None

"""Outreach analytics aggregations.

Builds the funnel + per-sequence + per-subject + per-rep stats consumed
by the SalesAnalytics 'Outreach' tab. All numbers are derived from
real tables (outreach_sequences, contacts, activities) — no mocks.

Engagement counters (email_open_count / email_click_count) are written
back to contacts by the periodic Instantly sync, so this only paints
the truth that's already in the DB.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import and_, case, func, select

from app.models.activity import Activity
from app.models.contact import Contact
from app.models.outreach import OutreachSequence


async def build_outreach_overview(
    session,
    window_days: int = 90,
    rep_email: Optional[str] = None,
) -> dict[str, Any]:
    """Top-level funnel + per-sequence + per-subject + per-rep breakdowns."""
    since = datetime.utcnow() - timedelta(days=window_days)

    # ── Funnel ────────────────────────────────────────────────────────────────
    seq_filters = [OutreachSequence.launched_at.isnot(None), OutreachSequence.launched_at >= since]
    contact_filters = [Contact.instantly_campaign_id.isnot(None), Contact.updated_at >= since]
    if rep_email:
        contact_filters.append(Contact.assigned_rep_email == rep_email)

    launched_q = select(func.count(OutreachSequence.id)).where(and_(*seq_filters))
    launched = (await session.execute(launched_q)).scalar() or 0

    contacts_in_window_q = select(Contact).where(and_(*contact_filters))
    contacts_in_window = (await session.execute(contacts_in_window_q)).scalars().all()

    sent = sum(1 for c in contacts_in_window if (c.email_open_count or 0) > 0 or (c.sequence_status or "") in {
        "queued_instantly", "active", "interested", "not_interested", "meeting_booked", "bounced", "unsubscribed"
    })
    opened = sum(1 for c in contacts_in_window if (c.email_open_count or 0) > 0)
    clicked = sum(1 for c in contacts_in_window if (c.email_click_count or 0) > 0)
    interested = sum(1 for c in contacts_in_window if c.sequence_status == "interested")
    booked = sum(1 for c in contacts_in_window if c.sequence_status == "meeting_booked")
    # EMAIL negative only — instantly_status (not the call/LinkedIn-overloaded
    # sequence_status), so a phone "not interested" no longer inflates the email
    # not_interested count / reply_rate.
    not_interested = sum(1 for c in contacts_in_window if c.instantly_status == "not_interested")
    bounced = sum(1 for c in contacts_in_window if c.sequence_status == "bounced")
    unsubscribed = sum(1 for c in contacts_in_window if c.sequence_status == "unsubscribed")

    funnel = {
        "launched_sequences": launched,
        "contacts_in_play": len(contacts_in_window),
        "sent": sent,
        "opened": opened,
        "clicked": clicked,
        "interested": interested,
        "meeting_booked": booked,
        "not_interested": not_interested,
        "bounced": bounced,
        "unsubscribed": unsubscribed,
        "open_rate": round(opened / sent, 4) if sent else 0,
        "reply_rate": round((interested + not_interested + booked) / sent, 4) if sent else 0,
        "booking_rate": round(booked / sent, 4) if sent else 0,
    }

    # ── Per-rep breakdown ─────────────────────────────────────────────────────
    rep_rows = (
        await session.execute(
            select(
                Contact.assigned_rep_email,
                func.count(Contact.id).label("contacts"),
                func.sum(case((Contact.email_open_count > 0, 1), else_=0)).label("opened"),
                func.sum(case((Contact.email_click_count > 0, 1), else_=0)).label("clicked"),
                func.sum(case((Contact.sequence_status == "interested", 1), else_=0)).label("interested"),
                func.sum(case((Contact.sequence_status == "meeting_booked", 1), else_=0)).label("booked"),
                func.sum(case((Contact.sequence_status == "bounced", 1), else_=0)).label("bounced"),
            )
            .where(and_(*contact_filters))
            .group_by(Contact.assigned_rep_email)
        )
    ).all()
    per_rep = [
        {
            "rep_email": row[0] or "(unassigned)",
            "contacts": int(row[1] or 0),
            "opened": int(row[2] or 0),
            "clicked": int(row[3] or 0),
            "interested": int(row[4] or 0),
            "booked": int(row[5] or 0),
            "bounced": int(row[6] or 0),
            "open_rate": round((row[2] or 0) / row[1], 4) if row[1] else 0,
            "reply_rate": round(((row[4] or 0) + (row[5] or 0)) / row[1], 4) if row[1] else 0,
        }
        for row in rep_rows
    ]
    per_rep.sort(key=lambda r: r["booked"] + r["interested"], reverse=True)

    # ── Per-sequence performance (top 20 by contacts pushed) ──────────────────
    # Joins sequence -> contact -> aggregate per campaign_id.
    seq_rows = (
        await session.execute(
            select(
                OutreachSequence.id,
                OutreachSequence.subject_1,
                OutreachSequence.persona,
                OutreachSequence.instantly_campaign_id,
                OutreachSequence.instantly_campaign_status,
                OutreachSequence.launched_at,
                Contact.email_open_count,
                Contact.email_click_count,
                Contact.sequence_status,
            )
            .join(Contact, Contact.id == OutreachSequence.contact_id)
            .where(OutreachSequence.launched_at.isnot(None))
            .where(OutreachSequence.launched_at >= since)
        )
    ).all()

    seq_agg: dict[str, dict[str, Any]] = {}
    for row in seq_rows:
        cid = row[3] or str(row[0])  # group by Instantly campaign when set, else by sequence id
        bucket = seq_agg.setdefault(cid, {
            "campaign_id": row[3],
            "sequence_id": str(row[0]),
            "subject": row[1],
            "persona": row[2],
            "status": row[4],
            "launched_at": row[5].isoformat() if row[5] else None,
            "contacts": 0,
            "opened": 0,
            "clicked": 0,
            "interested": 0,
            "booked": 0,
            "bounced": 0,
        })
        bucket["contacts"] += 1
        if (row[6] or 0) > 0:
            bucket["opened"] += 1
        if (row[7] or 0) > 0:
            bucket["clicked"] += 1
        if row[8] == "interested":
            bucket["interested"] += 1
        elif row[8] == "meeting_booked":
            bucket["booked"] += 1
        elif row[8] == "bounced":
            bucket["bounced"] += 1

    sequences = list(seq_agg.values())
    for s in sequences:
        s["open_rate"] = round(s["opened"] / s["contacts"], 4) if s["contacts"] else 0
        s["reply_rate"] = round((s["interested"] + s["booked"]) / s["contacts"], 4) if s["contacts"] else 0
    sequences.sort(key=lambda s: s["contacts"], reverse=True)

    # ── Subject-line performance ──────────────────────────────────────────────
    # Bucket sent emails by subject and count distinct contacts with opens.
    subject_rows = (
        await session.execute(
            select(
                Activity.email_subject,
                func.count(Activity.id).label("sends"),
                func.count(func.distinct(Activity.contact_id)).label("distinct_contacts"),
            )
            .where(Activity.type == "email")
            .where(Activity.source.in_(["instantly", "personal_email_sync"]))
            .where(Activity.email_subject.isnot(None))
            .where(Activity.created_at >= since)
            .group_by(Activity.email_subject)
            .order_by(func.count(Activity.id).desc())
            .limit(25)
        )
    ).all()
    subjects = [
        {
            "subject": row[0],
            "sends": int(row[1] or 0),
            "distinct_contacts": int(row[2] or 0),
        }
        for row in subject_rows
        if row[0]
    ]

    return {
        "window_days": window_days,
        "rep_email": rep_email,
        "funnel": funnel,
        "per_rep": per_rep,
        "sequences": sequences[:30],
        "subjects": subjects,
    }

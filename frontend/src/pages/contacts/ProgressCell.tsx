import { Mail, PhoneCall, Linkedin } from "lucide-react";
import type { Contact } from "../../types";
import type { LifecycleSummary } from "../../lib/api";
import { linkedinOutcomeColor } from "../../lib/prospectWorkflow";

/**
 * Compact single-line progress cell.
 *
 * One small chip per channel (✉ email, ☎ call, and LinkedIn when a motion
 * exists), colored by state so a rep can scan dozens of rows:
 *   GREY  — not started
 *   AMBER — activity in flight (sent/opened, attempts, callback scheduled)
 *   GREEN — positive outcome (reply, meeting/demo booked)
 *   RED   — hard negative (not interested, wrong number, DNC, bounce)
 *
 * Full detail (sub-status, timestamps, follow-up date) lives in the title
 * tooltip on each chip. A single muted meta line below shows last-touch
 * recency and live-sequence progress ONLY when that adds information.
 *
 * The internal color contract below (yellow/blue/green/red/white) is kept
 * as-is because it mirrors the backend outcome-color filter buckets; the
 * chips fold yellow/blue/white into the AMBER "in flight" tone.
 */

type OutcomeColor = "yellow" | "blue" | "green" | "red" | "white";

type OutcomeDot = { color: OutcomeColor; title: string };

type ChannelState = {
  // Pre-terminal dots: attempts (yellow) and engagement (blue/white). These
  // render left-to-right before any overflow indicator.
  dots: OutcomeDot[];
  // Terminal outcome dot (green/red). Rendered AFTER the overflow pill so
  // the rail reads "● ● ● ● ● ● +6 ●" — visible activity, hidden activity,
  // then the decisive outcome. Optional because not every lane reaches a
  // terminal state (e.g. follow-up scheduled).
  terminalDot?: OutcomeDot;
  // Count of activity beyond the visible cap (for the "+N" pill).
  overflowCount?: number;
  // null = inactive lane; otherwise the strongest outcome on this lane,
  // used to color the cell hero strip.
  heroColor: OutcomeColor | null;
  label: string;
  sub: string;
  timestamp: Date | null;
  // For the follow-up case: month-day label rendered to the right of the
  // white pending dot so the rep knows when to call back.
  followupDateLabel?: string;
};

// Chip tones — the compact render surface. Grey = untouched, amber = in
// flight, green = positive, red = negative. Palette values follow the global
// design tokens (soft green #f3fbe3 / dark green text #4d7c0f, border
// #e3e9f2, muted #68788d).
type ChipTone = { bg: string; border: string; fg: string };
const TONE_IDLE: ChipTone = { bg: "#f4f6f9", border: "#e3e9f2", fg: "#68788d" };
const TONE_INFLIGHT: ChipTone = { bg: "#fffbeb", border: "#fde68a", fg: "#92400e" };
const TONE_POSITIVE: ChipTone = { bg: "#f3fbe3", border: "#cfe89a", fg: "#4d7c0f" };
const TONE_NEGATIVE: ChipTone = { bg: "#fef2f2", border: "#fecaca", fg: "#b91c1c" };

function chipTone(color: OutcomeColor | null): ChipTone {
  if (!color) return TONE_IDLE;
  if (color === "green") return TONE_POSITIVE;
  if (color === "red") return TONE_NEGATIVE;
  // yellow (attempted), blue (engaged/callback), white (pending slot) all
  // read as "in flight" at row-scan granularity; detail is in the tooltip.
  return TONE_INFLIGHT;
}

// Disposition buckets — kept in sync with the backend filter mappings in
// `app/repositories/contact.py` (call_outcome_color). When the backend
// changes those buckets, change them here too.
const CALL_HARD_NEG = new Set(["invalid_number_wrong_number", "do_not_contact_dnc"]);
const CALL_SOFT_NEG = new Set(["connected_not_interested", "contact_poor_fit", "gatekeeper_connected_to_admin"]);
const CALL_FOLLOWUP = new Set(["interested_follow_up_required", "call_back_later_rescheduled"]);
const CALL_BOOKED = new Set(["demo_scheduled_booked", "meeting_confirmed"]);

// instantly_status values that mean a send has NOT happened yet — the lead is
// queued/unverified, not contacted. Anything else (pushed, bounced, replied,
// paused, …) confirms the lead actually entered the campaign.
const PRE_SEND_INSTANTLY = new Set(["", "ready", "missing_email", "none"]);

// ── Manual email log ───────────────────────────────────────────────────────
// A rep-logged email touch (the "Log" button next to Email in the Action
// column). Mirrors the LinkedIn logger mechanism, but the Contact model has
// no email_status column, so the record rides in the enrichment_data JSONB
// under `manual_email`. The logger in Contacts.tsx writes it; this cell reads
// it so a personally-sent Gmail no longer renders as a grey "Not sent"
// forever. Exported (with the outcome options) so Contacts.tsx and this cell
// share one definition.
export type ManualEmailLog = {
  status?: string; // sent | replied | no_response | meeting_booked
  last_at?: string; // ISO timestamp of the latest manual log
  count?: number; // how many manual email touches were logged
};

export const EMAIL_LOG_OUTCOME_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "sent", label: "Sent" },
  { value: "replied", label: "Replied" },
  { value: "no_response", label: "No response" },
  { value: "meeting_booked", label: "Meeting booked" },
];

export function getManualEmailLog(contact: Pick<Contact, "enrichment_data">): ManualEmailLog | null {
  const ed = contact.enrichment_data;
  if (!ed || typeof ed !== "object") return null;
  const raw = (ed as Record<string, unknown>).manual_email;
  if (!raw || typeof raw !== "object") return null;
  return raw as ManualEmailLog;
}

// Sequence-status transition for a manual email log — same guardrails as the
// call/LinkedIn derivations in lib/prospectWorkflow.ts: a booked meeting is
// never downgraded, a hard negative only moves on a meeting, and a plain
// send only upgrades pre-launch states to "sent" (which also keeps the
// backend's email yellow color filter in agreement with this chip).
export function deriveSequenceStatusFromEmailLog(
  emailStatus?: string | null,
  currentStatus?: string | null,
): string | undefined {
  if (!emailStatus) return currentStatus ?? undefined;
  if (emailStatus === "meeting_booked") return "meeting_booked";
  if (currentStatus === "meeting_booked") return currentStatus;
  if (currentStatus === "not_interested") return currentStatus;
  if (emailStatus === "replied") return "replied";
  // sent / no_response — an outbound touch: move pre-launch states to "sent".
  if (!currentStatus || ["ready", "research_needed"].includes(currentStatus)) return "sent";
  return currentStatus ?? undefined;
}

function getEmailChannel(contact: Contact): ChannelState {
  const seq = contact.sequence_status || "";
  const opens = contact.email_open_count ?? 0;
  const clicks = contact.email_click_count ?? 0;
  const openedAt = contact.email_last_opened_at ? new Date(contact.email_last_opened_at) : null;
  // Manual email log (see getManualEmailLog above): a rep-logged send counts
  // as real evidence and feeds this lane's timestamp → lastTouch.
  const manual = getManualEmailLog(contact);
  const manualAtRaw = manual?.last_at ? new Date(manual.last_at) : null;
  const manualAt = manualAtRaw && !Number.isNaN(manualAtRaw.getTime()) ? manualAtRaw : null;
  const lastAt = openedAt && manualAt
    ? (manualAt.getTime() > openedAt.getTime() ? manualAt : openedAt)
    : (openedAt ?? manualAt);
  // A send is only "real" with evidence: tracked opens/clicks, an
  // instantly_status past the pre-send states, or a manual rep log. This
  // stops the lane claiming a phantom "sent" for contacts whose
  // sequence_status was set manually or by import with no email actually
  // behind it (drives the drawer/ProgressCell agreement — see "Emails sent 0"
  // reconciliation).
  const inst = (contact.instantly_status || "").toLowerCase();
  const trackedSend = opens > 0 || clicks > 0 || (inst !== "" && !PRE_SEND_INSTANTLY.has(inst));
  const manualOnly = !trackedSend && !!manual;
  const reallySent = trackedSend || !!manual;
  if (!reallySent) {
    return { dots: [], heroColor: null, label: "Not sent", sub: "Email pending", timestamp: null };
  }

  const replied = seq === "replied";
  const booked = seq === "meeting_booked";
  // EMAIL negative only: instantly_status is set just by the genuine email paths,
  // whereas sequence_status='not_interested' is ALSO written by negative CALL/
  // LinkedIn dispositions — using seq here made a phone "not interested" render
  // as a red "Negative reply · email" (the reported bug). The call's own red dot
  // (from call_disposition, handled separately below) still shows correctly.
  const negative = inst === "not_interested";
  // A bounce is a HARD stop: the mail never landed. It used to fall through to
  // the plain "Sent · Awaiting open" yellow, so a dead address read as a
  // delivered email that just hadn't been opened yet. The palette contract at
  // the top of this file already assigns RED to bounces.
  const bounced = inst === "bounced" || seq === "bounced";
  const sent = reallySent;

  const dots: OutcomeDot[] = [];
  if (sent) dots.push({ color: "yellow", title: manualOnly ? "Email sent (logged by rep)" : "Email sent" });
  // One blue dot per open, capped at 6 so the rail stays compact. Excess
  // opens become a "+N" pill rendered between the blues and any terminal
  // outcome dot — so a hot reader with 12 opens replying positively reads
  // `● ● ● ● ● ● ● +6 ●` instead of silently dropping the last 6.
  const MAX_BLUE_OPENS = 6;
  const blueOpens = Math.min(opens, MAX_BLUE_OPENS);
  for (let i = 0; i < blueOpens; i++) {
    dots.push({ color: "blue", title: opens === 1 ? "Email opened" : `Open ${i + 1} of ${opens}` });
  }
  const overflowCount = Math.max(0, opens - MAX_BLUE_OPENS);
  let terminalDot: OutcomeDot | undefined;
  if (bounced) terminalDot = { color: "red", title: "Email bounced" };
  else if (booked) terminalDot = { color: "green", title: "Meeting booked" };
  else if (replied) terminalDot = { color: "green", title: "Positive reply" };
  else if (negative) terminalDot = { color: "red", title: "Negative reply" };

  let heroColor: OutcomeColor | null;
  let label: string;
  let sub: string;
  if (bounced) { heroColor = "red"; label = "Bounced"; sub = "Address invalid · do not retry"; }
  else if (booked) { heroColor = "green"; label = "Meeting booked"; sub = opens > 0 ? `${opens} open${opens === 1 ? "" : "s"}` : "From email"; }
  else if (replied) { heroColor = "green"; label = "Positive reply"; sub = opens > 0 ? `${opens} open${opens === 1 ? "" : "s"}` : "Reply received"; }
  else if (negative) { heroColor = "red"; label = "Negative reply"; sub = "Not interested · email"; }
  else if (opens > 0) { heroColor = "blue"; label = "Opened"; sub = `${opens} open${opens === 1 ? "" : "s"} · no reply yet`; }
  else if (manualOnly) {
    // Only evidence is the rep's own log — distinct sub-state so a manually
    // sent Gmail reads amber "Sent (manual)" instead of grey "Not sent".
    heroColor = "yellow";
    label = "Sent (manual)";
    sub = manual?.status === "no_response" ? "No response yet · logged by rep" : "Logged by rep";
  }
  else if (sent) { heroColor = "yellow"; label = "Sent"; sub = "Awaiting open"; }
  else { heroColor = null; label = "Not sent"; sub = "Email pending"; }

  return { dots, terminalDot, overflowCount, heroColor, label, sub, timestamp: lastAt };
}

function getCallChannel(contact: Contact): ChannelState {
  const attempts = contact.call_attempt_count ?? 0;
  const disp = contact.call_disposition || "";
  const ts = contact.call_last_at ? new Date(contact.call_last_at) : null;
  // Fallback for legacy rows that haven't had the aggregate populated yet:
  // if call_status indicates activity, assume at least one attempt happened.
  const effectiveAttempts = attempts > 0
    ? attempts
    : (contact.call_status && contact.call_status !== "none" ? 1 : 0);

  const dots: OutcomeDot[] = [];
  // N yellow dots — one per attempt. Capped at 8 so a hot prospect with
  // double-digit attempts doesn't blow out the row height; excess shows as
  // a "+N" pill before the terminal outcome dot.
  const MAX_YELLOW = 8;
  const yellowCount = Math.min(effectiveAttempts, MAX_YELLOW);
  for (let i = 0; i < yellowCount; i++) {
    dots.push({ color: "yellow", title: `Call attempt #${i + 1}` });
  }
  const overflowCount = Math.max(0, effectiveAttempts - MAX_YELLOW);

  let heroColor: OutcomeColor | null = effectiveAttempts > 0 ? "yellow" : null;
  let label: string;
  let sub: string;
  let followupDateLabel: string | undefined;
  let terminalDot: OutcomeDot | undefined;

  if (effectiveAttempts === 0) {
    label = "Not called";
    sub = "Awaiting first call";
  } else if (CALL_HARD_NEG.has(disp)) {
    terminalDot = { color: "red", title: disp === "invalid_number_wrong_number" ? "Wrong number" : "Do not contact" };
    heroColor = "red";
    label = disp === "invalid_number_wrong_number" ? "Wrong number" : "Do not contact";
    sub = "Hard stop";
  } else if (CALL_SOFT_NEG.has(disp)) {
    terminalDot = { color: "red", title: disp === "connected_not_interested" ? "Not interested" : disp === "gatekeeper_connected_to_admin" ? "Gatekeeper" : "Poor fit" };
    heroColor = "red";
    label = disp === "gatekeeper_connected_to_admin" ? "Gatekeeper" : disp === "contact_poor_fit" ? "Poor fit" : "Not interested";
    sub = "Connected · soft pass";
  } else if (CALL_BOOKED.has(disp)) {
    terminalDot = { color: "green", title: disp === "demo_scheduled_booked" ? "Demo booked" : "Meeting confirmed" };
    heroColor = "green";
    label = disp === "demo_scheduled_booked" ? "Demo booked" : "Meeting confirmed";
    sub = "Booked from call";
  } else if (CALL_FOLLOWUP.has(disp)) {
    // Blue (callback requested) + white (pending slot) + month-day label.
    // These aren't "terminal" in the green/red sense — they stay in the
    // pre-terminal dots[] so the date pill renders right after them.
    dots.push({ color: "blue", title: "Follow-up requested" });
    dots.push({ color: "white", title: "Follow-up pending" });
    heroColor = "blue";
    label = disp === "interested_follow_up_required" ? "Interested · follow-up" : "Callback scheduled";
    sub = "Awaiting follow-up";
    if (contact.next_followup_at) {
      const d = new Date(contact.next_followup_at);
      if (!Number.isNaN(d.getTime())) {
        followupDateLabel = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
      }
    }
  } else {
    label = `${effectiveAttempts} attempt${effectiveAttempts === 1 ? "" : "s"}`;
    sub = disp ? disp.replace(/_/g, " ") : "Awaiting disposition";
  }

  return { dots, terminalDot, overflowCount, heroColor, label, sub, timestamp: ts, followupDateLabel };
}

// LinkedIn lane. Reads `contact.linkedin_status` (set by the inline logger)
// through the shared `linkedinOutcomeColor` map so the dot colors stay in sync
// with the logger options:
//   sent/inmail → yellow · accepted/follow_up → blue · meeting_booked → green ·
//   meeting_rejected → red. Every motion implies an initial outreach dot.
function getLinkedinChannel(contact: Contact): ChannelState {
  const status = contact.linkedin_status || "";
  const ts = contact.linkedin_last_at ? new Date(contact.linkedin_last_at) : null;
  const color = linkedinOutcomeColor(status);

  if (!color) {
    return { dots: [], heroColor: null, label: "No LinkedIn motion", sub: "No request sent", timestamp: null };
  }

  const dots: OutcomeDot[] = [{ color: "yellow", title: "LinkedIn request sent" }];
  let terminalDot: OutcomeDot | undefined;
  let label: string;
  let sub: string;

  switch (status) {
    case "accepted":
      dots.push({ color: "blue", title: "Connection accepted" });
      label = "Accepted"; sub = "Ready to message";
      break;
    case "follow_up":
      dots.push({ color: "blue", title: "Follow-up sent" });
      label = "Follow-up"; sub = "Conversation in flight";
      break;
    case "meeting_booked":
      dots.push({ color: "blue", title: "Engaged" });
      terminalDot = { color: "green", title: "Meeting booked" };
      label = "Meeting booked"; sub = "From LinkedIn";
      break;
    case "meeting_rejected":
      terminalDot = { color: "red", title: "Meeting rejected" };
      label = "Meeting rejected"; sub = "Declined on LinkedIn";
      break;
    case "inmail":
      dots[0] = { color: "yellow", title: "LinkedIn InMail sent" };
      label = "In mail"; sub = "Awaiting reply";
      break;
    case "sent":
    default:
      label = "Request sent"; sub = "Awaiting acceptance";
  }

  return { dots, terminalDot, heroColor: color, label, sub, timestamp: ts };
}

// Sales-ops glance-value relative time. Tight on recent activity, falls
// back to absolute date for stale items.
function formatRecent(d: Date | null): string {
  if (!d) return "";
  const diffMs = Date.now() - d.getTime();
  if (diffMs < 0) return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const min = Math.floor(diffMs / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h`;
  const days = Math.floor(hr / 24);
  if (days < 7) return `${days}d`;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

// Full-detail tooltip line for one channel — label, sub-status, attempt/open
// detail (via the still-computed dot titles), timestamp, follow-up date.
function channelTitle(channel: string, state: ChannelState): string {
  const parts = [
    `${channel}: ${state.label}`,
    state.sub,
    state.overflowCount ? `+${state.overflowCount} more` : null,
    state.timestamp ? state.timestamp.toLocaleString() : null,
    state.followupDateLabel ? `Follow-up: ${state.followupDateLabel}` : null,
  ].filter(Boolean) as string[];
  return parts.join(" · ");
}

function StatusChip({ Icon, channel, state }: {
  Icon: typeof Mail;
  channel: string;
  state: ChannelState;
}) {
  const tone = chipTone(state.heroColor);
  const text = state.followupDateLabel ? `${state.label} · ${state.followupDateLabel}` : state.label;
  return (
    <span
      title={channelTitle(channel, state)}
      aria-label={channelTitle(channel, state)}
      style={{
        display: "inline-flex", alignItems: "center", gap: 5,
        height: 22, padding: "0 8px", borderRadius: 999,
        background: tone.bg, border: `1px solid ${tone.border}`, color: tone.fg,
        fontSize: 11.5, fontWeight: 700, lineHeight: "20px",
        whiteSpace: "nowrap", maxWidth: 168, minWidth: 0,
      }}
    >
      <Icon size={11} style={{ flexShrink: 0 }} />
      <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{text}</span>
    </span>
  );
}

export function ProgressCell({
  contact,
  lifecycle,
}: {
  contact: Contact;
  lifecycle: LifecycleSummary | undefined;
}) {
  const email = getEmailChannel(contact);
  const call = getCallChannel(contact);
  const linkedin = getLinkedinChannel(contact);

  // Pick the most recent per-event timestamp across both channels. When
  // there's clear activity (heroColor set) but no per-event timestamp —
  // Instantly sometimes records the open count without populating
  // `email_last_opened_at` — fall back to `contact.updated_at` so the hero
  // strip doesn't lie with "No touches yet" next to a green pill.
  const lastTouch = (() => {
    const stamps = [email, call, linkedin]
      .map((c) => c.timestamp)
      .filter((t): t is Date => !!t);
    if (stamps.length === 0) {
      if ((email.heroColor || call.heroColor || linkedin.heroColor) && contact.updated_at) {
        const u = new Date(contact.updated_at);
        return Number.isNaN(u.getTime()) ? null : u;
      }
      return null;
    }
    return stamps.reduce((latest, t) => (t.getTime() > latest.getTime() ? t : latest));
  })();

  const hasLiveSequence =
    lifecycle &&
    lifecycle.total_steps > 0 &&
    !["never_launched", "ready"].includes(lifecycle.status);

  // Meta line renders only when it adds information beyond the chips:
  // last-touch recency and/or live-sequence progress. "No touches yet" would
  // duplicate two grey chips, so it stays in the tooltip only.
  const showMeta = Boolean(lastTouch || hasLiveSequence);

  const cellTitle = [
    channelTitle("Email", email),
    channelTitle("Call", call),
    linkedin.heroColor || contact.linkedin_url ? channelTitle("LinkedIn", linkedin) : null,
    lastTouch ? `Last touch: ${lastTouch.toLocaleString()}` : "No touches yet",
  ].filter(Boolean).join("\n");

  return (
    <div
      title={cellTitle}
      style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 180, maxWidth: 280 }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 5, flexWrap: "wrap" }}>
        <StatusChip Icon={Mail} channel="Email" state={email} />
        <StatusChip Icon={PhoneCall} channel="Call" state={call} />
        {/* LinkedIn chip only once a motion is logged — an untouched LinkedIn
            channel stays out of the rail to keep rows scannable; the Action
            column still exposes the LinkedIn buttons. */}
        {linkedin.heroColor ? <StatusChip Icon={Linkedin} channel="LinkedIn" state={linkedin} /> : null}
      </div>
      {showMeta ? (
        <span style={{
          fontSize: 11, color: "#68788d", fontWeight: 600,
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
          fontVariantNumeric: "tabular-nums",
        }}>
          {lastTouch ? `Last touch ${formatRecent(lastTouch)}` : null}
          {lastTouch && hasLiveSequence ? " · " : null}
          {hasLiveSequence ? (
            <>
              {`Seq ${lifecycle!.done_count}/${lifecycle!.total_steps}`}
              {lifecycle!.days_since_launch != null ? ` · D${lifecycle!.days_since_launch}` : null}
              {lifecycle!.overdue_count > 0 ? (
                <span style={{ color: "#b91c1c", fontWeight: 700 }}>{` · ${lifecycle!.overdue_count} late`}</span>
              ) : null}
            </>
          ) : null}
        </span>
      ) : null}
    </div>
  );
}

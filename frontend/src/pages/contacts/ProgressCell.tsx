import { useState } from "react";
import { Mail, PhoneCall, Linkedin } from "lucide-react";
import type { Contact } from "../../types";
import type { LifecycleSummary } from "../../lib/api";
import { linkedinOutcomeColor } from "../../lib/prospectWorkflow";

/**
 * Outcome-colored progress cell.
 *
 * The dot rail reads left-to-right as a *timeline of outcomes*, not event
 * types — one yellow dot per recorded activity, then a colored qualifier
 * dot for the decisive outcome. This matches how SDRs talk about prospects:
 * "called 3 times, one positive reply" rather than "1 sent, 2 opened, 1
 * clicked."
 *
 * Color contract (consistent across email + call):
 *   YELLOW — activity happened (call attempt, email sent)
 *   BLUE   — engaged / follow-up in flight (email opened, callback scheduled)
 *   GREEN  — positive outcome (reply, meeting booked, demo scheduled)
 *   RED    — hard negative (not interested, wrong number, DNC, bounce)
 *   WHITE  — pending follow-up slot (only after blue, paired with a date)
 *
 * Call lane example: ●●● ◐ ◯  Apr 12      (3 attempts, callback scheduled)
 *                    yyy  b   w
 * Email lane:        ● ● ●                 (sent → opened → positive reply)
 *                    y b g
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

// Outcome-color palette. Each color carries the full surface kit (dot fill,
// chip bg/fg, tint for the row background, the left accent strip, and a
// translucent ring for hover glow).
const PALETTE: Record<OutcomeColor, { dot: string; border: string; chipBg: string; chipFg: string; tint: string; bar: string; ring: string }> = {
  yellow: { dot: "#facc15", border: "#ca8a04", chipBg: "#fffbeb", chipFg: "#854d0e", tint: "#fffbf0", bar: "#facc15", ring: "rgba(250,204,21,0.30)" },
  blue:   { dot: "#3b82f6", border: "#2563eb", chipBg: "#eff6ff", chipFg: "#1d4ed8", tint: "#f5f9ff", bar: "#3b82f6", ring: "rgba(59,130,246,0.28)" },
  green:  { dot: "#22c55e", border: "#16a34a", chipBg: "#ecfdf5", chipFg: "#15803d", tint: "#f3fbf6", bar: "#22c55e", ring: "rgba(34,197,94,0.30)" },
  red:    { dot: "#ef4444", border: "#dc2626", chipBg: "#fef2f2", chipFg: "#b91c1c", tint: "#fff5f5", bar: "#ef4444", ring: "rgba(239,68,68,0.30)" },
  white:  { dot: "#ffffff", border: "#cbd5e1", chipBg: "#f8fafc", chipFg: "#475569", tint: "#fafbfd", bar: "#cbd5e1", ring: "rgba(148,164,189,0.22)" },
};

// Hero-color resolution — red and green are the decisive outcomes; blue is
// in-flight; yellow is activity-only; white is pending. When multiple lanes
// have signal, the hottest color drives the cell border + accent strip.
const COLOR_RANK: Record<OutcomeColor, number> = { red: 5, green: 4, blue: 3, yellow: 2, white: 1 };
function hottest(a: OutcomeColor | null, b: OutcomeColor | null): OutcomeColor | null {
  if (!a) return b;
  if (!b) return a;
  return COLOR_RANK[a] >= COLOR_RANK[b] ? a : b;
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

function getEmailChannel(contact: Contact): ChannelState {
  const seq = contact.sequence_status || "";
  const opens = contact.email_open_count ?? 0;
  const clicks = contact.email_click_count ?? 0;
  const lastAt = contact.email_last_opened_at ? new Date(contact.email_last_opened_at) : null;
  // A send is only "real" with evidence: tracked opens/clicks, or an
  // instantly_status past the pre-send states. This stops the lane claiming a
  // phantom "sent" for contacts whose sequence_status was set manually or by
  // import with no email actually behind it (drives the drawer/ProgressCell
  // agreement — see "Emails sent 0" reconciliation).
  const inst = (contact.instantly_status || "").toLowerCase();
  const reallySent = opens > 0 || clicks > 0 || (inst !== "" && !PRE_SEND_INSTANTLY.has(inst));
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
  const sent = reallySent;

  const dots: OutcomeDot[] = [];
  if (sent) dots.push({ color: "yellow", title: "Email sent" });
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
  if (booked) terminalDot = { color: "green", title: "Meeting booked" };
  else if (replied) terminalDot = { color: "green", title: "Positive reply" };
  else if (negative) terminalDot = { color: "red", title: "Negative reply" };

  let heroColor: OutcomeColor | null;
  let label: string;
  let sub: string;
  if (booked) { heroColor = "green"; label = "Meeting booked"; sub = opens > 0 ? `${opens} open${opens === 1 ? "" : "s"}` : "From email"; }
  else if (replied) { heroColor = "green"; label = "Positive reply"; sub = opens > 0 ? `${opens} open${opens === 1 ? "" : "s"}` : "Reply received"; }
  else if (negative) { heroColor = "red"; label = "Negative reply"; sub = "Not interested · email"; }
  else if (opens > 0) { heroColor = "blue"; label = "Opened"; sub = `${opens} open${opens === 1 ? "" : "s"} · no reply yet`; }
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
//   sent → yellow · accepted/follow_up → blue · meeting_booked → green ·
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

function Dot({ color, title }: OutcomeDot) {
  const p = PALETTE[color];
  return (
    <span
      title={title}
      aria-label={title}
      style={{
        width: 9, height: 9, borderRadius: 999,
        background: p.dot,
        border: color === "white" ? `1.5px solid ${p.border}` : "none",
        boxShadow: color === "white" ? "none" : `0 0 0 2px ${p.dot}22`,
        flexShrink: 0,
      }}
    />
  );
}

// Dot rail layout: pre-terminal dots → "+N" overflow pill → terminal
// outcome dot → optional date label. The ordering matters: putting "+N"
// before the terminal dot reads "activity, more activity hidden, decisive
// outcome" — natural for both lanes.
function DotRail({ dots, overflowCount, terminalDot, followupDateLabel }: {
  dots: OutcomeDot[];
  overflowCount?: number;
  terminalDot?: OutcomeDot;
  followupDateLabel?: string;
}) {
  const isEmpty = dots.length === 0 && !overflowCount && !terminalDot && !followupDateLabel;
  if (isEmpty) {
    return <span aria-hidden="true" style={{ width: 1, height: 10, display: "inline-block" }} />;
  }
  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
      {dots.map((d, i) => <Dot key={i} color={d.color} title={d.title} />)}
      {overflowCount && overflowCount > 0 ? (
        <span
          title={`${overflowCount} more`}
          style={{
            fontSize: 10, fontWeight: 800, color: "#67768a",
            letterSpacing: "0.02em",
            padding: "1px 5px", borderRadius: 999,
            background: "#f1f5f9", border: "1px solid #dbe3ee",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          +{overflowCount}
        </span>
      ) : null}
      {terminalDot ? <Dot color={terminalDot.color} title={terminalDot.title} /> : null}
      {followupDateLabel ? (
        <span style={{
          marginLeft: 2,
          fontSize: 10.5, fontWeight: 800,
          color: PALETTE.blue.chipFg,
          background: PALETTE.blue.chipBg,
          padding: "2px 7px", borderRadius: 999,
          border: `1px solid ${PALETTE.blue.ring}`,
          fontVariantNumeric: "tabular-nums",
        }}>
          {followupDateLabel}
        </span>
      ) : null}
    </div>
  );
}

function ChannelRow({ Icon, channel, state }: {
  Icon: typeof Mail;
  channel: string;
  state: ChannelState;
}) {
  const palette = state.heroColor ? PALETTE[state.heroColor] : PALETTE.white;
  const titleParts = [
    `${channel}: ${state.label}`,
    state.sub,
    state.timestamp ? state.timestamp.toLocaleString() : null,
    state.followupDateLabel ? `Follow-up: ${state.followupDateLabel}` : null,
  ].filter(Boolean) as string[];
  const isInactive = !state.heroColor;

  return (
    <div
      title={titleParts.join(" · ")}
      style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "8px 10px", borderRadius: 10,
        background: isInactive ? "#fafbfd" : `linear-gradient(180deg, #ffffff 0%, ${palette.tint} 100%)`,
        border: `1px solid ${isInactive ? "#e8eef5" : palette.ring}`,
      }}
    >
      <div style={{
        width: 26, height: 26, borderRadius: 8,
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        background: palette.chipBg,
        border: `1px solid ${palette.ring}`,
        color: palette.chipFg, flexShrink: 0,
      }}>
        <Icon size={13} />
      </div>

      <div style={{ minWidth: 0, flex: 1, display: "flex", flexDirection: "column", gap: 1 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{
            fontSize: 10, fontWeight: 800, color: "#67768a",
            textTransform: "uppercase", letterSpacing: "0.06em", flexShrink: 0,
          }}>
            {channel}
          </span>
          <span style={{
            fontSize: 12.5, fontWeight: 800, color: palette.chipFg,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          }}>
            {state.label}
          </span>
        </div>
        <span style={{
          fontSize: 10.5, color: "#7a8ea4", fontWeight: 600,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
          {state.sub}
        </span>
      </div>

      <DotRail
        dots={state.dots}
        overflowCount={state.overflowCount}
        terminalDot={state.terminalDot}
        followupDateLabel={state.followupDateLabel}
      />

      {state.timestamp ? (
        <span style={{
          fontSize: 10, fontWeight: 800, color: palette.chipFg,
          background: "#ffffff", padding: "2px 7px", borderRadius: 999,
          border: `1px solid ${palette.ring}`,
          flexShrink: 0, fontVariantNumeric: "tabular-nums",
        }}>
          {formatRecent(state.timestamp)}
        </span>
      ) : null}
    </div>
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
  const heroColor = hottest(hottest(email.heroColor, call.heroColor), linkedin.heroColor);
  const heroPalette = heroColor ? PALETTE[heroColor] : PALETTE.white;
  const [hover, setHover] = useState(false);

  // Pick the strongest signal across all channels for the hero pill.
  const heroState = (() => {
    const active = [email, call, linkedin].filter((c) => c.heroColor);
    if (active.length === 0) return email;
    return active.reduce((best, c) =>
      COLOR_RANK[c.heroColor!] > COLOR_RANK[best.heroColor!] ? c : best,
    );
  })();
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
  const seqPct = hasLiveSequence ? Math.min(100, Math.round((lifecycle!.done_count / Math.max(1, lifecycle!.total_steps)) * 100)) : 0;

  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        minWidth: 320,
        position: "relative",
        background: "#ffffff",
        borderRadius: 14,
        border: "1px solid #e5edf5",
        overflow: "hidden",
        boxShadow: hover
          ? `0 8px 24px -8px ${heroPalette.ring}, 0 0 0 1px ${heroPalette.ring}`
          : "inset 0 1px 0 rgba(255,255,255,0.9), 0 1px 2px rgba(15,39,68,0.04)",
        transform: hover ? "translateY(-1px)" : "translateY(0)",
        transition: "box-shadow 160ms ease, transform 160ms ease",
        cursor: "pointer",
      }}
    >
      <div style={{
        position: "absolute", left: 0, top: 0, bottom: 0,
        width: 3,
        background: heroColor
          ? `linear-gradient(180deg, ${heroPalette.bar} 0%, ${heroPalette.bar}99 100%)`
          : "#e5edf5",
      }} />

      <div style={{
        padding: "10px 14px 8px",
        background: heroColor
          ? `linear-gradient(135deg, ${heroPalette.tint} 0%, #ffffff 70%)`
          : "#ffffff",
        borderBottom: "1px solid #eef2f7",
        display: "flex", alignItems: "center", gap: 8, justifyContent: "space-between",
      }}>
        <span style={{
          fontSize: 10.5, fontWeight: 800,
          textTransform: "uppercase", letterSpacing: "0.07em",
          color: heroPalette.chipFg,
          padding: "3px 9px", borderRadius: 999,
          background: heroPalette.chipBg,
          border: `1px solid ${heroPalette.ring}`,
        }}>
          {heroColor ? heroState.label : "No activity"}
        </span>
        {lastTouch ? (
          <span style={{
            fontSize: 10.5, color: "#67768a", fontWeight: 700,
            fontVariantNumeric: "tabular-nums",
          }}>
            {formatRecent(lastTouch)} ago
          </span>
        ) : (
          <span style={{ fontSize: 10.5, color: "#9aa9bb", fontWeight: 600, fontStyle: "italic" }}>
            No touches yet
          </span>
        )}
      </div>

      <div style={{ padding: "8px 10px", display: "flex", flexDirection: "column", gap: 6 }}>
        <ChannelRow Icon={Mail} channel="Email" state={email} />
        <ChannelRow Icon={PhoneCall} channel="Call" state={call} />
        {/* LinkedIn lane only renders once there's a logged motion, so the
            common email/call-only rows stay compact. */}
        {linkedin.heroColor ? <ChannelRow Icon={Linkedin} channel="LinkedIn" state={linkedin} /> : null}
      </div>

      {hasLiveSequence ? (
        <div style={{
          padding: "6px 14px 10px",
          display: "flex", alignItems: "center", gap: 8,
        }}>
          <span style={{ fontSize: 10, fontWeight: 800, color: "#67768a", textTransform: "uppercase", letterSpacing: "0.06em" }}>
            Seq
          </span>
          <div style={{
            flex: 1, height: 4, borderRadius: 999, background: "#eef2f7", overflow: "hidden",
          }}>
            <div style={{
              width: `${seqPct}%`,
              height: "100%",
              borderRadius: 999,
              background: `linear-gradient(90deg, ${heroPalette.bar}, ${heroPalette.dot})`,
              transition: "width 200ms ease",
            }} />
          </div>
          <span style={{ fontSize: 10.5, color: "#67768a", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
            {lifecycle!.days_since_launch != null ? `D${lifecycle!.days_since_launch} · ` : ""}
            {lifecycle!.done_count}/{lifecycle!.total_steps}
          </span>
          {lifecycle!.overdue_count > 0 ? (
            <span style={{
              fontSize: 10, color: "#b91c1c", fontWeight: 800,
              padding: "1px 6px", borderRadius: 999,
              background: "#fef2f2", border: "1px solid #fecaca",
            }}>
              {lifecycle!.overdue_count} late
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

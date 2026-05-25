import { useState } from "react";
import { Mail, PhoneCall } from "lucide-react";
import type { Contact } from "../../types";
import type { LifecycleSummary } from "../../lib/api";

/**
 * Channel-status progress cell — redesigned.
 *
 * Design influences:
 *   - Apollo / Outreach: micro-event dot rail per channel
 *   - Linear: soft hero header, hover lift, status pill grammar
 *   - Pipedrive: left-edge accent strip driven by the hottest signal
 *   - HubSpot: subtle gradient tint on positive-momentum cells
 *
 * Visual structure:
 *   ┌─[accent strip]─────────────────────────────────┐
 *   │  [72]  REPLIED · 3d ago               · last  │   ← Hero strip
 *   ├────────────────────────────────────────────────┤
 *   │  [✉]  Email    ● ● ● ●     Reply 3d ago        │   ← Channel row
 *   │  [☎]  Call     ○            Not called         │
 *   └────────────────────────────────────────────────┘
 *
 * Five-state color contract (consistent across both channels):
 *   WHITE  — no action / pending follow-up
 *   BLUE   — action taken (sent / called)
 *   GREEN  — positive outcome (reply / interested / meeting booked)
 *   ORANGE — soft negative (not interested but reachable)
 *   RED    — hard stop (DNC, invalid number)
 */

type ChannelColor = "white" | "blue" | "green" | "orange" | "red";

type ChannelState = {
  color: ChannelColor;
  label: string;
  sub: string;
  // Source-of-truth datetime for this lane. `null` = no event yet.
  timestamp: Date | null;
  // Ordered list of micro-events that have happened on this channel so the
  // dot rail can render them left-to-right (e.g. ["sent", "opened", "clicked", "replied"]).
  events: Array<"sent" | "opened" | "clicked" | "replied" | "bounced" | "called" | "no-answer">;
};

// Soft pastel-on-saturated palette. The `bar` is the left accent strip;
// `tint` is the gentle background gradient stop; `chip` is the status pill
// fill; `dot` is the filled-dot color.
const PALETTE: Record<ChannelColor, { dot: string; border: string; chipBg: string; chipFg: string; tint: string; bar: string; ring: string }> = {
  white:  { dot: "#ffffff", border: "#cdd9e6", chipBg: "#f4f8fc", chipFg: "#5e6f85", tint: "#fafbfd", bar: "#cdd9e6", ring: "rgba(148,164,189,0.20)" },
  blue:   { dot: "#3b82f6", border: "#2563eb", chipBg: "#eff6ff", chipFg: "#1d4ed8", tint: "#f5f9ff", bar: "#3b82f6", ring: "rgba(59,130,246,0.22)" },
  green:  { dot: "#22c55e", border: "#16a34a", chipBg: "#ecfdf5", chipFg: "#15803d", tint: "#f3fbf6", bar: "#22c55e", ring: "rgba(34,197,94,0.24)" },
  orange: { dot: "#f59e0b", border: "#d97706", chipBg: "#fff7ed", chipFg: "#b45309", tint: "#fffaf3", bar: "#f59e0b", ring: "rgba(245,158,11,0.26)" },
  red:    { dot: "#ef4444", border: "#dc2626", chipBg: "#fef2f2", chipFg: "#b91c1c", tint: "#fff5f5", bar: "#ef4444", ring: "rgba(239,68,68,0.26)" },
};

// Micro-event dot colors. Each event type has its own hue so the rail
// reads like a timeline: blue → teal → sky → violet → orange.
const EVENT_DOT: Record<ChannelState["events"][number], string> = {
  sent: "#3b82f6",
  opened: "#14b8a6",
  clicked: "#0ea5e9",
  replied: "#8b5cf6",
  bounced: "#f59e0b",
  called: "#22c55e",
  "no-answer": "#94a3b8",
};

// Hot-channel resolution order — when both lanes have signal, the cell's
// hero strip and accent bar reflect whichever lane is in the strongest
// state. RED > GREEN > ORANGE > BLUE > WHITE.
const COLOR_RANK: Record<ChannelColor, number> = { red: 5, green: 4, orange: 3, blue: 2, white: 1 };
function hottest(a: ChannelColor, b: ChannelColor): ChannelColor {
  return COLOR_RANK[a] >= COLOR_RANK[b] ? a : b;
}

function getEmailState(contact: Contact): ChannelState {
  const seq = contact.sequence_status || "";
  const opens = contact.email_open_count ?? 0;
  const clicks = contact.email_click_count ?? 0;
  const sent = ["queued_instantly", "sent", "replied", "meeting_booked", "not_interested"].includes(seq) || opens > 0;
  const replied = seq === "replied";
  const booked = seq === "meeting_booked";
  const lastOpenedAt = contact.email_last_opened_at ? new Date(contact.email_last_opened_at) : null;

  const events: ChannelState["events"] = [];
  if (sent) events.push("sent");
  if (opens > 0) events.push("opened");
  if (clicks > 0) events.push("clicked");
  if (replied || booked) events.push("replied");

  if (sent && seq === "not_interested") {
    return { color: "orange", label: "Not interested", sub: opens > 0 ? `${opens} open${opens === 1 ? "" : "s"} · email` : "From email outreach", timestamp: lastOpenedAt, events };
  }
  if (booked) {
    return { color: "green", label: "Meeting booked", sub: opens > 0 ? `${opens} open${opens === 1 ? "" : "s"} · ${clicks} click${clicks === 1 ? "" : "s"}` : "From email outreach", timestamp: lastOpenedAt, events };
  }
  if (replied) {
    return { color: "green", label: "Replied", sub: opens > 0 ? `${opens} open${opens === 1 ? "" : "s"} · ${clicks} click${clicks === 1 ? "" : "s"}` : "Reply received", timestamp: lastOpenedAt, events };
  }
  if (sent) {
    return { color: "blue", label: "Sent", sub: opens > 0 ? `${opens} open${opens === 1 ? "" : "s"} · no reply yet` : "Awaiting reply", timestamp: lastOpenedAt, events };
  }
  return { color: "white", label: "Not sent", sub: "Email pending", timestamp: null, events };
}

function getCallState(contact: Contact): ChannelState {
  const status = contact.call_status || "";
  const disp = contact.call_disposition || "";
  const ts = contact.call_last_at ? new Date(contact.call_last_at) : null;

  const HARD_NEG = new Set(["invalid_number_wrong_number", "do_not_contact_dnc"]);
  const SOFT_NEG = new Set(["connected_not_interested", "contact_poor_fit", "gatekeeper_connected_to_admin"]);
  const POSITIVE = new Set(["demo_scheduled_booked", "interested_follow_up_required", "meeting_confirmed"]);
  const FOLLOW_UP = new Set(["call_back_later_rescheduled"]);

  const events: ChannelState["events"] = [];
  if (status && status !== "none") events.push(status === "connected" || status === "callback" || POSITIVE.has(disp) ? "called" : "no-answer");
  if (POSITIVE.has(disp)) events.push("replied"); // treat positive disposition as the "reply-equivalent" pulse

  if (HARD_NEG.has(disp)) {
    return { color: "red", label: disp === "invalid_number_wrong_number" ? "Wrong number" : "Do not contact", sub: "Hard stop · do not retry", timestamp: ts, events };
  }
  if (POSITIVE.has(disp)) {
    return {
      color: "green",
      label: disp === "demo_scheduled_booked" ? "Demo booked" : disp === "meeting_confirmed" ? "Meeting confirmed" : "Interested",
      sub: status === "connected" ? "Connected on call" : (status || "Connected"),
      timestamp: ts, events,
    };
  }
  if (SOFT_NEG.has(disp)) {
    return {
      color: "orange",
      label: disp === "connected_not_interested" ? "Not interested" : disp === "gatekeeper_connected_to_admin" ? "Gatekeeper" : "Poor fit",
      sub: "Connected · soft pass", timestamp: ts, events,
    };
  }
  if (FOLLOW_UP.has(disp)) {
    return { color: "white", label: "Follow-up scheduled", sub: "Call back later", timestamp: ts, events };
  }
  if (status && status !== "none") {
    return {
      color: "blue",
      label:
        status === "connected" ? "Connected"
        : status === "voicemail" ? "Voicemail"
        : status === "callback" ? "Callback requested"
        : "Attempted",
      sub: disp ? disp.replace(/_/g, " ") : "Awaiting disposition", timestamp: ts, events,
    };
  }
  return { color: "white", label: "Not called", sub: "Awaiting first call", timestamp: null, events };
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

// Tiny dot-rail. Up to 4 dots — Apollo-style timeline of channel pulses.
function DotRail({ events, max = 4 }: { events: ChannelState["events"]; max?: number }) {
  const filled = events.slice(0, max);
  const empty = Math.max(0, max - filled.length);
  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      {filled.map((ev, i) => (
        <span
          key={`f-${i}`}
          title={ev}
          style={{
            width: 8, height: 8, borderRadius: 999,
            background: EVENT_DOT[ev],
            boxShadow: `0 0 0 2px ${EVENT_DOT[ev]}22`,
          }}
        />
      ))}
      {Array.from({ length: empty }).map((_, i) => (
        <span
          key={`e-${i}`}
          style={{
            width: 8, height: 8, borderRadius: 999,
            background: "#ffffff", border: "1.5px solid #d8e0ea",
          }}
        />
      ))}
    </div>
  );
}

function ChannelRow({ Icon, channel, state }: { Icon: typeof Mail; channel: string; state: ChannelState }) {
  const palette = PALETTE[state.color];
  const titleParts = [
    `${channel}: ${state.label}`,
    state.sub,
    state.timestamp ? state.timestamp.toLocaleString() : null,
  ].filter(Boolean) as string[];
  return (
    <div
      title={titleParts.join(" · ")}
      style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "8px 10px", borderRadius: 10,
        background: `linear-gradient(180deg, #ffffff 0%, ${palette.tint} 100%)`,
        border: `1px solid ${state.color === "white" ? "#e8eef5" : palette.ring.replace("0.20", "0.30").replace("0.22", "0.35").replace("0.24", "0.36").replace("0.26", "0.38")}`,
      }}
    >
      {/* Channel icon chip */}
      <div style={{
        width: 26, height: 26, borderRadius: 8,
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        background: palette.chipBg,
        border: `1px solid ${palette.ring.replace("0.20", "0.30").replace("0.22", "0.35").replace("0.24", "0.36").replace("0.26", "0.38")}`,
        color: palette.chipFg, flexShrink: 0,
      }}>
        <Icon size={13} />
      </div>

      {/* Label + sub stack */}
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

      {/* Mini event-dot rail */}
      <DotRail events={state.events} />

      {/* Right-aligned relative timestamp chip */}
      {state.timestamp ? (
        <span style={{
          fontSize: 10, fontWeight: 800, color: palette.chipFg,
          background: "#ffffff", padding: "2px 7px", borderRadius: 999,
          border: `1px solid ${palette.ring.replace("0.20", "0.32").replace("0.22", "0.36").replace("0.24", "0.38").replace("0.26", "0.40")}`,
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
  const email = getEmailState(contact);
  const call = getCallState(contact);
  const heroColor = hottest(email.color, call.color);
  const heroPalette = PALETTE[heroColor];
  const [hover, setHover] = useState(false);

  // Strongest signal across both channels — drives the hero status text.
  const heroState = COLOR_RANK[email.color] >= COLOR_RANK[call.color] ? email : call;
  const lastTouch = (() => {
    const a = email.timestamp?.getTime() ?? 0;
    const b = call.timestamp?.getTime() ?? 0;
    const ts = a > b ? email.timestamp : (b > 0 ? call.timestamp : null);
    return ts;
  })();

  // Sequence progress mini-bar — only when a live sequence is running.
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
        // Hover lift + faint glow in the hero color. Matches Linear's
        // "this is clickable" affordance without a heavy outline.
        boxShadow: hover
          ? `0 8px 24px -8px ${heroPalette.ring}, 0 0 0 1px ${heroPalette.ring}`
          : "inset 0 1px 0 rgba(255,255,255,0.9), 0 1px 2px rgba(15,39,68,0.04)",
        transform: hover ? "translateY(-1px)" : "translateY(0)",
        transition: "box-shadow 160ms ease, transform 160ms ease",
        cursor: "pointer",
      }}
    >
      {/* Pipedrive-style accent strip on the left edge, colored by the
          hottest channel signal. Sits flush with the card edge. */}
      <div style={{
        position: "absolute", left: 0, top: 0, bottom: 0,
        width: 3,
        background: `linear-gradient(180deg, ${heroPalette.bar} 0%, ${heroPalette.bar}99 100%)`,
      }} />

      {/* Hero strip — score + status pill + last-touch. Tinted gradient
          background matching the hottest signal. */}
      <div style={{
        padding: "10px 14px 8px",
        background: `linear-gradient(135deg, ${heroPalette.tint} 0%, #ffffff 70%)`,
        borderBottom: "1px solid #eef2f7",
        display: "flex", alignItems: "center", gap: 8, justifyContent: "space-between",
      }}>
        <span style={{
          fontSize: 10.5, fontWeight: 800,
          textTransform: "uppercase", letterSpacing: "0.07em",
          color: heroPalette.chipFg,
          padding: "3px 9px", borderRadius: 999,
          background: heroPalette.chipBg,
          border: `1px solid ${heroPalette.ring.replace("0.20", "0.32").replace("0.22", "0.36").replace("0.24", "0.38").replace("0.26", "0.40")}`,
        }}>
          {heroState.label}
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
            No activity
          </span>
        )}
      </div>

      {/* Channel rows */}
      <div style={{ padding: "8px 10px", display: "flex", flexDirection: "column", gap: 6 }}>
        <ChannelRow Icon={Mail} channel="Email" state={email} />
        <ChannelRow Icon={PhoneCall} channel="Call" state={call} />
      </div>

      {/* Sequence progress mini-bar — HubSpot-style. Only when a live
          sequence is running. */}
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

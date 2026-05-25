import React, { useState } from "react";
import { Mail, PhoneCall, Link2, ChevronDown, ChevronRight, ExternalLink, User, Clock, AtSign, Headphones } from "lucide-react";
import type { LifecycleStep, LifecycleStepState, LifecycleEvent } from "../../lib/api";

export const LIFECYCLE_DOT_STYLE: Record<LifecycleStepState, { bg: string; ring: string; border: string; text: string }> = {
  sent:     { bg: "#22c55e", ring: "#dcfce7", border: "#16a34a", text: "#15803d" },
  opened:   { bg: "#14b8a6", ring: "#ccfbf1", border: "#0d9488", text: "#0f766e" },
  clicked:  { bg: "#0ea5e9", ring: "#e0f2fe", border: "#0284c7", text: "#0369a1" },
  replied:  { bg: "#7c3aed", ring: "#ede9fe", border: "#6d28d9", text: "#6d28d9" },
  done:     { bg: "#16a34a", ring: "#dcfce7", border: "#15803d", text: "#15803d" },
  overdue:  { bg: "#ef4444", ring: "#fee2e2", border: "#dc2626", text: "#b91c1c" },
  upcoming: { bg: "#ffffff", ring: "transparent", border: "#d6e0ea", text: "#8aa0b5" },
  skipped:  { bg: "#f1f5f9", ring: "transparent", border: "#cbd5e1", text: "#94a3b8" },
  failed:   { bg: "#f97316", ring: "#ffedd5", border: "#ea580c", text: "#c2410c" },
};

const CHANNEL_ICON: Record<"email" | "call" | "linkedin", React.ReactNode> = {
  email: <Mail size={13} />,
  call: <PhoneCall size={13} />,
  linkedin: <Link2 size={13} />,
};

export function formatLifecycleDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) +
    " " + d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

function formatDuration(seconds: number | null | undefined): string | null {
  if (!seconds || seconds <= 0) return null;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  if (m === 0) return `${s}s`;
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

/** Best-effort actor label for a recorded event. CRM user wins, then
 * Aircall agent, then the email-from address, then the integration source
 * (e.g. "Instantly"). Returns null if we genuinely don't know — better
 * than rendering "Unknown by Unknown". */
function actorLabel(ev: LifecycleEvent | null | undefined): string | null {
  if (!ev) return null;
  if (ev.created_by_name) return ev.created_by_name;
  if (ev.aircall_user_name) return `${ev.aircall_user_name} (Aircall)`;
  if (ev.email_from) return ev.email_from;
  if (ev.source) {
    const s = ev.source.toLowerCase();
    if (s === "instantly") return "Instantly";
    if (s === "manual") return "Manually logged";
    return ev.source.replace(/_/g, " ");
  }
  return null;
}

/** Tiny meta-row used inside event cards — icon + label + value. Keeps the
 * card vertical rhythm consistent and lets the renderer hide whole rows
 * when the field is empty. */
function MetaRow({ icon, label, value }: { icon: React.ReactNode; label: string; value: React.ReactNode }) {
  if (value == null || value === "" || value === false) return null;
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 8, fontSize: 12, color: "#475569", lineHeight: 1.5 }}>
      <span style={{ color: "#94a3b8", display: "inline-flex", marginTop: 1, flexShrink: 0 }}>{icon}</span>
      <span style={{ color: "#64748b", fontWeight: 700, minWidth: 56, flexShrink: 0 }}>{label}</span>
      <span style={{ color: "#1e293b", wordBreak: "break-word", overflowWrap: "anywhere" }}>{value}</span>
    </div>
  );
}

/** Renders one event row inside the step. Color reflects the event type
 * (send vs reply vs bounce), and a click expands the row to reveal the
 * body / call notes inline. Defaults to collapsed for replies + sends,
 * since the rep usually scans timestamps first then drills in. */
function EventCard({
  kind,
  ev,
  whenIso,
  tone,
  preview,
  startExpanded = false,
}: {
  kind: string;             // "Sent" | "Opened" | "Replied" | "Call logged" | ...
  ev: LifecycleEvent | null | undefined;
  whenIso: string | null | undefined;
  tone: { fg: string; bg: string; border: string };
  preview?: string | null;  // 1-line preview shown while collapsed
  startExpanded?: boolean;
}) {
  const [expanded, setExpanded] = useState(startExpanded);
  if (!ev && !whenIso) return null;

  const subject = ev?.email_subject;
  const body = ev?.content;
  const summary = ev?.ai_summary;
  const actor = actorLabel(ev);
  const duration = formatDuration(ev?.call_duration_seconds);
  const recording = ev?.recording_url;
  const hasExpandableContent = Boolean(body || summary || ev?.email_to || ev?.email_cc || recording || duration);

  return (
    <div style={{
      borderRadius: 10,
      border: `1px solid ${tone.border}`,
      background: tone.bg,
      overflow: "hidden",
    }}>
      <button
        type="button"
        onClick={() => hasExpandableContent && setExpanded((v) => !v)}
        style={{
          width: "100%", display: "flex", alignItems: "center", gap: 8,
          padding: "8px 10px", border: "none", background: "transparent",
          textAlign: "left",
          cursor: hasExpandableContent ? "pointer" : "default",
        }}
      >
        <span style={{
          fontSize: 10.5, fontWeight: 800, color: tone.fg,
          background: "#ffffffcc", padding: "2px 8px", borderRadius: 999,
          border: `1px solid ${tone.border}`,
          textTransform: "uppercase", letterSpacing: "0.05em",
          flexShrink: 0,
        }}>{kind}</span>
        <span style={{ fontSize: 12, color: "#334155", fontWeight: 600, flexShrink: 0 }}>
          {formatLifecycleDate(whenIso)}
        </span>
        {actor ? (
          <span style={{ fontSize: 11.5, color: "#64748b", display: "inline-flex", alignItems: "center", gap: 4, minWidth: 0, overflow: "hidden" }}>
            <User size={11} />
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{actor}</span>
          </span>
        ) : null}
        <span style={{ flex: 1 }} />
        {hasExpandableContent ? (
          expanded ? <ChevronDown size={14} color="#64748b" /> : <ChevronRight size={14} color="#64748b" />
        ) : null}
      </button>

      {/* Inline preview when collapsed — keeps the most important line visible. */}
      {!expanded && (subject || preview) ? (
        <div style={{ padding: "0 10px 8px", fontSize: 12.5, color: "#1e293b" }}>
          {subject ? <div style={{ fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{subject}</div> : null}
          {preview ? (
            <div style={{ color: "#475569", overflow: "hidden", textOverflow: "ellipsis", display: "-webkit-box", WebkitLineClamp: 1, WebkitBoxOrient: "vertical" }}>
              {preview}
            </div>
          ) : null}
        </div>
      ) : null}

      {expanded ? (
        <div style={{ padding: "0 10px 10px", display: "grid", gap: 8 }}>
          {subject ? (
            <div style={{ fontSize: 13, fontWeight: 700, color: "#0f172a" }}>{subject}</div>
          ) : null}
          <div style={{ display: "grid", gap: 4 }}>
            <MetaRow icon={<AtSign size={12} />} label="From" value={ev?.email_from} />
            <MetaRow icon={<AtSign size={12} />} label="To" value={ev?.email_to} />
            <MetaRow icon={<AtSign size={12} />} label="Cc" value={ev?.email_cc} />
            <MetaRow icon={<Clock size={12} />} label="Duration" value={duration} />
            <MetaRow
              icon={<Headphones size={12} />}
              label="Recording"
              value={recording ? (
                <a href={recording} target="_blank" rel="noreferrer" style={{ color: "#1d4ed8", display: "inline-flex", alignItems: "center", gap: 4, fontWeight: 700, textDecoration: "none" }}>
                  Listen <ExternalLink size={11} />
                </a>
              ) : null}
            />
          </div>
          {summary ? (
            <div style={{ padding: "8px 10px", background: "#ffffff", borderRadius: 8, border: "1px solid #e2e8f0" }}>
              <div style={{ fontSize: 10.5, fontWeight: 800, color: "#7c3aed", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 4 }}>
                AI Summary
              </div>
              <div style={{ fontSize: 12, color: "#334155", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{summary}</div>
            </div>
          ) : null}
          {body ? (
            <div style={{ padding: "10px 12px", background: "#ffffff", borderRadius: 8, border: "1px solid #e2e8f0", maxHeight: 260, overflowY: "auto" }}>
              <div style={{ fontSize: 10.5, fontWeight: 800, color: "#475569", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 6 }}>
                {ev?.medium === "email" || ev?.email_subject ? "Email body" : "Notes"}
              </div>
              <div style={{ fontSize: 12.5, color: "#1e293b", lineHeight: 1.6, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{body}</div>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

const TONE = {
  blue:   { fg: "#1d4ed8", bg: "#eff6ff", border: "#bfdbfe" },
  teal:   { fg: "#0d9488", bg: "#ecfeff", border: "#a5f3fc" },
  sky:    { fg: "#0284c7", bg: "#f0f9ff", border: "#bae6fd" },
  violet: { fg: "#7c3aed", bg: "#faf5ff", border: "#e9d5ff" },
  orange: { fg: "#c2410c", bg: "#fff7ed", border: "#fed7aa" },
  green:  { fg: "#15803d", bg: "#ecfdf5", border: "#bbf7d0" },
  slate:  { fg: "#475569", bg: "#f8fafc", border: "#e2e8f0" },
};

export function LifecycleStepRow({ step }: { step: LifecycleStep }) {
  const style = LIFECYCLE_DOT_STYLE[step.state];
  const stateLabels: Record<LifecycleStepState, string> = {
    sent: "Sent", opened: "Opened", clicked: "Clicked", replied: "Replied",
    done: "Done", overdue: "Overdue", upcoming: "Upcoming",
    skipped: "Skipped", failed: "Failed",
  };

  return (
    <div style={{ display: "flex", gap: 12, padding: "14px 0", borderBottom: "1px solid #eef2f7" }}>
      {/* Rail dot */}
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", paddingTop: 4 }}>
        <div style={{ width: 14, height: 14, borderRadius: 999, background: style.bg, border: `2px solid ${style.border}`, boxShadow: style.ring !== "transparent" ? `0 0 0 3px ${style.ring}` : "none", flexShrink: 0 }} />
      </div>

      {/* Content */}
      <div style={{ flex: 1, minWidth: 0, display: "grid", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 13, fontWeight: 800, color: "#0f2744" }}>
            {CHANNEL_ICON[step.channel]}
            Step {step.index + 1} · {step.channel[0].toUpperCase() + step.channel.slice(1)}
          </span>
          <span style={{ fontSize: 11, color: "#64748b", fontWeight: 600 }}>
            day {step.day_offset}
          </span>
          <span style={{ fontSize: 10.5, fontWeight: 800, padding: "2px 8px", borderRadius: 999, background: style.ring !== "transparent" ? style.ring : "#f1f5f9", color: style.border, border: `1px solid ${style.border}`, textTransform: "uppercase", letterSpacing: "0.05em" }}>
            {stateLabels[step.state]}
          </span>
          {step.state === "overdue" && (
            <span style={{ fontSize: 10.5, color: "#dc2626", fontWeight: 700 }}>
              due {formatLifecycleDate(step.due_at)}
            </span>
          )}
        </div>

        {step.objective && (
          <div style={{ fontSize: 12, color: "#64748b", lineHeight: 1.5 }}>{step.objective}</div>
        )}

        {/* ── Email lane events ───────────────────────────────────────── */}
        {step.channel === "email" && (
          <div style={{ display: "grid", gap: 6 }}>
            <EventCard
              kind="Sent"
              ev={step.send_event}
              whenIso={step.fired_at}
              tone={TONE.blue}
              preview={step.send_event?.content || null}
            />
            <EventCard
              kind="Opened"
              ev={step.open_event}
              whenIso={step.opened_at}
              tone={TONE.teal}
            />
            <EventCard
              kind="Clicked"
              ev={step.click_event}
              whenIso={step.clicked_at}
              tone={TONE.sky}
            />
            <EventCard
              kind="Replied"
              ev={step.reply_event}
              whenIso={step.replied_at}
              tone={TONE.violet}
              preview={step.reply_event?.ai_summary || step.reply_event?.content || null}
              startExpanded
            />
            <EventCard
              kind="Bounced"
              ev={step.bounce_event}
              whenIso={step.bounced_at}
              tone={TONE.orange}
              startExpanded
            />
          </div>
        )}

        {/* ── Call lane events ────────────────────────────────────────── */}
        {step.channel === "call" && step.fired_at && (
          <EventCard
            kind={step.call_outcome ? `Call · ${step.call_outcome}` : "Call logged"}
            ev={step.call_event}
            whenIso={step.fired_at}
            tone={TONE.green}
            preview={step.call_event?.ai_summary || step.note || null}
            startExpanded
          />
        )}

        {/* ── LinkedIn lane events ────────────────────────────────────── */}
        {step.channel === "linkedin" && step.fired_at && (
          <EventCard
            kind="LinkedIn touch"
            ev={step.linkedin_event}
            whenIso={step.fired_at}
            tone={TONE.slate}
            preview={step.note || null}
            startExpanded
          />
        )}

        {step.state === "skipped" && step.skip_reason && (
          <div style={{ fontSize: 12, color: "#64748b", fontStyle: "italic" }}>
            Skipped — {step.skip_reason.replace(/_/g, " ")}
          </div>
        )}
        {step.state === "upcoming" && !step.fired_at && (
          <div style={{ fontSize: 12, color: "#64748b" }}>
            Due {formatLifecycleDate(step.due_at)}
          </div>
        )}
      </div>
    </div>
  );
}

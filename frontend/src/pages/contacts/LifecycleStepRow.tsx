import React from "react";
import { Mail, PhoneCall, Link2 } from "lucide-react";
import type { LifecycleStep, LifecycleStepState } from "../../lib/api";

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

export function LifecycleStepRow({ step }: { step: LifecycleStep }) {
  const style = LIFECYCLE_DOT_STYLE[step.state];
  const stateLabels: Record<LifecycleStepState, string> = {
    sent: "Sent", opened: "Opened", clicked: "Clicked", replied: "Replied",
    done: "Done", overdue: "Overdue", upcoming: "Upcoming",
    skipped: "Skipped", failed: "Failed",
  };
  const showFired = step.fired_at;
  return (
    <div style={{ display: "flex", gap: 12, padding: "12px 0", borderBottom: "1px solid #eef2f7" }}>
      {/* Rail dot */}
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", paddingTop: 4 }}>
        <div style={{ width: 14, height: 14, borderRadius: 999, background: style.bg, border: `2px solid ${style.border}`, boxShadow: style.ring !== "transparent" ? `0 0 0 3px ${style.ring}` : "none", flexShrink: 0 }} />
      </div>

      {/* Content */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12.5, fontWeight: 700, color: "#0f2744" }}>
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
          <div style={{ marginTop: 4, fontSize: 11.5, color: "#64748b" }}>{step.objective}</div>
        )}

        {/* Channel-specific details */}
        {step.channel === "email" && (
          <div style={{ marginTop: 6, display: "grid", gap: 3 }}>
            {step.subject && <div style={{ fontSize: 12, fontWeight: 600, color: "#24364b" }}>{step.subject}</div>}
            {showFired && <div style={{ fontSize: 11.5, color: "#334155" }}>Sent: {formatLifecycleDate(step.fired_at)}</div>}
            {step.opened_at && <div style={{ fontSize: 11.5, color: "#0d9488" }}>Opened: {formatLifecycleDate(step.opened_at)}</div>}
            {step.clicked_at && <div style={{ fontSize: 11.5, color: "#0284c7" }}>Clicked: {formatLifecycleDate(step.clicked_at)}</div>}
            {step.replied_at && <div style={{ fontSize: 11.5, color: "#7c3aed", fontWeight: 700 }}>Replied: {formatLifecycleDate(step.replied_at)}</div>}
            {step.bounced_at && <div style={{ fontSize: 11.5, color: "#ea580c", fontWeight: 700 }}>Bounced: {formatLifecycleDate(step.bounced_at)}</div>}
          </div>
        )}
        {step.channel === "call" && step.fired_at && (
          <div style={{ marginTop: 6, display: "grid", gap: 3 }}>
            <div style={{ fontSize: 11.5, color: "#334155" }}>
              Call logged: {formatLifecycleDate(step.fired_at)}
              {step.call_outcome && <> · {step.call_outcome}</>}
            </div>
            {step.note && <div style={{ fontSize: 11.5, color: "#64748b", lineHeight: 1.5 }}>{step.note}</div>}
          </div>
        )}
        {step.channel === "linkedin" && step.fired_at && (
          <div style={{ marginTop: 6, display: "grid", gap: 3 }}>
            <div style={{ fontSize: 11.5, color: "#334155" }}>Logged: {formatLifecycleDate(step.fired_at)}</div>
            {step.note && <div style={{ fontSize: 11.5, color: "#64748b", lineHeight: 1.5 }}>{step.note}</div>}
          </div>
        )}
        {step.state === "skipped" && step.skip_reason && (
          <div style={{ marginTop: 4, fontSize: 11.5, color: "#64748b", fontStyle: "italic" }}>
            Skipped — {step.skip_reason.replace(/_/g, " ")}
          </div>
        )}
        {step.state === "upcoming" && (
          <div style={{ marginTop: 4, fontSize: 11.5, color: "#64748b" }}>
            Due {formatLifecycleDate(step.due_at)}
          </div>
        )}
      </div>
    </div>
  );
}

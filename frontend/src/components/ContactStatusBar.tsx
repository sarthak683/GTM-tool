import { useState } from "react";
import { Check } from "lucide-react";
import { CONTACT_STATUS_OPTIONS, type ContactStatusValue } from "../lib/contactStatus";

type ContactStatusBarProps = {
  readOnly?: boolean;
  value?: string | null;
  saving?: boolean;
  compact?: boolean;
  /** `reason` is set when the status needs one (Not the Right Prospect / Reach Out Later / DND). */
  onChange: (value: ContactStatusValue, reason?: string) => void;
  /** Latest saved reason, shown under the bar while the prospect is in a reason-required status. */
  reason?: string | null;
  /** Prospect name for the popup heading. */
  subjectName?: string;
  /** Stop row/card click-through when embedded in a clickable parent. */
  stopPropagation?: boolean;
};

const REASON_REQUIRED = new Set<string>(["not_the_right_prospect", "reach_out_later", "dnd"]);

export default function ContactStatusBar({
  readOnly = false,
  value,
  saving = false,
  compact = false,
  onChange,
  reason,
  subjectName,
  stopPropagation = false,
}: ContactStatusBarProps) {
  const [pending, setPending] = useState<ContactStatusValue | null>(null);
  const [draft, setDraft] = useState("");
  const pendingLabel = CONTACT_STATUS_OPTIONS.find((o) => o.value === pending)?.label ?? "";
  const wrapProps = stopPropagation
    ? {
        onClick: (e: React.MouseEvent) => e.stopPropagation(),
        onKeyDown: (e: React.KeyboardEvent) => e.stopPropagation(),
      }
    : {};

  return (
    <>
    <div
      {...wrapProps}
      style={{
        display: "flex",
        alignItems: "center",
        gap: compact ? 6 : 9,
        flexWrap: "wrap",
        ...(compact
          ? { marginTop: 8 }
          : {
              marginTop: 14,
              padding: "11px 15px",
              background: "linear-gradient(135deg, #f3f6fb 0%, #e9eef7 100%)",
              border: "1px solid #dbe3f0",
              borderRadius: 14,
              boxShadow: "0 2px 10px rgba(30,55,95,0.05)",
            }),
      }}
    >
      <span
        style={{
          fontSize: compact ? 10 : 11,
          fontWeight: 800,
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          color: "#6b7a90",
          marginRight: compact ? 0 : 2,
        }}
      >
        Status
      </span>
      {CONTACT_STATUS_OPTIONS.map((option) => {
        const active = value === option.value;
        return (
          <button
            key={option.value}
            type="button"
            onClick={(e) => {
              if (stopPropagation) e.stopPropagation();
              if (!active && REASON_REQUIRED.has(option.value)) {
                setDraft("");
                setPending(option.value);
                return;
              }
              onChange(option.value);
            }}
            disabled={saving || readOnly}
            title={active ? "Click to clear this status" : `Set status to ${option.label}`}
            onMouseEnter={(e) => {
              if (!active) e.currentTarget.style.boxShadow = `0 2px 8px ${option.color}33`;
            }}
            onMouseLeave={(e) => {
              if (!active) e.currentTarget.style.boxShadow = "none";
            }}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: compact ? 4 : 5,
              borderRadius: 999,
              padding: active
                ? compact
                  ? "4px 10px 4px 8px"
                  : "6px 13px 6px 10px"
                : compact
                  ? "4px 10px"
                  : "6px 13px",
              fontSize: compact ? 11 : 12.5,
              fontWeight: 800,
              cursor: saving ? "wait" : "pointer",
              background: active ? option.color : option.bg,
              color: active ? "#fff" : option.color,
              border: `1px solid ${active ? option.color : "transparent"}`,
              boxShadow: active ? `0 3px 10px ${option.color}55` : "none",
              transform: active ? "translateY(-1px)" : "none",
              opacity: saving && !active ? 0.55 : 1,
              transition: "background 0.14s, color 0.14s, box-shadow 0.14s, transform 0.14s",
            }}
          >
            {active ? <Check size={compact ? 11 : 13} strokeWidth={3} /> : null}
            {option.label}
          </button>
        );
      })}
    </div>
    {value && REASON_REQUIRED.has(value) && reason ? (
      <div
        {...wrapProps}
        style={{ marginTop: 6, fontSize: compact ? 11.5 : 12.5, lineHeight: 1.5, color: "#5f7390" }}
      >
        <strong style={{ color: "#1d2b3c" }}>Reason:</strong> {reason}
      </div>
    ) : null}
    {pending && (
      <div
        role="dialog"
        aria-modal="true"
        onClick={(e) => { e.stopPropagation(); if (!saving) setPending(null); }}
        onKeyDown={(e) => e.stopPropagation()}
        style={{ position: "fixed", inset: 0, zIndex: 130, background: "rgba(15,26,42,0.45)", display: "flex", alignItems: "center", justifyContent: "center", padding: 16, cursor: "default" }}
      >
        <div
          onClick={(e) => e.stopPropagation()}
          style={{ width: "min(480px, 100%)", background: "#fff", borderRadius: 16, padding: "22px 24px", boxShadow: "0 24px 60px rgba(15,23,42,0.25)", textAlign: "left" }}
        >
          <div style={{ fontSize: 17, fontWeight: 800, color: "#1d2b3c" }}>
            Why is {subjectName || "this prospect"} being moved to {pendingLabel}?
          </div>
          <div style={{ marginTop: 6, fontSize: 13, color: "#5f7390", lineHeight: 1.5 }}>
            A short reason is required. It is saved on the prospect&apos;s timeline.
          </div>
          <textarea
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="e.g. Wrong persona / asked us to follow up in Q1 / left the company"
            rows={4}
            style={{ marginTop: 14, width: "100%", boxSizing: "border-box", borderRadius: 12, border: "1px solid #dbe3f0", padding: "10px 12px", fontSize: 14, lineHeight: 1.5, resize: "vertical", fontFamily: "inherit" }}
          />
          <div style={{ marginTop: 16, display: "flex", justifyContent: "flex-end", gap: 10 }}>
            <button type="button" className="crm-button soft" disabled={saving} onClick={() => setPending(null)}>
              Cancel
            </button>
            <button
              type="button"
              className="crm-button primary"
              disabled={saving || draft.trim().length < 5}
              onClick={() => {
                const target = pending;
                setPending(null);
                onChange(target, draft.trim());
              }}
            >
              Save status
            </button>
          </div>
        </div>
      </div>
    )}
    </>
  );
}

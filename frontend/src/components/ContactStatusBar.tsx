import { Check } from "lucide-react";
import { CONTACT_STATUS_OPTIONS, type ContactStatusValue } from "../lib/contactStatus";

type ContactStatusBarProps = {
  value?: string | null;
  saving?: boolean;
  compact?: boolean;
  onChange: (value: ContactStatusValue) => void;
  /** Stop row/card click-through when embedded in a clickable parent. */
  stopPropagation?: boolean;
};

export default function ContactStatusBar({
  value,
  saving = false,
  compact = false,
  onChange,
  stopPropagation = false,
}: ContactStatusBarProps) {
  const wrapProps = stopPropagation
    ? {
        onClick: (e: React.MouseEvent) => e.stopPropagation(),
        onKeyDown: (e: React.KeyboardEvent) => e.stopPropagation(),
      }
    : {};

  return (
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
              onChange(option.value);
            }}
            disabled={saving}
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
  );
}

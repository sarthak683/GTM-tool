import { useEffect, useRef, useState, type CSSProperties } from "react";
import { CalendarRange, ChevronDown } from "lucide-react";

export type DateRangeValue = { from: string; to: string };

function formatShort(value: string): string {
  if (!value) return "";
  const d = new Date(`${value}T00:00:00`);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/**
 * From/To date-range filter rendered as a popover, styled to match
 * MultiSelectFilter / RangeFilter. Values are `YYYY-MM-DD` strings (native
 * date inputs); an empty string means "unbounded" on that side. Quick presets
 * are supplied by the caller so the same component serves both forward-looking
 * (scheduled follow-up) and backward-looking (last call) ranges.
 */
export default function DateRangeFilter({
  label,
  value,
  onChange,
  allLabel,
  minWidth,
  presets,
  hideLabel,
}: {
  label: string;
  value: DateRangeValue;
  onChange: (value: DateRangeValue) => void;
  allLabel: string;
  minWidth?: number;
  presets?: { label: string; getRange: () => DateRangeValue }[];
  hideLabel?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const handleClick = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open]);

  const active = !!(value.from || value.to);
  const displayLabel = !active
    ? allLabel
    : value.from && value.to
      ? `${formatShort(value.from)} – ${formatShort(value.to)}`
      : value.from
        ? `From ${formatShort(value.from)}`
        : `Until ${formatShort(value.to)}`;

  const buttonStyle: CSSProperties = {
    width: "100%",
    height: 42,
    borderRadius: 12,
    border: active ? "1.5px solid #cfe89a" : "1px solid #d9e1ec",
    background: active ? "#f3fbe3" : "#fff",
    padding: "0 28px 0 34px",
    fontSize: 13,
    color: "#1d2b3c",
    cursor: "pointer",
    outline: "none",
    textAlign: "left",
    position: "relative",
    minWidth: minWidth ?? 180,
  };

  const dateInputStyle: CSSProperties = {
    width: "100%",
    height: 34,
    borderRadius: 9,
    border: "1px solid #e2eaf2",
    background: "#f8fafc",
    padding: "0 10px",
    fontSize: 13,
    color: "#1d2b3c",
    outline: "none",
    boxSizing: "border-box",
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {!hideLabel && <label style={{ fontSize: 10, fontWeight: 700, color: "#7f8fa5", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</label>}
      <div ref={ref} style={{ position: "relative" }}>
        <button type="button" onClick={() => setOpen((current) => !current)} style={buttonStyle}>
          <CalendarRange size={14} style={{ position: "absolute", left: 11, top: "50%", transform: "translateY(-50%)", color: active ? "#9ace3d" : "#7f8fa5" }} />
          {displayLabel}
          <ChevronDown size={13} style={{ position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)", pointerEvents: "none", color: "#7f8fa5" }} />
        </button>
        {open && (
          <div className="beacon-pop" style={{ position: "absolute", top: "calc(100% + 6px)", left: 0, zIndex: 20, minWidth: 260, borderRadius: 14, border: "1px solid #dbe6f2", background: "#fff", boxShadow: "0 18px 36px rgba(15,23,42,0.14)", padding: 12, display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
              <span style={{ fontSize: 11, fontWeight: 800, color: "#6f8095", textTransform: "uppercase", letterSpacing: "0.08em" }}>{label}</span>
              {active && (
                <button type="button" onClick={() => onChange({ from: "", to: "" })} style={{ border: "none", background: "transparent", color: "#9ace3d", fontSize: 11, fontWeight: 800, cursor: "pointer" }}>
                  Clear
                </button>
              )}
            </div>
            {presets && presets.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {presets.map((preset) => {
                  const range = preset.getRange();
                  const isOn = range.from === value.from && range.to === value.to;
                  return (
                    <button
                      key={preset.label}
                      type="button"
                      onClick={() => onChange(range)}
                      style={{
                        border: isOn ? "1px solid #cfe89a" : "1px solid #e2eaf2",
                        background: isOn ? "#f3fbe3" : "#f8fafc",
                        color: isOn ? "#4d7c0f" : "#4d6178",
                        borderRadius: 999,
                        padding: "4px 10px",
                        fontSize: 11.5,
                        fontWeight: 700,
                        cursor: "pointer",
                      }}
                    >
                      {preset.label}
                    </button>
                  );
                })}
              </div>
            )}
            <div style={{ display: "grid", gap: 8 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                <span style={{ fontSize: 10.5, fontWeight: 700, color: "#8597ab" }}>From</span>
                <input
                  type="date"
                  value={value.from}
                  max={value.to || undefined}
                  onChange={(e) => onChange({ from: e.target.value, to: value.to })}
                  style={dateInputStyle}
                />
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                <span style={{ fontSize: 10.5, fontWeight: 700, color: "#8597ab" }}>To</span>
                <input
                  type="date"
                  value={value.to}
                  min={value.from || undefined}
                  onChange={(e) => onChange({ from: value.from, to: e.target.value })}
                  style={dateInputStyle}
                />
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

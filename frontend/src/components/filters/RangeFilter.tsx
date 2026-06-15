import { useEffect, useRef, useState, type CSSProperties } from "react";
import { ChevronDown } from "lucide-react";

/**
 * Numeric min/max range filter rendered as a popover button, styled to match
 * MultiSelectFilter. Either bound is optional, so the rep can express
 * "2 or more", "up to 5", or a closed "2–5" range. `null` means "unbounded".
 */
export default function RangeFilter({
  label,
  min,
  max,
  onChange,
  allLabel,
  unit,
  minWidth,
  presets,
  hideLabel,
}: {
  label: string;
  min: number | null;
  max: number | null;
  onChange: (min: number | null, max: number | null) => void;
  allLabel: string;
  unit?: string;
  minWidth?: number;
  presets?: { label: string; min: number | null; max: number | null }[];
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

  const active = min != null || max != null;
  const unitSuffix = unit ? ` ${unit}` : "";
  const displayLabel = !active
    ? allLabel
    : min != null && max != null
      ? `${min}–${max}${unitSuffix}`
      : min != null
        ? `≥ ${min}${unitSuffix}`
        : `≤ ${max}${unitSuffix}`;

  const buttonStyle: CSSProperties = {
    width: "100%",
    height: 42,
    borderRadius: 12,
    border: active ? "1.5px solid #cfe89a" : "1px solid #d9e1ec",
    background: active ? "#f3fbe3" : "#fff",
    padding: "0 28px 0 12px",
    fontSize: 13,
    color: "#1d2b3c",
    cursor: "pointer",
    outline: "none",
    textAlign: "left",
    position: "relative",
    minWidth: minWidth ?? 150,
  };

  const numberInputStyle: CSSProperties = {
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

  const parse = (raw: string): number | null => {
    if (raw.trim() === "") return null;
    const n = Number(raw);
    return Number.isFinite(n) && n >= 0 ? Math.floor(n) : null;
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {!hideLabel && <label style={{ fontSize: 10, fontWeight: 700, color: "#7f8fa5", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</label>}
      <div ref={ref} style={{ position: "relative" }}>
        <button type="button" onClick={() => setOpen((current) => !current)} style={buttonStyle}>
          {displayLabel}
          <ChevronDown size={13} style={{ position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)", pointerEvents: "none", color: "#7f8fa5" }} />
        </button>
        {open && (
          <div className="beacon-pop" style={{ position: "absolute", top: "calc(100% + 6px)", left: 0, zIndex: 20, minWidth: 230, borderRadius: 14, border: "1px solid #dbe6f2", background: "#fff", boxShadow: "0 18px 36px rgba(15,23,42,0.14)", padding: 12, display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
              <span style={{ fontSize: 11, fontWeight: 800, color: "#6f8095", textTransform: "uppercase", letterSpacing: "0.08em" }}>{label}</span>
              {active && (
                <button type="button" onClick={() => onChange(null, null)} style={{ border: "none", background: "transparent", color: "#9ace3d", fontSize: 11, fontWeight: 800, cursor: "pointer" }}>
                  Clear
                </button>
              )}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input
                type="number"
                min={0}
                inputMode="numeric"
                placeholder="Min"
                value={min ?? ""}
                onChange={(e) => onChange(parse(e.target.value), max)}
                style={numberInputStyle}
              />
              <span style={{ color: "#9aa8b7", fontSize: 13, fontWeight: 700 }}>–</span>
              <input
                type="number"
                min={0}
                inputMode="numeric"
                placeholder="Max"
                value={max ?? ""}
                onChange={(e) => onChange(min, parse(e.target.value))}
                style={numberInputStyle}
              />
            </div>
            {presets && presets.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {presets.map((preset) => {
                  const isOn = preset.min === min && preset.max === max;
                  return (
                    <button
                      key={preset.label}
                      type="button"
                      onClick={() => onChange(preset.min, preset.max)}
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
          </div>
        )}
      </div>
    </div>
  );
}

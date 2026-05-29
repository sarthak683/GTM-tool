import { useEffect, useRef, useState, type CSSProperties } from "react";
import { ChevronDown, Search } from "lucide-react";

type Option = { value: string; label: string };

export default function MultiSelectFilter({
  values,
  onChange,
  options,
  label,
  allLabel,
  minWidth,
}: {
  values: string[];
  onChange: (value: string[]) => void;
  options: Option[];
  label: string;
  allLabel: string;
  minWidth?: number;
}) {
  const [open, setOpen] = useState(false);
  const [filterText, setFilterText] = useState("");
  const ref = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const handleClick = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) {
        setOpen(false);
        setFilterText("");
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 40);
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
      setFilterText("");
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open]);

  const toggle = (value: string) => {
    onChange(values.includes(value) ? values.filter((item) => item !== value) : [...values, value]);
  };

  const visibleOptions = filterText
    ? options.filter((option) => option.label.toLowerCase().includes(filterText.toLowerCase()))
    : options;

  const buttonStyle: CSSProperties = {
    width: "100%",
    height: 42,
    borderRadius: 12,
    border: values.length ? "1.5px solid #ffc9b4" : "1px solid #d9e1ec",
    background: values.length ? "#fff3ec" : "#fff",
    padding: "0 28px 0 12px",
    fontSize: 13,
    color: "#1d2b3c",
    cursor: "pointer",
    outline: "none",
    textAlign: "left",
    position: "relative",
    minWidth: minWidth ?? 150,
  };

  const displayLabel =
    values.length === 0
      ? allLabel
      : values.length === 1
        ? options.find((option) => option.value === values[0])?.label ?? allLabel
        : `${values.length} selected`;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <label style={{ fontSize: 10, fontWeight: 700, color: "#7f8fa5", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</label>
      <div ref={ref} style={{ position: "relative" }}>
        <button type="button" onClick={() => setOpen((current) => !current)} style={buttonStyle}>
          {displayLabel}
          {values.length > 1 && (
            <span
              style={{
                position: "absolute",
                right: 28,
                top: "50%",
                transform: "translateY(-50%)",
                minWidth: 18,
                height: 18,
                padding: "0 6px",
                borderRadius: 999,
                background: "#ff6b35",
                color: "#fff",
                fontSize: 10,
                fontWeight: 800,
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              {values.length}
            </span>
          )}
          <ChevronDown size={13} style={{ position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)", pointerEvents: "none", color: "#7f8fa5" }} />
        </button>
        {open && (
          <div style={{ position: "absolute", top: "calc(100% + 6px)", left: 0, right: 0, zIndex: 20, borderRadius: 14, border: "1px solid #dbe6f2", background: "#fff", boxShadow: "0 18px 36px rgba(15,23,42,0.14)", padding: 8, display: "flex", flexDirection: "column", gap: 6, maxHeight: 300 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "2px 4px 0" }}>
              <span style={{ fontSize: 11, fontWeight: 800, color: "#6f8095", textTransform: "uppercase", letterSpacing: "0.08em" }}>{label}</span>
              {values.length > 0 && (
                <button
                  type="button"
                  onClick={() => onChange([])}
                  style={{ border: "none", background: "transparent", color: "#ff6b35", fontSize: 11, fontWeight: 800, cursor: "pointer" }}
                >
                  Clear
                </button>
              )}
            </div>
            <div style={{ position: "relative", flexShrink: 0 }}>
              <Search size={12} style={{ position: "absolute", left: 9, top: "50%", transform: "translateY(-50%)", color: "#94a3b8", pointerEvents: "none" }} />
              <input
                ref={inputRef}
                type="text"
                placeholder={`Search ${label.toLowerCase()}...`}
                value={filterText}
                onChange={(e) => setFilterText(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                style={{ width: "100%", height: 32, borderRadius: 8, border: "1px solid #e2eaf2", background: "#f8fafc", paddingLeft: 28, paddingRight: 8, fontSize: 12, outline: "none", boxSizing: "border-box" }}
              />
            </div>
            <div style={{ overflowY: "auto", maxHeight: 220, display: "flex", flexDirection: "column", gap: 2 }}>
              {!filterText && (
                <button
                  type="button"
                  onClick={() => onChange([])}
                  style={{ border: "none", background: values.length === 0 ? "#fff3ec" : "transparent", color: values.length === 0 ? "#b85024" : "#4d6178", borderRadius: 8, padding: "8px 9px", textAlign: "left", fontSize: 12, fontWeight: 700, cursor: "pointer", flexShrink: 0 }}
                >
                  {allLabel}
                </button>
              )}
              {visibleOptions.length === 0 && (
                <div style={{ padding: "8px 10px", fontSize: 11, color: "#94a3b8" }}>No matches</div>
              )}
              {visibleOptions.map((option) => (
                <label key={option.value} style={{ display: "flex", alignItems: "center", gap: 8, borderRadius: 8, padding: "8px 9px", background: values.includes(option.value) ? "#fff7f2" : "transparent", color: "#2d4258", fontSize: 12, cursor: "pointer", flexShrink: 0 }}>
                  <input type="checkbox" checked={values.includes(option.value)} onChange={() => toggle(option.value)} />
                  <span>{option.label}</span>
                </label>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

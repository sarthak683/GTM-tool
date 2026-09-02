import { Columns3, List } from "lucide-react";

import type { ViewMode } from "../lib/viewMode";

export default function ViewModeToggle({
  value,
  onChange,
}: {
  value: ViewMode;
  onChange: (value: ViewMode) => void;
}) {
  return (
    <div
      role="group"
      aria-label="View mode"
      style={{ display: "inline-flex", gap: 3, padding: 3, border: "1px solid #dbe6f2", borderRadius: 10, background: "#f7faff" }}
    >
      {([
        { value: "board" as const, label: "Board", Icon: Columns3 },
        { value: "table" as const, label: "Table", Icon: List },
      ]).map(({ value: option, label, Icon }) => {
        const active = value === option;
        return (
          <button
            key={option}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option)}
            style={{ height: 30, padding: "0 9px", border: active ? "1px solid #bdd2ea" : "1px solid transparent", borderRadius: 7, background: active ? "#fff" : "transparent", color: active ? "#175089" : "#6b7f93", display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11.5, fontWeight: 800, cursor: "pointer", boxShadow: active ? "0 1px 3px rgba(15, 35, 75, 0.08)" : "none" }}
          >
            <Icon size={13} />
            {label}
          </button>
        );
      })}
    </div>
  );
}

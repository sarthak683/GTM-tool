import { CalendarDays, X } from "lucide-react";

// Event tags as small chips. In lists (max=2) the overflow collapses to "+N"
// with the full list on hover; on detail pages (editable) every tag shows
// with an "x" to remove it.
export default function EventChips({
  events,
  max = 2,
  highlight = [],
  onRemove,
  size = "sm",
  tone = "violet",
}: {
  events?: string[] | null;
  max?: number;
  /** Event names currently selected in a filter — rendered in the accent colour. */
  highlight?: string[];
  onRemove?: (event: string) => void;
  size?: "sm" | "md";
  /** "ink" = solid dark chip, for crowded rows where pastel colours are already taken. */
  tone?: "violet" | "ink";
}) {
  const list = events ?? [];
  if (list.length === 0) return null;
  const shown = onRemove ? list : list.slice(0, max);
  const hidden = list.length - shown.length;
  const marked = new Set(highlight.map((h) => h.toLowerCase()));
  const pad = size === "md" ? "3px 9px" : "2px 7px";
  const font = size === "md" ? 12 : 10;
  return (
    <>
      {shown.map((event) => {
        const active = marked.has(event.toLowerCase());
        return (
          <span
            key={event}
            title={event}
            style={{
              display: "inline-flex", alignItems: "center", gap: 4, maxWidth: 190,
              fontSize: font, fontWeight: 700, padding: pad, borderRadius: 999, lineHeight: 1.4, whiteSpace: "nowrap",
              ...(tone === "ink"
                ? { background: "#1e293b", color: "#f8fafc", border: "1px solid #0f172a" }
                : {
                    background: active ? "#ede9fe" : "#f5f3ff", color: active ? "#5b21b6" : "#6d28d9",
                    border: `1px solid ${active ? "#c4b5fd" : "#ddd6fe"}`,
                  }),
            }}
          >
            <CalendarDays size={size === "md" ? 12 : 9} style={{ flexShrink: 0 }} />
            <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{event}</span>
            {onRemove && (
              <button
                type="button"
                aria-label={`Remove ${event}`}
                onClick={(e) => { e.stopPropagation(); onRemove(event); }}
                style={{ border: "none", background: "transparent", color: "inherit", cursor: "pointer", padding: 0, display: "inline-flex" }}
              >
                <X size={size === "md" ? 12 : 10} />
              </button>
            )}
          </span>
        );
      })}
      {hidden > 0 && (
        <span
          title={list.slice(shown.length).join("\n")}
          style={{ fontSize: font, fontWeight: 800, padding: pad, borderRadius: 999, background: "#f1f5f9", color: "#64748b", border: "1px solid #e2e8f0", lineHeight: 1.4 }}
        >
          +{hidden}
        </span>
      )}
    </>
  );
}

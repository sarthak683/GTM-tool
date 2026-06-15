import type { CSSProperties } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

/**
 * Numbered pager with first/last anchors and ellipsis gaps — the standard
 * "‹ 1 … 5 6 [7] 8 9 … 20 ›" pattern. Shows a "X–Y of Z" range on the left.
 * Designed to drop in at both the top and bottom of a list.
 */
function pageItems(current: number, total: number): (number | "…")[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const left = Math.max(2, current - 1);
  const right = Math.min(total - 1, current + 1);
  const items: (number | "…")[] = [1];
  if (left > 2) items.push("…");
  for (let i = left; i <= right; i++) items.push(i);
  if (right < total - 1) items.push("…");
  items.push(total);
  return items;
}

const baseBtn: CSSProperties = {
  minWidth: 32,
  height: 32,
  padding: "0 8px",
  borderRadius: 9,
  border: "1px solid #dce8f4",
  background: "#fff",
  color: "#41526a",
  fontSize: 12.5,
  fontWeight: 700,
  cursor: "pointer",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  fontVariantNumeric: "tabular-nums",
  transition: "background 0.12s ease, border-color 0.12s ease, color 0.12s ease",
};

export function Pagination({
  page,
  totalPages,
  total,
  pageSize,
  onChange,
}: {
  page: number;
  totalPages: number;
  total: number;
  pageSize: number;
  onChange: (page: number) => void;
}) {
  const pages = Math.max(1, totalPages);
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  const go = (p: number) => {
    const next = Math.min(pages, Math.max(1, p));
    if (next !== page) onChange(next);
  };

  const navBtn = (disabled: boolean): CSSProperties => ({
    ...baseBtn,
    background: disabled ? "#f7f9fc" : "#fff",
    color: disabled ? "#aebccd" : "#41526a",
    cursor: disabled ? "not-allowed" : "pointer",
  });

  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
      <span style={{ color: "#71839a", fontSize: 12, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
        {total === 0 ? "No prospects" : <>Showing <strong style={{ color: "#34495f" }}>{from}–{to}</strong> of <strong style={{ color: "#34495f" }}>{total}</strong></>}
      </span>

      {pages > 1 && (
        <div style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
          <button type="button" aria-label="Previous page" onClick={() => go(page - 1)} disabled={page <= 1} style={navBtn(page <= 1)}>
            <ChevronLeft size={15} />
          </button>

          {pageItems(page, pages).map((it, i) =>
            it === "…" ? (
              <span key={`gap-${i}`} style={{ minWidth: 22, textAlign: "center", color: "#aebccd", fontSize: 13, fontWeight: 700, userSelect: "none" }}>…</span>
            ) : (
              <button
                key={it}
                type="button"
                aria-label={`Page ${it}`}
                aria-current={it === page ? "page" : undefined}
                onClick={() => go(it)}
                style={
                  it === page
                    ? { ...baseBtn, background: "linear-gradient(135deg, #6fae27 0%, #9ace3d 100%)", border: "1px solid #6fae27", color: "#fff", boxShadow: "0 3px 10px rgba(154,206,61,0.4)" }
                    : baseBtn
                }
              >
                {it}
              </button>
            ),
          )}

          <button type="button" aria-label="Next page" onClick={() => go(page + 1)} disabled={page >= pages} style={navBtn(page >= pages)}>
            <ChevronRight size={15} />
          </button>
        </div>
      )}
    </div>
  );
}

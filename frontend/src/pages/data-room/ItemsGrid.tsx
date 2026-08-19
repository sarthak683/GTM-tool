import { useState } from "react";
import { AlertTriangle, FileText, LoaderCircle, PackageCheck, Presentation, Trash2, Video } from "lucide-react";
import type { DataRoomCategory, DataRoomItem } from "../../types";

const ACCENT = "#4d7c0f";
const BORDER = "#e8eef5";
const TEXT = "#0f2744";
const MUTED = "#6c8097";

const TYPE_META: Record<DataRoomCategory, { label: string; icon: typeof FileText }> = {
  documentation: { label: "Google Docs", icon: FileText },
  decks: { label: "Google Slides / PDF", icon: Presentation },
  videos: { label: "Demo video", icon: Video },
  demo_recordings: { label: "Call recording", icon: Video },
  post_poc_collaterals: { label: "Post-POC collateral", icon: PackageCheck },
};

/**
 * Card grid for a Data Room category. Handles loading / error / empty states.
 * Cards show the item's title + a small type icon; clicking opens the preview
 * modal via onOpen. A hover-visible trash button deletes the item after a
 * confirm — the parent owns the API call, so failures surface as `deleteError`
 * and keep the card in place.
 */
export default function ItemsGrid({
  category,
  items,
  loading,
  error,
  onOpen,
  onRetry,
  onDelete,
  deletingId,
  deleteError,
}: {
  category: DataRoomCategory;
  items: DataRoomItem[];
  loading: boolean;
  error: string | null;
  onOpen: (item: DataRoomItem) => void;
  onRetry: () => void;
  onDelete: (item: DataRoomItem) => void;
  deletingId: string | null;
  deleteError: string | null;
}) {
  const meta = TYPE_META[category];
  const Icon = meta.icon;
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  if (loading && items.length === 0) {
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 10, minHeight: "40vh", color: MUTED }}>
        <LoaderCircle size={22} className="spin" />
        <div style={{ fontSize: 13, fontWeight: 600 }}>Loading files…</div>
      </div>
    );
  }

  if (error && items.length === 0) {
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 12, minHeight: "40vh", textAlign: "center", padding: 24 }}>
        <AlertTriangle size={22} color="#b42318" />
        <div style={{ fontSize: 13, fontWeight: 700, color: TEXT }}>Couldn't load files</div>
        <div style={{ fontSize: 12.5, color: MUTED, maxWidth: 420, lineHeight: 1.5 }}>{error}</div>
        <button onClick={onRetry} className="crm-button soft" style={{ minHeight: 36 }}>
          Retry
        </button>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 8, minHeight: "40vh", textAlign: "center", padding: 24 }}>
        <Icon size={26} color={MUTED} />
        <div style={{ fontSize: 13.5, fontWeight: 700, color: TEXT }}>No files yet — add one from Google Drive</div>
        <div style={{ fontSize: 12.5, color: MUTED, maxWidth: 420, lineHeight: 1.5 }}>
          Paste a Google Docs, Slides or Drive preview link into the {"+ Add File"} form and it will show up here.
        </div>
      </div>
    );
  }

  return (
    <div>
      {deleteError ? (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "10px 14px",
            borderRadius: 10,
            background: "#fef2f2",
            border: "1px solid #fecaca",
            color: "#b42318",
            fontSize: 12.5,
            fontWeight: 600,
            marginBottom: 14,
          }}
        >
          <AlertTriangle size={14} />
          {deleteError}
        </div>
      ) : null}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 14 }}>
        {items.map((item) => {
          const isDeleting = deletingId === item.id;
          return (
            <div
              key={item.id}
              role="button"
              tabIndex={0}
              onClick={() => onOpen(item)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onOpen(item);
                }
              }}
              className="crm-panel"
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 10,
                padding: 14,
                textAlign: "left",
                cursor: "pointer",
                border: `1px solid ${BORDER}`,
                borderRadius: 12,
                background: "#fff",
                transition: "border-color 0.14s ease, transform 0.14s ease",
                minHeight: 108,
              }}
              onMouseEnter={(e) => {
                setHoveredId(item.id);
                e.currentTarget.style.borderColor = "#cbd8e6";
                e.currentTarget.style.transform = "translateY(-1px)";
              }}
              onMouseLeave={(e) => {
                setHoveredId(null);
                e.currentTarget.style.borderColor = BORDER;
                e.currentTarget.style.transform = "translateY(0)";
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                    width: 34,
                    height: 34,
                    borderRadius: 9,
                    background: "#f3fbe3",
                    color: ACCENT,
                    flexShrink: 0,
                  }}
                >
                  <Icon size={17} />
                </span>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: 0.4, textTransform: "uppercase", color: MUTED }}>
                    {meta.label}
                  </span>
                  <button
                    type="button"
                    aria-label={`Delete ${item.title}`}
                    title="Delete"
                    disabled={isDeleting}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (!window.confirm(`Delete "${item.title}" from this Data Room? This cannot be undone.`)) return;
                      onDelete(item);
                    }}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      justifyContent: "center",
                      width: 26,
                      height: 26,
                      borderRadius: 7,
                      border: "none",
                      background: "transparent",
                      color: MUTED,
                      cursor: "pointer",
                      opacity: hoveredId === item.id || isDeleting ? 1 : 0,
                      transition: "opacity 0.14s ease, color 0.14s ease, background 0.14s ease",
                      flexShrink: 0,
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.color = "#dc2626";
                      e.currentTarget.style.background = "#fef2f2";
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.color = MUTED;
                      e.currentTarget.style.background = "transparent";
                    }}
                  >
                    {isDeleting ? <LoaderCircle size={14} className="spin" /> : <Trash2 size={14} />}
                  </button>
                </div>
              </div>
              <div style={{ fontSize: 13.5, fontWeight: 700, color: TEXT, lineHeight: 1.4, wordBreak: "break-word" }}>
                {item.title}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

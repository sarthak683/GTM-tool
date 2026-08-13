import { X } from "lucide-react";
import type { DataRoomItem } from "../../types";

const ACCENT = "#4d7c0f";
const BORDER = "#e8eef5";
const TEXT = "#0f2744";
const MUTED = "#6c8097";

/**
 * Full-width preview of a Data Room item. Google Docs / Slides / Drive
 * links render best when the source URL is their `/preview` or embed URL.
 */
export default function PreviewModal({
  item,
  onClose,
}: {
  item: DataRoomItem;
  onClose: () => void;
}) {
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1200,
        background: "rgba(15, 39, 68, 0.5)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="crm-panel"
        style={{
          width: "min(1000px, 100%)",
          maxHeight: "88vh",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          borderRadius: 14,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
            padding: "12px 16px",
            borderBottom: `1px solid ${BORDER}`,
          }}
        >
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 14, fontWeight: 800, color: TEXT }}>{item.title}</div>
            <div style={{ fontSize: 12, color: MUTED }}>{item.embed_url}</div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close preview"
            className="crm-button soft"
            style={{ minHeight: 34, padding: "0 10px", flexShrink: 0 }}
          >
            <X size={15} color={TEXT} />
          </button>
        </div>
        <div style={{ flex: 1, minHeight: 0, background: "#f8fbff" }}>
          <iframe
            title={item.title}
            src={item.embed_url}
            style={{ width: "100%", height: "100%", minHeight: "60vh", border: 0, display: "block", background: "#fff" }}
          />
        </div>
        <a
          href={item.embed_url}
          target="_blank"
          rel="noopener noreferrer"
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            padding: "10px 16px",
            borderTop: `1px solid ${BORDER}`,
            fontSize: 12.5,
            fontWeight: 700,
            color: ACCENT,
            textDecoration: "none",
          }}
        >
          Open in new tab
        </a>
      </div>
    </div>
  );
}

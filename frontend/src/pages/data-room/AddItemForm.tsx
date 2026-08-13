import { useState, type CSSProperties } from "react";
import { LoaderCircle, X } from "lucide-react";
import { dataRoomApi } from "../../lib/api";
import type { DataRoomCategory, DataRoomItem } from "../../types";

const ACCENT = "#4d7c0f";
const BORDER = "#e8eef5";
const TEXT = "#0f2744";
const MUTED = "#6c8097";

const inputBase: CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  border: "1px solid #d6e0eb",
  borderRadius: 8,
  padding: "7px 10px",
  fontSize: 13,
  color: TEXT,
  background: "#fff",
  outline: "none",
  fontFamily: "inherit",
};

function Field({
  label,
  value,
  onChange,
  placeholder,
  hint,
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  hint?: string;
}) {
  return (
    <label style={{ display: "grid", gap: 5 }}>
      <span style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: 0.5, color: "#8a9bb0", textTransform: "uppercase" }}>
        {label}
      </span>
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        style={inputBase}
      />
      {hint ? <span style={{ fontSize: 11, color: MUTED, lineHeight: 1.5 }}>{hint}</span> : null}
    </label>
  );
}

/**
 * Modal form to add a file to a Data Room category. POSTs to
 * /data-room/items via dataRoomApi.create, then calls onSaved with the new
 * item so the parent can refresh the grid.
 */
export default function AddItemForm({
  category,
  onClose,
  onSaved,
}: {
  category: DataRoomCategory;
  onClose: () => void;
  onSaved: (item: DataRoomItem) => void;
}) {
  const [title, setTitle] = useState("");
  const [embedUrl, setEmbedUrl] = useState("");
  const [thumbnailUrl, setThumbnailUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = title.trim().length > 0 && embedUrl.trim().length > 0 && !saving;

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSaving(true);
    setError(null);
    try {
      const item = await dataRoomApi.create({
        category,
        title: title.trim(),
        embed_url: embedUrl.trim(),
        thumbnail_url: thumbnailUrl.trim() ? thumbnailUrl.trim() : null,
      });
      onSaved(item);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add file.");
    } finally {
      setSaving(false);
    }
  };

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
        style={{ width: "min(460px, 100%)", borderRadius: 14, overflow: "hidden" }}
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
          <div style={{ fontSize: 14, fontWeight: 800, color: TEXT }}>Add file</div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="crm-button soft"
            style={{ minHeight: 32, padding: "0 9px" }}
          >
            <X size={14} color={TEXT} />
          </button>
        </div>

        <div style={{ padding: 16, display: "grid", gap: 14 }}>
          <Field
            label="Title"
            value={title}
            onChange={setTitle}
            placeholder="e.g. 2026 Sales Deck"
          />
          <Field
            label="Embed URL"
            value={embedUrl}
            onChange={setEmbedUrl}
            placeholder="https://docs.google.com/..."
            hint="Use the Google Docs / Slides / Drive share link (view-only), or a direct PDF."
          />
          <Field
            label="Thumbnail URL (optional)"
            value={thumbnailUrl}
            onChange={setThumbnailUrl}
            placeholder="https://..."
          />

          {error ? (
            <div style={{ fontSize: 12.5, color: "#b42318", background: "#fdeceb", border: "1px solid #f5cfcd", borderRadius: 8, padding: "8px 10px" }}>
              {error}
            </div>
          ) : null}

          <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
            <button onClick={onClose} className="crm-button soft" style={{ minHeight: 38 }}>
              Cancel
            </button>
            <button
              onClick={handleSubmit}
              disabled={!canSubmit}
              className="crm-button primary"
              style={{ minHeight: 38, display: "inline-flex", alignItems: "center", gap: 8, color: "#fff", background: ACCENT }}
            >
              {saving ? <LoaderCircle size={15} className="spin" /> : null}
              {saving ? "Saving…" : "Add file"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

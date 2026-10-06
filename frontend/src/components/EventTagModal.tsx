import { useEffect, useState } from "react";
import { CalendarDays, Loader2, X } from "lucide-react";
import { eventsApi } from "../lib/api";

// Pick an existing event or type a new one, then apply it to the current
// selection. Shared by the Prospecting and Account Sourcing bulk actions and
// the detail-page "+ Event" chip.
export default function EventTagModal({
  title = "Add to event",
  subtitle,
  open,
  onClose,
  onSubmit,
}: {
  title?: string;
  subtitle?: string;
  open: boolean;
  onClose: () => void;
  onSubmit: (event: string) => Promise<void>;
}) {
  const [event, setEvent] = useState("");
  const [options, setOptions] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setEvent("");
    setError("");
    eventsApi.list().then(setOptions).catch(() => setOptions([]));
  }, [open]);

  if (!open) return null;

  const submit = async () => {
    if (!event.trim()) return;
    setBusy(true);
    setError("");
    try {
      await onSubmit(event.trim());
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to tag");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 9000, display: "flex", alignItems: "center", justifyContent: "center" }} onClick={() => !busy && onClose()}>
      <div style={{ position: "absolute", inset: 0, background: "rgba(10,20,40,0.45)" }} />
      <div onClick={(e) => e.stopPropagation()} style={{ position: "relative", width: 420, maxWidth: "95vw", background: "#fff", borderRadius: 18, boxShadow: "0 24px 60px rgba(14,38,66,0.22)", padding: 22, display: "grid", gap: 14 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ width: 34, height: 34, borderRadius: 10, background: "linear-gradient(135deg,#7c3aed,#6d28d9)", display: "grid", placeItems: "center" }}>
              <CalendarDays size={16} color="#fff" />
            </div>
            <div>
              <div style={{ fontSize: 14, fontWeight: 700, color: "#0f2744" }}>{title}</div>
              {subtitle && <div style={{ fontSize: 12, color: "#7a96b0" }}>{subtitle}</div>}
            </div>
          </div>
          <button onClick={onClose} style={{ border: 0, background: "transparent", color: "#7a96b0", cursor: "pointer" }}><X size={18} /></button>
        </div>
        <div>
          <label style={{ fontSize: 12, fontWeight: 700, color: "#2c4a63", display: "block", marginBottom: 6 }}>Event *</label>
          <input
            list="event-tag-options"
            value={event}
            onChange={(e) => setEvent(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") void submit(); }}
            placeholder="Pick an existing event or type a new one"
            autoFocus
            style={{ width: "100%", boxSizing: "border-box", height: 40, border: "1px solid #c8d9e8", borderRadius: 10, padding: "0 12px", fontSize: 13 }}
          />
          <datalist id="event-tag-options">
            {options.map((o) => <option key={o} value={o} />)}
          </datalist>
          <p style={{ margin: "8px 0 0", fontSize: 12, color: "#7a96b0", lineHeight: 1.5 }}>
            Adds the event to what's already there — existing tags are never removed.
          </p>
        </div>
        {error && <div style={{ color: "#ef4444", fontSize: 12 }}>{error}</div>}
        <div style={{ display: "flex", gap: 10 }}>
          <button onClick={onClose} disabled={busy} style={{ flex: 1, padding: "10px 0", borderRadius: 12, border: "1px solid #dce8f4", background: "#f7faff", color: "#4a6580", fontWeight: 700, cursor: "pointer" }}>Cancel</button>
          <button onClick={() => void submit()} disabled={busy || !event.trim()} style={{ flex: 2, padding: "10px 0", borderRadius: 12, border: "none", background: event.trim() ? "linear-gradient(135deg,#7c3aed,#6d28d9)" : "#c7d2dd", color: "#fff", fontWeight: 700, cursor: event.trim() && !busy ? "pointer" : "default", display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}>
            {busy && <Loader2 size={15} className="animate-spin" />}
            {busy ? "Tagging…" : "Add event"}
          </button>
        </div>
      </div>
    </div>
  );
}

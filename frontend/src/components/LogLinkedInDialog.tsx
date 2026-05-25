import { useState } from "react";
import { Linkedin, Loader2, X } from "lucide-react";
import { activitiesApi } from "../lib/api";

export type LinkedInAction =
  | "connection_request_sent"
  | "connection_accepted"
  | "message_sent"
  | "inmail_sent"
  | "profile_viewed"
  | "post_engaged";

const ACTION_OPTIONS: { value: LinkedInAction; label: string }[] = [
  { value: "connection_request_sent", label: "Connection request sent" },
  { value: "connection_accepted", label: "Connection accepted" },
  { value: "message_sent", label: "Message sent" },
  { value: "inmail_sent", label: "InMail sent" },
  { value: "profile_viewed", label: "Profile viewed" },
  { value: "post_engaged", label: "Engaged with a post" },
];

interface Props {
  contactId: string;
  dealId?: string;
  open: boolean;
  onClose: () => void;
  onLogged?: () => void;
}

export default function LogLinkedInDialog({ contactId, dealId, open, onClose, onLogged }: Props) {
  const [action, setAction] = useState<LinkedInAction>("message_sent");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const labelText = ACTION_OPTIONS.find((o) => o.value === action)?.label ?? action;
      await activitiesApi.create({
        contact_id: contactId,
        deal_id: dealId,
        type: "linkedin",
        medium: "linkedin",
        source: "manual",
        content: notes ? `${labelText} — ${notes}` : labelText,
        // event_metadata carries the structured subtype so analytics/timeline can filter on it
        event_metadata: { linkedin_action: action, logged_via: "manual" },
      } as never);
      onLogged?.();
      setNotes("");
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to log activity");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      data-mobile-modal
      style={{
        position: "fixed", inset: 0, zIndex: 9000,
        background: "rgba(15, 39, 68, 0.4)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
      onClick={onClose}
    >
      <div
        data-mobile-modal-panel
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 440, background: "#fff", borderRadius: 16,
          boxShadow: "0 24px 60px rgba(15, 39, 68, 0.25)",
          maxWidth: "92vw",
        }}
      >
        <div data-mobile-modal-body style={{ padding: "22px 24px 0" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <Linkedin size={18} color="#0a66c2" />
              <h3 style={{ fontSize: 16, fontWeight: 700, color: "#0f2744", margin: 0 }}>Log LinkedIn touch</h3>
            </div>
            <button
              onClick={onClose}
              style={{ border: "none", background: "transparent", cursor: "pointer", color: "#64748b" }}
              aria-label="Close"
            >
              <X size={18} />
            </button>
          </div>

        <label style={{ display: "block", fontSize: 12, fontWeight: 600, color: "#2c4a63", marginBottom: 6 }}>
          What did you do?
        </label>
        <select
          value={action}
          onChange={(e) => setAction(e.target.value as LinkedInAction)}
          style={{
            width: "100%", border: "1px solid #c8d9e8", borderRadius: 10,
            padding: "9px 12px", fontSize: 13, color: "#0f2744", background: "#fff",
            outline: "none", marginBottom: 14,
          }}
        >
          {ACTION_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>

        <label style={{ display: "block", fontSize: 12, fontWeight: 600, color: "#2c4a63", marginBottom: 6 }}>
          Notes <span style={{ color: "#94a3b8", fontWeight: 500 }}>(optional)</span>
        </label>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="What did they say? Any signal worth capturing…"
          rows={4}
          style={{
            width: "100%", border: "1px solid #c8d9e8", borderRadius: 10,
            padding: "9px 12px", fontSize: 13, color: "#0f2744", background: "#fff",
            outline: "none", resize: "vertical", fontFamily: "inherit", marginBottom: 16,
          }}
        />

        {error && (
          <div style={{ color: "#ef4444", fontSize: 12, marginBottom: 10 }}>{error}</div>
        )}
        </div>

        <div data-mobile-modal-footer style={{ display: "flex", gap: 10, justifyContent: "flex-end", padding: "16px 24px" }}>
          <button
            onClick={onClose}
            style={{
              padding: "11px 16px", minHeight: 44, borderRadius: 10, border: "1px solid #c8d9e8",
              background: "#fff", color: "#0f2744", fontSize: 13, fontWeight: 600, cursor: "pointer",
            }}
          >
            Cancel
          </button>
          <button
            onClick={() => void handleSave()}
            disabled={saving}
            style={{
              padding: "11px 16px", minHeight: 44, borderRadius: 10, border: "none",
              background: "linear-gradient(135deg, #0a66c2, #0f2744)",
              color: "#fff", fontSize: 13, fontWeight: 700, cursor: saving ? "not-allowed" : "pointer",
              display: "inline-flex", alignItems: "center", gap: 8, opacity: saving ? 0.7 : 1,
            }}
          >
            {saving && <Loader2 size={14} className="animate-spin" />}
            {saving ? "Saving…" : "Log touch"}
          </button>
        </div>
      </div>
    </div>
  );
}

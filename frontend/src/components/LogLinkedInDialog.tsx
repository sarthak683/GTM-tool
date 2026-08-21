import { useEffect, useState } from "react";
import { CheckCircle2, ExternalLink, Link2, Loader2, X } from "lucide-react";
import { activitiesApi, contactsApi } from "../lib/api";
import { LINKEDIN_STATUS_OPTIONS, deriveSequenceStatusFromLinkedinStatus } from "../lib/prospectWorkflow";

// Use the same outcome taxonomy as the prospecting-table logger so logging from
// the detail pages updates `linkedin_status` and drives the same progress-bar
// lane (sent/inmail/accepted/follow_up/meeting_booked/meeting_rejected).
const ACTION_OPTIONS = LINKEDIN_STATUS_OPTIONS;

const ACTION_HINTS: Record<string, string> = {
  sent: "You sent a connection request.",
  inmail: "You sent a paid InMail message — no connection needed.",
  accepted: "They accepted your request — ready to message.",
  follow_up: "Conversation in flight — you followed up.",
  meeting_booked: "A meeting is on the calendar from this thread.",
  meeting_rejected: "They declined a meeting — treat as a hard no.",
};

interface Props {
  contactId: string;
  /** Optional contact name shown under the header (matches the list logger). */
  contactName?: string;
  /** Optional LinkedIn URL shown as an "Open LinkedIn profile" link. */
  linkedinUrl?: string | null;
  dealId?: string;
  /** Current sequence_status so a booked/rejected outcome advances the funnel. */
  sequenceStatus?: string | null;
  /** Preselected outcome (e.g. the contact's current linkedin_status). */
  initialStatus?: string | null;
  open: boolean;
  onClose: () => void;
  onLogged?: () => void;
}

export default function LogLinkedInDialog({ contactId, contactName, linkedinUrl, dealId, sequenceStatus, initialStatus, open, onClose, onLogged }: Props) {
  const [action, setAction] = useState<string>("sent");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset the form each time the dialog opens so a previously used contact's
  // selection never leaks into the next log.
  useEffect(() => {
    if (open) {
      setAction(initialStatus && initialStatus !== "none" ? initialStatus : "sent");
      setNotes("");
      setError(null);
    }
  }, [open, initialStatus]);

  if (!open) return null;

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const labelText = ACTION_OPTIONS.find((o) => o.value === action)?.label ?? action;
      // 1) Update the contact's LinkedIn outcome so the progress bar reflects it.
      const derivedSeq = deriveSequenceStatusFromLinkedinStatus(action, sequenceStatus);
      await contactsApi.update(contactId, {
        linkedin_status: action,
        linkedin_last_at: new Date().toISOString(),
        ...(derivedSeq && derivedSeq !== sequenceStatus ? { sequence_status: derivedSeq } : {}),
      } as never);
      // 2) Write the timeline activity (with rep attribution + structured subtype).
      await activitiesApi.create({
        contact_id: contactId,
        deal_id: dealId,
        type: "linkedin",
        medium: "linkedin",
        source: "manual",
        content: notes ? `${labelText} — ${notes}` : labelText,
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
      style={{ position: "fixed", inset: 0, zIndex: 210, display: "flex", alignItems: "center", justifyContent: "center" }}
      onClick={onClose}
    >
      <div style={{ position: "absolute", inset: 0, background: "rgba(10,20,40,0.45)" }} />
      <div
        style={{ position: "relative", width: 420, maxWidth: "95vw", background: "#fff", borderRadius: 20, boxShadow: "0 24px 60px rgba(14,38,66,0.22)", overflow: "hidden" }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div style={{ padding: "20px 22px 16px", borderBottom: "1px solid #e8eef5", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ width: 36, height: 36, borderRadius: 10, background: "linear-gradient(135deg,#0a66c2,#1e88e5)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <Link2 size={16} color="#fff" />
            </div>
            <div>
              <div style={{ fontSize: 14, fontWeight: 700, color: "#0f2744" }}>Log LinkedIn touch</div>
              <div style={{ fontSize: 12, color: "#7a96b0" }}>{contactName}</div>
            </div>
          </div>
          <button onClick={onClose} style={{ border: 0, background: "transparent", color: "#7a96b0", cursor: "pointer", padding: 4 }}>
            <X size={18} />
          </button>
        </div>

        {/* Form */}
        <div style={{ padding: "18px 22px 22px", display: "grid", gap: 14 }}>
          {linkedinUrl && (
            <a href={linkedinUrl} target="_blank" rel="noopener noreferrer"
              style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, color: "#0a66c2", fontWeight: 600, textDecoration: "none" }}>
              <ExternalLink size={13} /> Open LinkedIn profile
            </a>
          )}

          <div>
            <label style={{ fontSize: 12, fontWeight: 700, color: "#2c4a63", display: "block", marginBottom: 6 }}>What happened? *</label>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
              {ACTION_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setAction(opt.value)}
                  style={{
                    padding: "10px 0", borderRadius: 10, border: `2px solid ${action === opt.value ? "#0a66c2" : "#dce8f4"}`,
                    background: action === opt.value ? "#e8f2ff" : "#f7faff",
                    color: action === opt.value ? "#0a66c2" : "#4a6580",
                    fontSize: 13, fontWeight: 700, cursor: "pointer",
                  }}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            <div style={{ marginTop: 8, fontSize: 12, color: "#7a96b0" }}>
              {ACTION_HINTS[action]}
            </div>
          </div>

          <div>
            <label style={{ fontSize: 12, fontWeight: 700, color: "#2c4a63", display: "block", marginBottom: 6 }}>Notes (optional)</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="What did you say or observe? Any signals…"
              rows={3}
              style={{ width: "100%", boxSizing: "border-box", border: "1px solid #c8d9e8", borderRadius: 10, padding: "9px 12px", fontSize: 13, color: "#0f2744", background: "#fff", outline: "none", resize: "vertical", fontFamily: "inherit" }}
            />
          </div>

          {error && (
            <div style={{ color: "#ef4444", fontSize: 12 }}>{error}</div>
          )}

          <div style={{ display: "flex", gap: 10 }}>
            <button onClick={onClose} style={{ flex: 1, padding: "11px 0", borderRadius: 12, border: "1px solid #dce8f4", background: "#f7faff", color: "#4a6580", fontSize: 14, fontWeight: 700, cursor: "pointer" }}>
              Cancel
            </button>
            <button
              onClick={() => void handleSave()}
              disabled={saving}
              style={{ flex: 2, padding: "11px 0", borderRadius: 12, border: "none", background: "linear-gradient(135deg,#0a66c2,#1e88e5)", color: "#fff", fontSize: 14, fontWeight: 700, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: 8, opacity: saving ? 0.7 : 1 }}
            >
              {saving ? <Loader2 size={15} className="animate-spin" /> : <CheckCircle2 size={15} />}
              {saving ? "Saving…" : "Log touch"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

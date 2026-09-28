import { useEffect, useState } from "react";
import { CheckCircle2, Copy, Loader2, Mail, X } from "lucide-react";
import { activitiesApi, companiesApi, contactsApi } from "../lib/api";
import { EMAIL_LOG_OUTCOME_OPTIONS, deriveSequenceStatusFromEmailLog, getManualEmailLog } from "../pages/contacts/ProgressCell";
import type { Contact } from "../types";

// Same outcome taxonomy and save behavior as the Prospecting table's inline
// email logger (Contacts.tsx saveEmailLog) — this is a standalone version of
// that same flow so it can also be opened from an Outreach Sequence task in
// the Tasks tab, without duplicating that logic inline there.
const EMAIL_HINTS: Record<string, string> = {
  sent: "You sent an email.",
  replied: "They replied.",
  no_response: "No response yet.",
  meeting_booked: "A meeting is on the calendar from this thread.",
};

interface Props {
  contact: Contact;
  open: boolean;
  onClose: () => void;
  onLogged?: () => void;
  /** Set when opened from an Outreach Sequence cadence task — logging the
   * email (unchanged below) also advances that task's step. */
  cadenceTaskId?: string;
}

export default function LogEmailDialog({ contact, open, onClose, onLogged, cadenceTaskId }: Props) {
  const [status, setStatus] = useState<string>("sent");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [zippyAlias, setZippyAlias] = useState<string | null>(null);
  const [zippyCopied, setZippyCopied] = useState(false);

  useEffect(() => {
    if (open) {
      setStatus("sent");
      setNotes("");
      setError(null);
      setZippyAlias(null);
      setZippyCopied(false);
      if (contact.company_id) {
        companiesApi.get(contact.company_id)
          .then((company) => setZippyAlias(company.email_cc_alias ? `zippy+${company.email_cc_alias}@beacon.li` : null))
          .catch(() => setZippyAlias(null));
      }
    }
  }, [open, contact.id, contact.company_id]);

  if (!open) return null;

  const copyZippyAlias = () => {
    if (!zippyAlias) return;
    navigator.clipboard?.writeText(zippyAlias);
    setZippyCopied(true);
    window.setTimeout(() => setZippyCopied(false), 1500);
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const nowIso = new Date().toISOString();
      const derivedSeqStatus = deriveSequenceStatusFromEmailLog(status, contact.sequence_status);
      const prevLog = getManualEmailLog(contact);
      const nextEnrichment = {
        ...(contact.enrichment_data ?? {}),
        manual_email: { status, last_at: nowIso, count: (prevLog?.count ?? 0) + 1 },
      };
      await contactsApi.update(contact.id, {
        enrichment_data: nextEnrichment,
        ...(derivedSeqStatus && derivedSeqStatus !== contact.sequence_status ? { sequence_status: derivedSeqStatus } : {}),
        ...(cadenceTaskId ? { cadence_task_id: cadenceTaskId } : {}),
      } as never);

      const emailLabel = EMAIL_LOG_OUTCOME_OPTIONS.find((o) => o.value === status)?.label ?? status;
      const contactLabel = `${contact.first_name ?? ""} ${contact.last_name ?? ""}`.trim();
      const activityContent = notes ? `${emailLabel} — ${contactLabel}: ${notes}` : `${emailLabel} — ${contactLabel}`;
      try {
        await activitiesApi.create({
          type: "email",
          source: "manual",
          content: activityContent,
          contact_id: contact.id,
          event_metadata: { event_type: "manual_email_logged", email_status: status, logged_at: nowIso },
        } as never);
      } catch {
        // Non-fatal — the contact update above already saved.
      }

      onLogged?.();
      setNotes("");
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to log email");
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
        <div style={{ padding: "20px 22px 16px", borderBottom: "1px solid #e8eef5", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ width: 36, height: 36, borderRadius: 10, background: "linear-gradient(135deg,#1f7a4d,#2fae6f)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <Mail size={16} color="#fff" />
            </div>
            <div>
              <div style={{ fontSize: 14, fontWeight: 700, color: "#0f2744" }}>Log email</div>
              <div style={{ fontSize: 12, color: "#7a96b0" }}>{`${contact.first_name ?? ""} ${contact.last_name ?? ""}`.trim()}</div>
            </div>
          </div>
          <button onClick={onClose} style={{ border: 0, background: "transparent", color: "#7a96b0", cursor: "pointer", padding: 4 }}>
            <X size={18} />
          </button>
        </div>

        <div style={{ padding: "18px 22px 22px", display: "grid", gap: 14 }}>
          {contact.email && (
            <a href={`mailto:${contact.email}`} target="_blank" rel="noopener noreferrer"
              style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, color: "#1f7a4d", fontWeight: 600, textDecoration: "none" }}>
              <Mail size={13} /> Open compose
            </a>
          )}

          {contact.email && zippyAlias && (
            <span
              style={{ display: "inline-flex", alignItems: "center", gap: 8, height: 32, padding: "0 6px 0 10px", borderRadius: 9, border: "1px solid #dbe6f2", background: "#f8fbff", width: "fit-content" }}
              title="This account's Zippy ID — CC it on this email to attribute it."
            >
              <span style={{ fontWeight: 700, fontSize: 12.5, color: "#0f2744", whiteSpace: "nowrap" }}>{zippyAlias}</span>
              <button
                type="button"
                onClick={copyZippyAlias}
                style={{ height: 24, padding: "0 9px", borderRadius: 7, border: "1px solid #cfe89a", background: "#f3fbe3", color: "#4d7c0f", fontSize: 11.5, fontWeight: 700, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4 }}
              >
                <Copy size={11} /> {zippyCopied ? "Copied" : "Copy"}
              </button>
            </span>
          )}

          <div>
            <label style={{ fontSize: 12, fontWeight: 700, color: "#2c4a63", display: "block", marginBottom: 6 }}>What happened? *</label>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 8 }}>
              {EMAIL_LOG_OUTCOME_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setStatus(opt.value)}
                  style={{
                    padding: "10px 0", borderRadius: 10, border: `2px solid ${status === opt.value ? "#1f7a4d" : "#dce8f4"}`,
                    background: status === opt.value ? "#ecfdf3" : "#f7faff",
                    color: status === opt.value ? "#1f7a4d" : "#4a6580",
                    fontSize: 13, fontWeight: 700, cursor: "pointer",
                  }}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            <div style={{ marginTop: 8, fontSize: 12, color: "#7a96b0" }}>{EMAIL_HINTS[status]}</div>
          </div>

          <div>
            <label style={{ fontSize: 12, fontWeight: 700, color: "#2c4a63", display: "block", marginBottom: 6 }}>Notes (optional)</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="What did you say? Any signals…"
              rows={3}
              style={{ width: "100%", boxSizing: "border-box", border: "1px solid #c8d9e8", borderRadius: 10, padding: "9px 12px", fontSize: 13, color: "#0f2744", background: "#fff", outline: "none", resize: "vertical", fontFamily: "inherit" }}
            />
          </div>

          {error && <div style={{ color: "#ef4444", fontSize: 12 }}>{error}</div>}

          <div style={{ display: "flex", gap: 10 }}>
            <button onClick={onClose} style={{ flex: 1, padding: "11px 0", borderRadius: 12, border: "1px solid #dce8f4", background: "#f7faff", color: "#4a6580", fontSize: 14, fontWeight: 700, cursor: "pointer" }}>
              Cancel
            </button>
            <button
              onClick={() => void handleSave()}
              disabled={saving}
              style={{ flex: 2, padding: "11px 0", borderRadius: 12, border: "none", background: "linear-gradient(135deg,#1f7a4d,#2fae6f)", color: "#fff", fontSize: 14, fontWeight: 700, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: 8, opacity: saving ? 0.7 : 1 }}
            >
              {saving ? <Loader2 size={15} className="animate-spin" /> : <CheckCircle2 size={15} />}
              {saving ? "Saving…" : "Log email"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

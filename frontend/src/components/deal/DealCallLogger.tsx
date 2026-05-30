import { useEffect, useState } from "react";
import { Phone, Save, Loader2, CheckCircle2, ChevronDown, ChevronRight } from "lucide-react";
import { CallRecordingPanel, type AISuggestion } from "../../pages/contacts/CallRecordingPanel";
import { activitiesApi } from "../../lib/api";
import { CALL_DISPOSITION_OPTIONS, formatCallDisposition } from "../../lib/prospectWorkflow";
import type { Activity, Deal, DealContact } from "../../types";

/**
 * Log a call on a DEAL — the AE counterpart to the prospect call drawer.
 *
 * AEs make few calls, so the flow lives inline on the deal: open the deal on
 * mobile, pick who you're calling, record (the panel transcribes + suggests a
 * disposition), then save. On save we mirror the prospect behaviour:
 *   • write a `call` Activity on the deal (+ the contact, + recording link),
 *   • set the deal's Next Step (pre-filled from the disposition, editable),
 *   • set the Follow-up date (deal.next_step_due_at).
 * Deal STAGE is deliberately left to the AE — a spoken disposition shouldn't
 * silently move the pipeline.
 */

// Disposition → suggested Next Step. Pre-fills the editable field so the AE
// starts from a sensible default instead of a blank box.
const NEXT_STEP_SUGGESTION: Record<string, string> = {
  demo_scheduled_booked: "Prep and send the demo agenda",
  meeting_confirmed: "Confirm attendees and prep for the meeting",
  interested_follow_up_required: "Send recap + the materials they asked for",
  call_back_later_rescheduled: "Call back at the agreed time",
  gatekeeper_connected_to_admin: "Get routed to the economic buyer",
  connected_not_interested: "Capture objections; decide nurture vs. close-lost",
  redirected_other_icp: "Reach out to the redirected contact",
  no_answer_busy_signal: "Try another time / channel",
  invalid_number_wrong_number: "Find a valid phone number",
  contact_poor_fit: "Find a better-fit contact on the account",
  do_not_contact_dnc: "Respect DNC — stop outreach on this contact",
};

function toLocalDatetimeInput(iso?: string | null): string {
  if (!iso) return "";
  const utc = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(utc.getTime())) return "";
  return new Date(utc.getTime() - utc.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}
function fromLocalDatetimeInput(local: string): string | undefined {
  if (!local) return undefined;
  const d = new Date(local);
  if (Number.isNaN(d.getTime())) return undefined;
  return d.toISOString().slice(0, -1); // naive-UTC for the DB
}

function contactLabel(dc: DealContact): string {
  return `${dc.first_name ?? ""} ${dc.last_name ?? ""}`.trim() || dc.email || "Contact";
}

const inputStyle: React.CSSProperties = {
  width: "100%", padding: "9px 11px", borderRadius: 9,
  border: "1px solid #d5e0ec", background: "#fff",
  fontSize: 13, color: "#0f2744", fontFamily: "inherit", outline: "none",
};

export default function DealCallLogger({
  deal,
  dealContacts,
  onLogged,
  onPatchDeal,
}: {
  deal: Deal;
  dealContacts: DealContact[];
  onLogged: () => void;
  onPatchDeal: (data: Partial<Deal>) => Promise<void> | void;
}) {
  const [open, setOpen] = useState(false);
  const [contactId, setContactId] = useState("");
  const [disposition, setDisposition] = useState("");
  const [callStatus, setCallStatus] = useState("attempted");
  const [notes, setNotes] = useState("");
  const [nextStep, setNextStep] = useState(deal.next_step ?? "");
  const [followupAt, setFollowupAt] = useState(toLocalDatetimeInput(deal.next_step_due_at));
  const [recordingId, setRecordingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  // Default to the first linked contact so a one-contact deal needs no picking.
  useEffect(() => {
    if (!contactId && dealContacts.length > 0) setContactId(dealContacts[0].contact_id);
  }, [dealContacts, contactId]);

  const applyDisposition = (value: string) => {
    setSaved(false);
    setDisposition(value);
    const opt = CALL_DISPOSITION_OPTIONS.find((o) => o.value === value);
    if (opt?.suggestedCallStatus) setCallStatus(opt.suggestedCallStatus);
    // Only seed Next Step if the AE hasn't already written one.
    setNextStep((prev) => (prev.trim() ? prev : NEXT_STEP_SUGGESTION[value] ?? prev));
  };

  const handleSuggestion = (s: AISuggestion) => {
    if (s.disposition) applyDisposition(s.disposition);
    setNotes((existing) => (existing.trim() ? existing : s.summary));
  };

  const handleSave = async () => {
    if (!disposition) return;
    setSaving(true);
    try {
      const dc = contactId ? dealContacts.find((x) => x.contact_id === contactId) : undefined;
      const who = dc ? contactLabel(dc) : (deal.name || "this deal");
      const dispoLabel = formatCallDisposition(disposition);
      const followIso = fromLocalDatetimeInput(followupAt);
      const content = notes.trim()
        ? `${dispoLabel} — call with ${who}: ${notes.trim()}`
        : `${dispoLabel} — call with ${who}`;

      await activitiesApi.create({
        type: "call",
        source: "manual",
        medium: "call",
        deal_id: deal.id,
        ...(contactId ? { contact_id: contactId } : {}),
        call_outcome: callStatus || undefined,
        content,
        event_metadata: {
          event_type: "manual_call_logged",
          call_disposition: disposition,
          call_status: callStatus,
          followup_at: followIso,
          logged_at: new Date().toISOString(),
          ...(recordingId ? { recording_id: recordingId } : {}),
        },
      } as Partial<Activity>);

      // Mirror the prospect flow: the call updates Next Step + Follow-up.
      const patch: Partial<Deal> = {};
      if (nextStep.trim() && nextStep.trim() !== (deal.next_step ?? "")) patch.next_step = nextStep.trim();
      if (followIso) patch.next_step_due_at = followIso;
      if (Object.keys(patch).length) await onPatchDeal(patch);

      onLogged();
      setSaved(true);
      setDisposition("");
      setNotes("");
      setRecordingId(null);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{
      border: "1px solid #e3ebf4", borderRadius: 12, background: "#fbfdff",
      marginBottom: 14, overflow: "hidden",
    }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          width: "100%", display: "flex", alignItems: "center", gap: 8,
          padding: "11px 14px", border: "none", background: "transparent",
          cursor: "pointer", fontSize: 13, fontWeight: 800, color: "#0f2744",
        }}
      >
        <Phone size={14} style={{ color: "#1f6feb" }} />
        Log a call
        {saved ? (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 4, color: "#15803d", fontSize: 12, fontWeight: 700 }}>
            <CheckCircle2 size={12} /> Saved
          </span>
        ) : null}
        <span style={{ flex: 1 }} />
        {open ? <ChevronDown size={16} color="#7a96b0" /> : <ChevronRight size={16} color="#7a96b0" />}
      </button>

      {open ? (
        <div style={{ padding: "0 14px 14px", display: "grid", gap: 12 }}>
          {/* Who did you call? Optional — most deals have no linked contact,
              so the recording attaches to the deal directly when none is set. */}
          {dealContacts.length > 0 && (
            <div>
              <label style={{ fontSize: 11, fontWeight: 800, color: "#5e7290", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                Who did you call? <span style={{ fontWeight: 600, color: "#94a3b8", textTransform: "none" }}>(optional)</span>
              </label>
              <select
                value={contactId}
                onChange={(e) => setContactId(e.target.value)}
                style={{ ...inputStyle, marginTop: 5 }}
              >
                <option value="">— No specific contact —</option>
                {dealContacts.map((dc) => (
                  <option key={dc.contact_id} value={dc.contact_id}>
                    {contactLabel(dc)}{dc.title ? ` · ${dc.title}` : ""}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Recording + AI disposition. Attaches to the chosen contact, or to
              the deal directly when none. Keyed by contact/deal so switching the
              callee gives a fresh recorder. */}
          <CallRecordingPanel
            key={contactId || deal.id}
            contactId={contactId || undefined}
            dealId={deal.id}
            onRecordingChange={setRecordingId}
            onSuggestion={handleSuggestion}
          />

          <div>
            <label style={{ fontSize: 11, fontWeight: 800, color: "#5e7290", textTransform: "uppercase", letterSpacing: "0.05em" }}>
              Disposition
            </label>
            <select
              value={disposition}
              onChange={(e) => applyDisposition(e.target.value)}
              style={{ ...inputStyle, marginTop: 5 }}
            >
              <option value="">Select what happened…</option>
              {CALL_DISPOSITION_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>

          <div>
            <label style={{ fontSize: 11, fontWeight: 800, color: "#5e7290", textTransform: "uppercase", letterSpacing: "0.05em" }}>
              Notes
            </label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              placeholder="What was said, objections, commitments…"
              style={{ ...inputStyle, marginTop: 5, resize: "vertical" }}
            />
          </div>

          <div>
            <label style={{ fontSize: 11, fontWeight: 800, color: "#5e7290", textTransform: "uppercase", letterSpacing: "0.05em" }}>
              Next step
            </label>
            <input
              type="text"
              value={nextStep}
              onChange={(e) => setNextStep(e.target.value)}
              placeholder="What happens next on this deal"
              style={{ ...inputStyle, marginTop: 5 }}
            />
          </div>

          <div>
            <label style={{ fontSize: 11, fontWeight: 800, color: "#5e7290", textTransform: "uppercase", letterSpacing: "0.05em" }}>
              Follow-up date
            </label>
            <input
              type="datetime-local"
              value={followupAt}
              onChange={(e) => setFollowupAt(e.target.value)}
              style={{ ...inputStyle, marginTop: 5 }}
            />
          </div>

          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving || !disposition}
            style={{
              width: "100%", padding: "11px 14px", borderRadius: 10, border: "none",
              background: !disposition ? "#c7d4e2" : "#1f6feb",
              color: "#fff", fontSize: 13.5, fontWeight: 800,
              cursor: (saving || !disposition) ? "not-allowed" : "pointer",
              display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 8,
            }}
          >
            {saving ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : <Save size={14} />}
            {saving ? "Saving…" : "Save call → update activity, next step & follow-up"}
          </button>
        </div>
      ) : null}
    </div>
  );
}

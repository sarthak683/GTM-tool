import { useEffect, useState } from "react";
import { Repeat, X } from "lucide-react";
import { sequencesApi, type Sequence, type Enrollment } from "../lib/api";

// The "Add to sequence" entry point used on both Account Sourcing →
// Stakeholders and Contact detail (same component, two placements per the
// spec). Once a contact is actively enrolled, the button is replaced by a
// status line — a contact can never be double-enrolled.
export default function AddToSequenceButton({ contactId, readOnly = false }: { contactId: string; readOnly?: boolean }) {
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  const [loading, setLoading] = useState(true);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [sequences, setSequences] = useState<Sequence[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = () => {
    setLoading(true);
    sequencesApi.getContactEnrollment(contactId).then(setEnrollment).catch(() => setEnrollment(null)).finally(() => setLoading(false));
  };
  useEffect(refresh, [contactId]);

  const openPicker = () => {
    setError("");
    setSelected("");
    sequencesApi.list().then(setSequences).catch(() => setSequences([]));
    setPickerOpen(true);
  };

  const handleEnroll = async () => {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      await sequencesApi.enroll(contactId, selected);
      setPickerOpen(false);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to enroll");
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async () => {
    if (!enrollment) return;
    if (!window.confirm("Remove this contact from the sequence?")) return;
    await sequencesApi.unenroll(enrollment.id);
    refresh();
  };

  if (loading) return null;

  if (enrollment) {
    return (
      <div style={{ display: "inline-flex", alignItems: "center", gap: 8, padding: "6px 10px", borderRadius: 999, background: "#eef2ff", border: "1px solid #c7d2fe", fontSize: 12.5, fontWeight: 700, color: "#4338ca" }}>
        <Repeat size={13} />
        In: {enrollment.sequence_name} — Step {enrollment.current_step}/{enrollment.total_steps}
        <button disabled={readOnly} onClick={() => void handleRemove()} title="Remove / switch sequence" style={{ border: "none", background: "transparent", color: "#4338ca", cursor: "pointer", padding: 0, display: "flex" }}>
          <X size={13} />
        </button>
      </div>
    );
  }

  return (
    <>
      <button disabled={readOnly} onClick={openPicker} style={{ display: "inline-flex", alignItems: "center", gap: 6, border: "1px solid #dce8f4", background: "#fff", borderRadius: 9, padding: "7px 12px", fontSize: 12.5, fontWeight: 700, color: "#2563eb", cursor: "pointer" }}>
        <Repeat size={13} /> Add to sequence
      </button>

      {pickerOpen && (
        <div style={{ position: "fixed", inset: 0, zIndex: 260, display: "flex", alignItems: "center", justifyContent: "center" }} onClick={() => setPickerOpen(false)}>
          <div style={{ position: "absolute", inset: 0, background: "rgba(10,20,40,0.45)" }} />
          <div style={{ position: "relative", width: 380, maxWidth: "92vw", background: "#fff", borderRadius: 16, boxShadow: "0 24px 60px rgba(14,38,66,0.22)", padding: 20 }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <div style={{ fontSize: 14, fontWeight: 800, color: "#0f2744" }}>Add to sequence</div>
              <button onClick={() => setPickerOpen(false)} style={{ border: 0, background: "transparent", color: "#7a96b0", cursor: "pointer" }}><X size={16} /></button>
            </div>
            {sequences.length === 0 ? (
              <div style={{ fontSize: 13, color: "#9aa8b7" }}>No sequences yet — build one from the Sequences tab.</div>
            ) : (
              <div style={{ display: "grid", gap: 6, maxHeight: 260, overflowY: "auto" }}>
                {sequences.map((s) => (
                  <label key={s.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px", borderRadius: 9, border: `1px solid ${selected === s.id ? "#93c5fd" : "#e8eef5"}`, background: selected === s.id ? "#eff6ff" : "#fff", cursor: "pointer" }}>
                    <input type="radio" name="sequence" checked={selected === s.id} onChange={() => setSelected(s.id)} />
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 700, color: "#0f2744" }}>{s.name}</div>
                      <div style={{ fontSize: 11.5, color: "#7a96b0" }}>{s.step_count} steps{s.shared ? " · Team" : ""}</div>
                    </div>
                  </label>
                ))}
              </div>
            )}
            {error && <div style={{ color: "#ef4444", fontSize: 12, marginTop: 8 }}>{error}</div>}
            <div style={{ display: "flex", gap: 8, marginTop: 14, justifyContent: "flex-end" }}>
              <button onClick={() => setPickerOpen(false)} style={{ padding: "8px 14px", borderRadius: 9, border: "1px solid #dce8f4", background: "#fff", color: "#4a6580", fontWeight: 700, cursor: "pointer" }}>Cancel</button>
              <button onClick={() => void handleEnroll()} disabled={!selected || busy} style={{ padding: "8px 16px", borderRadius: 9, border: "none", background: "#6fae27", color: "#fff", fontWeight: 700, cursor: "pointer", opacity: !selected || busy ? 0.6 : 1 }}>
                {busy ? "Enrolling…" : "Enroll"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

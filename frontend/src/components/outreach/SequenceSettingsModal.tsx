import { useEffect, useState } from "react";
import { Settings2, Plus, Trash2, X, Check, Loader2 } from "lucide-react";
import { settingsApi } from "../../lib/api";

// Keep this in lockstep with MAX_SEQUENCE_STEPS in OutreachDrawer.tsx — the
// two components author cadences at different scopes (workspace defaults
// here, per-prospect overrides there) but the ceiling should match so reps
// don't see one surface allow more steps than another.
const MAX_SEQUENCE_STEPS = 20;

interface Props {
  open: boolean;
  onClose: () => void;
  onSaved?: (steps: Array<{ step_number: number; day: number; channel: "email" | "call" | "linkedin" }>) => void;
}

export default function SequenceSettingsModal({ open, onClose, onSaved }: Props) {
  const [steps, setSteps] = useState<Array<{ step_number: number; day: number; channel: "email" | "call" | "linkedin" }>>([
    { step_number: 1, day: 0, channel: "email" },
    { step_number: 2, day: 3, channel: "linkedin" },
    { step_number: 3, day: 7, channel: "call" },
  ]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError("");
    setSaved(false);
    settingsApi.getOutreach()
      .then((d) => setSteps(d.steps))
      .catch(() => setError("Failed to load settings"))
      .finally(() => setLoading(false));
  }, [open]);

  const updateDay = (i: number, val: string) => {
    const n = parseInt(val, 10);
    if (isNaN(n) || n < 0) return;
    setSteps((prev) => prev.map((step, idx) => (idx === i ? { ...step, day: n } : step)));
  };

  const updateChannel = (i: number, channel: string) => {
    const nextChannel = channel === "call" || channel === "linkedin" ? channel : "email";
    setSteps((prev) => prev.map((step, idx) => (idx === i ? { ...step, channel: nextChannel } : step)));
  };

  const addStep = () => {
    const last = steps[steps.length - 1];
    const nextDay = (last?.day ?? 0) + 3;
    setSteps((prev) => [...prev, { step_number: prev.length + 1, day: nextDay, channel: "email" }]);
  };

  const removeStep = (i: number) => {
    if (steps.length <= 1) return;
    setSteps((prev) => prev.filter((_, idx) => idx !== i).map((step, idx) => ({ ...step, step_number: idx + 1 })));
  };

  const handleSave = async () => {
    setSaving(true);
    setError("");
    try {
      const result = await settingsApi.updateOutreach(steps.map((step, index) => ({ ...step, step_number: index + 1 })));
      setSaved(true);
      onSaved?.(result.steps);
      setTimeout(onClose, 800);
    } catch {
      setError("Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;

  return (
    <div
      data-mobile-modal
      style={{
        position: "fixed", inset: 0, zIndex: 1000,
        background: "rgba(15, 39, 68, 0.45)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div data-mobile-modal-panel style={{
        background: "#fff",
        borderRadius: 20,
        border: "1px solid #d5e3ef",
        boxShadow: "0 24px 48px rgba(14, 38, 66, 0.18)",
        width: 420,
        maxWidth: "calc(100vw - 32px)",
      }}>
       <div data-mobile-modal-body style={{ padding: "28px 28px 0" }}>
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{
              width: 36, height: 36, borderRadius: 10,
              background: "linear-gradient(135deg, #0f2744, #175089)",
              display: "flex", alignItems: "center", justifyContent: "center",
            }}>
              <Settings2 size={18} color="#fff" />
            </div>
            <div>
              <div style={{ fontWeight: 700, fontSize: 15, color: "#0f2744" }}>Sequence Settings</div>
              <div style={{ fontSize: 12, color: "#7a96b0" }}>Global defaults for new sequences</div>
            </div>
          </div>
          <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", color: "#7a96b0", padding: 4 }}>
            <X size={18} />
          </button>
        </div>

        {loading ? (
          <div style={{ display: "flex", justifyContent: "center", padding: "32px 0" }}>
            <Loader2 size={22} color="#175089" className="animate-spin" />
          </div>
        ) : (
          <>
            {/* Steps list */}
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "#546679", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 12 }}>
                Sequence Steps
              </div>

              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {steps.map((step, i) => (
                  <div key={i} style={{
                    display: "flex", alignItems: "center", gap: 10,
                    background: "#f7fbff",
                    border: "1px solid #dbe6f2",
                    borderRadius: 12,
                    padding: "10px 14px",
                  }}>
                    {/* Step badge */}
                    <div style={{
                      width: 26, height: 26, borderRadius: 8, flexShrink: 0,
                      background: i === 0 ? "linear-gradient(135deg, #0f2744, #175089)" : "#e8f0f8",
                      color: i === 0 ? "#fff" : "#175089",
                      display: "flex", alignItems: "center", justifyContent: "center",
                      fontSize: 12, fontWeight: 700,
                    }}>
                      {i + 1}
                    </div>

                    <div style={{ flex: 1, fontSize: 13, color: "#2c4a63" }}>
                      <select
                        value={step.channel}
                        onChange={(e) => updateChannel(i, e.target.value)}
                        style={{
                          width: "100%",
                          border: "1px solid #c8d9e8",
                          borderRadius: 8,
                          padding: "6px 10px",
                          fontSize: 13,
                          color: "#0f2744",
                          background: "#fff",
                          outline: "none",
                        }}
                      >
                        <option value="email">Email</option>
                        <option value="call">Call</option>
                        <option value="linkedin">LinkedIn</option>
                      </select>
                    </div>

                    {/* Day input */}
                    <input
                      type="number"
                      min={0}
                      value={step.day}
                      onChange={(e) => updateDay(i, e.target.value)}
                      style={{
                        width: 64, textAlign: "center",
                        border: "1px solid #c8d9e8",
                        borderRadius: 8, padding: "5px 8px",
                        fontSize: 14, fontWeight: 600, color: "#0f2744",
                        background: "#fff",
                        outline: "none",
                      }}
                    />

                    {/* Remove button — only for steps beyond the first two */}
                    {steps.length > 2 && i > 0 ? (
                      <button
                        onClick={() => removeStep(i)}
                        style={{ background: "none", border: "none", cursor: "pointer", color: "#c0cdd8", padding: 2 }}
                        title="Remove step"
                      >
                        <Trash2 size={14} />
                      </button>
                    ) : (
                      <div style={{ width: 18 }} />
                    )}
                  </div>
                ))}
              </div>
            </div>

            {/* Add step */}
            {steps.length < MAX_SEQUENCE_STEPS && (
              <button
                onClick={addStep}
                style={{
                  display: "flex", alignItems: "center", gap: 6,
                  background: "none", border: "1px dashed #b8cfe0",
                  borderRadius: 10, padding: "8px 14px",
                  width: "100%", cursor: "pointer",
                  fontSize: 13, color: "#4a7fa5", fontWeight: 500,
                  marginTop: 6, marginBottom: 20,
                }}
              >
                <Plus size={14} />
                Add another step
              </button>
            )}

            <div style={{ marginBottom: 20, fontSize: 12, color: "#8fa8bf", lineHeight: 1.5 }}>
              Day 0 = sent immediately when the sequence launches. These delays apply to all new sequences generated going forward.
            </div>

            {error && (
              <div style={{ color: "#c0392b", fontSize: 13, marginBottom: 12 }}>{error}</div>
            )}
          </>
        )}
        </div>

        {/* Sticky footer (only renders when not loading) */}
        <div data-mobile-modal-footer style={{ display: "flex", gap: 10, justifyContent: "flex-end", padding: "16px 28px" }}>
          <button onClick={onClose} style={{
            padding: "11px 18px", minHeight: 44, borderRadius: 10, border: "1px solid #d5e3ef",
            background: "#fff", color: "#546679", fontSize: 13, fontWeight: 500, cursor: "pointer",
          }}>
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            style={{
              padding: "11px 20px", minHeight: 44, borderRadius: 10, border: "none",
              background: saved ? "#16a34a" : "linear-gradient(135deg, #0f2744, #175089)",
              color: "#fff", fontSize: 13, fontWeight: 600, cursor: "pointer",
              display: "flex", alignItems: "center", gap: 6,
              opacity: saving ? 0.75 : 1,
              transition: "background 0.3s",
            }}
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : saved ? <Check size={14} /> : null}
            {saved ? "Saved!" : saving ? "Saving…" : "Save Changes"}
          </button>
        </div>
      </div>
    </div>
  );
}

import { useEffect, useMemo, useState } from "react";
import { GripVertical, Mail, Phone, Link2 as LinkedInIcon, Plus, Repeat, Trash2, X } from "lucide-react";
import { DndContext, PointerSensor, closestCenter, useSensor, useSensors, type DragEndEvent } from "@dnd-kit/core";
import { SortableContext, arrayMove, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { sequencesApi, outreachApi, type Sequence, type SequenceStepDraft, type SequenceStepType, type SequenceLinkedinCategory } from "../lib/api";
import { useAuth } from "../lib/AuthContext";

const STEP_TYPE_META: Record<SequenceStepType, { label: string; icon: typeof Mail; color: string }> = {
  email: { label: "Email", icon: Mail, color: "#1f7a4d" },
  call: { label: "Call", icon: Phone, color: "#2563eb" },
  linkedin: { label: "LinkedIn", icon: LinkedInIcon, color: "#0a66c2" },
};

function emptyStep(): SequenceStepDraft {
  return { type: "email", delay_minutes: 0, send_via: "personal", instantly_campaign_id: null };
}

type DelayUnit = "days" | "hours" | "minutes";
const UNIT_MINUTES: Record<DelayUnit, number> = { days: 1440, hours: 60, minutes: 1 };

// delay_minutes is always the normalized-to-minutes value on the wire; the
// amount + unit fields are purely a friendlier input for it. Pick the
// largest unit that divides the stored value evenly so editing an existing
// "3 days" step shows "3" + "Days", not "4320" + "Min".
function unitAndAmountFromMinutes(total: number): { unit: DelayUnit; amount: number } {
  if (total !== 0 && total % 1440 === 0) return { unit: "days", amount: total / 1440 };
  if (total !== 0 && total % 60 === 0) return { unit: "hours", amount: total / 60 };
  return { unit: "minutes", amount: total };
}

function SortableStepCard({
  id,
  index,
  total,
  step,
  unit,
  campaigns,
  onUpdate,
  onSetUnit,
  onSetAmount,
  onRemove,
}: {
  id: string;
  index: number;
  total: number;
  step: SequenceStepDraft;
  unit: DelayUnit;
  campaigns: { id: string; name: string }[];
  onUpdate: (patch: Partial<SequenceStepDraft>) => void;
  onSetUnit: (unit: DelayUnit) => void;
  onSetAmount: (amount: number) => void;
  onRemove: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });
  const meta = STEP_TYPE_META[step.type];
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  return (
    <div ref={setNodeRef} style={{ ...style, border: "1px solid #e8eef5", borderRadius: 14, padding: 14, display: "grid", gap: 10, background: "#fbfdff" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button
          type="button"
          {...attributes}
          {...listeners}
          title="Drag to reorder"
          style={{ border: "1px solid #dce8f4", background: "#fff", borderRadius: 8, width: 28, height: 28, display: "grid", placeItems: "center", cursor: "grab", touchAction: "none", flexShrink: 0 }}
        >
          <GripVertical size={14} color="#7a96b0" />
        </button>
        <div style={{ width: 26, height: 26, borderRadius: 8, background: meta.color, color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
          <meta.icon size={13} />
        </div>
        <span style={{ fontSize: 12, fontWeight: 800, color: "#7a96b0" }}>STEP {index + 1}</span>
        <div style={{ flex: 1 }} />
        <button type="button" onClick={onRemove} style={{ border: "1px solid #f0c1c8", background: "#fff5f6", color: "#b42336", borderRadius: 8, width: 28, height: 28, cursor: "pointer" }}><Trash2 size={13} /></button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <div>
          <label style={{ fontSize: 11, fontWeight: 700, color: "#7a96b0", display: "block", marginBottom: 4 }}>Channel <span style={{ color: "#ef4444" }}>*</span></label>
          <select value={step.type} onChange={(e) => onUpdate({ type: e.target.value as SequenceStepType, send_via: e.target.value === "email" ? "personal" : null, instantly_campaign_id: null, linkedin_category: e.target.value === "linkedin" ? "dm" : null })}
            style={{ width: "100%", height: 36, borderRadius: 9, border: "1px solid #c8d9e8", padding: "0 10px", fontSize: 13 }}>
            <option value="email">Email</option>
            <option value="call">Call</option>
            <option value="linkedin">LinkedIn</option>
          </select>
        </div>
        <div>
          <label style={{ fontSize: 11, fontWeight: 700, color: "#7a96b0", display: "block", marginBottom: 4 }}>
            Send {index > 0 ? "next" : "first"} message in <span style={{ color: "#ef4444" }}>*</span>
          </label>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              type="number"
              min={0}
              value={unitAndAmountFromMinutes(step.delay_minutes).amount}
              onChange={(e) => onSetAmount(Number(e.target.value))}
              style={{ width: 90, boxSizing: "border-box", height: 36, borderRadius: 9, border: "1px solid #c8d9e8", padding: "0 10px", fontSize: 13 }}
            />
            <select
              value={unit}
              onChange={(e) => onSetUnit(e.target.value as DelayUnit)}
              style={{ flex: 1, height: 36, borderRadius: 9, border: "1px solid #c8d9e8", padding: "0 10px", fontSize: 13 }}
            >
              <option value="days">Days</option>
              <option value="hours">Hours</option>
              <option value="minutes">Min</option>
            </select>
          </div>
        </div>
      </div>

      {step.type === "email" && (
        <div style={{ display: "grid", gridTemplateColumns: step.send_via === "instantly" ? "1fr 1fr" : "1fr", gap: 10 }}>
          <div>
            <label style={{ fontSize: 11, fontWeight: 700, color: "#7a96b0", display: "block", marginBottom: 4 }}>Category <span style={{ color: "#ef4444" }}>*</span></label>
            <div style={{ display: "inline-flex", border: "1px solid #dde6f0", borderRadius: 9, padding: 3, background: "#fff" }}>
              {[{ v: "personal", l: "Manual" }, { v: "instantly", l: "Instantly" }].map((opt) => (
                <button key={opt.v} type="button" onClick={() => onUpdate({ send_via: opt.v as "personal" | "instantly" })}
                  style={{ padding: "6px 12px", borderRadius: 7, border: "none", background: step.send_via === opt.v ? "#ecfdf3" : "transparent", color: step.send_via === opt.v ? "#1f7a4d" : "#7a96b0", fontWeight: 700, fontSize: 12.5, cursor: "pointer" }}>
                  {opt.l}
                </button>
              ))}
            </div>
          </div>
          {step.send_via === "instantly" && (
            <div>
              <label style={{ fontSize: 11, fontWeight: 700, color: "#7a96b0", display: "block", marginBottom: 4 }}>Instantly campaign <span style={{ color: "#ef4444" }}>*</span></label>
              <select value={step.instantly_campaign_id ?? ""} onChange={(e) => onUpdate({ instantly_campaign_id: e.target.value || null })}
                style={{ width: "100%", height: 36, borderRadius: 9, border: "1px solid #c8d9e8", padding: "0 10px", fontSize: 13 }}>
                <option value="">Select campaign…</option>
                {campaigns.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </div>
          )}
        </div>
      )}

      {step.type === "linkedin" && (
        <div>
          <label style={{ fontSize: 11, fontWeight: 700, color: "#7a96b0", display: "block", marginBottom: 4 }}>Category <span style={{ color: "#ef4444" }}>*</span></label>
          <div style={{ display: "inline-flex", border: "1px solid #dde6f0", borderRadius: 9, padding: 3, background: "#fff" }}>
            {[{ v: "dm", l: "DM" }, { v: "follow_up", l: "Follow up" }, { v: "inmail", l: "In-mail" }].map((opt) => (
              <button key={opt.v} type="button" onClick={() => onUpdate({ linkedin_category: opt.v as SequenceLinkedinCategory })}
                style={{ padding: "6px 12px", borderRadius: 7, border: "none", background: step.linkedin_category === opt.v ? "#ecfdf3" : "transparent", color: step.linkedin_category === opt.v ? "#1f7a4d" : "#7a96b0", fontWeight: 700, fontSize: 12.5, cursor: "pointer" }}>
                {opt.l}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function BuilderModal({
  editing,
  onClose,
  onSaved,
}: {
  editing: Sequence | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(editing?.name ?? "");
  const [shared, setShared] = useState(editing?.shared ?? false);
  const [steps, setSteps] = useState<SequenceStepDraft[]>([emptyStep()]);
  // Per-step display unit for the amount+unit delay picker — a UI-only
  // concern, kept parallel to `steps` by index. Never sent to the backend;
  // delay_minutes (already normalized to minutes) is the wire value.
  const [units, setUnits] = useState<DelayUnit[]>(["days"]);
  // Client-only stable ids for drag-and-drop reordering — SequenceStepDraft
  // has no id of its own (the backend assigns one on save), and dnd-kit
  // needs a stable key per item that survives reordering by index.
  const [stepIds, setStepIds] = useState<string[]>(() => [crypto.randomUUID()]);
  const [campaigns, setCampaigns] = useState<{ id: string; name: string }[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  useEffect(() => {
    outreachApi.listInstantlyCampaigns().then((r) => setCampaigns(r.campaigns || [])).catch(() => setCampaigns([]));
  }, []);

  useEffect(() => {
    if (editing) {
      sequencesApi.getSteps(editing.id).then((existing) => {
        if (existing.length) {
          setSteps(existing.map((s) => ({ type: s.type, delay_minutes: s.delay_minutes, send_via: s.send_via, instantly_campaign_id: s.instantly_campaign_id, linkedin_category: s.linkedin_category })));
          setUnits(existing.map((s) => unitAndAmountFromMinutes(s.delay_minutes).unit));
          setStepIds(existing.map(() => crypto.randomUUID()));
        } else {
          setSteps([emptyStep()]);
          setUnits(["days"]);
          setStepIds([crypto.randomUUID()]);
        }
      }).catch(() => {});
    }
  }, [editing]);

  const updateStep = (index: number, patch: Partial<SequenceStepDraft>) => {
    setSteps((current) => current.map((s, i) => (i === index ? { ...s, ...patch } : s)));
  };
  const setStepUnit = (index: number, unit: DelayUnit) => {
    setUnits((current) => current.map((u, i) => (i === index ? unit : u)));
    const amount = unitAndAmountFromMinutes(steps[index].delay_minutes).amount;
    updateStep(index, { delay_minutes: amount * UNIT_MINUTES[unit] });
  };
  const setStepAmount = (index: number, amount: number) => {
    updateStep(index, { delay_minutes: Math.max(0, amount) * UNIT_MINUTES[units[index]] });
  };
  const removeStep = (index: number) => {
    setSteps((current) => current.filter((_, i) => i !== index));
    setUnits((current) => current.filter((_, i) => i !== index));
    setStepIds((current) => current.filter((_, i) => i !== index));
  };
  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = stepIds.indexOf(String(active.id));
    const newIndex = stepIds.indexOf(String(over.id));
    if (oldIndex === -1 || newIndex === -1) return;
    setSteps((current) => arrayMove(current, oldIndex, newIndex));
    setUnits((current) => arrayMove(current, oldIndex, newIndex));
    setStepIds((current) => arrayMove(current, oldIndex, newIndex));
  };

  const handleSave = async () => {
    if (!name.trim()) {
      setError("Sequence name is required.");
      return;
    }
    if (shared !== true && shared !== false) {
      setError("Visibility is required.");
      return;
    }
    if (steps.length === 0) {
      setError("Add at least one step.");
      return;
    }
    for (let i = 0; i < steps.length; i++) {
      const s = steps[i];
      const label = `Step ${i + 1}`;
      if (!s.type) {
        setError(`${label}: channel is required.`);
        return;
      }
      if (unitAndAmountFromMinutes(s.delay_minutes).amount <= 0) {
        setError(`${label}: "Send message in" must be greater than 0.`);
        return;
      }
      if (s.type === "email" && !s.send_via) {
        setError(`${label}: category (Manual/Instantly) is required.`);
        return;
      }
      if (s.type === "email" && s.send_via === "instantly" && !s.instantly_campaign_id) {
        setError(`${label}: pick an Instantly campaign.`);
        return;
      }
      if (s.type === "linkedin" && !s.linkedin_category) {
        setError(`${label}: category (DM/Follow up/In-mail) is required.`);
        return;
      }
    }
    setSaving(true);
    setError("");
    try {
      const payload = { name: name.trim(), shared, steps };
      if (editing) await sequencesApi.update(editing.id, payload);
      else await sequencesApi.create(payload);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save sequence");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 300, display: "flex", alignItems: "center", justifyContent: "center" }} onClick={onClose}>
      <div style={{ position: "absolute", inset: 0, background: "rgba(10,20,40,0.5)" }} />
      <div
        style={{ position: "relative", width: 640, maxWidth: "95vw", maxHeight: "88vh", overflowY: "auto", background: "#fff", borderRadius: 20, boxShadow: "0 24px 60px rgba(14,38,66,0.25)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ padding: "20px 24px 14px", borderBottom: "1px solid #e8eef5", display: "flex", alignItems: "center", justifyContent: "space-between", position: "sticky", top: 0, background: "#fff", zIndex: 1 }}>
          <div style={{ fontSize: 16, fontWeight: 800, color: "#0f2744" }}>{editing ? "Edit sequence" : "New sequence"}</div>
          <button onClick={onClose} style={{ border: 0, background: "transparent", color: "#7a96b0", cursor: "pointer" }}><X size={18} /></button>
        </div>

        <div style={{ padding: 24, display: "grid", gap: 18 }}>
          <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 14 }}>
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: "#2c4a63", display: "block", marginBottom: 6 }}>Sequence name <span style={{ color: "#ef4444" }}>*</span></label>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Enterprise Cold Outreach"
                style={{ width: "100%", boxSizing: "border-box", height: 40, border: "1px solid #c8d9e8", borderRadius: 10, padding: "0 12px", fontSize: 14 }} />
            </div>
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: "#2c4a63", display: "block", marginBottom: 6 }}>Visibility <span style={{ color: "#ef4444" }}>*</span></label>
              <div style={{ display: "inline-flex", border: "1px solid #dde6f0", borderRadius: 10, padding: 3, background: "#f8fafc", height: 40, boxSizing: "border-box" }}>
                {[{ v: false, l: "Private" }, { v: true, l: "Team" }].map((opt) => (
                  <button key={String(opt.v)} type="button" onClick={() => setShared(opt.v)}
                    style={{ padding: "0 14px", borderRadius: 8, border: "none", background: shared === opt.v ? "#fff" : "transparent", color: shared === opt.v ? "#0f2744" : "#7a96b0", fontWeight: 700, fontSize: 13, cursor: "pointer" }}>
                    {opt.l}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div style={{ display: "grid", gap: 10 }}>
            <div style={{ fontSize: 13, fontWeight: 800, color: "#2c4a63" }}>Steps</div>
            <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
              <SortableContext items={stepIds} strategy={verticalListSortingStrategy}>
                {steps.map((step, i) => (
                  <SortableStepCard
                    key={stepIds[i]}
                    id={stepIds[i]}
                    index={i}
                    total={steps.length}
                    step={step}
                    unit={units[i]}
                    campaigns={campaigns}
                    onUpdate={(patch) => updateStep(i, patch)}
                    onSetUnit={(u) => setStepUnit(i, u)}
                    onSetAmount={(a) => setStepAmount(i, a)}
                    onRemove={() => removeStep(i)}
                  />
                ))}
              </SortableContext>
            </DndContext>
            <button type="button" onClick={() => { setSteps((s) => [...s, emptyStep()]); setUnits((u) => [...u, "days"]); setStepIds((ids) => [...ids, crypto.randomUUID()]); }}
              style={{ display: "inline-flex", alignItems: "center", gap: 6, justifySelf: "start", border: "1px dashed #b9cbe0", background: "#fff", color: "#2563eb", borderRadius: 10, padding: "8px 14px", fontSize: 13, fontWeight: 700, cursor: "pointer" }}>
              <Plus size={14} /> Add step
            </button>
          </div>

          {error && <div style={{ color: "#ef4444", fontSize: 13 }}>{error}</div>}

          <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
            <button onClick={onClose} style={{ padding: "10px 18px", borderRadius: 10, border: "1px solid #dce8f4", background: "#fff", color: "#4a6580", fontWeight: 700, cursor: "pointer" }}>Cancel</button>
            <button onClick={() => void handleSave()} disabled={saving}
              style={{ padding: "10px 20px", borderRadius: 10, border: "none", background: "#6fae27", color: "#fff", fontWeight: 700, cursor: "pointer", opacity: saving ? 0.7 : 1 }}>
              {saving ? "Saving…" : "Save sequence"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Sequences() {
  const { user } = useAuth();
  const [sequences, setSequences] = useState<Sequence[]>([]);
  const [loading, setLoading] = useState(true);
  const [builderOpen, setBuilderOpen] = useState(false);
  const [editing, setEditing] = useState<Sequence | null>(null);

  const load = () => {
    setLoading(true);
    sequencesApi.list().then(setSequences).catch(() => setSequences([])).finally(() => setLoading(false));
  };
  useEffect(load, []);

  const { mine, team } = useMemo(() => {
    const mine = sequences.filter((s) => s.owner_id === user?.id);
    const team = sequences.filter((s) => s.shared && s.owner_id !== user?.id);
    return { mine, team };
  }, [sequences, user?.id]);

  const canEdit = (s: Sequence) => s.owner_id === user?.id || user?.role === "admin";

  const handleDelete = async (s: Sequence) => {
    if (!window.confirm(`Delete "${s.name}"? Past enrollments and their task history are kept.`)) return;
    await sequencesApi.remove(s.id);
    load();
  };

  const renderRow = (s: Sequence) => (
    <div key={s.id} style={{ display: "flex", alignItems: "center", gap: 14, padding: "14px 16px", border: "1px solid #e8eef5", borderRadius: 12, background: "#fff" }}>
      <div style={{ width: 34, height: 34, borderRadius: 10, background: "#eef2ff", color: "#4338ca", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
        <Repeat size={16} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: "#0f2744" }}>{s.name}</div>
        <div style={{ fontSize: 12, color: "#7a96b0" }}>
          {s.step_count} step{s.step_count === 1 ? "" : "s"} · {s.active_enrollment_count} active · {s.owner_name || "—"}
          {s.shared ? " · Team" : " · Private"}
        </div>
      </div>
      {canEdit(s) && (
        <>
          <button onClick={() => { setEditing(s); setBuilderOpen(true); }} style={{ border: "1px solid #dce8f4", background: "#fff", borderRadius: 9, padding: "7px 12px", fontSize: 12.5, fontWeight: 700, color: "#2563eb", cursor: "pointer" }}>Edit</button>
          <button onClick={() => void handleDelete(s)} style={{ border: "1px solid #f0c1c8", background: "#fff5f6", borderRadius: 9, padding: "7px 12px", fontSize: 12.5, fontWeight: 700, color: "#b42336", cursor: "pointer" }}>Delete</button>
        </>
      )}
    </div>
  );

  return (
    <div style={{ padding: 24, maxWidth: 900, margin: "0 auto", display: "grid", gap: 24 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 800, color: "#0f2744", margin: 0 }}>Outreach Sequences</h1>
          <p style={{ fontSize: 13, color: "#7a96b0", margin: "4px 0 0" }}>Build a reusable cadence, then enroll contacts from Account Sourcing or Prospecting.</p>
        </div>
        <button onClick={() => { setEditing(null); setBuilderOpen(true); }}
          style={{ display: "inline-flex", alignItems: "center", gap: 8, background: "#6fae27", color: "#fff", border: "none", borderRadius: 10, padding: "10px 16px", fontSize: 13.5, fontWeight: 700, cursor: "pointer" }}>
          <Plus size={15} /> New Sequence
        </button>
      </div>

      {loading ? (
        <div style={{ color: "#7a96b0", fontSize: 13 }}>Loading…</div>
      ) : (
        <>
          <div style={{ display: "grid", gap: 10 }}>
            <div style={{ fontSize: 12, fontWeight: 800, color: "#7a96b0", textTransform: "uppercase", letterSpacing: 0.4 }}>My Sequences</div>
            {mine.length === 0 ? <div style={{ color: "#9aa8b7", fontSize: 13 }}>No sequences yet.</div> : mine.map(renderRow)}
          </div>
          <div style={{ display: "grid", gap: 10 }}>
            <div style={{ fontSize: 12, fontWeight: 800, color: "#7a96b0", textTransform: "uppercase", letterSpacing: 0.4 }}>Team Sequences</div>
            {team.length === 0 ? <div style={{ color: "#9aa8b7", fontSize: 13 }}>None shared yet.</div> : team.map(renderRow)}
          </div>
        </>
      )}

      {builderOpen && (
        <BuilderModal
          editing={editing}
          onClose={() => setBuilderOpen(false)}
          onSaved={() => { setBuilderOpen(false); load(); }}
        />
      )}
    </div>
  );
}

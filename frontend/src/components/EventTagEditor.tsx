import { useState } from "react";
import { Plus } from "lucide-react";
import { eventsApi } from "../lib/api";
import EventChips from "./EventChips";
import EventTagModal from "./EventTagModal";

// Detail-page event chips: every tag shown with an "x" to remove, plus a
// "+ Event" chip to add one. `onChanged` receives the entity's new event list
// so the page can update without a full reload.
export default function EventTagEditor({
  kind,
  id,
  events,
  onChanged,
}: {
  kind: "contact" | "company";
  id: string;
  events?: string[] | null;
  onChanged: (events: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const current = events ?? [];

  const add = async (event: string) => {
    const res = await eventsApi.tag({
      event,
      contactIds: kind === "contact" ? [id] : [],
      companyIds: kind === "company" ? [id] : [],
    });
    const lower = current.map((e) => e.toLowerCase());
    onChanged(lower.includes(res.event.toLowerCase()) ? current : [...current, res.event]);
  };

  const remove = async (event: string) => {
    await eventsApi.tag({
      event,
      contactIds: kind === "contact" ? [id] : [],
      companyIds: kind === "company" ? [id] : [],
      action: "remove",
    });
    onChanged(current.filter((e) => e.toLowerCase() !== event.toLowerCase()));
  };

  return (
    <>
      <EventChips events={current} onRemove={(e) => void remove(e)} size="md" />
      <button
        type="button"
        onClick={() => setOpen(true)}
        style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12, fontWeight: 700, padding: "3px 9px", borderRadius: 999, border: "1px dashed #c4b5fd", background: "#fff", color: "#6d28d9", cursor: "pointer" }}
      >
        <Plus size={12} /> Event
      </button>
      <EventTagModal
        open={open}
        onClose={() => setOpen(false)}
        subtitle={kind === "contact" ? "Also tags this prospect's account" : undefined}
        onSubmit={add}
      />
    </>
  );
}

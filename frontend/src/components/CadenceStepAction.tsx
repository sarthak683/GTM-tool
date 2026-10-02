import { useState } from "react";
import { Mail, Phone, Link2 as LinkedInIcon, Loader2, Send } from "lucide-react";
import { contactsApi, sequencesApi } from "../lib/api";
import type { Contact, TaskWorkspaceItem } from "../types";
import LogLinkedInDialog from "./LogLinkedInDialog";
import LogEmailDialog from "./LogEmailDialog";
import CallDispositionDrawer from "../pages/contacts/CallDispositionDrawer";

// Maps a step's build-time LinkedIn Category to the "Log LinkedIn touch"
// dialog's outcome dropdown, so it opens preselected to what the rep planned
// (dm -> "Sent" — a connection request/DM is logged as "sent" in that
// dialog's taxonomy).
const LINKEDIN_CATEGORY_TO_STATUS: Record<string, string> = {
  dm: "sent",
  follow_up: "follow_up",
  inmail: "inmail",
};

// The single action button on an Outreach Sequence cadence task row.
// Personal Email / Call / LinkedIn all reuse the exact same dialogs used
// from Prospecting — nothing new about the dialog itself, just opened here
// with a cadenceTaskId so saving it also advances the step. An Instantly
// email step is different: the click just adds the contact as a lead to
// the campaign; the task stays open until the existing Instantly webhook
// confirms the send.
export default function CadenceStepAction({ task, onAdvanced }: { task: TaskWorkspaceItem; onAdvanced: () => void }) {
  const [loadingContact, setLoadingContact] = useState(false);
  const [contact, setContact] = useState<Contact | null>(null);
  const [openDialog, setOpenDialog] = useState<"call" | "linkedin" | "email" | null>(null);
  const [addingInstantly, setAddingInstantly] = useState(false);
  const [instantlyAdded, setInstantlyAdded] = useState(false);
  const [error, setError] = useState("");

  if (task.status !== "open" || !task.contact_id || !task.step_type) return null;

  const openReusedDialog = async (dialog: "call" | "linkedin" | "email") => {
    if (!task.contact_id) return;
    setLoadingContact(true);
    setError("");
    try {
      const full = await contactsApi.get(task.contact_id);
      setContact(full);
      setOpenDialog(dialog);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load contact");
    } finally {
      setLoadingContact(false);
    }
  };

  const handleInstantlyAdd = async () => {
    setAddingInstantly(true);
    setError("");
    try {
      await sequencesApi.addTaskToInstantly(task.id);
      setInstantlyAdded(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add to Instantly campaign");
    } finally {
      setAddingInstantly(false);
    }
  };

  const closeDialog = () => {
    setOpenDialog(null);
    setContact(null);
  };

  const buttonStyle = { display: "inline-flex", alignItems: "center", gap: 6, border: "1px solid #dce8f4", background: "#fff", borderRadius: 9, padding: "7px 12px", fontSize: 12.5, fontWeight: 700, cursor: "pointer" } as const;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      {task.step_type === "call" && (
        <button onClick={() => void openReusedDialog("call")} disabled={loadingContact} style={{ ...buttonStyle, color: "#2563eb" }}>
          {loadingContact ? <Loader2 size={13} className="animate-spin" /> : <Phone size={13} />} Log call
        </button>
      )}
      {task.step_type === "linkedin" && (
        <button onClick={() => void openReusedDialog("linkedin")} disabled={loadingContact} style={{ ...buttonStyle, color: "#0a66c2" }}>
          {loadingContact ? <Loader2 size={13} className="animate-spin" /> : <LinkedInIcon size={13} />} Log LinkedIn
        </button>
      )}
      {task.step_type === "email" && task.step_send_via !== "instantly" && (
        <button onClick={() => void openReusedDialog("email")} disabled={loadingContact} style={{ ...buttonStyle, color: "#1f7a4d" }}>
          {loadingContact ? <Loader2 size={13} className="animate-spin" /> : <Mail size={13} />} Log email
        </button>
      )}
      {task.step_type === "email" && task.step_send_via === "instantly" && (
        <button onClick={() => void handleInstantlyAdd()} disabled={addingInstantly || instantlyAdded} style={{ ...buttonStyle, color: "#7c3aed", opacity: instantlyAdded ? 0.6 : 1 }}>
          {addingInstantly ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
          {instantlyAdded ? "Added — waiting on send" : "Add to Instantly campaign"}
        </button>
      )}
      {error && <span style={{ color: "#ef4444", fontSize: 12 }}>{error}</span>}

      {contact && openDialog === "call" && (
        <CallDispositionDrawer contact={contact} onClose={closeDialog} cadenceTaskId={task.id} onSaved={() => { closeDialog(); onAdvanced(); }} />
      )}
      {contact && openDialog === "linkedin" && (
        <LogLinkedInDialog
          contactId={contact.id}
          contactName={`${contact.first_name ?? ""} ${contact.last_name ?? ""}`.trim()}
          linkedinUrl={contact.linkedin_url}
          sequenceStatus={contact.sequence_status}
          initialStatus={LINKEDIN_CATEGORY_TO_STATUS[task.step_linkedin_category ?? "dm"]}
          open
          onClose={closeDialog}
          cadenceTaskId={task.id}
          onLogged={onAdvanced}
        />
      )}
      {contact && openDialog === "email" && (
        <LogEmailDialog contact={contact} open onClose={closeDialog} cadenceTaskId={task.id} onLogged={onAdvanced} />
      )}
    </div>
  );
}

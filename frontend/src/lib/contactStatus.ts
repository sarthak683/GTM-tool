// Canonical per-prospect sourcing status values + presentation. Mirrors
// CONTACT_STATUS_VALUES in app/models/contact.py. Keep the two in lockstep.
// This is the contact-level counterpart of ACCOUNT_STATUS_OPTIONS in
// ./accountStatus.ts (which drives the account-level control).

export type ContactStatusValue =
  | "cold"
  | "in_progress"
  | "meeting_booked"
  | "meeting_done"
  | "in_pipeline"
  | "not_the_right_prospect"
  | "dnd"
  | "reach_out_later"
  | "not_interested";

export type ContactStatusOption = {
  value: ContactStatusValue;
  label: string;
  // Pill palette: text + background.
  color: string;
  bg: string;
};

// Display order is authoritative here and mirrored by the backend value set.
// Nine visually distinct hues: slate, blue, violet, teal, green, maroon, red,
// amber, grey.
export const CONTACT_STATUS_OPTIONS: ContactStatusOption[] = [
  { value: "cold", label: "Cold", color: "#475569", bg: "#eef2f7" },
  { value: "in_progress", label: "In Progress", color: "#1d4ed8", bg: "#e6efff" },
  { value: "meeting_booked", label: "Meeting Booked", color: "#6d28d9", bg: "#f1ebfd" },
  { value: "meeting_done", label: "Meeting Done", color: "#0e7490", bg: "#e0f5fa" },
  { value: "in_pipeline", label: "In Pipeline", color: "#0e7c5a", bg: "#e3f7ee" },
  { value: "not_the_right_prospect", label: "Not the Right Prospect", color: "#9f1239", bg: "#fbe8ed" },
  { value: "dnd", label: "DND", color: "#b42318", bg: "#fdeceb" },
  { value: "reach_out_later", label: "Reach Out Later", color: "#92600a", bg: "#fdf3df" },
  { value: "not_interested", label: "Not interested", color: "#64748b", bg: "#f1f5f9" },
];

const STATUS_BY_VALUE: Record<string, ContactStatusOption> = Object.fromEntries(
  CONTACT_STATUS_OPTIONS.map((option) => [option.value, option]),
);

export function contactStatusLabel(value?: string | null): string {
  if (!value) return "No status";
  return STATUS_BY_VALUE[value]?.label ?? value;
}

export function contactStatusOption(value?: string | null): ContactStatusOption | null {
  if (!value) return null;
  return STATUS_BY_VALUE[value] ?? null;
}

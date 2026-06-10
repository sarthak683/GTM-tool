// Canonical account-sourcing status values + presentation. Mirrors
// ACCOUNT_STATUS_VALUES in app/models/company.py and ACCOUNT_STATUS_LABELS in
// app/api/v1/endpoints/analytics.py. Keep all three in lockstep.

export type AccountStatusValue =
  | "in_progress"
  | "cold"
  | "dnd"
  | "in_pipeline"
  | "reach_out_later";

export type AccountStatusOption = {
  value: AccountStatusValue;
  label: string;
  // Badge palette: text + background.
  color: string;
  bg: string;
};

export const ACCOUNT_STATUS_OPTIONS: AccountStatusOption[] = [
  { value: "in_progress", label: "In Progress", color: "#1d4ed8", bg: "#e6efff" },
  { value: "cold", label: "Cold", color: "#475569", bg: "#eef2f7" },
  { value: "dnd", label: "DND", color: "#b42318", bg: "#fdeceb" },
  { value: "in_pipeline", label: "In Pipeline", color: "#0e7c5a", bg: "#e3f7ee" },
  { value: "reach_out_later", label: "Reach Out Later", color: "#92600a", bg: "#fdf3df" },
];

const STATUS_BY_VALUE: Record<string, AccountStatusOption> = Object.fromEntries(
  ACCOUNT_STATUS_OPTIONS.map((option) => [option.value, option]),
);

export function accountStatusLabel(value?: string | null): string {
  if (!value) return "No status";
  return STATUS_BY_VALUE[value]?.label ?? value;
}

export function accountStatusOption(value?: string | null): AccountStatusOption | null {
  if (!value) return null;
  return STATUS_BY_VALUE[value] ?? null;
}

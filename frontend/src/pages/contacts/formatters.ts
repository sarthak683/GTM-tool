import type { Contact } from "../../types";


export function parseSearchParamList(value: string | null): string[] {
  if (!value) return [];
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

export const CONTACT_TABLE_COLUMNS: Array<{ key: string; label: string; required?: boolean }> = [
  { key: "name", label: "Name", required: true },
  { key: "company", label: "Company", required: true },
  { key: "title", label: "Title" },
  { key: "email", label: "Email", required: true },
  { key: "progress", label: "Progress", required: true },
  // Action sits here, not last. Reps work the Call / Email / Log / LinkedIn
  // buttons constantly and the reference columns after it rarely; with Action
  // last the table was 2217px wide in 1142px of space and the buttons started
  // 466px past the right edge, so every row needed a horizontal scroll.
  { key: "action", label: "Action", required: true },
  { key: "comments", label: "Comments" },
  { key: "timezone", label: "Timezone" },
  { key: "ae", label: "AE" },
  { key: "sdr", label: "SDR" },
  { key: "last_touch", label: "Last Touch" },
] as const;

export type ContactTableColumnKey = typeof CONTACT_TABLE_COLUMNS[number]["key"];
export const DEFAULT_CONTACT_TABLE_COLUMNS: ContactTableColumnKey[] = CONTACT_TABLE_COLUMNS.map((column) => column.key);

// Convert a `YYYY-MM-DD` date-filter value into a UTC ISO bound. `dayStartIso`
// anchors to local midnight (start of the rep's day) and `dayEndIso` to the
// last millisecond, so a single-day pick captures the whole day. The backend
// compares these against UTC-naive columns, so converting the local-day
// boundary to UTC ISO gives the rep "their day" semantics.
export function dayStartIso(date: string): string | undefined {
  if (!date) return undefined;
  const d = new Date(`${date}T00:00:00`);
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}

export function dayEndIso(date: string): string | undefined {
  if (!date) return undefined;
  const d = new Date(`${date}T23:59:59.999`);
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}

export function relativeTimeShort(iso?: string | null): string {
  if (!iso) return "";
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return "";
  const diff = Date.now() - ts;
  const m = Math.floor(diff / 60000);
  if (m < 1) return "now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d ago`;
  const mo = Math.floor(d / 30);
  if (mo < 12) return `${mo}mo ago`;
  return `${Math.floor(mo / 12)}y ago`;
}

export function latestProspectActivity(c: Contact): string | undefined {
  const candidates = [
    c.email_last_opened_at,
    c.call_last_at,
    c.linkedin_last_at,
    c.tracking_last_activity_at,
  ].filter(Boolean) as string[];
  if (!candidates.length) return undefined;
  return candidates.sort()[candidates.length - 1];
}

export function personaChipStyle(personaType?: string): { bg: string; fg: string; border: string; label: string } {
  const t = (personaType || "").toLowerCase();
  if (t === "champion") return { bg: "#ecfdf5", fg: "#047857", border: "#a7f3d0", label: "Champion" };
  if (t === "buyer")    return { bg: "#eff6ff", fg: "#1d4ed8", border: "#bfdbfe", label: "Buyer" };
  if (t === "evaluator")return { bg: "#f5f3ff", fg: "#6d28d9", border: "#ddd6fe", label: "Evaluator" };
  if (t === "blocker")  return { bg: "#fef2f2", fg: "#b91c1c", border: "#fecaca", label: "Blocker" };
  return { bg: "#f1f5f9", fg: "#475569", border: "#e2e8f0", label: personaType || "Unknown" };
}

export function normalizeContactTableColumns(raw: string | null): ContactTableColumnKey[] {
  if (!raw) return DEFAULT_CONTACT_TABLE_COLUMNS;
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return DEFAULT_CONTACT_TABLE_COLUMNS;
    const allowed = new Set(CONTACT_TABLE_COLUMNS.map((column) => column.key));
    const next = parsed.filter((value): value is ContactTableColumnKey => typeof value === "string" && allowed.has(value as ContactTableColumnKey));
    if (!next.length) return DEFAULT_CONTACT_TABLE_COLUMNS;
    // Auto-include any NEW columns added to the app since this layout was saved
    // (e.g. "comments") so existing users see them without re-enabling manually.
    // New columns are inserted just before "action" so they don't land after the
    // row action buttons; if "action" isn't present they're appended.
    const present = new Set(next);
    const missing = CONTACT_TABLE_COLUMNS
      .map((column) => column.key as ContactTableColumnKey)
      .filter((key) => !present.has(key));
    if (!missing.length) return next;
    // "last_touch" gets its own placement rule (between SDR and Comments)
    // rather than the generic before-Action rule, since it reads naturally
    // beside the other per-rep reference columns.
    const lastTouchIdx = missing.indexOf("last_touch");
    let result = next;
    if (lastTouchIdx !== -1) {
      missing.splice(lastTouchIdx, 1);
      const commentsIdx = result.indexOf("comments");
      const sdrIdx = result.indexOf("sdr");
      const insertAt = commentsIdx !== -1 ? commentsIdx : sdrIdx !== -1 ? sdrIdx + 1 : result.length;
      result = [...result.slice(0, insertAt), "last_touch", ...result.slice(insertAt)];
    }
    if (!missing.length) return result;
    const actionIdx = result.indexOf("action");
    return actionIdx === -1
      ? [...result, ...missing]
      : [...result.slice(0, actionIdx), ...missing, ...result.slice(actionIdx)];
  } catch {
    return DEFAULT_CONTACT_TABLE_COLUMNS;
  }
}

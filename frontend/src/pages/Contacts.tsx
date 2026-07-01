import "./prospects-refresh.css";
import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { accountSourcingApi, activitiesApi, angelMappingApi, assignmentsApi, companiesApi, contactsApi, dealsApi, outreachApi, pushApi, remindersApi } from "../lib/api";
import { getCachedRolePermissions, getCachedUsers } from "../lib/cachedFetch";
import type { PreCallBrief, SequenceLifecycle, LifecycleSummary } from "../lib/api";
import type { Activity, Contact, AngelInvestor, AngelMapping, Company, RolePermissionsSettings, User } from "../types";
import { useAuth } from "../lib/AuthContext";
import { useToast } from "../lib/ToastContext";
import {
  Search, Users, CheckCircle2, XCircle, Sparkles, Trash2, AlertCircle, Loader2,
  Network, ChevronDown, ChevronRight, ExternalLink, Star, Plus, Link2,
  Building2, Target, Settings2, Phone, Upload, Download, MoreHorizontal,
  Mail, Clock, PhoneCall, Globe, X, AlertTriangle, ArrowLeftRight, EyeOff, GripVertical,
  Mic, ArrowRight, MessageCircle, MessageSquare, Send,
} from "lucide-react";
import { avatarColor, formatDomain, getInitials, gmailComposeUrl } from "../lib/utils";
import {
  CALL_DISPOSITION_OPTIONS,
  LINKEDIN_STATUS_OPTIONS,
  deriveSequenceStatusFromCallDisposition,
  deriveSequenceStatusFromLinkedinStatus,
  formatCallDisposition,
} from "../lib/prospectWorkflow";
import OutreachDrawer from "../components/outreach/OutreachDrawer";
import AssignDropdown from "../components/AssignDropdown";
import MultiSelectFilter from "../components/filters/MultiSelectFilter";
import RangeFilter from "../components/filters/RangeFilter";
import DateRangeFilter, { type DateRangeValue } from "../components/filters/DateRangeFilter";
import { Pagination } from "../components/ui/Pagination";
import TaskCenterModal from "../components/tasks/TaskCenterModal";
import AddProspectModal from "./contacts/AddProspectModal";
import SearchableCompanySelect from "../components/SearchableCompanySelect";
import { ANGEL_SURFACE, ANGEL_TEXT, PERSONA_LABEL, PERSONA_STYLE, STRENGTH_LABEL, STRENGTH_STYLE } from "./contacts/constants";
import { filterAngelMappings, getMissingCompanyKey, groupAngelMappingsByCompany } from "./contacts/utils";
import type { ProspectImportSummary, ProspectingTab } from "./contacts/types";
import { ProgressCell } from "./contacts/ProgressCell";
import { LifecycleDrawer } from "./contacts/LifecycleDrawer";
import { CallRecordingPanel, type AISuggestion, type CallRecordingPanelHandle } from "./contacts/CallRecordingPanel";
import { PreCallIntelPanel } from "./contacts/PreCallIntelPanel";
import { ProspectingTabButton } from "./contacts/ProspectingTabButton";
import { AngelOverviewCard, SnapshotRow, StrengthBadge } from "./contacts/AngelOverviewCard";

const PERSONA_FILTER_OPTIONS = [
  { value: "economic_buyer", label: "Economic Buyer" },
  { value: "champion", label: "Champion" },
  { value: "technical_evaluator", label: "Tech Evaluator" },
  { value: "unknown", label: "Unknown" },
];

const SEQUENCE_FILTER_OPTIONS = [
  { value: "research_needed", label: "Research Needed" },
  { value: "ready", label: "Ready" },
  { value: "queued_instantly", label: "Queued — Instantly" },
  { value: "sent", label: "Sent" },
  { value: "replied", label: "Replied" },
  { value: "meeting_booked", label: "Meeting Booked" },
];

const CALL_DISPOSITION_FILTER_OPTIONS = [
  { value: "unreviewed", label: "Unreviewed" },
  ...CALL_DISPOSITION_OPTIONS.map((o) => ({ value: o.value, label: o.label })),
];

const LINKEDIN_STATUS_FILTER_OPTIONS = [
  { value: "not_contacted", label: "Not contacted" },
  ...LINKEDIN_STATUS_OPTIONS.map((o) => ({ value: o.value, label: o.label })),
];

const SEARCH_SCOPE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "all", label: "All columns" },
  { value: "name", label: "Name" },
  { value: "email", label: "Email" },
  { value: "company", label: "Company" },
  { value: "title", label: "Title" },
  { value: "phone", label: "Phone" },
  { value: "linkedin", label: "LinkedIn" },
];

const EMAIL_FILTER_OPTIONS = [
  { value: "has_email", label: "Has email" },
  { value: "missing_email", label: "Missing email" },
  { value: "verified", label: "Verified" },
  { value: "unverified", label: "Unverified" },
];

function parseSearchParamList(value: string | null): string[] {
  if (!value) return [];
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

// Dispositions that imply a future touchpoint — for these we show a
// datetime picker so the rep can schedule the follow-up while the call is
// still fresh.
const FOLLOWUP_DISPOSITIONS = new Set<string>([
  "interested_follow_up_required",
  "call_back_later_rescheduled",
]);

// Progress-dot color filter options. Labels use unicode color circles so the
// dropdown reads the same way the prospect-row dots do. Values must match
// the backend's `call_outcome_color` / `email_outcome_color` enums in
// app/repositories/contact.py — change them together.
const CALL_OUTCOME_COLOR_OPTIONS: { value: string; label: string }[] = [
  { value: "yellow", label: "\u{1F7E1} Attempted (no outcome)" },
  { value: "blue",   label: "\u{1F535} Follow-up / callback" },
  { value: "green",  label: "\u{1F7E2} Meeting booked" },
  { value: "red",    label: "\u{1F534} Not interested / wrong number" },
];
const EMAIL_OUTCOME_COLOR_OPTIONS: { value: string; label: string }[] = [
  { value: "yellow", label: "\u{1F7E1} Sent (no open)" },
  { value: "blue",   label: "\u{1F535} Opened (no reply)" },
  { value: "green",  label: "\u{1F7E2} Positive reply / meeting" },
  { value: "red",    label: "\u{1F534} Negative reply" },
];
const CALL_ATTEMPTS_BUCKET_OPTIONS: { value: string; label: string }[] = [
  { value: "0",     label: "0 calls" },
  { value: "1",     label: "1 call" },
  { value: "2",     label: "2 calls" },
  { value: "3",     label: "3 calls" },
  { value: "4plus", label: "4+ calls" },
];

// Default follow-up datetime, expressed in the *user's local timezone*
// formatted for <input type="datetime-local">. Anchored to "tomorrow at
// 10:00 AM PST" — a fixed instant in time — so a PST rep sees "10:00 AM"
// and an IST rep sees the equivalent local time (e.g. "11:30 PM"). Using
// a fixed -8 offset (PST, not PDT) is deliberate: it's predictable, and
// the input is user-editable so a stale DST offset only costs one click
// to correct in March/November.
function defaultFollowupLocalString(): string {
  const now = new Date();
  const targetUtc = new Date(Date.UTC(
    now.getUTCFullYear(),
    now.getUTCMonth(),
    now.getUTCDate() + 1,
    18, // 10:00 AM PST == 18:00 UTC
    0,
    0,
  ));
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${targetUtc.getFullYear()}-${pad(targetUtc.getMonth() + 1)}-${pad(targetUtc.getDate())}T${pad(targetUtc.getHours())}:${pad(targetUtc.getMinutes())}`;
}

const CONTACT_TABLE_COLUMNS: Array<{ key: string; label: string; required?: boolean }> = [
  { key: "name", label: "Name", required: true },
  { key: "company", label: "Company", required: true },
  { key: "title", label: "Title" },
  { key: "email", label: "Email", required: true },
  { key: "progress", label: "Progress", required: true },
  { key: "comments", label: "Comments" },
  { key: "timezone", label: "Timezone" },
  { key: "ae", label: "AE" },
  { key: "sdr", label: "SDR" },
  { key: "action", label: "Action", required: true },
] as const;

type ContactTableColumnKey = typeof CONTACT_TABLE_COLUMNS[number]["key"];
const DEFAULT_CONTACT_TABLE_COLUMNS: ContactTableColumnKey[] = CONTACT_TABLE_COLUMNS.map((column) => column.key);

const TIMEZONE_OPTIONS = [
  "IST",
  "PST",
  "MST",
  "CST",
  "EST",
  "GMT",
  "CET",
  "EET",
  "GST",
  "SGT",
  "JST",
  "AEST",
] as const;

const TIMEZONE_LABELS: Record<string, string> = {
  "Asia/Kolkata": "IST",
  "Asia/Calcutta": "IST",
  "America/Los_Angeles": "PST",
  "America/Vancouver": "PST",
  "America/Denver": "MST",
  "America/Phoenix": "MST",
  "America/Chicago": "CST",
  "America/New_York": "EST",
  "America/Toronto": "EST",
  "Europe/London": "GMT",
  "Europe/Dublin": "GMT",
  "Europe/Berlin": "CET",
  "Europe/Paris": "CET",
  "Europe/Amsterdam": "CET",
  "Europe/Madrid": "CET",
  "Europe/Rome": "CET",
  "Europe/Athens": "EET",
  "Asia/Dubai": "GST",
  "Asia/Singapore": "SGT",
  "Asia/Manila": "SGT",
  "Asia/Tokyo": "JST",
  "Australia/Sydney": "AEST",
};

function formatTimezoneLabel(value?: string | null): string {
  if (!value) return "";
  return TIMEZONE_LABELS[value] ?? value.replace(/^.*\//, "").replace(/_/g, " ").toUpperCase();
}

// Expand short labels (e.g. "IST") into the matching IANA names
// (e.g. "Asia/Kolkata", "Asia/Calcutta") plus the label itself, so the
// backend's case-insensitive IN check matches contacts however their
// timezone happens to be stored.
function expandTimezoneFilter(labels: string[]): string[] {
  const set = new Set<string>();
  for (const label of labels) {
    set.add(label);
    for (const [iana, mapped] of Object.entries(TIMEZONE_LABELS)) {
      if (mapped === label) set.add(iana);
    }
  }
  return Array.from(set);
}

// Convert a `YYYY-MM-DD` date-filter value into a UTC ISO bound. `dayStartIso`
// anchors to local midnight (start of the rep's day) and `dayEndIso` to the
// last millisecond, so a single-day pick captures the whole day. The backend
// compares these against UTC-naive columns, so converting the local-day
// boundary to UTC ISO gives the rep "their day" semantics.
function dayStartIso(date: string): string | undefined {
  if (!date) return undefined;
  const d = new Date(`${date}T00:00:00`);
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}
function dayEndIso(date: string): string | undefined {
  if (!date) return undefined;
  const d = new Date(`${date}T23:59:59.999`);
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}

// Local `YYYY-MM-DD` for the date-range presets. Offsets are in days from
// today; getRange() is evaluated at render/click time so "Today" always means
// the actual current day rather than page-load day.
function localDateStr(offsetDays = 0): string {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// Forward-looking presets for the scheduled-follow-up filter.
const followupDuePresets: { label: string; getRange: () => DateRangeValue }[] = [
  { label: "Overdue", getRange: () => ({ from: "", to: localDateStr(-1) }) },
  { label: "Today", getRange: () => ({ from: localDateStr(0), to: localDateStr(0) }) },
  { label: "Next 7 days", getRange: () => ({ from: localDateStr(0), to: localDateStr(7) }) },
  { label: "Next 30 days", getRange: () => ({ from: localDateStr(0), to: localDateStr(30) }) },
];

// Backward-looking presets for the last-call filter.
const lastCallPresets: { label: string; getRange: () => DateRangeValue }[] = [
  { label: "Today", getRange: () => ({ from: localDateStr(0), to: localDateStr(0) }) },
  { label: "Last 7 days", getRange: () => ({ from: localDateStr(-7), to: localDateStr(0) }) },
  { label: "Last 30 days", getRange: () => ({ from: localDateStr(-30), to: localDateStr(0) }) },
];

function relativeTimeShort(iso?: string | null): string {
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

function latestProspectActivity(c: Contact): string | undefined {
  const candidates = [
    c.email_last_opened_at,
    c.call_last_at,
    c.linkedin_last_at,
    c.tracking_last_activity_at,
  ].filter(Boolean) as string[];
  if (!candidates.length) return undefined;
  return candidates.sort()[candidates.length - 1];
}

function personaChipStyle(personaType?: string): { bg: string; fg: string; border: string; label: string } {
  const t = (personaType || "").toLowerCase();
  if (t === "champion") return { bg: "#ecfdf5", fg: "#047857", border: "#a7f3d0", label: "Champion" };
  if (t === "buyer")    return { bg: "#eff6ff", fg: "#1d4ed8", border: "#bfdbfe", label: "Buyer" };
  if (t === "evaluator")return { bg: "#f5f3ff", fg: "#6d28d9", border: "#ddd6fe", label: "Evaluator" };
  if (t === "blocker")  return { bg: "#fef2f2", fg: "#b91c1c", border: "#fecaca", label: "Blocker" };
  return { bg: "#f1f5f9", fg: "#475569", border: "#e2e8f0", label: personaType || "Unknown" };
}

function normalizeContactTableColumns(raw: string | null): ContactTableColumnKey[] {
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
    const actionIdx = next.indexOf("action");
    return actionIdx === -1
      ? [...next, ...missing]
      : [...next.slice(0, actionIdx), ...missing, ...next.slice(actionIdx)];
  } catch {
    return DEFAULT_CONTACT_TABLE_COLUMNS;
  }
}

export default function Contacts() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  // Filter hydration source. URL params WIN when present (bookmarks/shared
  // links keep working); otherwise fall back to the last-saved filters in
  // localStorage so returning via the left-nav link or a detail "back" button
  // (which land on the BARE path with no query string) restores the view
  // instead of resetting everything. Computed once at mount.
  const initParams = useMemo(() => {
    const FILTER_KEYS = ["q", "qf", "qm", "sb", "seq", "call", "li", "cc", "ec", "ca", "fcmin", "fcmax", "nfa", "nfb", "cla", "clb", "owner", "ae", "sdr", "own", "tz", "co", "pg", "tab"];
    const hasAny = FILTER_KEYS.some((k) => searchParams.has(k));
    if (hasAny) return searchParams;
    try {
      const saved = localStorage.getItem("crm.prospecting.filters");
      if (saved) return new URLSearchParams(saved);
    } catch {
      /* ignore */
    }
    return searchParams;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const { isAdmin, user } = useAuth();
  // SDRs are scoped to their own prospects everywhere (backend now returns only
  // own-assigned for this role). The owner-scope control's "All reps" /
  // "Unassigned" views would be misleading (they'd return only the SDR's own),
  // so for non-admin SDRs we force scope to "mine" and lock the toggle.
  const isSdrLocked = !isAdmin && user?.role === "sdr";
  const toast = useToast();
  const [tab, setTab] = useState<ProspectingTab>("contacts");
  const pageSize = 50;

  // ── Contacts state — initialised from URL so filters survive navigation ──
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [search, setSearch] = useState(() => initParams.get("q") ?? "");
  const [searchScope, setSearchScope] = useState<string>(() => initParams.get("qf") ?? "all");
  // Match mode for scoped/bulk search: "contains" treats each pasted entry as
  // a substring; "exact" requires whole-cell equality (case-insensitive).
  // URL-persisted as `qm`; only sent when scope !== "all".
  const [searchMatch, setSearchMatch] = useState<"contains" | "exact">(() => (initParams.get("qm") === "exact" ? "exact" : "contains"));
  // Explicit sort. Server-side so it covers the full dataset, not just the
  // visible page. URL-persisted as `sb`/`sd` so deep-linked alphabetised
  // views survive navigation.
  type ProspectSortKey = "recent" | "name_asc" | "name_desc" | "company_asc" | "company_desc";
  const PROSPECT_SORT_OPTIONS: Array<{ value: ProspectSortKey; label: string }> = [
    { value: "recent", label: "Newest first" },
    { value: "name_asc", label: "Name A → Z" },
    { value: "name_desc", label: "Name Z → A" },
    { value: "company_asc", label: "Company A → Z" },
    { value: "company_desc", label: "Company Z → A" },
  ];
  const [prospectSort, setProspectSort] = useState<ProspectSortKey>(() => {
    const raw = initParams.get("sb") ?? "recent";
    return (PROSPECT_SORT_OPTIONS.some((o) => o.value === raw) ? raw : "recent") as ProspectSortKey;
  });
  const sortToApi = (s: ProspectSortKey): { sortBy?: "name" | "company"; sortDir?: "asc" | "desc" } => {
    if (s === "name_asc") return { sortBy: "name", sortDir: "asc" };
    if (s === "name_desc") return { sortBy: "name", sortDir: "desc" };
    if (s === "company_asc") return { sortBy: "company", sortDir: "asc" };
    if (s === "company_desc") return { sortBy: "company", sortDir: "desc" };
    return {};
  };
  const [showFilters, setShowFilters] = useState<boolean>(() => {
    // Auto-open the filter card on mount when the URL already carries active
    // filters — otherwise users would have to hunt for the toggle to discover
    // why the list is narrowed.
    return Boolean(
      initParams.get("seq") || initParams.get("call") || initParams.get("ae") ||
      initParams.get("sdr") || initParams.get("own") || initParams.get("tz") ||
      initParams.get("co") || initParams.get("owner") === "mine" ||
      initParams.get("cc") || initParams.get("ec") || initParams.get("ca") ||
      initParams.get("fcmin") || initParams.get("fcmax") ||
      initParams.get("nfa") || initParams.get("nfb") ||
      initParams.get("cla") || initParams.get("clb")
    );
  });
  const [personaFilter, setPersonaFilter] = useState<string[]>([]);
  const [sequenceFilter, setSequenceFilter] = useState<string[]>(() => parseSearchParamList(initParams.get("seq")));
  const [callDispositionFilter, setCallDispositionFilter] = useState<string[]>(() => parseSearchParamList(initParams.get("call")));
  const [linkedinStatusFilter, setLinkedinStatusFilter] = useState<string[]>(() => parseSearchParamList(initParams.get("li")));
  // Progress-dot color filters. Map 1:1 to the dot colors rendered by
  // `ProgressCell`. URL keys: `cc` (call color), `ec` (email color), `ca`
  // (call attempts bucket). The backend translates colors to disposition /
  // sequence_status / count buckets — see app/repositories/contact.py.
  const [callOutcomeColorFilter, setCallOutcomeColorFilter] = useState<string[]>(() => parseSearchParamList(initParams.get("cc")));
  const [emailOutcomeColorFilter, setEmailOutcomeColorFilter] = useState<string[]>(() => parseSearchParamList(initParams.get("ec")));
  const [callAttemptsBucketFilter, setCallAttemptsBucketFilter] = useState<string[]>(() => parseSearchParamList(initParams.get("ca")));
  // Follow-up count range (calls logged). URL keys: `fcmin` / `fcmax`. Either
  // bound may be null (open-ended). Backend maps these to call_attempt_min/max.
  const parseCountParam = (raw: string | null): number | null => {
    if (raw == null || raw.trim() === "") return null;
    const n = Number(raw);
    return Number.isFinite(n) && n >= 0 ? Math.floor(n) : null;
  };
  const [followupCountMin, setFollowupCountMin] = useState<number | null>(() => parseCountParam(initParams.get("fcmin")));
  const [followupCountMax, setFollowupCountMax] = useState<number | null>(() => parseCountParam(initParams.get("fcmax")));
  // Date-range filters. `nextFollowupRange` filters on the rep-scheduled
  // callback (next_followup_at); `callLastRange` on the last call (call_last_at).
  // Values are `YYYY-MM-DD`; "" means unbounded. URL keys: nfa/nfb, cla/clb.
  const [nextFollowupRange, setNextFollowupRange] = useState<DateRangeValue>(() => ({
    from: initParams.get("nfa") ?? "",
    to: initParams.get("nfb") ?? "",
  }));
  const [callLastRange, setCallLastRange] = useState<DateRangeValue>(() => ({
    from: initParams.get("cla") ?? "",
    to: initParams.get("clb") ?? "",
  }));
  const [emailFilter, setEmailFilter] = useState<string[]>([]);
  // Client-side filter wired to the engagement KPI tiles. Clicking a tile
  // narrows the already-loaded prospect page to the matching population —
  // the same scope the tiles count. null = show everyone.
  const [cardFilter, setCardFilter] = useState<"calls_today" | "emails" | "linkedin" | "meetings" | null>(null);
  const [ownerScope, setOwnerScope] = useState<"all" | "mine">(() =>
    // SDRs only ever see their own prospects — force "mine" on load regardless
    // of any persisted ?owner= param.
    isSdrLocked || initParams.get("owner") === "mine" ? "mine" : "all"
  );
  const [aeFilter, setAeFilter] = useState<string[]>(() => parseSearchParamList(initParams.get("ae")));
  const [sdrFilter, setSdrFilter] = useState<string[]>(() => parseSearchParamList(initParams.get("sdr")));
  // Owner filter — multi-select that matches AE OR SDR ownership for any
  // selected user. Different from ownerScope (binary "mine vs all") and from
  // aeFilter/sdrFilter (role-specific). Sent to backend via owner_id +
  // scope_any_match=true so a single user_id matches contacts they own as
  // either AE or SDR.
  const [ownerFilter, setOwnerFilter] = useState<string[]>(() => parseSearchParamList(initParams.get("own")));
  // Timezone filter — values are short labels (IST, PST, etc.). When sent to
  // the backend they're expanded to include matching IANA names from
  // TIMEZONE_LABELS so a contact stored as "Asia/Kolkata" matches "IST".
  const [timezoneFilter, setTimezoneFilter] = useState<string[]>(() => parseSearchParamList(initParams.get("tz")));
  // Company filter — optional narrowing to a single company's prospects.
  // Backend's contacts list already accepts `company_id`; this just wires a
  // dropdown to it. Value is a single company UUID (or "" for all).
  const [companyFilter, setCompanyFilter] = useState<string>(() => initParams.get("co") ?? "");
  const [companyOptions, setCompanyOptions] = useState<Company[]>([]);
  const [teamUsers, setTeamUsers] = useState<User[]>([]);
  const [debouncedSearch, setDebouncedSearch] = useState(() => initParams.get("q") ?? "");
  const [page, setPage] = useState(() => parseInt(initParams.get("pg") ?? "1", 10) || 1);
  const [contactsTotal, setContactsTotal] = useState(0);
  const [contactsPages, setContactsPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [selectedContact, setSelectedContact] = useState<Contact | null>(null);
  const [resetting, setResetting] = useState(false);
  const [openActionsId, setOpenActionsId] = useState<string | null>(null);
  const [taskContact, setTaskContact] = useState<Contact | null>(null);
  const [selectedContactIds, setSelectedContactIds] = useState<Set<string>>(() => new Set());
  const [bulkClaimingSdr, setBulkClaimingSdr] = useState(false);
  const [deletingContacts, setDeletingContacts] = useState(false);
  // Admin bulk SDR reassignment for selected prospects (e.g. splitting an
  // account's prospects across SDRs by timezone).
  const [assignableUsers, setAssignableUsers] = useState<Array<{ id: string; name?: string | null; role: string }>>([]);
  const [bulkAssigningSdr, setBulkAssigningSdr] = useState(false);
  // Bulk "start campaign" — enroll the selected prospects into an existing Instantly campaign.
  const [campaignModalOpen, setCampaignModalOpen] = useState(false);
  const [campaignOptions, setCampaignOptions] = useState<{ id: string; name: string }[]>([]);
  const [campaignOptionsLoading, setCampaignOptionsLoading] = useState(false);
  const [selectedCampaignId, setSelectedCampaignId] = useState("");
  const [startingCampaign, setStartingCampaign] = useState(false);
  // Bulk "add follow-up" — set a per-contact Reminder on every selected
  // prospect so it surfaces on each of their detail pages. `bulkFollowupAt` is
  // a naive datetime-local string (rep's local time); `bulkFollowupNote` is an
  // optional note stored on each reminder.
  const [bulkFollowupOpen, setBulkFollowupOpen] = useState(false);
  const [bulkFollowupAt, setBulkFollowupAt] = useState("");
  const [bulkFollowupNote, setBulkFollowupNote] = useState("");
  const [bulkFollowupSaving, setBulkFollowupSaving] = useState(false);
  const [callContact, setCallContact] = useState<Contact | null>(null);
  // Pre-dial countdown. When a rep hits Call we open the drawer immediately but
  // hold the actual dial for 10s so they can prep (or cancel). `dialCountdown`
  // is the seconds remaining (null = not counting); `dialTimerRef` holds the
  // interval id so Stop / Call-now / drawer-close can cancel it.
  const [dialCountdown, setDialCountdown] = useState<number | null>(null);
  const dialTimerRef = useRef<number | null>(null);
  // Imperative handle to the recording panel so the countdown can auto-start
  // recording when it elapses.
  const callRecordingRef = useRef<CallRecordingPanelHandle | null>(null);
  // Captured scrollTop of the page scroll container (.crm-content, rendered by
  // Layout) so we can restore the rep's place in a long prospect list after the
  // call drawer closes. Queried via the DOM since the element lives outside
  // this component's tree.
  const restoreScrollRef = useRef<number | null>(null);
  // Monotonic request id for loadContacts: only the latest in-flight request
  // is allowed to write state (see loadContacts for the race it guards).
  const loadSeqRef = useRef(0);
  // Guards the "reset to page 1 on filter change" effect so it does NOT fire on
  // the initial mount. Without this, returning from a prospect detail (remount)
  // would clobber the `pg` restored from the URL/localStorage back to page 1 —
  // the rep lands on a different slice of prospects and the list appears to
  // "shuffle". We only want to reset the page for genuine post-mount filter
  // changes, so the very first run (mount) is skipped.
  const pageResetMountedRef = useRef(false);
  // Angel-mapping data is fetched lazily the first time that tab opens.
  const angelsLoadedRef = useRef(false);
  const [callDisposition, setCallDisposition] = useState("");
  const [callNotes, setCallNotes] = useState("");
  // Id of the recording attached to the in-progress call disposition,
  // if any. Threaded into the Activity row's metadata on save so the
  // lifecycle drawer can later surface the transcript.
  const [currentRecordingId, setCurrentRecordingId] = useState<string | null>(null);
  const [callStatus, setCallStatus] = useState("attempted");
  const [savingDisposition, setSavingDisposition] = useState(false);
  // Follow-up scheduling — only visible when the disposition is a callback
  // / follow-up. Default = "tomorrow 10:00 AM PST" expressed in the rep's
  // local time, so the <input type="datetime-local"> shows a friendly value.
  const [followupAt, setFollowupAt] = useState<string>("");
  const [precallBrief, setPrecallBrief] = useState<PreCallBrief | null>(null);
  const [precallLoading, setPrecallLoading] = useState(false);
  // Cadence lifecycle: compact summary per-row, full detail in the drawer.
  const [lifecycleSummaries, setLifecycleSummaries] = useState<Record<string, LifecycleSummary>>({});
  const [lifecycleContactId, setLifecycleContactId] = useState<string | null>(null);
  const [lifecycleDetail, setLifecycleDetail] = useState<SequenceLifecycle | null>(null);
  const [lifecycleLoading, setLifecycleLoading] = useState(false);
  const [linkedinContact, setLinkedinContact] = useState<Contact | null>(null);
  const [linkedinStatus, setLinkedinStatus] = useState("sent");
  const [linkedinNotes, setLinkedinNotes] = useState("");
  const [savingLinkedin, setSavingLinkedin] = useState(false);
  const [linkedinSuggestion, setLinkedinSuggestion] = useState<string | null>(null);
  const [linkedinSuggestionLoading, setLinkedinSuggestionLoading] = useState(false);
  const [linkedinSuggestionCopied, setLinkedinSuggestionCopied] = useState(false);
  const [whatsappContact, setWhatsappContact] = useState<Contact | null>(null);
  const [whatsappOutcome, setWhatsappOutcome] = useState("sent");
  const [whatsappNotes, setWhatsappNotes] = useState("");
  const [savingWhatsapp, setSavingWhatsapp] = useState(false);
  const [commentsContact, setCommentsContact] = useState<Contact | null>(null);
  const [commentsList, setCommentsList] = useState<Activity[]>([]);
  const [commentsLoading, setCommentsLoading] = useState(false);
  const [commentDraft, setCommentDraft] = useState("");
  const [savingComment, setSavingComment] = useState(false);
  const [uploadingProspects, setUploadingProspects] = useState(false);
  // Upload progress UI: phase = "uploading" while bytes are on the wire,
  // "processing" once they're all delivered and the server is parsing.
  // percent applies to the "uploading" phase only; "processing" is rendered
  // as an indeterminate stripe.
  const [uploadProgress, setUploadProgress] = useState<{ phase: "uploading" | "processing"; percent: number } | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [rolePermissions, setRolePermissions] = useState<RolePermissionsSettings | null>(null);
  const [columnMenuOpen, setColumnMenuOpen] = useState(false);
  // Rep-scoped count of calls the logged-in user has created since local
  // midnight. Replaces the broken page-bounded contact-derived counter that
  // would show 0 the moment pagination rotated past the just-called rows.
  const [myCallsTodayCount, setMyCallsTodayCount] = useState(0);
  const [tableColumns, setTableColumns] = useState<ContactTableColumnKey[]>(() => normalizeContactTableColumns(localStorage.getItem("crm.contacts.tableColumns")));
  const [draggedColumn, setDraggedColumn] = useState<ContactTableColumnKey | null>(null);
  const [editingTimezoneId, setEditingTimezoneId] = useState<string | null>(null);
  const [timezoneDraft, setTimezoneDraft] = useState("");
  const [savingTimezoneId, setSavingTimezoneId] = useState<string | null>(null);
  const [importSummary, setImportSummary] = useState<ProspectImportSummary | null>(null);
  const [creatingMissingCompanies, setCreatingMissingCompanies] = useState(false);
  const [enrichingMissingKey, setEnrichingMissingKey] = useState<string | null>(null);

  useEffect(() => {
    localStorage.setItem("crm.contacts.tableColumns", JSON.stringify(tableColumns));
  }, [tableColumns]);

  const visibleColumns = useMemo(
    () => tableColumns
      .map((key) => CONTACT_TABLE_COLUMNS.find((column) => column.key === key))
      .filter((column): column is typeof CONTACT_TABLE_COLUMNS[number] => Boolean(column)),
    [tableColumns],
  );
  const visibleCompanySelectionOptions = useMemo(() => {
    const byCompany = new Map<string, { id: string; name: string; count: number }>();
    for (const contact of contacts) {
      if (!contact.company_id) continue;
      const current = byCompany.get(contact.company_id);
      if (current) {
        current.count += 1;
      } else {
        byCompany.set(contact.company_id, {
          id: contact.company_id,
          name: contact.company_name || "Unnamed company",
          count: 1,
        });
      }
    }
    return Array.from(byCompany.values()).sort((a, b) => a.name.localeCompare(b.name));
  }, [contacts]);
  const allVisibleSelected = contacts.length > 0 && contacts.every((contact) => selectedContactIds.has(contact.id));
  // Any signed-in user may select + delete prospects (single or bulk).
  const canSelectProspects = !!user;

  const columnMenuItems = useMemo(() => {
    const ordered = tableColumns
      .map((key) => CONTACT_TABLE_COLUMNS.find((column) => column.key === key))
      .filter((column): column is typeof CONTACT_TABLE_COLUMNS[number] => Boolean(column));
    const hidden = CONTACT_TABLE_COLUMNS.filter((column) => !tableColumns.includes(column.key as ContactTableColumnKey));
    return [...ordered, ...hidden];
  }, [tableColumns]);

  const moveTableColumn = (key: ContactTableColumnKey, direction: -1 | 1) => {
    setTableColumns((current) => {
      const index = current.indexOf(key);
      if (index < 0) return current;
      const target = index + direction;
      if (target < 0 || target >= current.length) return current;
      const next = [...current];
      const [column] = next.splice(index, 1);
      next.splice(target, 0, column);
      return next;
    });
  };

  const moveTableColumnTo = (sourceKey: ContactTableColumnKey, targetKey: ContactTableColumnKey) => {
    if (sourceKey === targetKey) return;
    setTableColumns((current) => {
      const sourceIndex = current.indexOf(sourceKey);
      const targetIndex = current.indexOf(targetKey);
      if (sourceIndex < 0 || targetIndex < 0) return current;
      const next = [...current];
      const [column] = next.splice(sourceIndex, 1);
      next.splice(targetIndex, 0, column);
      return next;
    });
  };

  const toggleTableColumn = (key: ContactTableColumnKey) => {
    setTableColumns((current) => {
      const next = current.includes(key)
        ? current.filter((item) => item !== key)
        : [...current, key];
      return next.length === 0 ? current : next;
    });
  };

  const saveTimezone = async (contact: Contact, nextTimezone: string) => {
    const normalized = nextTimezone.trim();
    setSavingTimezoneId(contact.id);
    try {
      // Send null (not undefined) to actually clear — undefined is dropped by
      // JSON.stringify and ignored by the backend (exclude_unset), and an absent
      // timezone also triggers server-side re-inference.
      const updated = await contactsApi.update(contact.id, { timezone: normalized || null } as never);
      setContacts((current) => current.map((item) => item.id === contact.id ? { ...item, timezone: updated.timezone } : item));
      toast.success("Timezone updated.", "Prospect saved");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not update timezone.", "Save failed");
    } finally {
      setSavingTimezoneId(null);
      setEditingTimezoneId(null);
      setTimezoneDraft("");
    }
  };

  // ── Angel mapping state ──────────────────────────────────────────────
  const [mappings, setMappings] = useState<AngelMapping[]>([]);
  const [investors, setInvestors] = useState<AngelInvestor[]>([]);
  const [angelLoading, setAngelLoading] = useState(true);
  const [expandedCompany, setExpandedCompany] = useState<string | null>(null);
  const [angelSearch, setAngelSearch] = useState("");
  const [filterStrength, setFilterStrength] = useState<number>(0);
  const [showAddInvestor, setShowAddInvestor] = useState(false);
  const [newInvestor, setNewInvestor] = useState({ name: "", current_role: "", current_company: "" });
  const [showAddProspect, setShowAddProspect] = useState(false);
  const [aircallEnabled, setAircallEnabled] = useState<boolean>(() => localStorage.getItem("crm.aircall.enabled") === "true");
  const toggleAircall = () => {
    const next = !aircallEnabled;
    setAircallEnabled(next);
    localStorage.setItem("crm.aircall.enabled", next ? "true" : "false");
    window.dispatchEvent(new Event("crm:aircall:toggle"));
  };
  const canMigrateProspects =
    isAdmin || Boolean(user && user.role !== "admin" && rolePermissions?.[user.role]?.prospect_migration);

  useEffect(() => {
    if (searchParams.get("new") !== "prospect") return;
    setTab("contacts");
    setShowAddProspect(true);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("new");
      return next;
    }, { replace: true });
  }, [searchParams, setSearchParams]);

  const loadMyCallsToday = async () => {
    if (!user?.id) {
      setMyCallsTodayCount(0);
      return;
    }
    try {
      const total = await activitiesApi.myCallsToday();
      setMyCallsTodayCount(total);
    } catch {
      setMyCallsTodayCount(0);
    }
  };

  const loadContacts = (opts?: { silent?: boolean }) => {
    // A filter change fires this twice (once with the old page still in the
    // closure, once after the page-reset effect runs), so two requests race.
    // The sequence counter lets only the latest response write state.
    const seq = ++loadSeqRef.current;
    // Silent reloads skip the loading state so the table stays mounted — this
    // is what lets us preserve scroll position after the call drawer closes
    // (the loading flash would otherwise unmount the list and reset scroll).
    if (!opts?.silent) setLoading(true);
    contactsApi.searchPaginated({
      skip: (page - 1) * pageSize,
      limit: pageSize,
      q: debouncedSearch || undefined,
      qField: searchScope && searchScope !== "all" ? searchScope : undefined,
      qMatch: searchScope && searchScope !== "all" ? searchMatch : undefined,
      ...sortToApi(prospectSort),
      companyId: companyFilter || undefined,
      persona: personaFilter.length ? personaFilter : undefined,
      sequenceStatus: sequenceFilter.length ? sequenceFilter : undefined,
      callDisposition: callDispositionFilter.length ? callDispositionFilter : undefined,
      linkedinStatus: linkedinStatusFilter.length ? linkedinStatusFilter : undefined,
      callOutcomeColor: callOutcomeColorFilter.length ? callOutcomeColorFilter : undefined,
      emailOutcomeColor: emailOutcomeColorFilter.length ? emailOutcomeColorFilter : undefined,
      callAttemptsBucket: callAttemptsBucketFilter.length ? callAttemptsBucketFilter : undefined,
      followupCountMin: followupCountMin ?? undefined,
      followupCountMax: followupCountMax ?? undefined,
      nextFollowupAfter: dayStartIso(nextFollowupRange.from),
      nextFollowupBefore: dayEndIso(nextFollowupRange.to),
      callLastAfter: dayStartIso(callLastRange.from),
      callLastBefore: dayEndIso(callLastRange.to),
      aeId: aeFilter.length ? aeFilter : undefined,
      sdrId: sdrFilter.length ? sdrFilter : undefined,
      // Owner filter: any selected user matches contacts they own as AE OR SDR.
      // ownerScope === "mine" still wins when set; otherwise the multi-select drives.
      // Search is intentionally global: reps often search for a specific
      // prospect before claiming/owning them, so do not hide exact matches
      // behind the "My list" ownership filter.
      ownerId: ownerScope === "mine" && !debouncedSearch
        ? user?.id
        : (ownerFilter.length ? ownerFilter : undefined),
      timezone: timezoneFilter.length ? expandTimezoneFilter(timezoneFilter) : undefined,
      prospectOnly: true,
    }).then((result) => {
      // A newer request superseded this one — drop the stale response.
      if (seq !== loadSeqRef.current) return;
      setContacts(result.items);
      setContactsTotal(result.total);
      setContactsPages(result.pages);
      // On a silent reload, put the list back where the rep left it once the
      // new rows have painted (rAF waits for the post-state-update layout).
      if (opts?.silent && restoreScrollRef.current != null) {
        const target = restoreScrollRef.current;
        restoreScrollRef.current = null;
        requestAnimationFrame(() => {
          const scroller = document.querySelector<HTMLElement>(".crm-content");
          if (scroller) scroller.scrollTop = target;
        });
      }
    }).catch((error) => {
      if (seq !== loadSeqRef.current) return;
      toast.error(error instanceof Error ? error.message : "Failed to load prospects.", "Load failed");
    }).finally(() => {
      // Only the latest request controls the spinner; this also recovers if a
      // superseded non-silent request left loading=true behind.
      if (seq === loadSeqRef.current) setLoading(false);
    });
  };

  const downloadProspectTemplate = () => {
    const template = [
      // SDR / AE are OPTIONAL ownership columns: put a teammate's email or full
      // name to assign the prospect to them. Leave blank to assign it to you
      // (the uploader). Email is the most reliable; a name must match one
      // teammate exactly.
      ["Company Name", "Domain", "First Name", "Last Name", "Title", "Email", "LinkedIn URL", "Mobile Phone", "Direct Phone", "SDR", "AE"],
      ["BlackLine", "blackline.com", "Victoria", "Subbotina", "Director of Professional Services", "victoria.subbotina@blackline.com", "https://linkedin.com/in/victoriasubbotina", "+1 513-533-0040", "+1 513-533-0199", "mahesh@beacon.li", "Pravalika Jamalpur"],
    ]
      .map((row) => row.join(","))
      .join("\n");
    const blob = new Blob([template], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "beacon-prospect-upload-template.csv";
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const handleProspectUpload = async (file: File) => {
    setUploadingProspects(true);
    setUploadError(null);
    setUploadProgress({ phase: "uploading", percent: 0 });
    try {
      const result = await contactsApi.importCsv(
        file,
        isAdmin, // only admins auto-create accounts on import; AE/SDR add prospects only — unmatched rows are flagged for an admin to add the account
        (phase, percent) => setUploadProgress({ phase, percent }),
      );
      setImportSummary(result);
      setPage(1);
      loadContacts();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Failed to import prospects");
    } finally {
      setUploadingProspects(false);
      setUploadProgress(null);
    }
  };

  const removeMissingCompanyFromSummary = (name: string, domain?: string) => {
    setImportSummary((current) => {
      if (!current) return current;
      const nextMissing = current.missing_companies.filter(
        (company) => !(company.name === name && (company.domain || "") === (domain || ""))
      );
      return {
        ...current,
        missing_company_count: nextMissing.length,
        missing_companies: nextMissing,
      };
    });
  };

  const handleEnrichMissingCompany = async (company: { name: string; domain?: string }) => {
    const shouldEnrich = window.confirm(
      `Beacon already created a placeholder account for ${company.name}. Do you want to start enrichment now?`
    );
    if (!shouldEnrich) return;

    const key = getMissingCompanyKey(company);
    setEnrichingMissingKey(key);
    try {
      await accountSourcingApi.createManualCompany({
        name: company.name,
        domain: company.domain,
      });
      removeMissingCompanyFromSummary(company.name, company.domain);
      window.alert(`${company.name} was queued for enrichment.`);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Failed to queue company enrichment");
    } finally {
      setEnrichingMissingKey(null);
    }
  };

  const handleCreateMissingCompanies = async () => {
    if (!importSummary?.missing_companies.length) return;
    const shouldEnrich = window.confirm(
      `Beacon created ${importSummary.missing_company_count} placeholder compan${importSummary.missing_company_count === 1 ? "y" : "ies"}. Do you want to start enrichment for ${importSummary.missing_company_count === 1 ? "it" : "them"} now?`
    );
    if (!shouldEnrich) return;

    setCreatingMissingCompanies(true);
    try {
      for (const company of importSummary.missing_companies) {
        await accountSourcingApi.createManualCompany({
          name: company.name,
          domain: company.domain,
        });
      }
      setImportSummary((current) =>
        current
          ? { ...current, missing_company_count: 0, missing_companies: [] }
          : current
      );
      window.alert("The placeholder companies were queued for enrichment.");
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Failed to queue missing companies");
    } finally {
      setCreatingMissingCompanies(false);
    }
  };

  const loadAngels = () => {
    setAngelLoading(true);
    Promise.all([
      angelMappingApi.listMappings(),
      angelMappingApi.listInvestors(),
    ]).then(([m, inv]) => {
      setMappings(m);
      setInvestors(inv);
      setAngelLoading(false);
    }).catch(() => setAngelLoading(false));
  };

  // Angel-mapping data only renders on that tab, so fetch it lazily the first
  // time the tab becomes active instead of on every prospecting-page mount.
  useEffect(() => {
    if (tab !== "angel-mapping" || angelsLoadedRef.current) return;
    angelsLoadedRef.current = true;
    loadAngels();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  useEffect(() => {
    getCachedRolePermissions().then(setRolePermissions).catch(() => setRolePermissions(null));
  }, []);

  useEffect(() => {
    getCachedUsers()
      .then((users) => {
        setTeamUsers(
          users
            .filter((u) => u.is_active)
            .sort((left, right) => (left.name || left.email).localeCompare(right.name || right.email))
        );
      })
      .catch(() => setTeamUsers([]));
  }, []);

  // Seed the company filter with common CRM companies; the searchable selector
  // also loads the larger CRM + Account Sourcing catalog when opened.
  useEffect(() => {
    companiesApi
      .list(0, 500)
      .then((rows) => {
        const opts = rows
          .filter((company) => company.id && (company.name || company.domain))
          .sort((a, b) => a.name.localeCompare(b.name));
        setCompanyOptions(opts);
      })
      .catch(() => setCompanyOptions([]));
  }, []);

  useEffect(() => {
    setTab(location.pathname === "/angel-mapping" ? "angel-mapping" : "contacts");
  }, [location.pathname]);

  // Sync all filter state into URL so navigating away and back restores position
  // Guard: SDRs are scoped to their own prospects everywhere. Snap the
  // owner-scope back to "mine" if anything (stale URL/localStorage param,
  // future code path) ever sets it to "all" for a locked SDR.
  useEffect(() => {
    if (isSdrLocked && ownerScope !== "mine") setOwnerScope("mine");
  }, [isSdrLocked, ownerScope]);

  useEffect(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      search.trim() ? next.set("q", search.trim()) : next.delete("q");
      searchScope && searchScope !== "all" ? next.set("qf", searchScope) : next.delete("qf");
      searchScope && searchScope !== "all" && searchMatch === "exact" ? next.set("qm", "exact") : next.delete("qm");
      prospectSort !== "recent" ? next.set("sb", prospectSort) : next.delete("sb");
      ownerScope === "mine" ? next.set("owner", "mine") : next.delete("owner");
      sequenceFilter.length ? next.set("seq", sequenceFilter.join(",")) : next.delete("seq");
      callDispositionFilter.length ? next.set("call", callDispositionFilter.join(",")) : next.delete("call");
      linkedinStatusFilter.length ? next.set("li", linkedinStatusFilter.join(",")) : next.delete("li");
      callOutcomeColorFilter.length ? next.set("cc", callOutcomeColorFilter.join(",")) : next.delete("cc");
      emailOutcomeColorFilter.length ? next.set("ec", emailOutcomeColorFilter.join(",")) : next.delete("ec");
      callAttemptsBucketFilter.length ? next.set("ca", callAttemptsBucketFilter.join(",")) : next.delete("ca");
      followupCountMin != null ? next.set("fcmin", String(followupCountMin)) : next.delete("fcmin");
      followupCountMax != null ? next.set("fcmax", String(followupCountMax)) : next.delete("fcmax");
      nextFollowupRange.from ? next.set("nfa", nextFollowupRange.from) : next.delete("nfa");
      nextFollowupRange.to ? next.set("nfb", nextFollowupRange.to) : next.delete("nfb");
      callLastRange.from ? next.set("cla", callLastRange.from) : next.delete("cla");
      callLastRange.to ? next.set("clb", callLastRange.to) : next.delete("clb");
      aeFilter.length ? next.set("ae", aeFilter.join(",")) : next.delete("ae");
      sdrFilter.length ? next.set("sdr", sdrFilter.join(",")) : next.delete("sdr");
      ownerFilter.length ? next.set("own", ownerFilter.join(",")) : next.delete("own");
      timezoneFilter.length ? next.set("tz", timezoneFilter.join(",")) : next.delete("tz");
      companyFilter ? next.set("co", companyFilter) : next.delete("co");
      page > 1 ? next.set("pg", String(page)) : next.delete("pg");
      // The active "tab" here is route-driven (angel-mapping is its own route),
      // so the route itself already persists which view is shown — we only
      // persist the prospect-list filters. Persist the same params to
      // localStorage so a bare-path return (left-nav / detail back button) can
      // rehydrate them via initParams. URL still wins when present.
      try {
        localStorage.setItem("crm.prospecting.filters", next.toString());
      } catch {
        /* ignore */
      }
      return next;
    }, { replace: true });
  }, [aeFilter, callDispositionFilter, linkedinStatusFilter, callOutcomeColorFilter, emailOutcomeColorFilter, callAttemptsBucketFilter, followupCountMin, followupCountMax, nextFollowupRange.from, nextFollowupRange.to, callLastRange.from, callLastRange.to, companyFilter, ownerFilter, ownerScope, page, sdrFilter, search, searchScope, searchMatch, sequenceFilter, timezoneFilter, prospectSort, setSearchParams]);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setDebouncedSearch(search.trim());
    }, 250);
    return () => window.clearTimeout(handle);
  }, [search]);

  useEffect(() => {
    // Skip the mount run so the page restored from the URL/localStorage (`pg`)
    // survives a navigate-back from a prospect detail. Only reset to page 1 on
    // real filter/sort changes made after mount.
    if (!pageResetMountedRef.current) {
      pageResetMountedRef.current = true;
      return;
    }
    setPage(1);
  }, [aeFilter, callDispositionFilter, linkedinStatusFilter, callOutcomeColorFilter, emailOutcomeColorFilter, callAttemptsBucketFilter, followupCountMin, followupCountMax, nextFollowupRange.from, nextFollowupRange.to, callLastRange.from, callLastRange.to, companyFilter, debouncedSearch, ownerFilter, ownerScope, sdrFilter, sequenceFilter, timezoneFilter, searchScope, searchMatch, prospectSort]);

  // Load the rep-scoped daily call count once on mount and whenever the
  // logged-in user changes. Subsequent updates happen inline after each
  // disposition save (see saveCallDisposition).
  useEffect(() => {
    void loadMyCallsToday();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id]);

  useEffect(() => {
    if (tab !== "contacts") return;
    loadContacts();
  }, [aeFilter, callDispositionFilter, linkedinStatusFilter, callOutcomeColorFilter, emailOutcomeColorFilter, callAttemptsBucketFilter, followupCountMin, followupCountMax, nextFollowupRange.from, nextFollowupRange.to, callLastRange.from, callLastRange.to, companyFilter, debouncedSearch, ownerFilter, ownerScope, page, sdrFilter, sequenceFilter, timezoneFilter, tab, user?.id, searchScope, searchMatch, prospectSort]);

  useEffect(() => {
    if (contacts.length === 0 || selectedContactIds.size === 0) return;
    const visibleIds = new Set(contacts.map((contact) => contact.id));
    setSelectedContactIds((current) => {
      const next = new Set([...current].filter((id) => visibleIds.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [contacts, selectedContactIds.size]);

  // After the contacts list renders, fetch compact lifecycle summaries in
  // one batch call. Gives each row a progress bar (●━●━◉━○━○) and "Day 7 ·
  // 2/5 · 1 overdue" text without N+1 requests.
  useEffect(() => {
    if (tab !== "contacts" || contacts.length === 0) {
      setLifecycleSummaries({});
      return;
    }
    let cancelled = false;
    const ids = contacts.map((c) => c.id).filter(Boolean);
    contactsApi
      .getLifecycleSummaries(ids)
      .then((res) => {
        if (!cancelled) setLifecycleSummaries(res.summaries || {});
      })
      .catch(() => {
        if (!cancelled) setLifecycleSummaries({});
      });
    return () => {
      cancelled = true;
    };
  }, [tab, contacts]);

  // When the lifecycle drawer is opened for a contact, fetch the full
  // reconciled step list. Refetch if the user logs a disposition / reply
  // while the drawer is open (tracked via contacts state change).
  useEffect(() => {
    if (!lifecycleContactId) {
      setLifecycleDetail(null);
      setLifecycleLoading(false);
      return;
    }
    let cancelled = false;
    setLifecycleLoading(true);
    contactsApi
      .getSequenceLifecycle(lifecycleContactId)
      .then((detail) => {
        if (!cancelled) setLifecycleDetail(detail);
      })
      .catch(() => {
        if (!cancelled) setLifecycleDetail(null);
      })
      .finally(() => {
        if (!cancelled) setLifecycleLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [lifecycleContactId, contacts]);


  useEffect(() => {
    const dismiss = () => setOpenActionsId(null);
    window.addEventListener("click", dismiss);
    return () => window.removeEventListener("click", dismiss);
  }, []);

  // When the call sidebar opens, fetch the full pre-call brief: last email
  // sent & whether it was opened, recent signals, talking points, objection
  // playbook, and the AI sequence context. No network or AI in the brief
  // assembly — it's pure DB reads so it comes back in < 300ms.
  useEffect(() => {
    if (!callContact) {
      setPrecallBrief(null);
      setPrecallLoading(false);
      return;
    }
    let cancelled = false;
    setPrecallLoading(true);
    contactsApi
      .getPrecallBrief(callContact.id)
      .then((brief) => {
        if (!cancelled) setPrecallBrief(brief);
      })
      .catch(() => {
        if (!cancelled) setPrecallBrief(null);
      })
      .finally(() => {
        if (!cancelled) setPrecallLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [callContact]);

  // When the LinkedIn logger opens, fetch the contact's AI-generated
  // connect-note so the rep doesn't have to rewrite it from scratch.
  // We intentionally *suggest* (not auto-fill the notes field) so the rep
  // copy-pastes deliberately — the notes field captures what actually
  // happened on LinkedIn, which is different from what we generated.
  useEffect(() => {
    if (!linkedinContact) {
      setLinkedinSuggestion(null);
      setLinkedinSuggestionLoading(false);
      setLinkedinSuggestionCopied(false);
      return;
    }
    let cancelled = false;
    setLinkedinSuggestionLoading(true);
    setLinkedinSuggestionCopied(false);
    outreachApi
      .getSequenceOptional(linkedinContact.id)
      .then((seq) => {
        if (cancelled) return;
        setLinkedinSuggestion((seq?.linkedin_message || "").trim() || null);
      })
      .catch(() => {
        // No sequence yet — we just don't show the suggestion panel.
        if (!cancelled) setLinkedinSuggestion(null);
      })
      .finally(() => {
        if (!cancelled) setLinkedinSuggestionLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [linkedinContact]);

  // ── Angel mapping grouping ──────────────────────────────────────────
  const filteredMappings = filterAngelMappings(mappings, angelSearch, filterStrength);
  const groupedByCompany = groupAngelMappingsByCompany(filteredMappings);

  const investorMappingCounts = mappings.reduce<Record<string, number>>((acc, mapping) => {
    acc[mapping.angel_investor_id] = (acc[mapping.angel_investor_id] || 0) + 1;
    return acc;
  }, {});

  const visibleInvestorCount = new Set(filteredMappings.map((mapping) => mapping.angel_investor_id)).size;
  const visibleContactCount = new Set(
    filteredMappings.map((mapping) => `${mapping.company_name || "Unknown Company"}::${mapping.contact_name || mapping.contact_id}`)
  ).size;
  const strongPathCount = filteredMappings.filter((mapping) => mapping.strength >= 4).length;
  const avgStrength = filteredMappings.length
    ? (filteredMappings.reduce((sum, mapping) => sum + mapping.strength, 0) / filteredMappings.length).toFixed(1)
    : "0.0";

  const handleTabChange = (nextTab: ProspectingTab) => {
    const contactsPath = location.pathname === "/contacts" ? "/contacts" : "/prospecting";
    navigate(nextTab === "angel-mapping" ? "/angel-mapping" : contactsPath);
  };

  const handleAddInvestor = async () => {
    if (!newInvestor.name.trim()) return;
    try {
      const created = await angelMappingApi.createInvestor(newInvestor);
      setInvestors((prev) => [...prev, created]);
      setNewInvestor({ name: "", current_role: "", current_company: "" });
      setShowAddInvestor(false);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to create investor");
    }
  };

  const handleDeleteMapping = async (id: string) => {
    try {
      await angelMappingApi.deleteMapping(id);
      setMappings((prev) => prev.filter((m) => m.id !== id));
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to delete mapping");
    }
  };

  const toggleContactSelection = (contactId: string) => {
    setSelectedContactIds((current) => {
      const next = new Set(current);
      if (next.has(contactId)) {
        next.delete(contactId);
      } else {
        next.add(contactId);
      }
      return next;
    });
  };

  const toggleVisibleContactSelection = () => {
    setSelectedContactIds((current) => {
      if (allVisibleSelected) return new Set();
      const next = new Set(current);
      for (const contact of contacts) next.add(contact.id);
      return next;
    });
  };

  const selectVisibleCompanyContacts = (companyId: string) => {
    setSelectedContactIds((current) => {
      const next = new Set(current);
      for (const contact of contacts) {
        if (contact.company_id === companyId) next.add(contact.id);
      }
      return next;
    });
  };

  // Admin: reassign the selected prospects' SDR to any team member. Used to
  // split an account across SDRs (filter by timezone, select, assign).
  const bulkAssignSelectedSdr = async (userId: string) => {
    if (!userId || selectedContactIds.size === 0) return;
    setBulkAssigningSdr(true);
    try {
      const result = await assignmentsApi.bulkAssignContacts(Array.from(selectedContactIds), userId, "sdr");
      const who = assignableUsers.find((u) => u.id === userId)?.name || "SDR";
      toast.success(
        `${result.updated} prospect${result.updated === 1 ? "" : "s"} assigned to ${who}${result.skipped ? `, ${result.skipped} skipped` : ""}.`,
        "SDR reassigned",
      );
      setSelectedContactIds(new Set());
      await loadContacts();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to assign selected prospects.", "Assign failed");
    } finally {
      setBulkAssigningSdr(false);
    }
  };

  const bulkClaimSelectedSdr = async () => {
    if (!user || user.role !== "sdr" || selectedContactIds.size === 0) return;
    setBulkClaimingSdr(true);
    try {
      const result = await assignmentsApi.bulkAssignContacts(Array.from(selectedContactIds), user.id, "sdr");
      toast.success(
        `${result.updated} SDR assignment${result.updated === 1 ? "" : "s"} claimed${result.skipped ? `, ${result.skipped} skipped` : ""}.`,
        "SDR claim complete",
      );
      setSelectedContactIds(new Set());
      await loadContacts();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to claim selected prospects.", "Claim failed");
    } finally {
      setBulkClaimingSdr(false);
    }
  };

  const deleteContactsByIds = async (ids: string[]) => {
    const targets = Array.from(new Set(ids.filter(Boolean)));
    if (targets.length === 0 || deletingContacts) return;
    const n = targets.length;
    const warning =
      n === 1
        ? "Permanently delete this prospect? Their outreach sequences, reminders, stakeholder links, and call/LinkedIn recordings are also removed. Activity history and any linked deals are kept. This cannot be undone."
        : `Permanently delete ${n} prospects? Their outreach sequences, reminders, stakeholder links, and call/LinkedIn recordings are also removed. Activity history and any linked deals are kept. This cannot be undone.`;
    if (!window.confirm(warning)) return;
    setDeletingContacts(true);
    try {
      if (n === 1) {
        await contactsApi.delete(targets[0]);
      } else {
        await contactsApi.bulkDeleteByIds(targets);
      }
      setSelectedContactIds((current) => {
        if (current.size === 0) return current;
        const next = new Set(current);
        for (const id of targets) next.delete(id);
        return next;
      });
      setOpenActionsId(null);
      toast.success(`Deleted ${n} prospect${n === 1 ? "" : "s"}.`, "Prospects deleted");
      await loadContacts();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to delete prospects.", "Delete failed");
    } finally {
      setDeletingContacts(false);
    }
  };

  const handleConvertContactToDeal = async (contact: Contact) => {
    if (!contact.company_id) {
      toast.warning("This prospect needs a company before it can be converted to a deal.", "Company required");
      return;
    }
    const contactName = `${contact.first_name} ${contact.last_name}`.trim() || contact.email || "Prospect";
    try {
      const desiredName = `${contact.company_name ?? "Account"} - ${contactName}`;
      const existingDeals = await dealsApi.list(0, 50, contact.company_id);
      const duplicate = existingDeals.find(
        (deal) => deal.name.trim().toLowerCase() === desiredName.trim().toLowerCase()
      );
      if (duplicate) {
        await dealsApi.addContact(duplicate.id, contact.id, "champion");
        toast.info(`Opened existing deal "${duplicate.name}" instead of creating a duplicate.`, "Deal already exists");
        navigate(`/deals/${duplicate.id}`);
        return;
      }
      const deal = await dealsApi.create({
        name: desiredName,
        company_id: contact.company_id,
        assigned_to_id: contact.assigned_to_id || undefined,
        stage: "qualified_lead",
      });
      await dealsApi.addContact(deal.id, contact.id, "champion");
      toast.success(`${contactName} was converted into a deal.`, "Deal created");
      navigate("/pipeline");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to convert this prospect into a deal.", "Conversion failed");
    }
  };

  const clearDialTimer = () => {
    if (dialTimerRef.current != null) {
      window.clearInterval(dialTimerRef.current);
      dialTimerRef.current = null;
    }
  };

  // The actual dial: trigger Aircall (if enabled) and ring the rep's mobile
  // PWA. Runs once the countdown elapses or the rep taps "Call now".
  const performDial = (contact: Contact) => {
    if (!contact.phone) return;
    if (aircallEnabled && window.__aircallDial) {
      window.__aircallDial(contact.phone, `${contact.first_name} ${contact.last_name}`.trim());
    }
    // Best-effort: ring the user's mobile PWA so they can tap-to-dial on
    // their phone. Never blocks the sidebar — failures, missing VAPID, no
    // subscription, etc. all silently no-op.
    pushApi
      .ringMobile(contact.id)
      .then((res) => {
        if (res.sent > 0) {
          toast.info(`Rang ${res.sent} device${res.sent === 1 ? "" : "s"}.`, "Mobile call ready");
        } else if (res.configured === 0) {
          toast.warning("Mobile push is not configured yet. The call drawer is ready here.", "Mobile ring unavailable");
        } else if (res.total === 0) {
          toast.info("No mobile PWA is registered for your user yet. Enable mobile notifications from Settings.", "Mobile not registered");
        } else {
          toast.warning("No mobile device accepted the call notification. Re-enable notifications on the phone.", "Mobile ring failed");
        }
      })
      .catch(() => {
        toast.info("Call drawer is ready here. Mobile notification could not be sent.", "Mobile ring skipped");
      });
  };

  // What "the call starts" means once the countdown elapses (or the rep taps
  // "Start now"): kick off the in-browser recording. The dial/notification is
  // NOT here — it fires immediately on drawer open (see openCallSidebar) so the
  // rep can dial during the countdown. The recording panel guards against
  // double-starting if the rep already hit Record manually during the wait.
  const beginCall = (_contact: Contact) => {
    callRecordingRef.current?.startRecording();
  };

  // Hold the start for 10s. A closure-local counter drives the tick so the
  // call begins exactly once at zero (no side effects inside a setState
  // updater, which would double-fire under StrictMode).
  const startDialCountdown = (contact: Contact) => {
    clearDialTimer();
    let remaining = 10;
    setDialCountdown(remaining);
    dialTimerRef.current = window.setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) {
        clearDialTimer();
        setDialCountdown(null);
        beginCall(contact);
      } else {
        setDialCountdown(remaining);
      }
    }, 1000);
  };

  // Stop the auto-start within the 10s window — drawer stays open so the rep
  // can still record/log the call manually whenever they're ready.
  const cancelDial = () => {
    clearDialTimer();
    setDialCountdown(null);
  };

  // Skip the wait and start recording immediately (the dial/notification
  // already fired on open).
  const dialNow = () => {
    if (!callContact) return;
    clearDialTimer();
    setDialCountdown(null);
    beginCall(callContact);
  };

  const openCallSidebar = async (contact: Contact) => {
    // Remember where the rep is in the list so closing the drawer doesn't
    // bounce them back to the top of a long, scrolled prospect list.
    const scroller = document.querySelector<HTMLElement>(".crm-content");
    restoreScrollRef.current = scroller ? scroller.scrollTop : null;
    setCallContact(contact);
    setCallStatus("attempted");
    setCallDisposition("");
    setCallNotes("");
    setFollowupAt("");
    setCurrentRecordingId(null);
    // Ring the rep's phone / open the dialer NOW, on open — the whole point of
    // the 10s countdown is to give the rep time to dial and connect before the
    // recording auto-starts. Previously this fired only at the END of the
    // countdown, so there was nothing to dial during the wait.
    performDial(contact);
    startDialCountdown(contact);
  };

  // Safety net: whenever the drawer closes (any path), kill a pending
  // countdown so a dial can't fire after the rep moved on. NOTE: we must NOT
  // put clearDialTimer in this effect's cleanup — on the open transition
  // (null → contact) React runs the prior cleanup right after
  // openCallSidebar has already started the interval, which would clear it
  // instantly. Unmount cleanup lives in its own empty-deps effect below.
  useEffect(() => {
    if (!callContact) {
      clearDialTimer();
      setDialCountdown(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [callContact]);

  // Clear any running countdown only when the page itself unmounts.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => () => clearDialTimer(), []);

  const handleCallDispositionChange = (value: string) => {
    setCallDisposition(value);
    const matched = CALL_DISPOSITION_OPTIONS.find((option) => option.value === value);
    if (matched?.suggestedCallStatus) {
      setCallStatus(matched.suggestedCallStatus);
    }
    // Auto-seed a follow-up time when the disposition implies one — keeps
    // the rep from having to type a full datetime in the common case.
    if (FOLLOWUP_DISPOSITIONS.has(value) && !followupAt) {
      setFollowupAt(defaultFollowupLocalString());
    }
  };

  const saveCallDisposition = async (opts?: { advance?: boolean }) => {
    if (!callContact || !callDisposition) return;
    setSavingDisposition(true);
    try {
      const derivedSeqStatus = deriveSequenceStatusFromCallDisposition(
        callDisposition,
        callContact.sequence_status,
      );
      const nowIso = new Date().toISOString();
      // When a follow-up disposition is paired with a datetime, persist it on
      // the contact so the prospect-row progress bar can render the date next
      // to the blue+white dot pair. The backend clears this field automatically
      // when the disposition changes to something that doesn't imply a follow-up.
      const needsFollowup = FOLLOWUP_DISPOSITIONS.has(callDisposition);
      const followupLocal = needsFollowup ? (followupAt || defaultFollowupLocalString()) : "";
      const followupIso = needsFollowup
        ? (() => {
            const d = new Date(followupLocal);
            return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
          })()
        : undefined;
      if (needsFollowup && !followupIso) {
        toast.error("Choose a valid follow-up date and time before saving.", "Follow-up required");
        return;
      }
      if (needsFollowup && !followupAt) {
        setFollowupAt(followupLocal);
      }
      await contactsApi.update(callContact.id, {
        call_status: callStatus,
        call_disposition: callDisposition,
        call_notes: (callNotes || null) as never, // null clears; undefined is a silent no-op
        call_last_at: nowIso,
        ...(followupIso ? { next_followup_at: followupIso } : {}),
        ...(derivedSeqStatus && derivedSeqStatus !== callContact.sequence_status
          ? { sequence_status: derivedSeqStatus }
          : {}),
      });

      // Count/log the manual call only after the rep commits the disposition.
      const dispositionLabel = formatCallDisposition(callDisposition);
      const contactLabel = `${callContact.first_name ?? ""} ${callContact.last_name ?? ""}`.trim();
      const activityContent = callNotes
        ? `${dispositionLabel} call with ${contactLabel}: ${callNotes}`
        : `${dispositionLabel} call with ${contactLabel}`;
      try {
        await activitiesApi.create({
          type: "call",
          source: "manual",
          content: activityContent,
          contact_id: callContact.id,
          call_outcome: callStatus || undefined,
          event_metadata: {
            event_type: "manual_call_logged",
            call_disposition: callDisposition,
            call_status: callStatus,
            followup_at: followupIso,
            logged_at: nowIso,
            ...(currentRecordingId ? { recording_id: currentRecordingId } : {}),
          },
          // Link to the recording if one was attached. The lifecycle
          // drawer's activity timeline reads this to surface the
          // transcript + AI summary inline. Null/undefined means a
          // plain non-recorded call (the existing flow).
        } as Partial<Activity>);
      } catch {
        // Non-fatal — contact state already saved above; warn the rep so they
        // know to check the timeline manually.
        toast.error("Call logged but timeline write failed — check activity feed.", "Partial save");
      }

      // If the disposition implies a follow-up and a datetime is set,
      // persist it as a Reminder. The datetime-local input value is naive
      // (no TZ); we treat it as the rep's local time and convert to ISO via
      // the Date constructor, which interprets unsuffixed strings as local.
      if (needsFollowup && followupIso) {
        try {
          const due = new Date(followupIso);
          if (!Number.isNaN(due.getTime())) {
            await remindersApi.create({
              contact_id: callContact.id,
              company_id: callContact.company_id || undefined,
              note: `Follow-up call — ${formatCallDisposition(callDisposition)}${callNotes ? ` · ${callNotes}` : ""}`,
              due_at: due.toISOString(),
              assigned_to_id: user?.id,
            });
            toast.success(`Follow-up scheduled for ${due.toLocaleString()}.`, "Reminder set");
          }
        } catch {
          toast.error("Disposition saved, but the follow-up reminder couldn't be created.", "Reminder failed");
        }
      }

      toast.success(`Call logged for ${callContact.first_name}.`, "Call logged");
      // Save & next: jump to the next callable prospect to keep a dialing rep
      // in flow; otherwise close. Either way the list reloads silently so the
      // rep's scroll position is preserved.
      if (opts?.advance && nextCallable) {
        void openCallSidebar(nextCallable);
      } else {
        setCallContact(null);
      }
      loadContacts({ silent: true });
      // Refresh the rep-scoped tile so it reflects the just-saved call. It
      // queries activities directly so it doesn't depend on which page of
      // contacts is currently visible.
      void loadMyCallsToday();
    } catch {
      toast.error("Failed to save call disposition.", "Error");
    } finally {
      setSavingDisposition(false);
    }
  };

  const saveLinkedinTouch = async () => {
    if (!linkedinContact || !linkedinStatus) return;
    setSavingLinkedin(true);
    try {
      const derivedSeqStatus = deriveSequenceStatusFromLinkedinStatus(
        linkedinStatus,
        linkedinContact.sequence_status,
      );
      await contactsApi.update(linkedinContact.id, {
        linkedin_status: linkedinStatus,
        linkedin_last_at: new Date().toISOString(),
        // NOTE: the LinkedIn note is captured on the linkedin Activity below —
        // do NOT write it to call_notes (that clobbers real call notes).
        ...(derivedSeqStatus && derivedSeqStatus !== linkedinContact.sequence_status
          ? { sequence_status: derivedSeqStatus }
          : {}),
      });

      // Write an Activity row so LinkedIn touches appear in the timeline with
      // rep attribution. Sub-state (request sent / accepted / replied) is
      // captured via the `content` string; the sequence_status transition is
      // already handled by `deriveSequenceStatusFromLinkedinStatus`.
      const linkedinLabel = ({
        sent: "Sent LinkedIn connect request",
        accepted: "LinkedIn connect accepted",
        follow_up: "LinkedIn follow-up",
        meeting_booked: "Meeting booked via LinkedIn",
        meeting_rejected: "Meeting rejected on LinkedIn",
      } as Record<string, string>)[linkedinStatus] ?? `LinkedIn: ${linkedinStatus}`;
      const contactLabel = `${linkedinContact.first_name ?? ""} ${linkedinContact.last_name ?? ""}`.trim();
      const activityContent = linkedinNotes
        ? `${linkedinLabel} — ${contactLabel}: ${linkedinNotes}`
        : `${linkedinLabel} — ${contactLabel}`;
      try {
        await activitiesApi.create({
          type: "linkedin",
          source: "manual",
          content: activityContent,
          contact_id: linkedinContact.id,
        } as Partial<Activity>);
      } catch {
        toast.error("LinkedIn saved but timeline write failed — check activity feed.", "Partial save");
      }

      // Quick confirmation — reps log many touches in a row, so this toast
      // auto-dismisses fast (2s) instead of the 5s default.
      toast.show({
        tone: "success",
        message: `LinkedIn touch logged for ${linkedinContact.first_name}.`,
        title: "LinkedIn logged",
        durationMs: 2000,
      });
      // Preserve scroll: capture where the rep is, then reload silently so the
      // list stays mounted (a non-silent reload flips `loading` and bounces the
      // page to the top — the bug reps reported after logging a LinkedIn touch).
      const scroller = document.querySelector<HTMLElement>(".crm-content");
      restoreScrollRef.current = scroller ? scroller.scrollTop : null;
      setLinkedinContact(null);
      setLinkedinNotes("");
      loadContacts({ silent: true });
    } catch {
      toast.error("Failed to log LinkedIn touch.", "Error");
    } finally {
      setSavingLinkedin(false);
    }
  };

  // wa.me needs a bare international number (country code + digits, no + or spaces).
  const waPhoneDigits = (phone?: string | null) => (phone || "").replace(/[^\d]/g, "");

  const saveWhatsappTouch = async () => {
    if (!whatsappContact || !whatsappOutcome) return;
    setSavingWhatsapp(true);
    try {
      const label = ({
        sent: "Sent WhatsApp message",
        replied: "WhatsApp reply received",
        no_response: "WhatsApp — no response",
        meeting_booked: "Meeting booked via WhatsApp",
      } as Record<string, string>)[whatsappOutcome] ?? `WhatsApp: ${whatsappOutcome}`;
      const contactLabel = `${whatsappContact.first_name ?? ""} ${whatsappContact.last_name ?? ""}`.trim();
      const content = whatsappNotes ? `${label} — ${contactLabel}: ${whatsappNotes}` : `${label} — ${contactLabel}`;
      await activitiesApi.create({
        type: "whatsapp",
        medium: "whatsapp",
        source: "manual",
        content,
        contact_id: whatsappContact.id,
      } as Partial<Activity>);
      // Quick auto-dismiss toast (reps log many touches in a row).
      toast.show({
        tone: "success",
        message: `WhatsApp logged for ${whatsappContact.first_name}.`,
        title: "WhatsApp logged",
        durationMs: 2000,
      });
      const scroller = document.querySelector<HTMLElement>(".crm-content");
      restoreScrollRef.current = scroller ? scroller.scrollTop : null;
      setWhatsappContact(null);
      setWhatsappNotes("");
      loadContacts({ silent: true });
    } catch {
      toast.error("Failed to log WhatsApp message.", "Error");
    } finally {
      setSavingWhatsapp(false);
    }
  };

  // Open the comments panel for a prospect and load its history. Comments are
  // activity rows (type='comment'); we fetch the contact's activities and keep
  // only comments, newest first.
  const openComments = (contact: Contact) => {
    setCommentsContact(contact);
    setCommentDraft("");
    setCommentsList([]);
    setCommentsLoading(true);
    activitiesApi
      .list(undefined, contact.id)
      .then((items) => {
        const comments = (items || [])
          .filter((a) => a.type === "comment")
          .sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
        setCommentsList(comments);
      })
      .catch(() => setCommentsList([]))
      .finally(() => setCommentsLoading(false));
  };

  const saveComment = async () => {
    const body = commentDraft.trim();
    if (!commentsContact || !body) return;
    setSavingComment(true);
    try {
      const created = await activitiesApi.create({
        type: "comment",
        source: "manual",
        content: body,
        contact_id: commentsContact.id,
      } as Partial<Activity>);
      // Prepend optimistically so the new comment shows instantly.
      setCommentsList((prev) => [created, ...prev]);
      setCommentDraft("");
      toast.show({ tone: "success", message: "Comment added.", title: "Saved", durationMs: 2000 });
      // Refresh the list so the row's latest-comment column reflects it.
      const scroller = document.querySelector<HTMLElement>(".crm-content");
      restoreScrollRef.current = scroller ? scroller.scrollTop : null;
      loadContacts({ silent: true });
    } catch {
      toast.error("Failed to add comment.", "Error");
    } finally {
      setSavingComment(false);
    }
  };

  // Open the bulk "start campaign" modal and load the available Instantly campaigns.
  const openCampaignModal = () => {
    if (selectedContactIds.size === 0) return;
    setCampaignModalOpen(true);
    setSelectedCampaignId("");
    setCampaignOptionsLoading(true);
    outreachApi
      .listInstantlyCampaigns()
      .then((res) => setCampaignOptions(res.campaigns || []))
      .catch(() => setCampaignOptions([]))
      .finally(() => setCampaignOptionsLoading(false));
  };

  const startBulkCampaign = async () => {
    if (!selectedCampaignId || selectedContactIds.size === 0) return;
    setStartingCampaign(true);
    try {
      const res = await outreachApi.bulkAddToInstantlyCampaign(Array.from(selectedContactIds), selectedCampaignId);
      const skipped = res.skipped_no_email ? ` · ${res.skipped_no_email} skipped (no email)` : "";
      toast.success(`${res.enrolled} prospect${res.enrolled === 1 ? "" : "s"} added to the campaign${skipped}.`, "Campaign started");
      setCampaignModalOpen(false);
      setSelectedContactIds(new Set());
      loadContacts({ silent: true });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to start campaign.", "Error");
    } finally {
      setStartingCampaign(false);
    }
  };

  // Bulk follow-up: open the dialog pre-seeded with the same "tomorrow at
  // 10:00 AM PST" default the single call-follow-up uses.
  const openBulkFollowup = () => {
    if (selectedContactIds.size === 0) return;
    setBulkFollowupAt(defaultFollowupLocalString());
    setBulkFollowupNote("");
    setBulkFollowupOpen(true);
  };

  // Create one Reminder per selected prospect. The datetime-local value is
  // naive (rep's local time); `new Date(...)` interprets an unsuffixed string
  // as local, then we send ISO/UTC — mirrors the single-reminder create at
  // saveCallDisposition. Each reminder is per-contact, so it surfaces on that
  // prospect's detail page automatically (no backend change needed).
  const submitBulkFollowup = async () => {
    if (selectedContactIds.size === 0 || !bulkFollowupAt) return;
    const due = new Date(bulkFollowupAt);
    if (Number.isNaN(due.getTime())) {
      toast.error("Enter a valid follow-up date and time.", "Invalid date");
      return;
    }
    const dueIso = due.toISOString();
    const note = bulkFollowupNote.trim() || "Follow-up";
    const ids = Array.from(selectedContactIds);
    // Map contact id → company id from the loaded list so each reminder carries
    // its company (same field the single-reminder path sets).
    const companyById = new Map(contacts.map((c) => [c.id, c.company_id]));
    setBulkFollowupSaving(true);
    try {
      await Promise.all(
        ids.map((id) =>
          remindersApi.create({
            contact_id: id,
            company_id: companyById.get(id) || undefined,
            note,
            due_at: dueIso,
            assigned_to_id: user?.id,
          })
        )
      );
      toast.success(`Follow-up set for ${ids.length} prospect${ids.length === 1 ? "" : "s"}.`, "Reminders set");
      setBulkFollowupOpen(false);
      setSelectedContactIds(new Set());
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to set follow-up reminders.", "Error");
    } finally {
      setBulkFollowupSaving(false);
    }
  };

  const handleDeleteInvestor = async (id: string) => {
    if (!confirm("Delete this investor and all their mappings?")) return;
    try {
      await angelMappingApi.deleteInvestor(id);
      setInvestors((prev) => prev.filter((i) => i.id !== id));
      setMappings((prev) => prev.filter((m) => m.angel_investor_id !== id));
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to delete");
    }
  };

  const emailsOpenedCount = contacts.filter((c) => (c.email_open_count ?? 0) > 0).length;
  // Predicate per KPI tile — kept in sync with each tile's own count so the
  // number on the card matches the rows shown when it's clicked.
  const _today = new Date();
  const cardPredicates = {
    calls_today: (c: Contact) => {
      if (!c.call_last_at) return false;
      const d = new Date(c.call_last_at);
      return d.getFullYear() === _today.getFullYear() && d.getMonth() === _today.getMonth() && d.getDate() === _today.getDate();
    },
    emails: (c: Contact) => (c.email_open_count ?? 0) > 0,
    linkedin: (c: Contact) => !!c.linkedin_status && c.linkedin_status !== "none",
    meetings: (c: Contact) => c.sequence_status === "meeting_booked",
  } as const;
  const displayedContacts = cardFilter ? contacts.filter(cardPredicates[cardFilter]) : contacts;
  // The next callable prospect (has a phone) after the one in the open call
  // drawer, in the rep's current view order — powers the "Save & next" button.
  const nextCallable: Contact | null = (() => {
    if (!callContact) return null;
    const idx = displayedContacts.findIndex((c) => c.id === callContact.id);
    if (idx === -1) return null;
    for (let i = idx + 1; i < displayedContacts.length; i++) {
      if (displayedContacts[i].phone) return displayedContacts[i];
    }
    return null;
  })();
  const linkedinActiveCount = contacts.filter((c) => c.linkedin_status && c.linkedin_status !== "none").length;
  const meetingsBookedCount = contacts.filter((c) => c.sequence_status === "meeting_booked").length;

  return (
    <>
      <style>{`
        .prospect-mobile-only { display: none; }
        /* Breakpoint aligned with the global mobile layout switch in index.css
           (1080px). Earlier the mobile view only kicked in <=760px, leaving a
           broken middle band 760-1080px where the desktop sidebar was hidden
           but the desktop grid still rendered cramped. */
        @media (max-width: 1080px) {
          .contacts-page {
            /* No side padding: let the mobile shell paint edge-to-edge. The
               surrounding .crm-content keeps its own padding on desktop; on
               mobile we already reset it to 8px via the global rule. */
            padding: 0 0 88px !important;
            gap: 12px !important;
            margin: 0 !important;
            width: 100% !important;
            max-width: none !important;
          }
          .prospect-desktop-only {
            display: none !important;
            visibility: hidden !important;
            pointer-events: none !important;
          }
          .prospect-mobile-only {
            display: block !important;
            position: relative;
            z-index: 20;
          }
          .prospect-mobile-shell {
            /* Edge-to-edge: counter the .crm-content padding so the sticky
               search bar and cards run flush to the viewport edges. Use
               calc(-1 * padding) instead of fixed -10px so the shell tracks
               whatever the parent decides to use. */
            margin: 0 calc(-1 * env(safe-area-inset-left, 0)) 0;
            margin-left: -8px;
            margin-right: -8px;
            margin-top: -8px;
            background: #f4f8fc;
            min-height: calc(100dvh - 56px);
          }
          .prospect-mobile-top {
            position: sticky;
            top: 0;
            z-index: 12;
            padding: 12px 12px 10px;
            background: rgba(244, 248, 252, 0.96);
            backdrop-filter: blur(12px);
            border-bottom: 1px solid #dde8f4;
          }
          .prospect-mobile-search {
            position: relative;
            z-index: 3;
            width: 100%;
            height: 46px;
            border-radius: 14px;
            border: 1px solid #cfe0f3;
            background: #ffffff;
            padding: 0 12px 0 42px;
            color: #102a43;
            font-size: 15px;
            font-weight: 650;
            outline: none;
            box-shadow: 0 8px 22px rgba(16, 42, 67, 0.06);
          }
          .prospect-mobile-search::placeholder {
            color: #7b91a8;
            font-weight: 600;
          }
          .prospect-mobile-card {
            background: #ffffff;
            border: 1px solid #dce8f4;
            border-radius: 18px;
            box-shadow: 0 10px 26px rgba(16, 42, 67, 0.08);
            overflow: hidden;
          }
          .prospect-mobile-call {
            min-height: 48px;
            border-radius: 14px;
            border: 1px solid #a9cdf8;
            background: linear-gradient(135deg, #0f5fb8, #174ea6);
            color: #ffffff;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            font-size: 15px;
            font-weight: 800;
            box-shadow: 0 10px 20px rgba(23, 80, 137, 0.18);
          }
          .prospect-mobile-call:disabled {
            background: #eef3f8;
            color: #8ea1b4;
            border-color: #d9e4ef;
            box-shadow: none;
          }
          .prospect-mobile-secondary {
            height: 40px;
            border-radius: 12px;
            border: 1px solid #d7e4f0;
            background: #f8fbff;
            color: #35546f;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            font-size: 12.5px;
            font-weight: 750;
            text-decoration: none;
          }
        }
      `}</style>
      <div className="crm-page contacts-page space-y-6">
        {/* ── Tab switcher + action bar ──────────────────────────────── */}
        <div className="prospect-desktop-only" style={{ display: "flex", flexDirection: "column", gap: 12 }}>

          {/* Row 1 — tab cards */}
          {/* SDR Activity Cards — unified, modern, info-filled */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(5, minmax(0, 1fr))", gap: 10 }}>
            {([
              { label: "Total prospects", value: contactsTotal, icon: Users, color: "#1d4ed8", sub: contactsTotal > 0 ? "Active pipeline" : "No prospects yet", filterKey: null },
              { label: "Calls today", value: myCallsTodayCount, icon: PhoneCall, color: "#0891b2", sub: myCallsTodayCount > 0 ? "Logged today" : "No calls yet", filterKey: "calls_today" },
              { label: "Emails opened", value: emailsOpenedCount, icon: Mail, color: "#16a34a", sub: emailsOpenedCount > 0 ? "Engaged readers" : "Awaiting opens", filterKey: "emails" },
              { label: "LinkedIn touches", value: linkedinActiveCount, icon: Link2, color: "#7c3aed", sub: linkedinActiveCount > 0 ? "Active threads" : "No threads yet", filterKey: "linkedin" },
              { label: "Meetings booked", value: meetingsBookedCount, icon: Clock, color: "#d97706", sub: meetingsBookedCount > 0 ? "Pipeline added" : "None yet", filterKey: "meetings" },
            ] as const).map(({ label, value, icon: Icon, color, sub, filterKey }) => {
              // Total-prospects tile resets the filter; the four engagement tiles
              // toggle their own client-side filter on the loaded page.
              const isResetCard = filterKey === null;
              const isInteractive = isResetCard ? cardFilter !== null : value > 0;
              const isActive = !isResetCard && cardFilter === filterKey;
              const activate = () => {
                if (isResetCard) { setCardFilter(null); return; }
                setCardFilter((prev) => (prev === filterKey ? null : filterKey));
              };
              return (
              <div
                key={label}
                className="prospect-kpi-card"
                role={isInteractive ? "button" : undefined}
                tabIndex={isInteractive ? 0 : undefined}
                aria-pressed={isInteractive && !isResetCard ? isActive : undefined}
                title={isInteractive ? (isResetCard || isActive ? "Show all prospects" : `Show only ${label.toLowerCase()}`) : undefined}
                onClick={isInteractive ? activate : undefined}
                onKeyDown={isInteractive ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(); } } : undefined}
                style={{
                  position: "relative",
                  display: "flex", flexDirection: "column", gap: 10,
                  padding: "14px 16px",
                  borderRadius: 14,
                  border: `1px solid ${isActive ? color : "#e4ebf3"}`,
                  background: isActive ? `${color}10` : "#ffffff",
                  boxShadow: isActive ? `0 0 0 2px ${color}33, 0 1px 2px rgba(15,23,42,0.04)` : "0 1px 2px rgba(15,23,42,0.04)",
                  overflow: "hidden",
                  minWidth: 0,
                  cursor: isInteractive ? "pointer" : "default",
                  transition: "transform 150ms cubic-bezier(0.22, 1, 0.36, 1), border-color 120ms ease, box-shadow 120ms ease, background-color 120ms ease",
                }}
              >
                {/* top accent strip */}
                <span aria-hidden="true" style={{ position: "absolute", left: 0, top: 0, right: 0, height: 3, background: color }} />
                {/* radial wash in the top-right corner — fills the empty space subtly */}
                <span aria-hidden="true" style={{
                  position: "absolute", right: -36, top: -36, width: 120, height: 120, borderRadius: "50%",
                  background: `radial-gradient(circle, ${color}1a, transparent 70%)`,
                  pointerEvents: "none",
                }} />
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                  <span style={{
                    display: "inline-flex", alignItems: "center", justifyContent: "center",
                    width: 32, height: 32, borderRadius: 10,
                    background: color, color: "#ffffff",
                    boxShadow: `0 4px 10px ${color}40`,
                  }}>
                    <Icon size={15} />
                  </span>
                  <span style={{
                    fontSize: 10, fontWeight: 800, letterSpacing: 0.08, textTransform: "uppercase",
                    color: "#7d8ea6",
                  }}>
                    {label}
                  </span>
                </div>
                <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                  <span style={{
                    fontSize: 28, fontWeight: 800, color: "#0f1f33", lineHeight: 1, letterSpacing: "-0.025em",
                  }}>
                    {value}
                  </span>
                  <span style={{
                    fontSize: 11, fontWeight: 700, color, opacity: 0.85,
                  }}>
                    {sub}
                  </span>
                </div>
              </div>
              );
            })}
          </div>

          {/* Row 2 — contextual action bar */}
          <div style={{
            display: "flex", alignItems: "center", gap: 10,
            background: "#fff", borderRadius: 14,
            border: "1px solid #e8eef5",
            padding: "10px 14px",
            boxShadow: "0 2px 8px rgba(17,34,68,0.04)",
            flexWrap: "wrap",
          }}>
            {tab === "contacts" && (
              <>
                {/* Search — scope select + auto-growing textarea.
                    When scope != "all", the textarea accepts newline- or
                    comma-separated lists (e.g. paste 30 company names) and
                    backend ORs each entry against the chosen column. The
                    visible row count grows up to 5 lines, after which the
                    textarea scrolls. */}
                {(() => {
                  const lineCount = Math.min(5, Math.max(1, (search.match(/\n/g)?.length ?? 0) + 1));
                  const minH = 38; // single-row pill height (matches the other action buttons)
                  const rowH = 20;
                  const dynamicHeight = lineCount === 1 ? minH : minH + (lineCount - 1) * rowH;
                  const isScoped = searchScope !== "all";
                  const termCount = isScoped
                    ? search.split(/[,\n]+/).map((t) => t.trim()).filter(Boolean).length
                    : 0;
                  return (
                    <div style={{ display: "flex", flex: 1, minWidth: 260, alignItems: "stretch", borderRadius: 10, border: "1px solid #e0eaf4", background: "#f7fbff", overflow: "hidden" }}>
                      <select
                        value={searchScope}
                        onChange={(e) => setSearchScope(e.target.value)}
                        title="Scope search to a single column. When scoped, the input accepts a comma- or newline-separated list."
                        style={{
                          height: minH, alignSelf: "flex-start", border: "none", borderRight: "1px solid #e0eaf4",
                          background: "#eef5ff", padding: "0 26px 0 10px",
                          fontSize: 12, fontWeight: 700, color: "#175089", outline: "none",
                          cursor: "pointer",
                        }}
                      >
                        {SEARCH_SCOPE_OPTIONS.map((opt) => (
                          <option key={opt.value} value={opt.value}>{opt.label}</option>
                        ))}
                      </select>
                      <div style={{ position: "relative", flex: 1, display: "flex", flexDirection: "column" }}>
                        <Search size={14} style={{ position: "absolute", left: 11, top: 12, color: "#94a8bc", pointerEvents: "none" }} />
                        <textarea
                          rows={1}
                          spellCheck={false}
                          style={{
                            width: "100%", minHeight: minH, height: dynamicHeight,
                            border: "none", background: "transparent",
                            padding: "10px 12px 8px 32px", resize: "none",
                            fontSize: 13, lineHeight: "20px", color: "#1e3a52", outline: "none",
                            fontFamily: "inherit",
                          }}
                          placeholder={
                            isScoped
                              ? `Search ${SEARCH_SCOPE_OPTIONS.find((o) => o.value === searchScope)?.label ?? ""} — paste a list (comma or newline separated)`
                              : "Search people, title, email…"
                          }
                          value={search}
                          onChange={(e) => setSearch(e.target.value)}
                        />
                        {isScoped && termCount > 1 && (
                          <span style={{
                            position: "absolute", right: 8, top: 8,
                            fontSize: 10, fontWeight: 800, color: "#175089",
                            background: "#eaf2ff", border: "1px solid #c7d9f0",
                            borderRadius: 999, padding: "2px 8px",
                          }}>
                            {termCount} terms
                          </span>
                        )}
                      </div>
                      {isScoped && (
                        <div style={{ display: "flex", alignItems: "flex-start", padding: 4, gap: 2, borderLeft: "1px solid #e0eaf4", background: "#fbfdff" }}>
                          {(["contains", "exact"] as const).map((mode) => (
                            <button
                              key={mode}
                              type="button"
                              onClick={() => setSearchMatch(mode)}
                              title={mode === "exact" ? "Match must equal the cell exactly (case-insensitive)" : "Match anywhere inside the cell"}
                              style={{
                                height: 30, padding: "0 10px", borderRadius: 7,
                                border: "1px solid transparent",
                                background: searchMatch === mode ? "#175089" : "transparent",
                                color: searchMatch === mode ? "#fff" : "#4a6580",
                                fontSize: 11, fontWeight: 700, cursor: "pointer",
                                textTransform: "capitalize",
                              }}
                            >
                              {mode}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })()}

                {/* Filters toggle — collapses the inline filter card below */}
                <button
                  type="button"
                  onClick={() => setShowFilters((v) => !v)}
                  title={showFilters ? "Hide filters" : "Show filters"}
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 6,
                    height: 38, padding: "0 14px", borderRadius: 10,
                    border: showFilters ? "1px solid #b8d0f0" : "1px solid #d0dcea",
                    background: showFilters ? "#eef5ff" : "#f7fbff",
                    color: "#175089", fontSize: 13, fontWeight: 700,
                    cursor: "pointer", whiteSpace: "nowrap", flexShrink: 0,
                  }}
                >
                  <Settings2 size={14} />
                  Filters
                  {showFilters ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                </button>

                <button
                  type="button"
                  onClick={downloadProspectTemplate}
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 6,
                    height: 38, padding: "0 14px", borderRadius: 10,
                    border: "1px solid #d0dcea", background: "#ffffff",
                    color: "#2c4a63", fontSize: 13, fontWeight: 600,
                    cursor: "pointer", whiteSpace: "nowrap", flexShrink: 0,
                  }}
                >
                  <Download size={14} />
                  Template
                </button>

                <label
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 6,
                    height: 38, padding: "0 14px", borderRadius: 10,
                    border: "1px solid #cfe89a", background: "#f3fbe3",
                    color: "#4d7c0f", fontSize: 13, fontWeight: 700,
                    cursor: uploadingProspects || !canMigrateProspects ? "default" : "pointer", whiteSpace: "nowrap", flexShrink: 0,
                    opacity: uploadingProspects || !canMigrateProspects ? 0.7 : 1,
                  }}
                >
                  {uploadingProspects ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
                  Upload Prospects
                  <input
                    type="file"
                    accept=".csv,.xlsx"
                    style={{ display: "none" }}
                    disabled={uploadingProspects || !canMigrateProspects}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) {
                        void handleProspectUpload(file);
                      }
                      e.currentTarget.value = "";
                    }}
                  />
                </label>

                {/* Clear — danger, right side */}
                {isAdmin && (
                  <button
                    type="button"
                    disabled={resetting}
                    onClick={async () => {
                      if (!window.confirm("Clear all Prospecting contacts, outreach sequences, and contact activities while keeping companies?")) return;
                      setResetting(true);
                      try {
                        const result = await accountSourcingApi.resetData("prospecting");
                        setPage(1);
                        loadContacts();
                        window.alert(`Prospecting cleared.\n${Object.entries(result.summary).map(([key, value]) => `${key}: ${value}`).join("\n")}`);
                      } finally {
                        setResetting(false);
                      }
                    }}
                    style={{
                      display: "inline-flex", alignItems: "center", gap: 6,
                      height: 38, padding: "0 14px", borderRadius: 10,
                      border: "1px solid #fad2d6", background: "#fff8f8",
                      color: "#b42336", fontSize: 13, fontWeight: 600,
                      cursor: "pointer", whiteSpace: "nowrap", flexShrink: 0,
                      opacity: resetting ? 0.6 : 1,
                    }}
                  >
                    {resetting ? <Loader2 size={13} className="animate-spin" /> : <AlertCircle size={13} />}
                    Clear
                  </button>
                )}
                <button
                  type="button"
                  onClick={toggleAircall}
                  title={aircallEnabled ? "Aircall calling is enabled for this browser." : "Aircall is off by default. Turn it on only when you want to dial through Aircall."}
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 6,
                    height: 38, padding: "0 14px", borderRadius: 10,
                    border: aircallEnabled ? "1px solid #d4edda" : "1px solid #f5c6cb",
                    background: aircallEnabled ? "#eafbf0" : "#fff5f5",
                    color: aircallEnabled ? "#1f8f5f" : "#b42336",
                    fontSize: 13, fontWeight: 700,
                    cursor: "pointer", whiteSpace: "nowrap", flexShrink: 0,
                  }}
                >
                  <PhoneCall size={14} />
                  {aircallEnabled ? "Aircall: On" : "Aircall: Off"}
                </button>
                <button
                  type="button"
                  onClick={() => setShowAddProspect(true)}
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 6,
                    height: 38, padding: "0 14px", borderRadius: 10,
                    border: "1px solid #c7d5e5", background: "#fff",
                    color: "#175089", fontSize: 13, fontWeight: 700,
                    cursor: "pointer", whiteSpace: "nowrap", flexShrink: 0,
                  }}
                >
                  <Plus size={14} />
                  Add Prospect
                </button>
              </>
            )}

            {tab === "angel-mapping" && (
              <>
                <div style={{ position: "relative", flex: 1, minWidth: 180 }}>
                  <Search size={14} style={{ position: "absolute", left: 11, top: "50%", transform: "translateY(-50%)", color: "#94a8bc", pointerEvents: "none" }} />
                  <input
                    style={{
                      width: "100%", height: 38, borderRadius: 10,
                      border: "1px solid #e0eaf4", background: "#f7fbff",
                      paddingLeft: 34, paddingRight: 12,
                      fontSize: 13, color: "#1e3a52", outline: "none",
                    }}
                    placeholder="Search company, prospect, angel…"
                    value={angelSearch}
                    onChange={(e) => setAngelSearch(e.target.value)}
                  />
                </div>
                <button
                  onClick={() => setShowAddInvestor(true)}
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 6,
                    height: 38, padding: "0 14px", borderRadius: 10,
                    border: "1px solid #b2e0dc", background: "#f0faf9",
                    color: "#177b75", fontSize: 13, fontWeight: 600,
                    cursor: "pointer", whiteSpace: "nowrap", flexShrink: 0,
                  }}
                >
                  <Plus size={14} />
                  Add Investor
                </button>
              </>
            )}
          </div>
        </div>

        {/* ═══════════════════════════════════════════════════════════════ */}
        {/* CONTACTS TAB                                                   */}
        {/* ═══════════════════════════════════════════════════════════════ */}
        {tab === "contacts" && (
          <>
            <div className="prospect-mobile-only">
              <div className="prospect-mobile-shell">
                <div className="prospect-mobile-top">
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 10 }}>
                    <div>
                      <div style={{ fontSize: 18, fontWeight: 850, color: "#102a43", lineHeight: 1.1 }}>Prospects</div>
                      <div style={{ fontSize: 12, color: "#6f8297", fontWeight: 650, marginTop: 2 }}>
                        {contactsTotal} total · {myCallsTodayCount} calls today
                      </div>
                    </div>
                    {isSdrLocked ? (
                      // SDRs only see their own prospects — show a static label,
                      // no toggle to team-wide / unassigned views.
                      <div
                        style={{
                          height: 36,
                          padding: "0 12px",
                          borderRadius: 12,
                          border: "1px solid #ffb995",
                          background: "#f3fbe3",
                          color: "#4d7c0f",
                          fontSize: 12,
                          fontWeight: 800,
                          display: "flex",
                          alignItems: "center",
                        }}
                      >
                        My prospects
                      </div>
                    ) : (
                    <button
                      type="button"
                      onClick={() => setOwnerScope(ownerScope === "mine" ? "all" : "mine")}
                      style={{
                        height: 36,
                        padding: "0 12px",
                        borderRadius: 12,
                        border: ownerScope === "mine" ? "1px solid #ffb995" : "1px solid #d3e0ed",
                        background: ownerScope === "mine" ? "#f3fbe3" : "#ffffff",
                        color: ownerScope === "mine" ? "#4d7c0f" : "#35546f",
                        fontSize: 12,
                        fontWeight: 800,
                      }}
                    >
                      {ownerScope === "mine" ? "My list" : isAdmin ? "All reps" : "Mine + unassigned"}
                    </button>
                    )}
                  </div>
                  <div style={{ position: "relative" }}>
                    <Search size={17} style={{ position: "absolute", left: 14, top: "50%", transform: "translateY(-50%)", color: "#6f8297", pointerEvents: "none" }} />
                    <input
                      className="prospect-mobile-search"
                      placeholder="Search any name, email, company, phone..."
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      onFocus={() => setShowAddProspect(false)}
                      autoComplete="off"
                      inputMode="search"
                    />
                  </div>
                  <div style={{ display: "flex", gap: 8, marginTop: 10, overflowX: "auto", paddingBottom: 2 }}>
                    <button
                      type="button"
                      onClick={() => setCallDispositionFilter([])}
                      style={{
                        height: 32,
                        padding: "0 10px",
                        borderRadius: 999,
                        border: callDispositionFilter.length === 0 ? "1px solid #175089" : "1px solid #d3e0ed",
                        background: callDispositionFilter.length === 0 ? "#eaf2ff" : "#ffffff",
                        color: callDispositionFilter.length === 0 ? "#175089" : "#536a82",
                        fontSize: 12,
                        fontWeight: 800,
                        whiteSpace: "nowrap",
                      }}
                    >
                      All outcomes
                    </button>
                    {CALL_DISPOSITION_FILTER_OPTIONS.slice(0, 4).map((option) => {
                      const active = callDispositionFilter.includes(option.value);
                      return (
                        <button
                          key={option.value}
                          type="button"
                          onClick={() => setCallDispositionFilter(active ? callDispositionFilter.filter((value) => value !== option.value) : [option.value])}
                          style={{
                            height: 32,
                            padding: "0 10px",
                            borderRadius: 999,
                            border: active ? "1px solid #175089" : "1px solid #d3e0ed",
                            background: active ? "#eaf2ff" : "#ffffff",
                            color: active ? "#175089" : "#536a82",
                            fontSize: 12,
                            fontWeight: 800,
                            whiteSpace: "nowrap",
                          }}
                        >
                          {option.label}
                        </button>
                      );
                    })}
                  </div>
                  {(user?.role === "sdr" || selectedContactIds.size > 0) && (
                    <div style={{ marginTop: 10, border: "1px solid #dce8f4", background: "#ffffff", borderRadius: 14, padding: 10, display: "grid", gap: 8 }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                        <span style={{ color: "#35546f", fontSize: 12, fontWeight: 800 }}>
                          {selectedContactIds.size} selected
                        </span>
                        <button
                          type="button"
                          onClick={toggleVisibleContactSelection}
                          style={{ border: "1px solid #c8d9e8", background: "#f7fbff", color: "#175089", borderRadius: 10, height: 32, padding: "0 10px", fontSize: 12, fontWeight: 800 }}
                        >
                          {allVisibleSelected ? "Clear" : "Select page"}
                        </button>
                      </div>
                      <button
                        type="button"
                        onClick={() => void deleteContactsByIds(Array.from(selectedContactIds))}
                        disabled={selectedContactIds.size === 0 || deletingContacts}
                        style={{ height: 34, border: "1px solid #f0c2c2", borderRadius: 10, background: selectedContactIds.size ? "#fff1f1" : "#f6f8fb", color: selectedContactIds.size ? "#b3261e" : "#9aa8b7", padding: "0 12px", fontSize: 12, fontWeight: 850, display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6 }}
                      >
                        <Trash2 size={13} /> {deletingContacts ? "Deleting..." : "Delete selected"}
                      </button>
                      <button
                        type="button"
                        onClick={openBulkFollowup}
                        disabled={selectedContactIds.size === 0}
                        style={{ height: 34, border: "1px solid #f5d77a", borderRadius: 10, background: selectedContactIds.size ? "#fffbeb" : "#f6f8fb", color: selectedContactIds.size ? "#92400e" : "#9aa8b7", padding: "0 12px", fontSize: 12, fontWeight: 850, display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6 }}
                      >
                        <Clock size={13} /> Add follow-up
                      </button>
                      {user?.role === "sdr" && (
                        <div style={{ display: "flex", gap: 8 }}>
                          <select
                            value=""
                            onChange={(e) => {
                              if (e.target.value) selectVisibleCompanyContacts(e.target.value);
                              e.currentTarget.value = "";
                            }}
                            style={{ minWidth: 0, flex: 1, height: 34, border: "1px solid #c8d9e8", borderRadius: 10, color: "#35546f", fontSize: 12, fontWeight: 750, padding: "0 8px", background: "#fff" }}
                          >
                            <option value="">Select company on page</option>
                            {visibleCompanySelectionOptions.map((company) => (
                              <option key={company.id} value={company.id}>{company.name} ({company.count})</option>
                            ))}
                          </select>
                          <button
                            type="button"
                            onClick={() => void bulkClaimSelectedSdr()}
                            disabled={selectedContactIds.size === 0 || bulkClaimingSdr}
                            style={{ height: 34, border: "none", borderRadius: 10, background: selectedContactIds.size ? "#175089" : "#d9e4ef", color: "#fff", padding: "0 12px", fontSize: 12, fontWeight: 850 }}
                          >
                            {bulkClaimingSdr ? "Claiming..." : "Claim SDR"}
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>

                <div style={{ padding: 12, display: "grid", gap: 12 }}>
                  {loading ? (
                    <div className="prospect-mobile-card" style={{ padding: 18, textAlign: "center", color: "#6f8297", fontSize: 13, fontWeight: 700 }}>
                      Loading prospects...
                    </div>
                  ) : displayedContacts.length === 0 ? (
                    <div className="prospect-mobile-card" style={{ padding: 22, textAlign: "center" }}>
                      <Users size={30} style={{ margin: "0 auto 10px", color: "#9fb0c2" }} />
                      <div style={{ fontSize: 15, fontWeight: 800, color: "#25384d" }}>{cardFilter ? "No matching prospects in this view" : "No prospects found"}</div>
                      <div style={{ fontSize: 12.5, color: "#7a8ea4", marginTop: 5 }}>{cardFilter ? "None of the loaded prospects match this tile yet." : "Try another name, company, or call outcome."}</div>
                      {cardFilter && (
                        <button type="button" onClick={() => setCardFilter(null)} style={{ marginTop: 10, border: "1px solid #dce8f4", background: "#fff", borderRadius: 8, padding: "6px 10px", fontSize: 12, fontWeight: 700, color: "#175089", cursor: "pointer" }}>Show all prospects</button>
                      )}
                    </div>
                  ) : (
                    <>
                      {displayedContacts.map((c) => {
                        const name = `${c.first_name ?? ""} ${c.last_name ?? ""}`.trim() || "Unnamed prospect";
                        const phoneLabel = c.phone || "No phone number saved";
                        const callLabel = c.call_disposition ? formatCallDisposition(c.call_disposition) : c.call_status && c.call_status !== "none" ? formatCallDisposition(c.call_status) : "Not called yet";
                        return (
                          <div key={c.id} className="prospect-mobile-card">
                            <div style={{ padding: 14, display: "grid", gap: 12 }}>
                              <div style={{ display: "flex", gap: 11, alignItems: "flex-start" }}>
                                {canSelectProspects && (
                                  <input
                                    type="checkbox"
                                    checked={selectedContactIds.has(c.id)}
                                    onChange={() => toggleContactSelection(c.id)}
                                    aria-label={`Select ${name}`}
                                    style={{ marginTop: 13, width: 18, height: 18, accentColor: "#175089", flexShrink: 0 }}
                                  />
                                )}
                                <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-[12px] font-extrabold ${avatarColor(c.first_name + c.last_name)}`}>
                                  {getInitials(name)}
                                </div>
                                <div style={{ minWidth: 0, flex: 1 }}>
                                  <div style={{ fontSize: 16, fontWeight: 850, color: "#1d2b3c", lineHeight: 1.2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                    {name}
                                  </div>
                                  <div style={{ marginTop: 4, display: "flex", alignItems: "center", gap: 5, minWidth: 0 }}>
                                    <Building2 size={13} style={{ color: "#6f8297", flexShrink: 0 }} />
                                    {c.company_name ? (
                                      <button
                                        type="button"
                                        onClick={() => c.company_id ? navigate(`/account-sourcing/${c.company_id}`) : undefined}
                                        style={{
                                          border: "none",
                                          background: "transparent",
                                          padding: 0,
                                          color: "#2467a8",
                                          fontSize: 13,
                                          fontWeight: 750,
                                          overflow: "hidden",
                                          textOverflow: "ellipsis",
                                          whiteSpace: "nowrap",
                                          minWidth: 0,
                                        }}
                                      >
                                        {c.company_name}
                                      </button>
                                    ) : (
                                      <span style={{ color: "#9aaabd", fontSize: 13, fontWeight: 700 }}>No company mapped</span>
                                    )}
                                  </div>
                                  {c.title && (
                                    <div style={{ color: "#71839a", fontSize: 12.5, marginTop: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                      {c.title}
                                    </div>
                                  )}
                                </div>
                              </div>

                              <div style={{ border: "1px solid #e5eef7", background: "#f8fbff", borderRadius: 14, padding: "10px 12px", display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center" }}>
                                <div style={{ minWidth: 0 }}>
                                  <div style={{ fontSize: 10.5, color: "#71839a", fontWeight: 850, textTransform: "uppercase", letterSpacing: 0.4 }}>Phone</div>
                                  <div style={{ marginTop: 2, color: c.phone ? "#102a43" : "#96a7ba", fontSize: 15, fontWeight: 850, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                    {phoneLabel}
                                  </div>
                                </div>
                                <span style={{ flexShrink: 0, fontSize: 11.5, fontWeight: 800, color: "#536a82", background: "#fff", border: "1px solid #dce8f4", borderRadius: 999, padding: "4px 8px", whiteSpace: "nowrap" }}>
                                  {callLabel}
                                </span>
                              </div>

                              <button
                                type="button"
                                className="prospect-mobile-call"
                                disabled={!c.phone}
                                onClick={() => { if (c.phone) void openCallSidebar(c); }}
                                title={c.phone ? c.phone : "No phone number"}
                              >
                                <Phone size={17} />
                                Call prospect
                              </button>

                              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
                                <a
                                  className="prospect-mobile-secondary"
                                  href={c.email ? gmailComposeUrl(c.email) : undefined}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  onClick={(e) => { if (!c.email) e.preventDefault(); }}
                                  style={{ opacity: c.email ? 1 : 0.55 }}
                                >
                                  <Mail size={14} />
                                  Email
                                </a>
                                <a
                                  className="prospect-mobile-secondary"
                                  href={c.linkedin_url || undefined}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  onClick={(e) => { if (!c.linkedin_url) e.preventDefault(); }}
                                  style={{ opacity: c.linkedin_url ? 1 : 0.55 }}
                                >
                                  <Link2 size={14} />
                                  LinkedIn
                                </a>
                                <button
                                  type="button"
                                  className="prospect-mobile-secondary"
                                  onClick={() => navigate(`/contacts/${c.id}`)}
                                >
                                  <ChevronRight size={14} />
                                  Details
                                </button>
                              </div>
                            </div>
                          </div>
                        );
                      })}

                      <div className="prospect-mobile-card" style={{ padding: 12, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
                        <button
                          type="button"
                          onClick={() => setPage((current) => Math.max(1, current - 1))}
                          disabled={page <= 1}
                          style={{ height: 40, padding: "0 14px", borderRadius: 12, border: "1px solid #dce8f4", background: page <= 1 ? "#f7f9fc" : "#fff", color: page <= 1 ? "#9eb0c3" : "#35546f", fontSize: 13, fontWeight: 800 }}
                        >
                          Previous
                        </button>
                        <span style={{ color: "#71839a", fontSize: 12, fontWeight: 800, whiteSpace: "nowrap" }}>
                          Page {page} / {Math.max(contactsPages, 1)}
                        </span>
                        <button
                          type="button"
                          onClick={() => setPage((current) => Math.min(Math.max(contactsPages, 1), current + 1))}
                          disabled={page >= contactsPages}
                          style={{ height: 40, padding: "0 14px", borderRadius: 12, border: "1px solid #dce8f4", background: page >= contactsPages ? "#f7f9fc" : "#fff", color: page >= contactsPages ? "#9eb0c3" : "#35546f", fontSize: 13, fontWeight: 800 }}
                        >
                          Next
                        </button>
                      </div>
                    </>
                  )}
                </div>
              </div>
            </div>

            {/* Quick views — one-click prospecting filters for the common
                calling workflows; each toggles the underlying filter state. */}
            <div className="prospect-desktop-only" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontSize: 10, fontWeight: 800, color: "#7f8fa5", textTransform: "uppercase", letterSpacing: 0.6, marginRight: 2 }}>Quick views</span>
              {(() => {
                const today = localDateStr(0);
                const yesterday = localDateStr(-1);
                const views: { key: string; label: string; icon: typeof Clock; active: boolean; apply: () => void; clear: () => void }[] = [
                  { key: "callbacks", label: "Callbacks due today", icon: Clock,
                    active: nextFollowupRange.from === today && nextFollowupRange.to === today,
                    apply: () => setNextFollowupRange({ from: today, to: today }),
                    clear: () => setNextFollowupRange({ from: "", to: "" }) },
                  { key: "overdue", label: "Overdue follow-ups", icon: AlertTriangle,
                    active: nextFollowupRange.from === "" && nextFollowupRange.to === yesterday,
                    apply: () => setNextFollowupRange({ from: "", to: yesterday }),
                    clear: () => setNextFollowupRange({ from: "", to: "" }) },
                  { key: "never", label: "Never called", icon: Phone,
                    active: followupCountMin === 0 && followupCountMax === 0,
                    apply: () => { setFollowupCountMin(0); setFollowupCountMax(0); },
                    clear: () => { setFollowupCountMin(null); setFollowupCountMax(null); } },
                  { key: "3plus", label: "Called 3+", icon: PhoneCall,
                    active: followupCountMin === 3 && followupCountMax == null,
                    apply: () => { setFollowupCountMin(3); setFollowupCountMax(null); },
                    clear: () => { setFollowupCountMin(null); setFollowupCountMax(null); } },
                  // SDRs are locked to their own prospects — the toggle would be
                  // a no-op (and "clear" → "all" must not be reachable), so omit it.
                  ...(isSdrLocked ? [] : [{ key: "mine", label: "My prospects", icon: Users,
                    active: ownerScope === "mine",
                    apply: () => setOwnerScope("mine"),
                    clear: () => setOwnerScope("all") }]),
                ];
                return views.map((v) => {
                  const Icon = v.icon;
                  return (
                    <button
                      key={v.key}
                      type="button"
                      onClick={() => (v.active ? v.clear() : v.apply())}
                      style={{
                        height: 32, padding: "0 12px", borderRadius: 999,
                        border: v.active ? "1.5px solid #9ace3d" : "1px solid #dce8f4",
                        background: v.active ? "#f3fbe3" : "#fff",
                        color: v.active ? "#4d7c0f" : "#4a6580",
                        fontSize: 12.5, fontWeight: 700, cursor: "pointer",
                        display: "inline-flex", alignItems: "center", gap: 6, whiteSpace: "nowrap",
                      }}
                    >
                      <Icon size={13} /> {v.label}
                    </button>
                  );
                });
              })()}
            </div>

            {/* Filters — hidden by default; toggled by the "Filters" button in
                the top action row. Auto-shown when the user already has active
                filters so they can see what's narrowing the list. */}
            {showFilters && (() => {
              const hasFilters = !!(
                // For locked SDRs "mine" is the permanent baseline, not an
                // active filter — don't let it force the Reset / panel state.
                (!isSdrLocked && ownerScope === "mine") ||
                sequenceFilter.length ||
                callDispositionFilter.length ||
                linkedinStatusFilter.length ||
                callOutcomeColorFilter.length ||
                emailOutcomeColorFilter.length ||
                callAttemptsBucketFilter.length ||
                followupCountMin != null ||
                followupCountMax != null ||
                nextFollowupRange.from || nextFollowupRange.to ||
                callLastRange.from || callLastRange.to ||
                aeFilter.length ||
                sdrFilter.length ||
                ownerFilter.length ||
                timezoneFilter.length ||
                companyFilter ||
                search
              );
              const teamUserOptions = [
                // Sentinel for "no owner" — backend maps "__unassigned__" to an
                // IS NULL clause on the matching ownership slot(s) so reps can
                // surface prospects that slipped through with no AE/SDR.
                { value: "__unassigned__", label: "Unassigned" },
                ...teamUsers.map((u) => ({
                  value: u.id,
                  label: u.name || u.email,
                })),
              ];
              return (
                <div className="prospect-desktop-only" style={{
                  display: "flex", flexDirection: "column", alignItems: "stretch", gap: 9,
                  background: "#fff", borderRadius: 16,
                  border: "1px solid #e8eef5",
                  padding: "14px 18px",
                  boxShadow: "0 2px 10px rgba(17,34,68,0.05)",
                }}>
                  {/* GROUP: Prospects — scope + ownership */}
                  <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
                    <span style={{ fontSize: 11, fontWeight: 800, color: "#8294a8", textTransform: "uppercase", letterSpacing: "0.08em", width: 86, flexShrink: 0, lineHeight: 1.25 }}>Prospects</span>
                    <div className="filter-row" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end", flex: 1 }}>
                  {/* View — all vs mine. SDRs are locked to their own prospects,
                      so render a static label instead of an interactive select. */}
                  <div style={{ display: "flex", flexDirection: "column" }}>
                    {isSdrLocked ? (
                      <div
                        style={{
                          height: 42,
                          padding: "0 14px",
                          borderRadius: 12,
                          border: "1.5px solid #9ace3d",
                          fontSize: 13,
                          fontWeight: 700,
                          color: "#4d7c0f",
                          background: "#f3fbe3",
                          minWidth: 150,
                          display: "flex",
                          alignItems: "center",
                        }}
                      >
                        My prospects
                      </div>
                    ) : (
                    <select
                      value={ownerScope}
                      onChange={(e) => setOwnerScope(e.target.value === "mine" ? "mine" : "all")}
                      style={{
                        height: 42,
                        padding: "0 28px 0 12px",
                        borderRadius: 12,
                        border: ownerScope === "mine" ? "1.5px solid #9ace3d" : "1px solid #d9e1ec",
                        fontSize: 13,
                        color: "#1d2b3c",
                        background: ownerScope === "mine" ? "#f3fbe3" : "#fff",
                        outline: "none",
                        minWidth: 150,
                        cursor: "pointer",
                      }}
                    >
                      <option value="all">All prospects</option>
                      <option value="mine">My prospects</option>
                    </select>
                    )}
                  </div>
                  {/* Company */}
                  <div style={{ display: "flex", flexDirection: "column" }}>
                    <div style={{ width: "100%" }}>
                      <SearchableCompanySelect
                      value={companyFilter}
                        companies={companyOptions}
                        onChange={(companyId) => setCompanyFilter(companyId ?? "")}
                        placeholder="Search company..."
                        noneLabel="All companies"
                        allowNone
                      />
                    </div>
                  </div>
                    </div>{/* end Prospects row */}
                  </div>{/* end Prospects group */}

                  {/* GROUP: Engagement — sequence + outcome dots */}
                  <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
                    <span style={{ fontSize: 11, fontWeight: 800, color: "#8294a8", textTransform: "uppercase", letterSpacing: "0.08em", width: 86, flexShrink: 0, lineHeight: 1.25 }}>Engagement</span>
                    <div className="filter-row" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end", flex: 1 }}>
                  <MultiSelectFilter
                    hideLabel
                    label="Sequence"
                    values={sequenceFilter}
                    onChange={setSequenceFilter}
                    options={SEQUENCE_FILTER_OPTIONS}
                    allLabel="All sequence states"
                    minWidth={170}
                  />
                  <MultiSelectFilter
                    hideLabel
                    label="Call disposition"
                    values={callDispositionFilter}
                    onChange={setCallDispositionFilter}
                    options={CALL_DISPOSITION_FILTER_OPTIONS}
                    allLabel="All call outcomes"
                    minWidth={190}
                  />
                  <MultiSelectFilter
                    hideLabel
                    label="LinkedIn status"
                    values={linkedinStatusFilter}
                    onChange={setLinkedinStatusFilter}
                    options={LINKEDIN_STATUS_FILTER_OPTIONS}
                    allLabel="All LinkedIn"
                    minWidth={180}
                  />
                  {/* Outcome-color + attempt filters — match the dots
                      rendered by ProgressCell so the rep can click "Green"
                      and see only the prospects with a positive reply or
                      booked meeting. Backend translates colors via
                      app/repositories/contact.py. */}
                  <MultiSelectFilter
                    hideLabel
                    label="Call dots"
                    values={callOutcomeColorFilter}
                    onChange={setCallOutcomeColorFilter}
                    options={CALL_OUTCOME_COLOR_OPTIONS}
                    allLabel="All call dots"
                    minWidth={210}
                  />
                  <MultiSelectFilter
                    hideLabel
                    label="Email dots"
                    values={emailOutcomeColorFilter}
                    onChange={setEmailOutcomeColorFilter}
                    options={EMAIL_OUTCOME_COLOR_OPTIONS}
                    allLabel="All email dots"
                    minWidth={210}
                  />
                    </div>{/* end Engagement row */}
                  </div>{/* end Engagement group */}

                  {/* GROUP: Calling & follow-up */}
                  <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
                    <span style={{ fontSize: 11, fontWeight: 800, color: "#8294a8", textTransform: "uppercase", letterSpacing: "0.08em", width: 86, flexShrink: 0, lineHeight: 1.25 }}>Calling</span>
                    <div className="filter-row" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end", flex: 1 }}>
                  <MultiSelectFilter
                    hideLabel
                    label="Call attempts"
                    values={callAttemptsBucketFilter}
                    onChange={setCallAttemptsBucketFilter}
                    options={CALL_ATTEMPTS_BUCKET_OPTIONS}
                    allLabel="Any count"
                    minWidth={150}
                  />
                  {/* Follow-up count range — number of logged calls. More
                      granular than the bucket filter above (e.g. "called 2–5
                      times"). Backed by call_attempt_count on the server. */}
                  <RangeFilter
                    hideLabel
                    label="Follow-ups"
                    min={followupCountMin}
                    max={followupCountMax}
                    onChange={(min, max) => { setFollowupCountMin(min); setFollowupCountMax(max); }}
                    allLabel="Any number"
                    unit="calls"
                    minWidth={150}
                    presets={[
                      { label: "1+", min: 1, max: null },
                      { label: "2+", min: 2, max: null },
                      { label: "3–5", min: 3, max: 5 },
                      { label: "6+", min: 6, max: null },
                    ]}
                  />
                  {/* Scheduled follow-up date range — filters next_followup_at,
                      the callback the rep booked. Presets target the common
                      "who do I owe a follow-up" workflows. */}
                  <DateRangeFilter
                    hideLabel
                    label="Follow-up due"
                    value={nextFollowupRange}
                    onChange={setNextFollowupRange}
                    allLabel="Any follow-up date"
                    minWidth={190}
                    presets={followupDuePresets}
                  />
                  {/* Last-call date range — filters call_last_at, when the
                      prospect was last dialed. */}
                  <DateRangeFilter
                    hideLabel
                    label="Last call"
                    value={callLastRange}
                    onChange={setCallLastRange}
                    allLabel="Any last-call date"
                    minWidth={190}
                    presets={lastCallPresets}
                  />
                    </div>{/* end Calling row */}
                  </div>{/* end Calling group */}

                  {/* GROUP: Ownership & timezone */}
                  <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
                    <span style={{ fontSize: 11, fontWeight: 800, color: "#8294a8", textTransform: "uppercase", letterSpacing: "0.08em", width: 86, flexShrink: 0, lineHeight: 1.25 }}>Ownership</span>
                    <div className="filter-row" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end", flex: 1 }}>
                  {/* Owner filters */}
                  {teamUsers.length > 0 && (
                    <>
                      <MultiSelectFilter
                        hideLabel
                        label="Owner"
                        values={ownerFilter}
                        onChange={setOwnerFilter}
                        options={teamUserOptions}
                        allLabel="Owner: All"
                        minWidth={170}
                      />
                      <MultiSelectFilter
                        hideLabel
                        label="AE"
                        values={aeFilter}
                        onChange={setAeFilter}
                        options={teamUserOptions}
                        allLabel="AE: All"
                        minWidth={160}
                      />
                      <MultiSelectFilter
                        hideLabel
                        label="SDR"
                        values={sdrFilter}
                        onChange={setSdrFilter}
                        options={teamUserOptions}
                        allLabel="SDR: All"
                        minWidth={160}
                      />
                    </>
                  )}
                  <MultiSelectFilter
                    hideLabel
                    label="Timezone"
                    values={timezoneFilter}
                    onChange={setTimezoneFilter}
                    options={TIMEZONE_OPTIONS.map((tz) => ({ value: tz, label: tz }))}
                    allLabel="All timezones"
                    minWidth={170}
                  />
                    </div>{/* end Ownership row */}
                  </div>{/* end Ownership group */}

                  {/* TOOLBAR — sort · columns · count · reset */}
                  <div style={{ height: 1, background: "#eef2f7", margin: "2px 0 0" }} />
                  <div style={{ display: "flex", alignItems: "flex-end", gap: 10, flexWrap: "wrap" }}>

                  {/* Sort — server-side so it covers the full dataset, not
                      just the visible 50. Ties are broken by contact.id so
                      pagination is stable. */}
                  <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                    <span style={{ fontSize: 10, fontWeight: 700, color: "#4a6580", textTransform: "uppercase", letterSpacing: 0.4 }}>Sort</span>
                    <select
                      value={prospectSort}
                      onChange={(e) => setProspectSort(e.target.value as ProspectSortKey)}
                      style={{
                        height: 34, padding: "0 28px 0 10px", borderRadius: 9,
                        border: prospectSort === "recent" ? "1px solid #c8d9e8" : "1.5px solid #b8d0f0",
                        background: prospectSort === "recent" ? "#fff" : "#eef5ff",
                        fontSize: 13, color: "#0f2744", outline: "none",
                        minWidth: 170, cursor: "pointer",
                      }}
                    >
                      {PROSPECT_SORT_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                  </div>

                  <div style={{ position: "relative" }}>
                    <button
                      type="button"
                      onClick={() => setColumnMenuOpen((current) => !current)}
                      style={{
                        display: "inline-flex", alignItems: "center", gap: 6,
                        height: 34, padding: "0 12px", borderRadius: 10,
                        border: "1px solid #dce8f4", background: "#fff",
                        color: "#4a6580", fontSize: 12, fontWeight: 700, cursor: "pointer",
                      }}
                    >
                      <Settings2 size={13} />
                      Customize table
                    </button>
                    {columnMenuOpen && (
                      <div style={{
                        position: "absolute", top: "calc(100% + 8px)", left: 0, zIndex: 30,
                        width: 320, maxWidth: "calc(100vw - 32px)",
                        maxHeight: "70vh", overflowY: "auto",
                        borderRadius: 14, border: "1px solid #dbe6f2", background: "#fff",
                        boxShadow: "0 18px 36px rgba(15,23,42,0.14)", padding: 10, display: "flex", flexDirection: "column", gap: 8,
                      }}>
                        <div style={{ fontSize: 11, fontWeight: 800, color: "#6f8095", textTransform: "uppercase", letterSpacing: "0.08em", padding: "2px 4px" }}>
                          Rearrange columns
                        </div>
                        {columnMenuItems.map((column) => {
                          const active = tableColumns.includes(column.key);
                          return (
                            <div
                              key={column.key}
                              draggable={active}
                              onDragStart={(event) => {
                                if (!active) return;
                                setDraggedColumn(column.key);
                                event.dataTransfer.effectAllowed = "move";
                                event.dataTransfer.setData("text/plain", column.key);
                              }}
                              onDragOver={(event) => {
                                if (!active || !draggedColumn || draggedColumn === column.key) return;
                                event.preventDefault();
                                event.dataTransfer.dropEffect = "move";
                              }}
                              onDrop={(event) => {
                                event.preventDefault();
                                const source = (event.dataTransfer.getData("text/plain") || draggedColumn) as ContactTableColumnKey | null;
                                if (source && active) moveTableColumnTo(source, column.key);
                                setDraggedColumn(null);
                              }}
                              onDragEnd={() => setDraggedColumn(null)}
                              style={{
                                display: "flex",
                                alignItems: "center",
                                gap: 8,
                                padding: "6px 4px",
                                borderRadius: 10,
                                background: draggedColumn === column.key ? "#eaf2ff" : active ? "#f8fbff" : "transparent",
                                opacity: active ? 1 : 0.72,
                              }}
                            >
                              <button type="button" onClick={() => moveTableColumn(column.key, -1)} disabled={!active} title="Move left" style={{ border: "none", background: "transparent", cursor: active ? "grab" : "default", color: active ? "#7a8ea4" : "#c5d1de", display: "inline-flex" }}>
                                <GripVertical size={13} />
                              </button>
                              <span style={{ flex: 1, fontSize: 12.5, color: "#24364b", fontWeight: 600 }}>{column.label}</span>
                              <button type="button" onClick={() => toggleTableColumn(column.key)} style={{ border: "1px solid #dce8f4", background: active ? "#f3fbe3" : "#fff", color: active ? "#4d7c0f" : "#546679", borderRadius: 8, padding: "4px 8px", fontSize: 11, fontWeight: 700, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4 }}>
                                <EyeOff size={11} />
                                {active ? "Hide" : "Show"}
                              </button>
                              <button type="button" onClick={() => moveTableColumn(column.key, 1)} disabled={!active} title="Move right" style={{ border: "none", background: "transparent", cursor: active ? "pointer" : "default", color: active ? "#7a8ea4" : "#c5d1de", display: "inline-flex" }}>
                                <ArrowLeftRight size={13} />
                              </button>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>

                  <div style={{ flex: 1 }} />

                  {/* Count */}
                  <span style={{
                    fontSize: 12, fontWeight: 600, color: "#4a6580",
                    background: "#f0f5fb", border: "1px solid #dce8f4",
                    borderRadius: 999, padding: "3px 10px", whiteSpace: "nowrap",
                  }}>
                    {contactsTotal === 0 ? "0 shown" : `${(page - 1) * pageSize + 1}-${Math.min(page * pageSize, contactsTotal)} of ${contactsTotal}`}
                  </span>

                  {/* Reset — only when filters active */}
                  {hasFilters && (
                    <button
                      type="button"
                      onClick={() => {
                        setSearch("");
                        // SDRs stay locked to their own prospects on reset.
                        setOwnerScope(isSdrLocked ? "mine" : "all");
                        setSequenceFilter([]); setCallDispositionFilter([]);
                        setLinkedinStatusFilter([]);
                        setCallOutcomeColorFilter([]); setEmailOutcomeColorFilter([]);
                        setCallAttemptsBucketFilter([]);
                        setFollowupCountMin(null); setFollowupCountMax(null);
                        setNextFollowupRange({ from: "", to: "" });
                        setCallLastRange({ from: "", to: "" });
                        setAeFilter([]); setSdrFilter([]);
                        setOwnerFilter([]);
                        setTimezoneFilter([]);
                        setCompanyFilter("");
                      }}
                      style={{
                        display: "inline-flex", alignItems: "center", gap: 5,
                        height: 34, padding: "0 12px", borderRadius: 9,
                        border: "1px solid #dce8f4", background: "#fff",
                        color: "#4a6580", fontSize: 12, fontWeight: 600,
                        cursor: "pointer", whiteSpace: "nowrap",
                      }}
                    >
                      <XCircle size={12} />
                      Reset
                    </button>
                  )}
                  </div>{/* end toolbar */}
                </div>
              );
            })()}


            {contacts.length > 0 && (user?.role === "sdr" || selectedContactIds.size > 0) && (
              <div
                className="prospect-desktop-only"
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 12,
                  flexWrap: "wrap",
                  border: "1px solid #cfe0f2",
                  background: "#f7fbff",
                  borderRadius: 14,
                  padding: "10px 14px",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                  <span style={{ color: "#24455f", fontSize: 13, fontWeight: 800 }}>
                    {selectedContactIds.size} selected
                  </span>
                  <button
                    type="button"
                    onClick={toggleVisibleContactSelection}
                    style={{
                      height: 34,
                      border: "1px solid #bfd6ee",
                      background: "#ffffff",
                      color: "#175089",
                      borderRadius: 10,
                      padding: "0 12px",
                      fontSize: 12,
                      fontWeight: 800,
                      cursor: "pointer",
                    }}
                  >
                    {allVisibleSelected ? "Clear page" : "Select visible page"}
                  </button>
                  <select
                    value=""
                    onChange={(e) => {
                      if (e.target.value) selectVisibleCompanyContacts(e.target.value);
                      e.currentTarget.value = "";
                    }}
                    style={{
                      height: 34,
                      minWidth: 220,
                      border: "1px solid #bfd6ee",
                      background: "#ffffff",
                      color: "#24455f",
                      borderRadius: 10,
                      padding: "0 10px",
                      fontSize: 12,
                      fontWeight: 700,
                      outline: "none",
                    }}
                  >
                    <option value="">Select visible company...</option>
                    {visibleCompanySelectionOptions.map((company) => (
                      <option key={company.id} value={company.id}>{company.name} ({company.count})</option>
                    ))}
                  </select>
                  {selectedContactIds.size > 0 && (
                    <button
                      type="button"
                      onClick={() => setSelectedContactIds(new Set())}
                      style={{
                        height: 34,
                        border: "1px solid #dce8f4",
                        background: "#fff",
                        color: "#64748b",
                        borderRadius: 10,
                        padding: "0 12px",
                        fontSize: 12,
                        fontWeight: 750,
                        cursor: "pointer",
                      }}
                    >
                      Clear
                    </button>
                  )}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <button
                    type="button"
                    onClick={() => void deleteContactsByIds(Array.from(selectedContactIds))}
                    disabled={selectedContactIds.size === 0 || deletingContacts}
                    style={{
                      height: 36,
                      border: "1px solid #f0c2c2",
                      background: selectedContactIds.size ? "#fff1f1" : "#f6f8fb",
                      color: selectedContactIds.size ? "#b3261e" : "#9aa8b7",
                      borderRadius: 11,
                      padding: "0 14px",
                      fontSize: 13,
                      fontWeight: 850,
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 6,
                      cursor: selectedContactIds.size && !deletingContacts ? "pointer" : "not-allowed",
                    }}
                  >
                    <Trash2 size={14} /> {deletingContacts ? "Deleting..." : "Delete selected"}
                  </button>
                  <button
                    type="button"
                    onClick={openCampaignModal}
                    disabled={selectedContactIds.size === 0}
                    style={{
                      height: 36,
                      border: "1px solid #bfe3cb",
                      background: selectedContactIds.size ? "#e9f9ef" : "#f6f8fb",
                      color: selectedContactIds.size ? "#0f9d58" : "#9aa8b7",
                      borderRadius: 11,
                      padding: "0 14px",
                      fontSize: 13,
                      fontWeight: 850,
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 6,
                      cursor: selectedContactIds.size ? "pointer" : "not-allowed",
                    }}
                  >
                    <Send size={14} /> Start campaign
                  </button>
                  <button
                    type="button"
                    onClick={openBulkFollowup}
                    disabled={selectedContactIds.size === 0}
                    style={{
                      height: 36,
                      border: "1px solid #f5d77a",
                      background: selectedContactIds.size ? "#fffbeb" : "#f6f8fb",
                      color: selectedContactIds.size ? "#92400e" : "#9aa8b7",
                      borderRadius: 11,
                      padding: "0 14px",
                      fontSize: 13,
                      fontWeight: 850,
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 6,
                      cursor: selectedContactIds.size ? "pointer" : "not-allowed",
                    }}
                  >
                    <Clock size={14} /> Add follow-up
                  </button>
                  {user?.role === "sdr" && (
                    <button
                      type="button"
                      onClick={() => void bulkClaimSelectedSdr()}
                      disabled={selectedContactIds.size === 0 || bulkClaimingSdr}
                      style={{
                        height: 36,
                        border: "none",
                        background: selectedContactIds.size ? "#175089" : "#d9e4ef",
                        color: "#ffffff",
                        borderRadius: 11,
                        padding: "0 14px",
                        fontSize: 13,
                        fontWeight: 850,
                        cursor: selectedContactIds.size && !bulkClaimingSdr ? "pointer" : "not-allowed",
                      }}
                    >
                      {bulkClaimingSdr ? "Claiming..." : "Claim selected as SDR"}
                    </button>
                  )}
                  {isAdmin && (
                    <select
                      value=""
                      disabled={selectedContactIds.size === 0 || bulkAssigningSdr}
                      onFocus={() => { if (assignableUsers.length === 0) getCachedUsers().then((u) => setAssignableUsers(u as never)).catch(() => {}); }}
                      onChange={(e) => { if (e.target.value) void bulkAssignSelectedSdr(e.target.value); e.currentTarget.value = ""; }}
                      title="Assign selected prospects' SDR"
                      style={{
                        height: 36,
                        border: "1px solid #bfd6ee",
                        background: selectedContactIds.size ? "#fff" : "#f6f8fb",
                        color: selectedContactIds.size ? "#175089" : "#9aa8b7",
                        borderRadius: 11,
                        padding: "0 12px",
                        fontSize: 13,
                        fontWeight: 800,
                        cursor: selectedContactIds.size ? "pointer" : "not-allowed",
                      }}
                    >
                      <option value="">{bulkAssigningSdr ? "Assigning…" : "Assign SDR →"}</option>
                      {assignableUsers
                        .filter((u) => ["sdr", "ae", "admin"].includes((u.role || "").toLowerCase()))
                        .map((u) => (
                          <option key={u.id} value={u.id}>{u.name || u.id} ({(u.role || "").toUpperCase()})</option>
                        ))}
                    </select>
                  )}
                </div>
              </div>
            )}

            {/* Contacts Table */}
            {loading ? (
              <div className="crm-panel p-14 text-center crm-muted prospect-desktop-only">Loading contacts...</div>
            ) : displayedContacts.length === 0 ? (
              <div className="crm-panel p-14 text-center text-[#6f8297] prospect-desktop-only">
                {cardFilter ? (
                  <>
                    <Users size={36} style={{ margin: "0 auto 12px", opacity: 0.3, color: "#4b6b8f" }} />
                    <div style={{ fontSize: 15, fontWeight: 800, color: "#25384d", marginBottom: 6 }}>No matching prospects in this view</div>
                    <div style={{ fontSize: 13, color: "#7a8ea4", marginBottom: 12 }}>None of the loaded prospects match this tile yet.</div>
                    <button type="button" onClick={() => setCardFilter(null)} style={{ border: "1px solid #dce8f4", background: "#fff", borderRadius: 10, padding: "8px 14px", fontSize: 13, fontWeight: 700, color: "#175089", cursor: "pointer" }}>Show all prospects</button>
                  </>
                ) : contactsTotal === 0 ? (
                  <>
                    <Users size={36} style={{ margin: "0 auto 12px", opacity: 0.35 }} />
                    <div style={{ fontSize: 16, fontWeight: 800, color: "#25384d", marginBottom: 8 }}>No prospects found</div>
                    <div style={{ fontSize: 13, color: "#7a8ea4", maxWidth: 420, margin: "0 auto", lineHeight: 1.6 }}>
                      Upload a CSV from the Upload Prospects button above, or add prospects manually with Add Prospect.
                    </div>
                  </>
                ) : (
                  <>
                    <Users size={36} style={{ margin: "0 auto 12px", opacity: 0.25 }} />
                    <div style={{ fontSize: 15, fontWeight: 700, color: "#4a6580" }}>No contacts match your search</div>
                  </>
                )}
              </div>
            ) : (
              <div className="crm-panel overflow-hidden contacts-table-panel prospect-desktop-only">
                {/* Top pager — mirrors the bottom one so reps can page without
                    scrolling to the end of the list. */}
                <div style={{ padding: "12px 16px", borderBottom: "1px solid #e8eef5", background: "#fbfdff" }}>
                  <Pagination page={page} totalPages={Math.max(contactsPages, 1)} total={contactsTotal} pageSize={pageSize} onChange={setPage} />
                </div>
                <div className="overflow-x-auto">
                  <table className="crm-table" style={{ minWidth: 1080 }}>
                    <thead>
                      <tr>
                        {canSelectProspects && (
                          <th style={{ position: "sticky", top: 0, zIndex: 2, background: "#f7faff", width: 44 }}>
                            <input
                              type="checkbox"
                              checked={allVisibleSelected}
                              onChange={toggleVisibleContactSelection}
                              aria-label="Select all visible prospects"
                              style={{ width: 16, height: 16, accentColor: "#175089" }}
                            />
                          </th>
                        )}
                        {visibleColumns.map((column) => (
                          <th key={column.key} style={{ position: "sticky", top: 0, zIndex: 2, background: "#f7faff" }}>{column.label}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {displayedContacts.map((c) => (
                        <tr key={c.id} className="cursor-pointer" onClick={() => navigate(`/contacts/${c.id}`)}>
                          {canSelectProspects && (
                            <td onClick={(e) => e.stopPropagation()} style={{ width: 44 }}>
                              <input
                                type="checkbox"
                                checked={selectedContactIds.has(c.id)}
                                onChange={() => toggleContactSelection(c.id)}
                                aria-label={`Select ${c.first_name} ${c.last_name}`}
                                style={{ width: 16, height: 16, accentColor: "#175089" }}
                              />
                            </td>
                          )}
                          {visibleColumns.map((column) => {
                            switch (column.key) {
                              case "name": {
                                const persona = c.persona_type ? personaChipStyle(c.persona_type) : null;
                                const lastTouch = latestProspectActivity(c);
                                const opens = c.email_open_count ?? 0;
                                const clicks = c.email_click_count ?? 0;
                                return (
                                  <td key={column.key}>
                                    <div className="flex items-center gap-3 min-w-0">
                                      <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-[12px] font-extrabold ${avatarColor(c.first_name + c.last_name)}`}>
                                        {getInitials(`${c.first_name} ${c.last_name}`)}
                                      </div>
                                      <div className="min-w-0" style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                                        <p className="font-bold text-[#0f1f33] truncate" style={{ fontSize: 13.5, lineHeight: 1.25 }}>
                                          {c.first_name} {c.last_name}
                                        </p>
                                        {c.seniority && (
                                          <p style={{ fontSize: 11.5, color: "#7d8ea6", fontWeight: 600, lineHeight: 1.2, margin: 0 }}>
                                            {c.seniority}
                                          </p>
                                        )}
                                        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 2 }}>
                                          {persona && (
                                            <span style={{
                                              fontSize: 10, fontWeight: 700, padding: "2px 7px",
                                              borderRadius: 999, background: persona.bg, color: persona.fg,
                                              border: `1px solid ${persona.border}`, lineHeight: 1.4, whiteSpace: "nowrap",
                                            }}>
                                              {persona.label}
                                            </span>
                                          )}
                                          {opens > 0 && (
                                            <span title={`${opens} email opens`} style={{
                                              fontSize: 10, fontWeight: 700, padding: "2px 7px",
                                              borderRadius: 999, background: "#fef3c7", color: "#92400e",
                                              border: "1px solid #fde68a", lineHeight: 1.4, whiteSpace: "nowrap",
                                            }}>
                                              👁 {opens}
                                            </span>
                                          )}
                                          {clicks > 0 && (
                                            <span title={`${clicks} email clicks`} style={{
                                              fontSize: 10, fontWeight: 700, padding: "2px 7px",
                                              borderRadius: 999, background: "#dcfce7", color: "#166534",
                                              border: "1px solid #bbf7d0", lineHeight: 1.4, whiteSpace: "nowrap",
                                            }}>
                                              ↗ {clicks}
                                            </span>
                                          )}
                                          {lastTouch && (
                                            <span title={new Date(lastTouch).toLocaleString()} style={{
                                              fontSize: 10, fontWeight: 700, padding: "2px 7px",
                                              borderRadius: 999, background: "#eef2ff", color: "#3730a3",
                                              border: "1px solid #c7d2fe", lineHeight: 1.4, whiteSpace: "nowrap",
                                            }}>
                                              {relativeTimeShort(lastTouch)}
                                            </span>
                                          )}
                                        </div>
                                      </div>
                                    </div>
                                  </td>
                                );
                              }
                              case "company":
                                return (
                                  <td key={column.key}>
                                    <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
                                      {c.company_name ? (
                                        <button
                                          onClick={(e) => {
                                            e.stopPropagation();
                                            if (c.company_id) navigate(`/account-sourcing/${c.company_id}`);
                                          }}
                                          className="text-[#2b6cb0] font-semibold text-[13px] hover:underline"
                                          style={{ textAlign: "left", display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "100%" }}
                                        >
                                          {c.company_name}
                                        </button>
                                      ) : (
                                        <span className="text-[#96a7ba]">-</span>
                                      )}
                                    </div>
                                  </td>
                                );
                              case "title":
                                return <td key={column.key}>{c.title ?? <span className="text-[#96a7ba]">-</span>}</td>;
                              case "email":
                                return (
                                  <td key={column.key}>
                                    <div style={{ display: "flex", flexDirection: "column", gap: 5, minWidth: 0 }}>
                                      {c.email
                                        ? (
                                          <span
                                            title={c.email}
                                            style={{ fontSize: 12.5, color: "#1e3a52", fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "flex", alignItems: "center", gap: 5 }}
                                          >
                                            {c.email_verified && (
                                              <span title="Email verified" style={{ flexShrink: 0, width: 6, height: 6, borderRadius: 999, background: "#22c55e", boxShadow: "0 0 0 2px #dcfce7" }} />
                                            )}
                                            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.email}</span>
                                          </span>
                                        )
                                        : <span className="text-[#96a7ba]">No email</span>
                                      }
                                      <div style={{ display: "flex", gap: 5, alignItems: "center", flexWrap: "wrap" }}>
                                        <span title={c.phone ? `Phone: ${c.phone}` : "No phone"} style={{
                                          display: "inline-flex", alignItems: "center", gap: 3,
                                          fontSize: 10.5, fontWeight: 700, padding: "2px 7px",
                                          borderRadius: 999, lineHeight: 1.3,
                                          background: c.phone ? "#ecfeff" : "#f1f5f9",
                                          color: c.phone ? "#0891b2" : "#94a3b8",
                                          border: `1px solid ${c.phone ? "#a5f3fc" : "#e2e8f0"}`,
                                        }}>
                                          <Phone size={9} strokeWidth={2.5} /> {c.phone ? "Call" : "—"}
                                        </span>
                                        <span title={c.linkedin_url ? "LinkedIn available" : "No LinkedIn"} style={{
                                          display: "inline-flex", alignItems: "center", gap: 3,
                                          fontSize: 10.5, fontWeight: 700, padding: "2px 7px",
                                          borderRadius: 999, lineHeight: 1.3,
                                          background: c.linkedin_url ? "#eff6ff" : "#f1f5f9",
                                          color: c.linkedin_url ? "#1d4ed8" : "#94a3b8",
                                          border: `1px solid ${c.linkedin_url ? "#bfdbfe" : "#e2e8f0"}`,
                                        }}>
                                          <Link2 size={9} strokeWidth={2.5} /> {c.linkedin_url ? "LI" : "—"}
                                        </span>
                                      </div>
                                    </div>
                                  </td>
                                );
                              case "progress":
                                return (
                                  <td
                                    key={column.key}
                                    onClick={(e) => { e.stopPropagation(); setLifecycleContactId(c.id); }}
                                    style={{ cursor: "pointer" }}
                                  >
                                    <ProgressCell contact={c} lifecycle={lifecycleSummaries[c.id]} />
                                  </td>
                                );
                              case "comments":
                                return (
                                  <td
                                    key={column.key}
                                    onClick={(e) => { e.stopPropagation(); openComments(c); }}
                                    style={{ cursor: "pointer", maxWidth: 220 }}
                                    title={c.latest_comment || "Add a comment"}
                                  >
                                    {c.latest_comment ? (
                                      <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
                                        <span style={{ fontSize: 12.5, color: "#33485f", lineHeight: 1.35, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
                                          {c.latest_comment}
                                        </span>
                                        {(c.comment_count ?? 0) > 1 && (
                                          <span style={{ fontSize: 10.5, fontWeight: 700, color: "#6f8297" }}>
                                            {c.comment_count} comments · view all
                                          </span>
                                        )}
                                      </div>
                                    ) : (
                                      <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12, fontWeight: 700, color: "#8aa0b6" }}>
                                        <MessageSquare size={13} /> Add comment
                                      </span>
                                    )}
                                  </td>
                                );
                              case "timezone": {
                                const isEditing = editingTimezoneId === c.id;
                                const currentLabel = formatTimezoneLabel(c.timezone);
                                return (
                                  <td key={column.key} onClick={(e) => e.stopPropagation()}>
                                    {isEditing ? (
                                      <select
                                        autoFocus
                                        value={timezoneDraft}
                                        disabled={savingTimezoneId === c.id}
                                        onChange={(e) => {
                                          setTimezoneDraft(e.target.value);
                                          void saveTimezone(c, e.target.value);
                                        }}
                                        onBlur={() => {
                                          if (editingTimezoneId === c.id && timezoneDraft === (c.timezone ?? "")) {
                                            setEditingTimezoneId(null);
                                            setTimezoneDraft("");
                                          }
                                        }}
                                        style={{
                                          height: 30,
                                          borderRadius: 9,
                                          border: "1px solid #bfd6f3",
                                          background: "#fff",
                                          color: "#0f2744",
                                          padding: "0 8px",
                                          fontSize: 12,
                                          fontWeight: 700,
                                          outline: "none",
                                        }}
                                      >
                                        <option value="">Unassigned</option>
                                        {c.timezone && !TIMEZONE_OPTIONS.includes(c.timezone as typeof TIMEZONE_OPTIONS[number]) && !Object.values(TIMEZONE_LABELS).includes(c.timezone) && (
                                          <option value={c.timezone}>{currentLabel}</option>
                                        )}
                                        {TIMEZONE_OPTIONS.map((tz) => (
                                          <option key={tz} value={tz}>{tz}</option>
                                        ))}
                                      </select>
                                    ) : (
                                      <button
                                        type="button"
                                        onClick={() => {
                                          setEditingTimezoneId(c.id);
                                          setTimezoneDraft(currentLabel || "");
                                        }}
                                        style={{
                                          display: "inline-flex",
                                          alignItems: "center",
                                          gap: 5,
                                          border: "1px solid #dce8f4",
                                          borderRadius: 999,
                                          background: "#fff",
                                          color: currentLabel ? "#4a6580" : "#9aaabd",
                                          padding: "4px 9px",
                                          fontSize: 12,
                                          fontWeight: 700,
                                          cursor: "pointer",
                                          whiteSpace: "nowrap",
                                        }}
                                        title="Click to edit timezone"
                                      >
                                        <Globe size={11} />
                                        {currentLabel || "Add TZ"}
                                      </button>
                                    )}
                                  </td>
                                );
                              }
                              case "ae":
                                return (
                                  <td key={column.key} onClick={(e) => e.stopPropagation()}>
                                    <AssignDropdown entityType="contact" entityId={c.id} currentAssignedId={c.assigned_to_id} currentAssignedName={c.assigned_to_name || c.assigned_rep_email} onAssigned={() => loadContacts()} role="ae" label="AE" compact />
                                  </td>
                                );
                              case "sdr":
                                return (
                                  <td key={column.key} onClick={(e) => e.stopPropagation()}>
                                    <AssignDropdown entityType="contact" entityId={c.id} currentAssignedId={c.sdr_id} currentAssignedName={c.sdr_name} onAssigned={() => loadContacts()} role="sdr" label="SDR" compact />
                                  </td>
                                );
                              case "action":
                                return (
                                  <td key={column.key} onClick={(e) => e.stopPropagation()}>
                                    <div style={{ position: "relative", display: "inline-flex", alignItems: "flex-start", gap: 8 }}>
                                      {/* Call button + a caption showing how many
                                          times this prospect was called and how
                                          recently — so a rep sees touch history
                                          without opening the drawer. */}
                                      <div style={{ display: "inline-flex", flexDirection: "column", alignItems: "stretch", gap: 3 }}>
                                        <button type="button" disabled={!c.phone} onClick={(e) => { e.stopPropagation(); if (c.phone) openCallSidebar(c); }} style={{ height: 38, borderRadius: 10, border: "1px solid #c8daf0", background: c.phone ? "#eaf2ff" : "#f6f8fb", color: c.phone ? "#175089" : "#9aa8b7", padding: "0 10px", display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6, cursor: c.phone ? "pointer" : "default", fontSize: 12.5, fontWeight: 700 }} title={c.phone ? c.phone : "No phone number"}>
                                          <Phone size={13} /> Call
                                        </button>
                                        <span style={{ fontSize: 10.5, fontWeight: 600, lineHeight: 1.15, textAlign: "center", whiteSpace: "nowrap", color: (c.call_attempt_count ?? 0) > 0 ? "#5b6b7d" : "#9fb0c0" }}>
                                          {(c.call_attempt_count ?? 0) > 0
                                            ? `${c.call_attempt_count} call${c.call_attempt_count === 1 ? "" : "s"}${c.call_last_at ? ` · ${relativeTimeShort(c.call_last_at)}` : ""}`
                                            : "No calls yet"}
                                        </span>
                                      </div>
                                      <a href={c.email ? gmailComposeUrl(c.email) : undefined} target="_blank" rel="noopener noreferrer" onClick={(e) => { e.stopPropagation(); if (!c.email) e.preventDefault(); }} style={{ height: 38, borderRadius: 10, border: "1px solid #bfd8c7", background: c.email ? "#ecfdf3" : "#f6f8fb", color: c.email ? "#1f7a4d" : "#9aa8b7", padding: "0 10px", display: "inline-flex", alignItems: "center", gap: 6, cursor: c.email ? "pointer" : "default", fontSize: 12.5, fontWeight: 700, textDecoration: "none" }} title={c.email ? `Email ${c.email} in Gmail` : "No email saved"}>
                                        <Mail size={13} /> Email
                                      </a>
                                      <a href={c.linkedin_url || undefined} target="_blank" rel="noopener noreferrer" onClick={(e) => { e.stopPropagation(); if (!c.linkedin_url) e.preventDefault(); }} style={{ height: 38, borderRadius: 10, border: "1px solid #b8d4f0", background: c.linkedin_url ? "#e8f2ff" : "#f6f8fb", color: c.linkedin_url ? "#0a66c2" : "#9aa8b7", padding: "0 10px", display: "inline-flex", alignItems: "center", gap: 6, cursor: c.linkedin_url ? "pointer" : "default", fontSize: 12.5, fontWeight: 700, textDecoration: "none" }} title={c.linkedin_url ? "Open LinkedIn profile" : "No LinkedIn profile"}>
                                        <Link2 size={13} /> LinkedIn
                                      </a>
                                      <button type="button" onClick={(e) => { e.stopPropagation(); setLinkedinContact(c); setLinkedinStatus(c.linkedin_status && c.linkedin_status !== "none" ? c.linkedin_status : "sent"); setLinkedinNotes(""); }} style={{ height: 38, borderRadius: 10, border: "1px solid #ddd6fe", background: "#f5f3ff", color: "#6d28d9", padding: "0 10px", display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 12.5, fontWeight: 700 }} title="Log LinkedIn touch">
                                        <Link2 size={13} /> Log
                                      </button>
                                      <button type="button" disabled={!c.phone} onClick={(e) => { e.stopPropagation(); if (c.phone) { setWhatsappContact(c); setWhatsappOutcome("sent"); setWhatsappNotes(""); } }} style={{ height: 38, borderRadius: 10, border: "1px solid #b7e3c5", background: c.phone ? "#e7f9ef" : "#f6f8fb", color: c.phone ? "#0f9d58" : "#9aa8b7", padding: "0 10px", display: "inline-flex", alignItems: "center", gap: 6, cursor: c.phone ? "pointer" : "default", fontSize: 12.5, fontWeight: 700 }} title={c.phone ? "WhatsApp & log message" : "No phone number"}>
                                        <MessageCircle size={13} /> WhatsApp
                                      </button>
                                      <button type="button" onClick={(e) => { e.stopPropagation(); setOpenActionsId((current) => (current === c.id ? null : c.id)); }} style={{ width: 38, height: 38, borderRadius: 12, border: "1px solid #dce8f4", background: "#fff", color: "#4a6580", display: "inline-flex", alignItems: "center", justifyContent: "center", cursor: "pointer" }} title="Prospect actions">
                                        <MoreHorizontal size={16} />
                                      </button>
                                      {openActionsId === c.id ? (
                                        <div style={{ position: "absolute", top: 44, right: 0, zIndex: 20, minWidth: 180, borderRadius: 14, border: "1px solid #dce8f4", background: "#fff", boxShadow: "0 16px 36px rgba(15, 23, 42, 0.12)", padding: 8, display: "grid", gap: 4 }}>
                                          <button type="button" onClick={() => { setSelectedContact(c); setOpenActionsId(null); }} className="crm-button soft" style={{ width: "100%", justifyContent: "flex-start", height: 38, fontSize: 12.5 }}>
                                            <Sparkles className="h-3.5 w-3.5" />Outreach
                                          </button>
                                          <button type="button" onClick={() => { setTaskContact(c); setOpenActionsId(null); }} className="crm-button soft" style={{ width: "100%", justifyContent: "flex-start", height: 38, fontSize: 12.5 }}>
                                            <Plus className="h-3.5 w-3.5" />Manual task
                                          </button>
                                          <button type="button" disabled={!c.company_id} onClick={() => { setOpenActionsId(null); void handleConvertContactToDeal(c); }} className="crm-button soft" style={{ width: "100%", justifyContent: "flex-start", height: 38, fontSize: 12.5, opacity: c.company_id ? 1 : 0.55 }}>
                                            <Target className="h-3.5 w-3.5" />Convert to deal
                                          </button>
                                          <button type="button" disabled={deletingContacts} onClick={() => { void deleteContactsByIds([c.id]); }} className="crm-button soft" style={{ width: "100%", justifyContent: "flex-start", height: 38, fontSize: 12.5, color: "#b3261e" }}>
                                            <Trash2 className="h-3.5 w-3.5" />Delete prospect
                                          </button>
                                        </div>
                                      ) : null}
                                    </div>
                                  </td>
                                );
                              default:
                                return null;
                            }
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div style={{ padding: "12px 16px", borderTop: "1px solid #e8eef5", background: "#fbfdff" }}>
                  <Pagination page={page} totalPages={Math.max(contactsPages, 1)} total={contactsTotal} pageSize={pageSize} onChange={setPage} />
                </div>
              </div>
            )}
          </>
        )}

        {/* ═══════════════════════════════════════════════════════════════ */}
        {/* ANGEL MAPPING TAB                                              */}
        {/* ═══════════════════════════════════════════════════════════════ */}
        {tab === "angel-mapping" && (
          <>
            <div className="crm-panel overflow-hidden" style={ANGEL_SURFACE.hero}>
              <div className="grid gap-6 px-7 py-7 lg:grid-cols-[minmax(0,1.3fr)_minmax(320px,0.7fr)] lg:px-8">
                <div>
                  <span
                    className="inline-flex items-center gap-2 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.14em]"
                    style={{ borderRadius: 999, border: "1px solid rgba(255,255,255,0.15)", background: "rgba(255,255,255,0.1)", color: "rgba(255,255,255,0.82)" }}
                  >
                    <Network className="h-3.5 w-3.5" />
                    Warm Intro Graph
                  </span>
                  <h2 className="mt-4 text-[24px] font-bold tracking-[-0.02em]" style={{ color: "#ffffff" }}>
                    Angel Mapping for high-conviction prospecting
                  </h2>
                  <p className="mt-2 max-w-2xl text-[14px] leading-7" style={{ color: "rgba(255,255,255,0.78)" }}>
                    Rank investor-backed paths by strength, scan the best connection story for each stakeholder,
                    and decide where a warm introduction is worth spending team time.
                  </p>
                  <div className="mt-5 flex flex-wrap gap-2">
                    <span
                      className="inline-flex items-center gap-2 px-3 py-1.5 text-[12px] font-semibold"
                      style={{ borderRadius: 999, background: "rgba(255,255,255,0.12)", color: "#ffffff" }}
                    >
                      <Target className="h-3.5 w-3.5" />
                      {strongPathCount} strong path{strongPathCount === 1 ? "" : "s"} at strength 4+
                    </span>
                    <span
                      className="inline-flex items-center gap-2 px-3 py-1.5 text-[12px] font-semibold"
                      style={{ borderRadius: 999, background: "rgba(255,255,255,0.12)", color: "#ffffff" }}
                    >
                      <Users className="h-3.5 w-3.5" />
                      {visibleContactCount} prospects in view
                    </span>
                    <span
                      className="inline-flex items-center gap-2 px-3 py-1.5 text-[12px] font-semibold"
                      style={{ borderRadius: 999, background: "rgba(255,255,255,0.12)", color: "#ffffff" }}
                    >
                      <Building2 className="h-3.5 w-3.5" />
                      {groupedByCompany.length} mapped companies
                    </span>
                  </div>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <AngelOverviewCard
                    icon={<Users className="h-4 w-4" />}
                    label="Investors"
                    value={String(investors.length)}
                    caption="Angel and operator relationships available for intros."
                    tone="teal"
                  />
                  <AngelOverviewCard
                    icon={<Link2 className="h-4 w-4" />}
                    label="Visible Paths"
                    value={String(filteredMappings.length)}
                    caption="Filtered paths after search and strength thresholds."
                    tone="blue"
                  />
                  <AngelOverviewCard
                    icon={<Building2 className="h-4 w-4" />}
                    label="Accounts"
                    value={String(groupedByCompany.length)}
                    caption="Companies with at least one mapped connection path."
                    tone="amber"
                  />
                  <AngelOverviewCard
                    icon={<Star className="h-4 w-4" />}
                    label="Avg Strength"
                    value={avgStrength}
                    caption="Average path quality across the current working set."
                    tone="green"
                  />
                </div>
              </div>
            </div>

            <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
              <div className="crm-panel px-7 py-6" style={ANGEL_SURFACE.panel}>
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <div>
                    <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#8ca2ba]">Path Filters</p>
                    <p className="mt-3 text-[17px] font-bold text-[#1d2b3c]">Focus the intro graph</p>
                  </div>
                  <span className="crm-chip">
                    {filteredMappings.length} result{filteredMappings.length === 1 ? "" : "s"}
                  </span>
                </div>
                <div className="mt-5 flex flex-wrap items-center gap-3">
                  <span className="text-[13px] text-[#6f8297] font-medium" style={{ marginRight: 4 }}>Minimum path strength</span>
                  {[0, 3, 4, 5].map((s) => (
                    <button
                      key={s}
                      onClick={() => setFilterStrength(s)}
                      className="text-[12px] font-semibold border transition-colors"
                      style={{
                        padding: "8px 14px",
                        borderRadius: 12,
                        borderColor: filterStrength === s ? "#1f6feb" : "#d9e1ec",
                        background: filterStrength === s ? "#1f6feb" : "#ffffff",
                        color: filterStrength === s ? "#ffffff" : "#55657a",
                        boxShadow: filterStrength === s ? "0 10px 18px rgba(31,111,235,0.16)" : "none",
                      }}
                    >
                      {s === 0 ? "All" : `${s}+`}
                    </button>
                  ))}
                </div>
                <div className="mt-5 flex items-center gap-x-4 gap-y-3 flex-wrap">
                  {[5, 4, 3, 2, 1].map((s) => (
                    <span key={s} className="flex items-center gap-1.5">
                      <StrengthBadge strength={s} compact />
                      <span className="text-[11px] text-[#7f8fa5]">{STRENGTH_LABEL[s]}</span>
                    </span>
                  ))}
                </div>
              </div>

              <div className="crm-panel px-7 py-6" style={ANGEL_SURFACE.panel}>
                <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#8ca2ba]">Coverage Snapshot</p>
                <div className="mt-5 space-y-3.5">
                  <SnapshotRow label="Mapped prospects" value={String(visibleContactCount)} tone="blue" />
                  <SnapshotRow label="Connected investors" value={String(visibleInvestorCount)} tone="teal" />
                  <SnapshotRow label="Strength 5 paths" value={String(filteredMappings.filter((m) => m.strength === 5).length)} tone="green" />
                </div>
              </div>
            </div>

            {investors.length > 0 && (
              <div className="crm-panel px-7 py-6" style={ANGEL_SURFACE.panel}>
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <div>
                    <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#8ca2ba]">Relationship Bench</p>
                    <p className="mt-3 text-[17px] font-bold text-[#1d2b3c]">Investor network at a glance</p>
                  </div>
                  <span className="text-[12px] font-medium text-[#7f8fa5]">
                    Delete an investor here to remove their mapping graph.
                  </span>
                </div>
                <div className="mt-5 grid gap-3.5 md:grid-cols-2 xl:grid-cols-3">
                  {investors.map((inv) => {
                    const count = investorMappingCounts[inv.id] || 0;
                    const companyCount = new Set(
                      mappings
                        .filter((m) => m.angel_investor_id === inv.id)
                        .map((m) => m.company_name || "Unknown Company")
                    ).size;

                    return (
                      <div key={inv.id} className="group px-5 py-5" style={ANGEL_SURFACE.panel}>
                        <div className="flex items-start gap-3">
                          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-[#dff4f2] text-[#14766f]">
                            <Network className="h-4 w-4" />
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <p className="truncate text-[14px] font-bold text-[#1d2b3c]">{inv.name}</p>
                                <p className="mt-1 text-[12px] text-[#6c8196]">
                                  {inv.current_role && inv.current_company
                                    ? `${inv.current_role} @ ${inv.current_company}`
                                    : inv.current_role || inv.current_company || "Role or firm not added yet"}
                                </p>
                              </div>
                              <button
                                onClick={() => handleDeleteInvestor(inv.id)}
                                className="opacity-0 group-hover:opacity-100 rounded-lg p-1 text-[#aac0d4] transition hover:bg-[#fff2f2] hover:text-[#c0392b]"
                                title="Delete investor"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            </div>
                            <div className="mt-5 flex items-center gap-2 flex-wrap">
                              <span className="rounded-full bg-[#edf8f7] px-3 py-1.5 text-[11px] font-bold text-[#14766f]">
                                {count} mapped path{count === 1 ? "" : "s"}
                              </span>
                              {count > 0 && (
                                <span className="text-[11px] text-[#8aa0b4]">
                                  Active on {companyCount} account{companyCount === 1 ? "" : "s"}
                                </span>
                              )}
                            </div>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Mapping cards grouped by company */}
            {angelLoading ? (
              <div className="crm-panel p-14 text-center crm-muted">Loading angel mappings...</div>
            ) : groupedByCompany.length === 0 ? (
              <div className="crm-panel p-14 text-center text-[#6f8297]" style={ANGEL_SURFACE.panel}>
                <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center" style={{ borderRadius: 18, background: "#ecf7f6", color: "#16857d" }}>
                  <Network className="h-7 w-7" />
                </div>
                <p className="text-[17px] font-semibold text-[#2e4359]">No angel mappings in view yet</p>
                <p className="mx-auto mt-2 max-w-lg text-[13px] leading-6">
                  Import relationship data or add investors first, then use strength filters to focus the best warm-introduction paths.
                </p>
              </div>
            ) : (
              <div className="space-y-5">
                {groupedByCompany.map(({ companyName, contacts: companyContacts, totalMappings, maxStrength }) => (
                  <div
                    key={companyName}
                    className="crm-panel overflow-hidden transition-all"
                    style={{
                      ...ANGEL_SURFACE.companyCard,
                      boxShadow: expandedCompany === companyName
                        ? "0 18px 36px rgba(17, 34, 68, 0.1), 0 0 0 1px #dce8f7 inset"
                        : ANGEL_SURFACE.companyCard.boxShadow,
                    }}
                  >
                    <button
                      onClick={() => setExpandedCompany(expandedCompany === companyName ? null : companyName)}
                      className="w-full px-7 py-6 text-left transition-colors"
                      style={{
                        background: expandedCompany === companyName
                          ? "linear-gradient(180deg, #f7fbff 0%, #f4f9fd 100%)"
                          : "#ffffff",
                      }}
                    >
                      <div className="flex flex-wrap items-start gap-4 lg:flex-nowrap lg:items-center">
                        <div className="flex items-center gap-3">
                          <div className="flex h-11 w-11 shrink-0 items-center justify-center" style={{ borderRadius: 18, background: "#eaf3ff", color: "#1f6feb" }}>
                            <Building2 className="h-4 w-4" />
                          </div>
                          <div className="flex items-center gap-3">
                            {expandedCompany === companyName
                              ? <ChevronDown className="h-4 w-4 text-[#8094a8]" />
                              : <ChevronRight className="h-4 w-4 text-[#8094a8]" />
                            }
                            <div>
                              <p className="text-[16px] font-bold text-[#1d2b3c]">{companyName}</p>
                              <p className="mt-1.5 text-[12px] text-[#72879c]">
                                {companyContacts.length} stakeholder{companyContacts.length === 1 ? "" : "s"} with mapped intros
                              </p>
                            </div>
                          </div>
                        </div>
                        <div className="flex flex-wrap items-center gap-2.5 lg:ml-auto">
                          <span className="px-3.5 py-1.5 text-[11px] font-bold" style={{ borderRadius: 999, background: "#eef5ff", color: "#235dc6" }}>
                            {totalMappings} connection{totalMappings === 1 ? "" : "s"}
                          </span>
                          <span className="px-3.5 py-1.5 text-[11px] font-semibold" style={{ borderRadius: 999, background: "#f5f9fc", color: "#70849a" }}>
                            {companyContacts.filter((contact) => contact.mappings.some((mapping) => mapping.strength >= 4)).length} ready for warm intro
                          </span>
                          <StrengthBadge strength={maxStrength} labelPrefix="Best path" />
                        </div>
                      </div>
                    </button>

                    {expandedCompany === companyName && (
                      <div className="border-t border-[#e8eef5] p-6" style={{ background: "#fbfdff" }}>
                        <div className="space-y-5">
                          {companyContacts.map(({ name, title, linkedin, mappings: contactMappings }) => (
                            <div key={name} className="overflow-hidden" style={ANGEL_SURFACE.contactCard}>
                              <div
                                className="flex flex-wrap items-center gap-4 border-b border-[#eef3f8] px-6 py-5"
                                style={{ background: "linear-gradient(180deg, #ffffff 0%, #f9fbff 100%)" }}
                              >
                                <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-[11px] font-extrabold ${avatarColor(name)}`}>
                                  {getInitials(name)}
                                </div>
                                <div className="min-w-0 flex-1">
                                  <div className="flex items-center gap-2">
                                    <span className="truncate text-[14px] font-bold text-[#25384d]">{name}</span>
                                    {linkedin && (
                                      <a href={linkedin} target="_blank" rel="noopener noreferrer" className="text-[#2b6cb0] hover:text-[#1a4f8a]">
                                        <ExternalLink className="h-3.5 w-3.5" />
                                      </a>
                                    )}
                                  </div>
                                  {title && <p className="mt-1.5 text-[13px] text-[#7a8ea4]">{title}</p>}
                                </div>
                                <div className="px-3.5 py-1.5 text-[11px] font-semibold" style={{ borderRadius: 999, background: "#f2f6fb", color: "#6f8399" }}>
                                  {contactMappings.length} path{contactMappings.length === 1 ? "" : "s"}
                                </div>
                              </div>

                              <div className="space-y-4 p-5">
                                {contactMappings
                                  .sort((a, b) => a.rank - b.rank)
                                  .map((m) => (
                                    <div key={m.id} className="group transition" style={ANGEL_SURFACE.pathCard}>
                                      <div className="flex items-start gap-5">
                                        <div
                                          className="flex h-9 w-9 shrink-0 items-center justify-center text-[11px] font-mono font-bold"
                                          style={{ borderRadius: 12, background: "#eef4fb", color: "#5f7992" }}
                                        >
                                          #{m.rank}
                                        </div>
                                        <div className="min-w-0 flex-1">
                                          <div className="flex flex-wrap items-center gap-2.5">
                                            <span className="text-[14px] font-bold text-[#145d97]">{m.angel_name}</span>
                                            {m.angel_current_company && (
                                              <span className="text-[12px] text-[#7f8fa5]" style={{ lineHeight: 1.6 }}>
                                                {m.angel_current_role ? `${m.angel_current_role} @ ` : ""}
                                                {m.angel_current_company}
                                              </span>
                                            )}
                                            <StrengthBadge strength={m.strength} />
                                          </div>
                                          {m.connection_path && (
                                            <div
                                              className="mt-4 px-4 py-3.5 text-[13px] leading-7 text-[#55657a]"
                                              style={{ borderRadius: 16, background: "#f2f7fc" }}
                                            >
                                              <span className="font-semibold text-[#30465f]">Path</span>
                                              <p className="mt-1">{m.connection_path}</p>
                                            </div>
                                          )}
                                          {m.why_it_works && (
                                            <p className="mt-4 text-[13px] leading-7 text-[#677f96]" style={{ marginBottom: 0 }}>
                                              <span className="font-semibold text-[#3a4e63]">Why it works:</span> {m.why_it_works}
                                            </p>
                                          )}
                                          {m.recommended_strategy && (
                                            <div
                                              className="mt-4 inline-flex items-center px-3.5 py-2 text-[11px] font-semibold"
                                              style={{ borderRadius: 999, background: "#e8f5f4", color: "#126b64" }}
                                            >
                                              Strategy: {m.recommended_strategy}
                                            </div>
                                          )}
                                        </div>
                                        <button
                                          onClick={() => handleDeleteMapping(m.id)}
                                          className="opacity-0 group-hover:opacity-100 rounded-lg p-1.5 text-[#b7c6d4] transition hover:bg-[#fff2f2] hover:text-[#c0392b]"
                                          title="Remove mapping"
                                        >
                                          <Trash2 className="h-3.5 w-3.5" />
                                        </button>
                                      </div>
                                    </div>
                                  ))}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {/* Outreach drawer (contacts tab) */}
      <OutreachDrawer contact={selectedContact} onClose={() => setSelectedContact(null)} />

      {taskContact ? (
        <TaskCenterModal
          isOpen={Boolean(taskContact)}
          onClose={() => setTaskContact(null)}
          entityType="contact"
          entityId={taskContact.id}
          entityLabel={`${taskContact.first_name} ${taskContact.last_name}`.trim() || taskContact.email || "Prospect"}
          onChanged={() => loadContacts()}
        />
      ) : null}

      {uploadProgress && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
          <div className="bg-white rounded-2xl shadow-xl p-6 w-full max-w-md border border-[#d9e1ec]">
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
              <Loader2 size={16} className="animate-spin" color="#175089" />
              <h3 style={{ fontSize: 15, fontWeight: 700, color: "#1d2b3c" }}>
                {uploadProgress.phase === "uploading" ? "Uploading prospects" : "Processing on server"}
              </h3>
            </div>
            <p style={{ fontSize: 12.5, color: "#6b7e92", marginBottom: 14 }}>
              {uploadProgress.phase === "uploading"
                ? `Sending file… ${uploadProgress.percent}%`
                : "File received. Parsing rows, matching companies, and queuing enrichment. This usually takes a few seconds."}
            </p>
            {/* Bar: determinate during upload, indeterminate (striped pulse) during processing */}
            <div style={{ width: "100%", height: 8, background: "#eef3f9", borderRadius: 6, overflow: "hidden", position: "relative" }}>
              {uploadProgress.phase === "uploading" ? (
                <div
                  style={{
                    width: `${uploadProgress.percent}%`,
                    height: "100%",
                    background: "linear-gradient(90deg, #175089, #2c79c9)",
                    transition: "width 120ms ease-out",
                  }}
                />
              ) : (
                <div
                  style={{
                    position: "absolute",
                    inset: 0,
                    background:
                      "repeating-linear-gradient(90deg, #2c79c9 0 12px, #5fa6f0 12px 24px)",
                    backgroundSize: "48px 100%",
                    animation: "indeterminate-stripe 1s linear infinite",
                  }}
                />
              )}
            </div>
            <style>{`@keyframes indeterminate-stripe { 0% { background-position: 0 0; } 100% { background-position: 48px 0; } }`}</style>
          </div>
        </div>
      )}

      {uploadError && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={() => setUploadError(null)}>
          <div className="bg-white rounded-2xl shadow-xl p-6 w-full max-w-md border border-[#f0c8c8]" onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
              <AlertTriangle size={16} color="#b94a24" />
              <h3 style={{ fontSize: 15, fontWeight: 700, color: "#b94a24" }}>Upload failed</h3>
            </div>
            <p style={{ fontSize: 13, color: "#5b3320", marginBottom: 4, lineHeight: 1.5 }}>
              {uploadError}
            </p>
            {/* Suggest the most common fix in plain language */}
            {/no rows found/i.test(uploadError) && (
              <p style={{ fontSize: 12.5, color: "#856044", marginTop: 10, lineHeight: 1.5, background: "#fff6ee", padding: "10px 12px", borderRadius: 8, border: "1px solid #f1d3b0" }}>
                Tip: this usually means the file's headers weren't recognized. Make sure the first row has columns like <code>Company Name</code>, <code>First Name</code>, <code>Last Name</code>, <code>Email</code>. For <code>.xlsx</code> exports from Apollo / Sales Nav, saving the sheet as <strong>CSV</strong> in Excel often resolves the issue.
              </p>
            )}
            <div style={{ marginTop: 14, display: "flex", justifyContent: "flex-end" }}>
              <button
                type="button"
                onClick={() => setUploadError(null)}
                style={{
                  border: "1px solid #d9c1ae", background: "#fff", color: "#5b3320",
                  borderRadius: 10, padding: "8px 14px", fontSize: 13, fontWeight: 600, cursor: "pointer",
                }}
              >
                Dismiss
              </button>
            </div>
          </div>
        </div>
      )}

      {importSummary && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={() => setImportSummary(null)}>
          <div className="bg-white rounded-2xl shadow-xl p-6 w-full max-w-2xl border border-[#d9e1ec]" onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "start" }}>
              <div>
                <h2 className="text-[16px] font-bold text-[#1d2b3c] mb-1">Prospect upload complete</h2>
                <p className="text-[13px] text-[#6b7e92] mb-0">{importSummary.message}</p>
              </div>
              <button
                type="button"
                onClick={() => setImportSummary(null)}
                style={{ border: "1px solid #dce8f4", background: "#fff", color: "#5f7390", borderRadius: 10, width: 34, height: 34, cursor: "pointer" }}
              >
                <XCircle size={14} style={{ margin: "0 auto" }} />
              </button>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(5, minmax(0, 1fr))", gap: 10, marginTop: 18 }}>
              {[
                ["Rows", importSummary.imported_rows, "#1d2b3c"],
                ["Created", importSummary.created_count, "#1d2b3c"],
                ["Updated", importSummary.updated_count, "#1d2b3c"],
                ["Warnings", importSummary.warning_count ?? 0, (importSummary.warning_count ?? 0) > 0 ? "#b45309" : "#1d2b3c"],
                ["Skipped", importSummary.skipped_count, "#1d2b3c"],
              ].map(([label, value, color]) => (
                <div key={String(label)} style={{ border: "1px solid #dce8f4", borderRadius: 14, background: "#fbfdff", padding: "12px 14px" }}>
                  <div style={{ color: "#7f91ab", fontSize: 11, fontWeight: 800, letterSpacing: 0.3, textTransform: "uppercase" }}>{label}</div>
                  <div style={{ color: color as string, fontSize: 24, fontWeight: 800, marginTop: 6 }}>{value}</div>
                </div>
              ))}
            </div>

            {(importSummary.warning_count ?? 0) > 0 && (
              <div style={{ marginTop: 14, border: "1px solid #f5ddaa", background: "#fff8e8", borderRadius: 14, padding: "12px 14px" }}>
                <div style={{ color: "#8a5b00", fontSize: 12, fontWeight: 800, letterSpacing: 0.3, textTransform: "uppercase", marginBottom: 6 }}>
                  Hygiene warning
                </div>
                <div style={{ color: "#6b4a00", fontSize: 13, lineHeight: 1.5 }}>
                  {importSummary.warning_count} row{importSummary.warning_count === 1 ? "" : "s"} look{importSummary.warning_count === 1 ? "s" : ""} like a role mailbox (e.g. support@, info@) or placeholder name. We imported them anyway — review and clean them up in Prospecting if needed.
                </div>
              </div>
            )}

            {(importSummary.created_company_count ?? 0) > 0 && importSummary.created_companies && (
              <div style={{ marginTop: 14, border: "1px solid #b8d8c0", background: "#f1faf3", borderRadius: 14, padding: "14px 16px" }}>
                <div style={{ color: "#1f6b3a", fontSize: 12, fontWeight: 800, letterSpacing: 0.3, textTransform: "uppercase", marginBottom: 8 }}>
                  New accounts created
                </div>
                <div style={{ color: "#23553a", fontSize: 13, lineHeight: 1.6 }}>
                  {importSummary.created_company_count} account{importSummary.created_company_count === 1 ? "" : "s"} {importSummary.created_company_count === 1 ? "was" : "were"} created in Account Sourcing for prospects whose company didn't exist yet. ICP enrichment is queued — review them under Account Sourcing → Pending ICP review.
                </div>
                <div style={{ display: "grid", gap: 8, marginTop: 12, maxHeight: 200, overflowY: "auto" }}>
                  {importSummary.created_companies.map((company) => (
                    <div
                      key={company.id}
                      style={{
                        border: "1px solid #c8e6cf",
                        background: "#ffffff",
                        borderRadius: 12,
                        padding: "10px 12px",
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        gap: 12,
                        flexWrap: "wrap",
                      }}
                    >
                      <div>
                        <div style={{ color: "#1d2b3c", fontWeight: 700, fontSize: 13 }}>{company.name}</div>
                        <div style={{ color: "#4e7560", fontSize: 12, marginTop: 2 }}>
                          {company.domain ? formatDomain(company.domain) : "Domain pending"} · {company.contacts_count} prospect{company.contacts_count === 1 ? "" : "s"}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {importSummary.missing_company_count > 0 && (
              <div style={{ marginTop: 18, border: "1px solid #f5ddaa", background: "#fff8e8", borderRadius: 14, padding: "14px 16px" }}>
                <div style={{ color: "#8a5b00", fontSize: 12, fontWeight: 800, letterSpacing: 0.3, textTransform: "uppercase", marginBottom: 8 }}>
                  Company mapping warning
                </div>
                <div style={{ color: "#6c5a2f", fontSize: 13, lineHeight: 1.6 }}>
                  {importSummary.missing_company_count} compan{importSummary.missing_company_count === 1 ? "y was" : "ies were"} not matched cleanly to an existing account. Beacon created placeholder companies so the upload could proceed, but we recommend mapping them to the right existing company where possible.
                </div>
                <div style={{ display: "grid", gap: 8, marginTop: 12, maxHeight: 200, overflowY: "auto" }}>
                  {importSummary.missing_companies.map((company) => (
                    <div
                      key={`${company.domain || ""}-${company.name}`}
                      style={{
                        border: "1px solid #ead6ab",
                        background: "#fffdf6",
                        borderRadius: 12,
                        padding: "10px 12px",
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        gap: 12,
                        flexWrap: "wrap",
                      }}
                    >
                      <div>
                        <div style={{ color: "#1d2b3c", fontWeight: 700, fontSize: 13 }}>{company.name}</div>
                        <div style={{ color: "#7d6d4f", fontSize: 12, marginTop: 2 }}>
                          {company.domain ? formatDomain(company.domain) : "No domain provided"} · {company.contacts_count} prospect{company.contacts_count === 1 ? "" : "s"}
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => void handleEnrichMissingCompany(company)}
                        disabled={enrichingMissingKey === getMissingCompanyKey(company) || creatingMissingCompanies}
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: 6,
                          borderRadius: 10,
                          border: "1px solid #b8d0f0",
                          background: "#eef5ff",
                          color: "#175089",
                          padding: "8px 12px",
                          fontSize: 12,
                          fontWeight: 700,
                          cursor: enrichingMissingKey === getMissingCompanyKey(company) || creatingMissingCompanies ? "default" : "pointer",
                          opacity: enrichingMissingKey === getMissingCompanyKey(company) || creatingMissingCompanies ? 0.7 : 1,
                        }}
                      >
                        {enrichingMissingKey === getMissingCompanyKey(company) ? <Loader2 size={14} className="animate-spin" /> : <Building2 size={14} />}
                        Enrich account
                      </button>
                    </div>
                  ))}
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "center", marginTop: 14 }}>
                  <div style={{ color: "#7d6d4f", fontSize: 12.5 }}>
                    You can review these now and map them properly later. If you do nothing, the upload still stands and you can proceed for now.
                  </div>
                  <div style={{ display: "inline-flex", gap: 8, flexWrap: "wrap" }}>
                    <button
                      type="button"
                      onClick={() => setImportSummary(null)}
                      style={{
                        display: "inline-flex", alignItems: "center", gap: 6,
                        borderRadius: 10, border: "1px solid #e2c98d", background: "#fffdf6", color: "#8a5b00",
                        padding: "8px 12px", fontSize: 12, fontWeight: 700, cursor: "pointer",
                      }}
                    >
                      Proceed for now
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleCreateMissingCompanies()}
                      disabled={creatingMissingCompanies || importSummary.missing_companies.length === 0}
                      style={{
                        display: "inline-flex", alignItems: "center", gap: 6,
                        borderRadius: 10, border: "1px solid #b8d0f0", background: "#eef5ff", color: "#175089",
                        padding: "8px 12px", fontSize: 12, fontWeight: 700, cursor: creatingMissingCompanies || importSummary.missing_companies.length === 0 ? "default" : "pointer",
                        opacity: creatingMissingCompanies || importSummary.missing_companies.length === 0 ? 0.7 : 1,
                      }}
                    >
                      {creatingMissingCompanies ? <Loader2 size={14} className="animate-spin" /> : <Building2 size={14} />}
                      Enrich all missing companies
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Add Investor modal */}
      {showAddInvestor && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={() => setShowAddInvestor(false)}>
          <div className="bg-white rounded-2xl shadow-xl p-6 w-full max-w-md border border-[#d9e1ec]" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-[16px] font-bold text-[#1d2b3c] mb-4">Add Angel Investor</h2>
            <div className="space-y-3">
              <input
                placeholder="Name *"
                value={newInvestor.name}
                onChange={(e) => setNewInvestor({ ...newInvestor, name: e.target.value })}
                className="w-full h-11 px-4 rounded-xl border border-[#d7e2ee] bg-white text-[14px] placeholder-[#92a4b8] outline-none focus:border-[#1f6feb]"
                autoFocus
              />
              <input
                placeholder="Current Role (e.g. CEO, Partner)"
                value={newInvestor.current_role}
                onChange={(e) => setNewInvestor({ ...newInvestor, current_role: e.target.value })}
                className="w-full h-11 px-4 rounded-xl border border-[#d7e2ee] bg-white text-[14px] placeholder-[#92a4b8] outline-none focus:border-[#1f6feb]"
              />
              <input
                placeholder="Current Company"
                value={newInvestor.current_company}
                onChange={(e) => setNewInvestor({ ...newInvestor, current_company: e.target.value })}
                className="w-full h-11 px-4 rounded-xl border border-[#d7e2ee] bg-white text-[14px] placeholder-[#92a4b8] outline-none focus:border-[#1f6feb]"
              />
            </div>
            <div className="flex justify-end gap-3 mt-5">
              <button
                onClick={() => setShowAddInvestor(false)}
                className="crm-button soft h-11 px-5 text-[13px]"
              >
                Cancel
              </button>
              <button
                onClick={handleAddInvestor}
                className="h-11 px-5 rounded-xl bg-[#1f6feb] text-white text-[13px] font-semibold hover:bg-[#1960d1] transition-colors"
              >
                Add Investor
              </button>
            </div>
          </div>
        </div>
      )}

      <AddProspectModal
        open={showAddProspect}
        onClose={() => setShowAddProspect(false)}
        onCreated={loadContacts}
      />

      {/* ── Sequence lifecycle drawer ─────────────────────────────────── */}
      <LifecycleDrawer
        contactId={lifecycleContactId}
        detail={lifecycleDetail}
        loading={lifecycleLoading}
        onClose={() => setLifecycleContactId(null)}
        onOpenOutreach={() => {
          const contact = contacts.find((c) => c.id === lifecycleContactId);
          setLifecycleContactId(null);
          if (contact) setSelectedContact(contact);
        }}
      />

      {/* ── Call Disposition Sidebar — redesigned ────────────────────── */}
      {callContact && (() => {
        const outcomes: Array<{ value: string; label: string; icon: typeof Phone; color: string }> = [
          { value: "connected", label: "Connected",  icon: CheckCircle2, color: "#16a34a" },
          { value: "voicemail", label: "Voicemail",  icon: Mail,         color: "#7c3aed" },
          { value: "callback",  label: "Callback",   icon: Clock,        color: "#d97706" },
          { value: "attempted", label: "No answer",  icon: Phone,        color: "#64748b" },
        ];
        const dispoGroups: Array<{ title: string; tone: { bg: string; fg: string; border: string }; values: string[] }> = [
          {
            title: "Positive",
            tone: { bg: "#ecfdf5", fg: "#047857", border: "#a7f3d0" },
            values: ["demo_scheduled_booked", "interested_follow_up_required", "meeting_confirmed"],
          },
          {
            title: "Follow-up",
            tone: { bg: "#fffbeb", fg: "#92400e", border: "#fde68a" },
            values: ["call_back_later_rescheduled", "gatekeeper_connected_to_admin"],
          },
          {
            title: "Negative",
            tone: { bg: "#fef2f2", fg: "#b91c1c", border: "#fecaca" },
            values: ["connected_not_interested", "do_not_contact_dnc"],
          },
          {
            title: "No contact",
            tone: { bg: "#f1f5f9", fg: "#475569", border: "#e2e8f0" },
            values: ["no_answer_busy_signal", "invalid_number_wrong_number"],
          },
        ];
        const dispoLabel = (v: string) => CALL_DISPOSITION_OPTIONS.find((o) => o.value === v)?.label ?? v;
        const localTime = callContact.timezone
          ? (() => { try { return new Date().toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", timeZone: callContact.timezone }); } catch { return null; } })()
          : null;
        const noteChips = ["Send follow-up email", "Booked meeting", "Asked to call back later", "Not the right person", "Voicemail left, no callback yet"];

        return (
          <div className="prospect-call-drawer-shell" style={{
            position: "fixed", inset: 0, zIndex: 200,
            display: "flex", justifyContent: "flex-end",
          }}>
            <div className="prospect-call-drawer-backdrop" style={{ flex: 1, background: "rgba(10,20,40,0.45)", backdropFilter: "blur(2px)" }} />

            <div className="prospect-call-drawer-panel" style={{
              width: "min(1180px, 95vw)", maxWidth: "100vw",
              background: "#ffffff",
              borderLeft: "1px solid #d5e3ef",
              boxShadow: "-24px 0 60px rgba(14,38,66,0.18)",
              display: "flex", flexDirection: "column",
              position: "relative",
            }}>
              {/* BODY — header + countdown pinned at top, then a two-column
                  split so the brief and form sit side-by-side without scrolling
                  the whole drawer. */}
              <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>

                {/* PRE-CALL COUNTDOWN — recording auto-starts when this hits 0.
                    The rep can Stop it within the window, or skip the wait with
                    Start now. */}
                {dialCountdown != null && (
                  <div style={{
                    display: "flex", alignItems: "center", justifyContent: "space-between", gap: 14,
                    padding: "14px 22px",
                    background: "linear-gradient(90deg, #fff7ed 0%, #fbfef4 100%)",
                    borderBottom: "1px solid #e9f6d2",
                  }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 14, minWidth: 0 }}>
                      <div style={{
                        width: 46, height: 46, borderRadius: "50%", flexShrink: 0,
                        display: "grid", placeItems: "center",
                        background: "#fff", border: "2px solid #9ace3d",
                        color: "#5fa024", fontSize: 18, fontWeight: 800,
                        boxShadow: "0 0 0 4px #e3f4c6, 0 0 18px rgba(154,206,61,0.5)",
                        animation: "ringPulse 1.3s ease-in-out infinite",
                      }}>
                        {dialCountdown}
                      </div>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ fontSize: 13.5, fontWeight: 800, color: "#3f6212" }}>Recording starts in {dialCountdown}s…</div>
                        <div style={{ fontSize: 11.5, color: "#5b7a32", marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                          {callContact.phone ? `Dialing ${callContact.phone} — ` : ""}get ready, or stop before it begins.
                        </div>
                      </div>
                    </div>
                    <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
                      <button type="button" onClick={cancelDial} style={{ height: 36, padding: "0 14px", borderRadius: 10, border: "1px solid #f3b6b6", background: "#fff", color: "#b91c1c", fontSize: 12.5, fontWeight: 800, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 6 }}>
                        <X size={13} /> Stop
                      </button>
                      <button type="button" onClick={dialNow} style={{ height: 36, padding: "0 14px", borderRadius: 10, border: "none", background: "#9ace3d", color: "#fff", fontSize: 12.5, fontWeight: 800, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 6 }}>
                        <Mic size={13} /> Start now
                      </button>
                    </div>
                  </div>
                )}

                {/* HERO — brand-green header, pinned at the top of the drawer. */}
                <div style={{
                  padding: "16px 22px 14px",
                  background: "linear-gradient(180deg, #f6faf0 0%, #ffffff 100%)",
                  borderBottom: "1px solid #e8eef5",
                  position: "relative",
                  boxShadow: "0 6px 14px rgba(15,23,42,0.04)",
                }}>
                  <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 3, background: "linear-gradient(90deg, #9ace3d, #6fae27)" }} />
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
                    <span style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: "0.08em", textTransform: "uppercase", color: callDisposition ? "#4d7c0f" : "#5e7290", display: "inline-flex", alignItems: "center", gap: 7 }}>
                      <span style={{ width: 8, height: 8, borderRadius: 999, background: callDisposition ? "#16a34a" : "#9ace3d", boxShadow: `0 0 0 3px ${callDisposition ? "#dcfce7" : "#e3f4c6"}`, animation: "callpulse 1.6s ease-in-out infinite" }} />
                      {callDisposition ? "Ready to log" : "Call in progress"}
                    </span>
                    <button
                      type="button"
                      onClick={() => setCallContact(null)}
                      aria-label="Close"
                      style={{ width: 30, height: 30, borderRadius: 9, border: "1px solid #d5e3ef", background: "#fff", color: "#546679", display: "inline-flex", alignItems: "center", justifyContent: "center", cursor: "pointer" }}
                    >
                      <X size={14} />
                    </button>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
                    <div className={`flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl text-[15px] font-extrabold ${avatarColor(callContact.first_name + callContact.last_name)}`} style={{ boxShadow: "0 0 0 3px #fff, 0 0 0 5px #e3f4c6, 0 6px 16px rgba(154,206,61,0.28)" }}>
                      {getInitials(`${callContact.first_name} ${callContact.last_name}`)}
                    </div>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ fontSize: 21, fontWeight: 800, color: "#0f1f33", letterSpacing: "-0.02em", lineHeight: 1.2 }}>
                        {callContact.first_name} {callContact.last_name}
                      </div>
                      {callContact.title && (
                        <div style={{ fontSize: 14, color: "#4a5b73", marginTop: 3, fontWeight: 600 }}>{callContact.title}</div>
                      )}
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 6 }}>
                        {callContact.company_name && (
                          <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, fontWeight: 700, padding: "3px 8px", borderRadius: 999, background: "#f1f5f9", color: "#41526a", border: "1px solid #e2e8f0" }}>
                            <Building2 size={10} /> {callContact.company_name}
                          </span>
                        )}
                        {callContact.timezone && (
                          <span title={`Local time: ${localTime ?? "—"}`} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, fontWeight: 700, padding: "3px 8px", borderRadius: 999, background: "#f5f3ff", color: "#6d28d9", border: "1px solid #ddd6fe" }}>
                            <Clock size={10} /> {callContact.timezone}{localTime ? ` · ${localTime}` : ""}
                          </span>
                        )}
                        {callContact.persona_type && (() => {
                          const p = personaChipStyle(callContact.persona_type);
                          return (
                            <span style={{ fontSize: 11, fontWeight: 700, padding: "3px 8px", borderRadius: 999, background: p.bg, color: p.fg, border: `1px solid ${p.border}` }}>
                              {p.label}
                            </span>
                          );
                        })()}
                      </div>
                    </div>
                  </div>
                  {/* Contact reach row */}
                  <div style={{ display: "flex", gap: 6, marginTop: 12 }}>
                    {callContact.phone && (
                      <span style={{ flex: 1, display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 700, padding: "8px 10px", borderRadius: 10, background: "#f3fbe3", color: "#4d7c0f", border: "1px solid #cfe89a" }}>
                        <Phone size={12} /> {callContact.phone}
                      </span>
                    )}
                    {callContact.email && (
                      <a
                        href={gmailComposeUrl(callContact.email)}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 700, padding: "8px 10px", borderRadius: 10, background: "#fff", color: "#41526a", border: "1px solid #dce8f4", textDecoration: "none" }}
                        title="Open in Gmail compose"
                      >
                        <Mail size={12} /> Email
                      </a>
                    )}
                    {callContact.linkedin_url && (
                      <a
                        href={callContact.linkedin_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 700, padding: "8px 10px", borderRadius: 10, background: "#fff", color: "#0a66c2", border: "1px solid #dce8f4", textDecoration: "none" }}
                      >
                        <Link2 size={12} /> LinkedIn
                      </a>
                    )}
                  </div>
                </div>

                {/* TWO-COLUMN BODY — left: brief + recording; right: form. Each
                    column scrolls independently so the form stays reachable. */}
                <div style={{ flex: 1, minHeight: 0, display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)" }}>
                  <div style={{ overflowY: "auto", minHeight: 0, borderRight: "1px solid #eef2f7" }}>
                {/* PRECALL INTEL */}
                <PreCallIntelPanel
                  contact={callContact}
                  brief={precallBrief}
                  loading={precallLoading}
                />

                {/* RECORDING PANEL — optional. When the rep records the
                    call, Whisper transcribes it and Claude pre-fills the
                    disposition below. The rep still confirms before save. */}
                <CallRecordingPanel
                  // Key by contact so switching to the next prospect (the drawer
                  // stays mounted, only contactId changes) gives a FRESH panel.
                  // Without this the panel kept the previous call's recording +
                  // "ready" transcript, so the next call showed the old
                  // transcript and had no way to start recording.
                  key={callContact.id}
                  ref={callRecordingRef}
                  contactId={callContact.id}
                  onRecordingChange={setCurrentRecordingId}
                  onSuggestion={(s: AISuggestion) => {
                    handleCallDispositionChange(s.disposition);
                    // Don't clobber notes the rep already typed — only
                    // seed the AI summary into an empty notes field.
                    setCallNotes((existing) => existing.trim() ? existing : s.summary);
                  }}
                />
                  </div>{/* end left column */}

                  <div style={{ overflowY: "auto", minHeight: 0 }}>
                {/* FORM */}
                <div style={{ padding: "22px 26px 28px", animation: "callRise 300ms cubic-bezier(0.22, 1, 0.36, 1) both" }}>
                  {/* Call outcome — segmented cards */}
                  <div style={{ marginBottom: 18 }}>
                    <div style={{ fontSize: 12, fontWeight: 800, color: "#5e7290", textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 8 }}>
                      Call outcome
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 8 }}>
                      {outcomes.map((o) => {
                        const active = callStatus === o.value;
                        return (
                          <button
                            key={o.value}
                            type="button"
                            onClick={() => setCallStatus(o.value)}
                            style={{
                              display: "flex", alignItems: "center", gap: 9,
                              padding: "11px 14px",
                              borderRadius: 11,
                              border: `1.5px solid ${active ? o.color : "#e4ebf3"}`,
                              background: active ? `${o.color}11` : "#fff",
                              color: active ? o.color : "#3c4f68",
                              fontSize: 13.5, fontWeight: 700,
                              cursor: "pointer",
                              transition: "all 0.12s ease",
                            }}
                          >
                            <span style={{
                              display: "inline-flex", width: 26, height: 26, borderRadius: 7,
                              alignItems: "center", justifyContent: "center",
                              background: active ? o.color : "#f1f5f9",
                              color: active ? "#fff" : "#64748b",
                              flexShrink: 0,
                            }}>
                              <o.icon size={14} />
                            </span>
                            {o.label}
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  {/* Disposition — grouped pills */}
                  <div style={{ marginBottom: 18 }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                      <div style={{ fontSize: 12, fontWeight: 800, color: "#5e7290", textTransform: "uppercase", letterSpacing: "0.07em" }}>
                        Disposition <span style={{ color: "#ef4444" }}>*</span>
                      </div>
                      {callDisposition && (
                        <span style={{ fontSize: 11, color: "#16a34a", fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 4 }}>
                          <CheckCircle2 size={11} /> Selected
                        </span>
                      )}
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                      {dispoGroups.map((g) => (
                        <div key={g.title}>
                          <div style={{ fontSize: 11, fontWeight: 800, color: g.tone.fg, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 6 }}>
                            {g.title}
                          </div>
                          <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>
                            {g.values.map((v) => {
                              const active = callDisposition === v;
                              return (
                                <button
                                  key={v}
                                  type="button"
                                  onClick={() => handleCallDispositionChange(v)}
                                  style={{
                                    padding: "8px 15px",
                                    borderRadius: 999,
                                    border: `1.5px solid ${active ? g.tone.fg : g.tone.border}`,
                                    background: active ? g.tone.fg : g.tone.bg,
                                    color: active ? "#ffffff" : g.tone.fg,
                                    fontSize: 13, fontWeight: 700,
                                    cursor: "pointer",
                                    transition: "all 0.12s ease",
                                    transform: active ? "translateY(-1px)" : "none",
                                    boxShadow: active ? `0 0 0 3px ${g.tone.fg}22, 0 5px 16px ${g.tone.fg}55` : "none",
                                  }}
                                >
                                  {dispoLabel(v)}
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      ))}
                    </div>
                    {!callDisposition && (
                      <div style={{ fontSize: 11, color: "#ef4444", marginTop: 8 }}>Pick a disposition to enable Save.</div>
                    )}
                  </div>

                  {/* Follow-up scheduler — conditional */}
                  {FOLLOWUP_DISPOSITIONS.has(callDisposition) && (
                    <div style={{ marginBottom: 18, padding: "12px 14px", borderRadius: 12, background: "#fffbeb", border: "1px solid #fde68a" }}>
                      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                        <label style={{ fontSize: 11.5, fontWeight: 800, color: "#92400e", textTransform: "uppercase", letterSpacing: "0.06em", display: "inline-flex", alignItems: "center", gap: 6 }}>
                          <Clock size={12} /> Follow-up <span style={{ color: "#dc2626" }}>*</span>
                        </label>
                        <span style={{ fontSize: 10.5, color: "#92400e", fontWeight: 600 }}>Reminder created on save</span>
                      </div>
                      <input
                        type="datetime-local"
                        value={followupAt}
                        onChange={(e) => setFollowupAt(e.target.value)}
                        style={{
                          width: "100%", border: "1px solid #f5d77a", borderRadius: 10,
                          padding: "9px 12px", fontSize: 13, color: "#0f1f33",
                          background: "#fff", outline: "none", fontFamily: "inherit",
                        }}
                      />
                    </div>
                  )}

                  {/* Notes — with quick chips */}
                  <div style={{ marginBottom: 8 }}>
                    <div style={{ fontSize: 12, fontWeight: 800, color: "#5e7290", textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 8 }}>
                      Notes
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginBottom: 8 }}>
                      {noteChips.map((chip) => (
                        <button
                          key={chip}
                          type="button"
                          onClick={() => setCallNotes((current) => (current ? `${current}\n${chip}` : chip))}
                          style={{
                            padding: "6px 12px", borderRadius: 999,
                            border: "1px dashed #c8d6e6", background: "#f7fafc",
                            color: "#475569", fontSize: 12.5, fontWeight: 600,
                            cursor: "pointer",
                          }}
                          title="Add to notes"
                        >
                          + {chip}
                        </button>
                      ))}
                    </div>
                    <textarea
                      value={callNotes}
                      onChange={(e) => setCallNotes(e.target.value)}
                      placeholder="What came up on the call? Objections, signals, next steps..."
                      rows={6}
                      style={{ width: "100%", border: "1px solid #d8e2ed", borderRadius: 12, padding: "13px 15px", fontSize: 14.5, color: "#0f1f33", background: "#fff", outline: "none", resize: "vertical", fontFamily: "inherit", lineHeight: 1.6 }}
                    />
                  </div>
                </div>
                  </div>{/* end right column */}
                </div>{/* end two-column grid */}
              </div>

              {/* STICKY SAVE BAR */}
              <div style={{
                position: "sticky", bottom: 0, left: 0, right: 0,
                padding: "12px 18px",
                background: "rgba(255,255,255,0.97)",
                backdropFilter: "saturate(180%) blur(10px)",
                borderTop: "1px solid #e4ebf3",
                boxShadow: "0 -8px 24px rgba(15,23,42,0.06)",
              }}>
                <div style={{ display: "flex", gap: 8 }}>
                  <button
                    className="prospect-call-drawer-save"
                    onClick={() => void saveCallDisposition()}
                    disabled={!callDisposition || savingDisposition}
                    style={{
                      flex: nextCallable && callDisposition ? "0 0 auto" : 1,
                      padding: "13px 16px", borderRadius: 12,
                      border: nextCallable && callDisposition ? "1px solid #cdd9e6" : "none",
                      background: nextCallable && callDisposition ? "#fff" : (callDisposition ? "linear-gradient(135deg, #6fae27 0%, #9ace3d 100%)" : "#e8eef5"),
                      color: nextCallable && callDisposition ? "#41526a" : (callDisposition ? "#fff" : "#9aafbe"),
                      fontSize: 14, fontWeight: 800, letterSpacing: "-0.005em",
                      cursor: callDisposition ? "pointer" : "not-allowed",
                      display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
                      opacity: savingDisposition ? 0.7 : 1,
                      boxShadow: callDisposition && !nextCallable ? "0 6px 16px rgba(111,174,39,0.42)" : "none",
                      transition: "all 0.14s ease", whiteSpace: "nowrap",
                    }}
                  >
                    {savingDisposition ? <Loader2 size={15} className="animate-spin" /> : <CheckCircle2 size={15} />}
                    {savingDisposition ? "Saving..." : (nextCallable && callDisposition ? "Save & close" : (callDisposition ? `Save: ${dispoLabel(callDisposition)}` : "Save & close"))}
                  </button>
                  {nextCallable && callDisposition && (
                    <button
                      type="button"
                      onClick={() => void saveCallDisposition({ advance: true })}
                      disabled={savingDisposition}
                      title={`Next: ${nextCallable.first_name} ${nextCallable.last_name}`}
                      style={{
                        flex: 1, padding: "13px 0", borderRadius: 12, border: "none",
                        background: "linear-gradient(135deg, #6fae27 0%, #9ace3d 100%)",
                        color: "#fff", fontSize: 14, fontWeight: 800, letterSpacing: "-0.005em",
                        cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
                        opacity: savingDisposition ? 0.7 : 1,
                        boxShadow: "0 6px 16px rgba(111,174,39,0.42)", transition: "all 0.14s ease", whiteSpace: "nowrap",
                      }}
                    >
                      Save &amp; next <ArrowRight size={15} />
                    </button>
                  )}
                </div>
              </div>
              <style>{`
                @keyframes callpulse { 0%, 100% { opacity: 1 } 50% { opacity: 0.5 } }
                @keyframes ringPulse { 0%, 100% { transform: scale(1) } 50% { transform: scale(1.08) } }
                @keyframes callRise { from { opacity: 0; transform: translateY(10px) } to { opacity: 1; transform: none } }
                @media (prefers-reduced-motion: reduce) { .prospect-call-drawer-panel *, .prospect-call-drawer-panel { animation-duration: 0.001ms !important; } }
              `}</style>
            </div>
          </div>
        );
      })()}

      {/* ── LinkedIn Touch Logger ────────────────────────────────────── */}
      {linkedinContact && (
        <div style={{ position: "fixed", inset: 0, zIndex: 210, display: "flex", alignItems: "center", justifyContent: "center" }}
          onClick={() => setLinkedinContact(null)}
        >
          <div style={{ position: "absolute", inset: 0, background: "rgba(10,20,40,0.45)" }} />
          <div
            style={{ position: "relative", width: 420, maxWidth: "95vw", background: "#fff", borderRadius: 20, boxShadow: "0 24px 60px rgba(14,38,66,0.22)", overflow: "hidden" }}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div style={{ padding: "20px 22px 16px", borderBottom: "1px solid #e8eef5", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <div style={{ width: 36, height: 36, borderRadius: 10, background: "linear-gradient(135deg,#0a66c2,#1e88e5)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <Link2 size={16} color="#fff" />
                </div>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 700, color: "#0f2744" }}>Log LinkedIn touch</div>
                  <div style={{ fontSize: 12, color: "#7a96b0" }}>{linkedinContact.first_name} {linkedinContact.last_name}</div>
                </div>
              </div>
              <button onClick={() => setLinkedinContact(null)} style={{ border: 0, background: "transparent", color: "#7a96b0", cursor: "pointer", padding: 4 }}>
                <X size={18} />
              </button>
            </div>

            {/* Form */}
            <div style={{ padding: "18px 22px 22px", display: "grid", gap: 14 }}>
              {linkedinContact.linkedin_url && (
                <a href={linkedinContact.linkedin_url} target="_blank" rel="noopener noreferrer"
                  style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, color: "#0a66c2", fontWeight: 600, textDecoration: "none" }}>
                  <ExternalLink size={13} /> Open LinkedIn profile
                </a>
              )}

              <div>
                <label style={{ fontSize: 12, fontWeight: 700, color: "#2c4a63", display: "block", marginBottom: 6 }}>What happened? *</label>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
                  {LINKEDIN_STATUS_OPTIONS.map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => setLinkedinStatus(opt.value)}
                      style={{
                        padding: "10px 0", borderRadius: 10, border: `2px solid ${linkedinStatus === opt.value ? "#0a66c2" : "#dce8f4"}`,
                        background: linkedinStatus === opt.value ? "#e8f2ff" : "#f7faff",
                        color: linkedinStatus === opt.value ? "#0a66c2" : "#4a6580",
                        fontSize: 13, fontWeight: 700, cursor: "pointer",
                      }}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
                <div style={{ marginTop: 8, fontSize: 12, color: "#7a96b0" }}>
                  {linkedinStatus === "sent" && "You sent a connection request or InMail."}
                  {linkedinStatus === "accepted" && "They accepted your request — ready to message."}
                  {linkedinStatus === "follow_up" && "Conversation in flight — you followed up."}
                  {linkedinStatus === "meeting_booked" && "A meeting is on the calendar from this thread."}
                  {linkedinStatus === "meeting_rejected" && "They declined a meeting — treat as a hard no."}
                </div>
              </div>

              {/* ── AI-generated suggested message (if a sequence exists) ── */}
              {(linkedinSuggestionLoading || linkedinSuggestion) && (
                <div style={{ padding: "12px 14px", borderRadius: 10, background: "#f4f9ff", border: "1px solid #cfe2ff" }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                    <div style={{ fontSize: 11, fontWeight: 800, color: "#1a56db", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                      Suggested message (from sequence)
                    </div>
                    {linkedinSuggestion && (
                      <button
                        type="button"
                        onClick={async () => {
                          try {
                            await navigator.clipboard.writeText(linkedinSuggestion);
                            setLinkedinSuggestionCopied(true);
                            setTimeout(() => setLinkedinSuggestionCopied(false), 1800);
                          } catch {
                            toast.error("Copy failed — select and copy manually.", "Clipboard");
                          }
                        }}
                        style={{ padding: "4px 10px", borderRadius: 8, border: "1px solid #b6d0ff", background: "#fff", color: "#1a56db", fontSize: 11, fontWeight: 700, cursor: "pointer" }}
                      >
                        {linkedinSuggestionCopied ? "Copied" : "Copy"}
                      </button>
                    )}
                  </div>
                  {linkedinSuggestionLoading ? (
                    <div style={{ fontSize: 12, color: "#7a96b0" }}>Loading…</div>
                  ) : (
                    <div style={{ fontSize: 12.5, color: "#1f3a5f", whiteSpace: "pre-wrap", lineHeight: 1.55 }}>
                      {linkedinSuggestion}
                    </div>
                  )}
                </div>
              )}

              <div>
                <label style={{ fontSize: 12, fontWeight: 700, color: "#2c4a63", display: "block", marginBottom: 6 }}>Notes (optional)</label>
                <textarea
                  value={linkedinNotes}
                  onChange={(e) => setLinkedinNotes(e.target.value)}
                  placeholder="What did you say or observe? Any signals…"
                  rows={3}
                  style={{ width: "100%", boxSizing: "border-box", border: "1px solid #c8d9e8", borderRadius: 10, padding: "9px 12px", fontSize: 13, color: "#0f2744", background: "#fff", outline: "none", resize: "vertical", fontFamily: "inherit" }}
                />
              </div>

              <div style={{ display: "flex", gap: 10 }}>
                <button onClick={() => setLinkedinContact(null)} style={{ flex: 1, padding: "11px 0", borderRadius: 12, border: "1px solid #dce8f4", background: "#f7faff", color: "#4a6580", fontSize: 14, fontWeight: 700, cursor: "pointer" }}>
                  Cancel
                </button>
                <button
                  onClick={() => void saveLinkedinTouch()}
                  disabled={savingLinkedin}
                  style={{ flex: 2, padding: "11px 0", borderRadius: 12, border: "none", background: "linear-gradient(135deg,#0a66c2,#1e88e5)", color: "#fff", fontSize: 14, fontWeight: 700, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: 8, opacity: savingLinkedin ? 0.7 : 1 }}
                >
                  {savingLinkedin ? <Loader2 size={15} className="animate-spin" /> : <CheckCircle2 size={15} />}
                  {savingLinkedin ? "Saving…" : "Log touch"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
      {whatsappContact && (
        <div style={{ position: "fixed", inset: 0, zIndex: 210, display: "flex", alignItems: "center", justifyContent: "center" }}
          onClick={() => setWhatsappContact(null)}
        >
          <div style={{ position: "absolute", inset: 0, background: "rgba(10,20,40,0.45)" }} />
          <div
            style={{ position: "relative", width: 420, maxWidth: "95vw", background: "#fff", borderRadius: 20, boxShadow: "0 24px 60px rgba(14,38,66,0.22)", overflow: "hidden" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ padding: "20px 22px 16px", borderBottom: "1px solid #e8eef5", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <div style={{ width: 36, height: 36, borderRadius: 10, background: "linear-gradient(135deg,#25D366,#128C7E)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <MessageCircle size={16} color="#fff" />
                </div>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 700, color: "#0f2744" }}>Log WhatsApp</div>
                  <div style={{ fontSize: 12, color: "#7a96b0" }}>{whatsappContact.first_name} {whatsappContact.last_name}</div>
                </div>
              </div>
              <button onClick={() => setWhatsappContact(null)} style={{ border: 0, background: "transparent", color: "#7a96b0", cursor: "pointer", padding: 4 }}>
                <X size={18} />
              </button>
            </div>

            <div style={{ padding: "18px 22px 22px", display: "grid", gap: 14 }}>
              {waPhoneDigits(whatsappContact.phone) ? (
                <a href={`https://wa.me/${waPhoneDigits(whatsappContact.phone)}`} target="_blank" rel="noopener noreferrer"
                  style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, color: "#0f9d58", fontWeight: 700, textDecoration: "none" }}>
                  <MessageCircle size={14} /> Open WhatsApp chat ({whatsappContact.phone})
                </a>
              ) : (
                <div style={{ fontSize: 12.5, color: "#b06a00" }}>No phone number saved — you can still log a message.</div>
              )}

              <div>
                <label style={{ fontSize: 12, fontWeight: 700, color: "#2c4a63", display: "block", marginBottom: 6 }}>What happened? *</label>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 8 }}>
                  {([
                    { value: "sent", label: "Message sent" },
                    { value: "replied", label: "Replied" },
                    { value: "no_response", label: "No response" },
                    { value: "meeting_booked", label: "Meeting booked" },
                  ]).map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => setWhatsappOutcome(opt.value)}
                      style={{
                        padding: "10px 0", borderRadius: 10, border: `2px solid ${whatsappOutcome === opt.value ? "#0f9d58" : "#dce8f4"}`,
                        background: whatsappOutcome === opt.value ? "#e7f9ef" : "#f7faff",
                        color: whatsappOutcome === opt.value ? "#0f7a47" : "#4a6580",
                        fontSize: 13, fontWeight: 700, cursor: "pointer",
                      }}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label style={{ fontSize: 12, fontWeight: 700, color: "#2c4a63", display: "block", marginBottom: 6 }}>Notes (optional)</label>
                <textarea
                  value={whatsappNotes}
                  onChange={(e) => setWhatsappNotes(e.target.value)}
                  placeholder="What did you send or hear back? Any signals…"
                  rows={3}
                  style={{ width: "100%", boxSizing: "border-box", border: "1px solid #c8d9e8", borderRadius: 10, padding: "9px 12px", fontSize: 13, color: "#0f2744", background: "#fff", outline: "none", resize: "vertical", fontFamily: "inherit" }}
                />
              </div>

              <div style={{ display: "flex", gap: 10 }}>
                <button onClick={() => setWhatsappContact(null)} style={{ flex: 1, padding: "11px 0", borderRadius: 12, border: "1px solid #dce8f4", background: "#f7faff", color: "#4a6580", fontSize: 14, fontWeight: 700, cursor: "pointer" }}>
                  Cancel
                </button>
                <button
                  onClick={() => void saveWhatsappTouch()}
                  disabled={savingWhatsapp}
                  style={{ flex: 2, padding: "11px 0", borderRadius: 12, border: "none", background: "linear-gradient(135deg,#25D366,#128C7E)", color: "#fff", fontSize: 14, fontWeight: 700, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: 8, opacity: savingWhatsapp ? 0.7 : 1 }}
                >
                  {savingWhatsapp ? <Loader2 size={15} className="animate-spin" /> : <CheckCircle2 size={15} />}
                  {savingWhatsapp ? "Saving…" : "Log message"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
      {commentsContact && (
        <div style={{ position: "fixed", inset: 0, zIndex: 210, display: "flex", alignItems: "center", justifyContent: "center" }}
          onClick={() => setCommentsContact(null)}
        >
          <div style={{ position: "absolute", inset: 0, background: "rgba(10,20,40,0.45)" }} />
          <div
            style={{ position: "relative", width: 460, maxWidth: "95vw", maxHeight: "85vh", background: "#fff", borderRadius: 20, boxShadow: "0 24px 60px rgba(14,38,66,0.22)", overflow: "hidden", display: "flex", flexDirection: "column" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ padding: "20px 22px 16px", borderBottom: "1px solid #e8eef5", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <div style={{ width: 36, height: 36, borderRadius: 10, background: "linear-gradient(135deg,#475569,#1e293b)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <MessageSquare size={16} color="#fff" />
                </div>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 700, color: "#0f2744" }}>Comments</div>
                  <div style={{ fontSize: 12, color: "#7a96b0" }}>{commentsContact.first_name} {commentsContact.last_name}</div>
                </div>
              </div>
              <button onClick={() => setCommentsContact(null)} style={{ border: 0, background: "transparent", color: "#7a96b0", cursor: "pointer", padding: 4 }}>
                <X size={18} />
              </button>
            </div>

            {/* Add a new comment */}
            <div style={{ padding: "14px 22px 12px", borderBottom: "1px solid #eef2f7" }}>
              <textarea
                value={commentDraft}
                onChange={(e) => setCommentDraft(e.target.value)}
                placeholder="Add a comment for this prospect…"
                rows={2}
                onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") void saveComment(); }}
                style={{ width: "100%", boxSizing: "border-box", border: "1px solid #c8d9e8", borderRadius: 10, padding: "9px 12px", fontSize: 13, color: "#0f2744", background: "#fff", outline: "none", resize: "vertical", fontFamily: "inherit" }}
              />
              <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8 }}>
                <button
                  onClick={() => void saveComment()}
                  disabled={savingComment || !commentDraft.trim()}
                  style={{ padding: "9px 16px", borderRadius: 10, border: "none", background: commentDraft.trim() ? "linear-gradient(135deg,#475569,#1e293b)" : "#c7d2dd", color: "#fff", fontSize: 13, fontWeight: 700, cursor: commentDraft.trim() ? "pointer" : "default", display: "inline-flex", alignItems: "center", gap: 8 }}
                >
                  {savingComment ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                  {savingComment ? "Saving…" : "Add comment"}
                </button>
              </div>
            </div>

            {/* Previous comments */}
            <div style={{ padding: "12px 22px 20px", overflowY: "auto" }}>
              <div style={{ fontSize: 11, fontWeight: 800, color: "#8294a8", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 10 }}>
                Previous comments{commentsList.length ? ` (${commentsList.length})` : ""}
              </div>
              {commentsLoading ? (
                <div style={{ fontSize: 13, color: "#7a96b0" }}>Loading…</div>
              ) : commentsList.length === 0 ? (
                <div style={{ fontSize: 13, color: "#9aa8b7" }}>No comments yet — add the first one above.</div>
              ) : (
                <div style={{ display: "grid", gap: 10 }}>
                  {commentsList.map((cm) => (
                    <div key={cm.id} style={{ border: "1px solid #e7eef5", borderRadius: 12, padding: "10px 12px", background: "#fbfdff" }}>
                      <div style={{ fontSize: 13, color: "#27384a", lineHeight: 1.5, whiteSpace: "pre-wrap" }}>{cm.content}</div>
                      <div style={{ fontSize: 11, color: "#8aa0b6", marginTop: 6, fontWeight: 600 }}>
                        {cm.user_name ? `${cm.user_name} · ` : ""}{cm.created_at ? new Date(cm.created_at).toLocaleString() : ""}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
      {bulkFollowupOpen && (
        <div style={{ position: "fixed", inset: 0, zIndex: 210, display: "flex", alignItems: "center", justifyContent: "center" }}
          onClick={() => setBulkFollowupOpen(false)}
        >
          <div style={{ position: "absolute", inset: 0, background: "rgba(10,20,40,0.45)" }} />
          <div
            style={{ position: "relative", width: 440, maxWidth: "95vw", background: "#fff", borderRadius: 20, boxShadow: "0 24px 60px rgba(14,38,66,0.22)", overflow: "hidden" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ padding: "20px 22px 16px", borderBottom: "1px solid #e8eef5", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <div style={{ width: 36, height: 36, borderRadius: 10, background: "linear-gradient(135deg,#f59e0b,#d97706)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <Clock size={16} color="#fff" />
                </div>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 700, color: "#0f2744" }}>Add follow-up</div>
                  <div style={{ fontSize: 12, color: "#7a96b0" }}>{selectedContactIds.size} prospect{selectedContactIds.size === 1 ? "" : "s"} selected</div>
                </div>
              </div>
              <button onClick={() => setBulkFollowupOpen(false)} style={{ border: 0, background: "transparent", color: "#7a96b0", cursor: "pointer", padding: 4 }}>
                <X size={18} />
              </button>
            </div>

            <div style={{ padding: "18px 22px 22px", display: "grid", gap: 14 }}>
              <div>
                <label style={{ fontSize: 12, fontWeight: 700, color: "#2c4a63", display: "block", marginBottom: 6 }}>Due date &amp; time *</label>
                <input
                  type="datetime-local"
                  value={bulkFollowupAt}
                  onChange={(e) => setBulkFollowupAt(e.target.value)}
                  style={{ width: "100%", height: 42, border: "1px solid #c8d9e8", borderRadius: 10, padding: "0 12px", fontSize: 13, color: "#0f2744", background: "#fff", outline: "none", fontFamily: "inherit" }}
                />
              </div>
              <div>
                <label style={{ fontSize: 12, fontWeight: 700, color: "#2c4a63", display: "block", marginBottom: 6 }}>Note (optional)</label>
                <textarea
                  value={bulkFollowupNote}
                  onChange={(e) => setBulkFollowupNote(e.target.value)}
                  rows={3}
                  placeholder="e.g. Follow up on pricing conversation"
                  style={{ width: "100%", border: "1px solid #c8d9e8", borderRadius: 10, padding: "10px 12px", fontSize: 13, color: "#0f2744", background: "#fff", outline: "none", fontFamily: "inherit", resize: "vertical" }}
                />
                <p className="crm-muted" style={{ fontSize: 12, marginTop: 8, lineHeight: 1.5 }}>
                  A reminder is created on each selected prospect and shows on their detail page.
                </p>
              </div>

              <div style={{ display: "flex", gap: 10 }}>
                <button onClick={() => setBulkFollowupOpen(false)} style={{ flex: 1, padding: "11px 0", borderRadius: 12, border: "1px solid #dce8f4", background: "#f7faff", color: "#4a6580", fontSize: 14, fontWeight: 700, cursor: "pointer" }}>
                  Cancel
                </button>
                <button
                  onClick={() => void submitBulkFollowup()}
                  disabled={bulkFollowupSaving || !bulkFollowupAt}
                  style={{ flex: 2, padding: "11px 0", borderRadius: 12, border: "none", background: bulkFollowupAt ? "linear-gradient(135deg,#f59e0b,#d97706)" : "#c7d2dd", color: "#fff", fontSize: 14, fontWeight: 700, cursor: bulkFollowupAt && !bulkFollowupSaving ? "pointer" : "default", display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}
                >
                  {bulkFollowupSaving ? <Loader2 size={15} className="animate-spin" /> : <Clock size={15} />}
                  {bulkFollowupSaving ? "Setting…" : "Set follow-up"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {campaignModalOpen && (
        <div style={{ position: "fixed", inset: 0, zIndex: 210, display: "flex", alignItems: "center", justifyContent: "center" }}
          onClick={() => setCampaignModalOpen(false)}
        >
          <div style={{ position: "absolute", inset: 0, background: "rgba(10,20,40,0.45)" }} />
          <div
            style={{ position: "relative", width: 440, maxWidth: "95vw", background: "#fff", borderRadius: 20, boxShadow: "0 24px 60px rgba(14,38,66,0.22)", overflow: "hidden" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ padding: "20px 22px 16px", borderBottom: "1px solid #e8eef5", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <div style={{ width: 36, height: 36, borderRadius: 10, background: "linear-gradient(135deg,#0f9d58,#0b7a43)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <Send size={16} color="#fff" />
                </div>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 700, color: "#0f2744" }}>Start campaign</div>
                  <div style={{ fontSize: 12, color: "#7a96b0" }}>{selectedContactIds.size} prospect{selectedContactIds.size === 1 ? "" : "s"} selected</div>
                </div>
              </div>
              <button onClick={() => setCampaignModalOpen(false)} style={{ border: 0, background: "transparent", color: "#7a96b0", cursor: "pointer", padding: 4 }}>
                <X size={18} />
              </button>
            </div>

            <div style={{ padding: "18px 22px 22px", display: "grid", gap: 14 }}>
              <div>
                <label style={{ fontSize: 12, fontWeight: 700, color: "#2c4a63", display: "block", marginBottom: 6 }}>Instantly campaign *</label>
                {campaignOptionsLoading ? (
                  <div style={{ fontSize: 13, color: "#7a96b0" }}>Loading campaigns…</div>
                ) : campaignOptions.length === 0 ? (
                  <div style={{ fontSize: 13, color: "#b06a00" }}>No Instantly campaigns found. Create one in Instantly first.</div>
                ) : (
                  <select
                    value={selectedCampaignId}
                    onChange={(e) => setSelectedCampaignId(e.target.value)}
                    style={{ width: "100%", height: 42, border: "1px solid #c8d9e8", borderRadius: 10, padding: "0 12px", fontSize: 13, color: "#0f2744", background: "#fff", outline: "none" }}
                  >
                    <option value="">Select a campaign…</option>
                    {campaignOptions.map((c) => (
                      <option key={c.id} value={c.id}>{c.name}</option>
                    ))}
                  </select>
                )}
                <p className="crm-muted" style={{ fontSize: 12, marginTop: 8, lineHeight: 1.5 }}>
                  Selected prospects are added as leads to this campaign. Prospects without an email are skipped.
                </p>
              </div>

              <div style={{ display: "flex", gap: 10 }}>
                <button onClick={() => setCampaignModalOpen(false)} style={{ flex: 1, padding: "11px 0", borderRadius: 12, border: "1px solid #dce8f4", background: "#f7faff", color: "#4a6580", fontSize: 14, fontWeight: 700, cursor: "pointer" }}>
                  Cancel
                </button>
                <button
                  onClick={() => void startBulkCampaign()}
                  disabled={startingCampaign || !selectedCampaignId}
                  style={{ flex: 2, padding: "11px 0", borderRadius: 12, border: "none", background: selectedCampaignId ? "linear-gradient(135deg,#0f9d58,#0b7a43)" : "#c7d2dd", color: "#fff", fontSize: 14, fontWeight: 700, cursor: selectedCampaignId ? "pointer" : "default", display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}
                >
                  {startingCampaign ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
                  {startingCampaign ? "Starting…" : "Start campaign"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

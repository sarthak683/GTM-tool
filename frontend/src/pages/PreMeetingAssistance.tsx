import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { SkeletonList } from "../components/ui/Skeleton";
import {
  AlertTriangle,
  BrainCircuit,
  CalendarDays,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock3,
  ExternalLink,
  Filter,
  Loader2,
  MailCheck,
  MessageSquare,
  RefreshCw,
  Search,
  Sparkles,
  Target,
  TrendingUp,
  User,
  Users,
  Zap,
  Building2,
  Activity as ActivityIcon,
  ListChecks,
  Swords,
  Briefcase,
  AlertCircle,
  X,
} from "lucide-react";
import { activitiesApi, companiesApi, dealsApi, meetingsApi } from "../lib/api";
import { getCachedUsers } from "../lib/cachedFetch";
import { useAuth } from "../lib/AuthContext";
import type { Activity, Company, Deal, Meeting, MeetingPrepMonitor, User as UserType } from "../types/index";
import { formatOptionalDate, isValidDateValue, suggestCompanyNameFromMeetingTitle } from "../lib/utils";
const DEVELOPER_EMAILS = new Set(["sarthak@beacon.li"]);

const colors = {
  border: "#d9e1ec",
  text: "#1d2b3c",
  sub: "#55657a",
  faint: "#7f8fa5",
  primary: "#1f6feb",
  primarySoft: "#eef5ff",
  green: "#1f8f5f",
  greenSoft: "#e8f8f0",
  violet: "#7a2dd9",
  violetSoft: "#f3eaff",
  amber: "#b56d00",
  amberSoft: "#fff4df",
  orange: "#b94a20",
  orangeSoft: "#fff2ec",
  red: "#c0392b",
  redSoft: "#fff5f5",
};

function isDeveloperUser(user?: Pick<UserType, "email" | "name"> | null) {
  if (!user) return false;
  const email = (user.email || "").trim().toLowerCase();
  const name = (user.name || "").trim().toLowerCase();
  return DEVELOPER_EMAILS.has(email) || name === "sarthak aitha";
}

function hoursUntil(dateStr?: string): number | null {
  if (!dateStr) return null;
  if (!isValidDateValue(dateStr)) return null;
  const diff = new Date(dateStr).getTime() - Date.now();
  return Math.round(diff / (1000 * 60 * 60));
}

function timeAgo(dateStr?: string): string {
  if (!dateStr) return "—";
  const diff = Date.now() - new Date(dateStr).getTime();
  const days = Math.floor(diff / (1000 * 60 * 60 * 24));
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days}d ago`;
  if (days < 30) return `${Math.floor(days / 7)}w ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

type NormalizedMeetingAttendee = {
  name: string;
  email?: string;
  title?: string;
  contactId?: string;
};

type PrepStakeholder = {
  name: string;
  title?: string;
  email?: string;
  contactId?: string;
  linkedinUrl?: string;
  role: string;
  roleLabel: string;
  status: "attending" | "recommended";
  likelyFocus?: string;
  talkTrack?: string;
  questions: string[];
};

function normalizeMeetingAttendees(attendees: unknown): NormalizedMeetingAttendee[] {
  if (!Array.isArray(attendees)) return [];
  return attendees
    .filter((item): item is Record<string, unknown> => !!item && typeof item === "object")
    .map((item) => ({
      name: String(item.name || item.email || "Unknown attendee").trim(),
      email: typeof item.email === "string" ? item.email : undefined,
      title: typeof item.title === "string" ? item.title : undefined,
      contactId: typeof item.contact_id === "string" ? item.contact_id : undefined,
    }));
}

function normalizeStakeholderCards(attendeeIntel: Record<string, any>, attendees: NormalizedMeetingAttendee[]): PrepStakeholder[] {
  const cards = Array.isArray(attendeeIntel.stakeholder_cards) ? attendeeIntel.stakeholder_cards : [];
  if (cards.length === 0) {
    return attendees.map((attendee) => ({
      name: attendee.name,
      title: attendee.title,
      email: attendee.email,
      contactId: attendee.contactId,
      role: "unknown",
      roleLabel: "Stakeholder",
      status: "attending",
      questions: [],
    }));
  }

  return cards
    .filter((item): item is Record<string, any> => !!item && typeof item === "object")
    .map((item) => ({
      name: String(item.name || item.email || "Unknown stakeholder").trim(),
      title: typeof item.title === "string" ? item.title : undefined,
      email: typeof item.email === "string" ? item.email : undefined,
      contactId: typeof item.contact_id === "string" ? item.contact_id : undefined,
      linkedinUrl: typeof item.linkedin_url === "string" ? item.linkedin_url : undefined,
      role: typeof item.role === "string" ? item.role : "unknown",
      roleLabel: typeof item.role_label === "string" ? item.role_label : "Stakeholder",
      status: item.status === "recommended" ? "recommended" : "attending",
      likelyFocus: typeof item.likely_focus === "string" ? item.likely_focus : undefined,
      talkTrack: typeof item.talk_track === "string" ? item.talk_track : undefined,
      questions: Array.isArray(item.questions_to_ask) ? item.questions_to_ask.filter((value): value is string => typeof value === "string") : [],
    }));
}

function activityChannel(activity: Activity): "email" | "call" | "linkedin" | "meeting" | "other" {
  const medium = (activity.medium || "").toLowerCase();
  const type = (activity.type || "").toLowerCase();
  const source = (activity.source || "").toLowerCase();
  if (medium === "linkedin" || source.includes("linkedin")) return "linkedin";
  if (type === "email" || medium === "email" || source === "instantly") return "email";
  if (type === "call" || medium === "call") return "call";
  if (type === "meeting" || type === "transcript" || medium === "meeting" || medium === "in_person") return "meeting";
  return "other";
}

function activityChannelLabel(channel: ReturnType<typeof activityChannel>): string {
  switch (channel) {
    case "email":
      return "Email";
    case "call":
      return "Call";
    case "linkedin":
      return "LinkedIn";
    case "meeting":
      return "Meeting";
    default:
      return "Other";
  }
}

function activitySnippet(activity: Activity): string {
  if (activity.ai_summary) return activity.ai_summary;
  if (activity.email_subject) return activity.email_subject;
  if (activity.content) return activity.content;
  if (activity.call_outcome) return `Call outcome: ${activity.call_outcome}`;
  return "Activity logged";
}

function domainUrl(domain?: string): string | null {
  if (!domain) return null;
  const trimmed = domain.trim();
  if (!trimmed) return null;
  return /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
}

function SectionHeader({ icon: Icon, label, color }: { icon: any; label: string; color: string }) {
  return (
    <div style={{ fontSize: 10, fontWeight: 700, color, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 6, display: "flex", alignItems: "center", gap: 5 }}>
      <Icon size={10} />
      {label}
    </div>
  );
}

function Pill({ label, color, bg, border }: { label: string; color: string; bg: string; border: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", padding: "2px 8px", borderRadius: 999, background: bg, color, border: `1px solid ${border}`, fontSize: 11, fontWeight: 700 }}>
      {label}
    </span>
  );
}

// Normalize for loose, whole-token substring matching (same idea as the
// backend's `_normalize_name_key`). Strips punctuation, lowercases, collapses
// whitespace. Used to flag when a meeting *title* contains a company name
// that differs from the one currently linked — the classic "Procore X
// Beacon" event mislinked to Azentio because an Azentio contact attended.
function normalizeNameKey(value: string): string {
  return (value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .replace(/\s+/g, " ");
}

function detectTitleCompanyMismatch(
  title: string,
  linkedCompanyId: string | undefined,
  companies: Company[]
): Company | null {
  if (!title || !linkedCompanyId) return null;
  const normTitle = ` ${normalizeNameKey(title)} `;
  if (normTitle.trim().length < 4) return null;
  const candidates = companies
    .map((c) => ({ company: c, key: normalizeNameKey(c.name || "") }))
    .filter((x) => x.key.length >= 4 && normTitle.includes(` ${x.key} `))
    .sort((a, b) => b.key.length - a.key.length);
  if (!candidates.length) return null;
  // Accept only an unambiguous longest match.
  const longest = candidates[0].key.length;
  const topIds = new Set(
    candidates.filter((c) => c.key.length === longest).map((c) => c.company.id)
  );
  if (topIds.size !== 1) return null;
  const titleCompany = candidates[0].company;
  return titleCompany.id !== linkedCompanyId ? titleCompany : null;
}

// Shared parser lives in lib/utils.ts — see suggestCompanyNameFromMeetingTitle.
// The previous local copy was missing em-dash/en-dash separators and let
// half-cleaned segments win over the real customer name (e.g.
// "POC Kickoff Disucssion – Beacon<>Fabtech" yielded the meeting-name half
// instead of "Fabtech"). The shared util mirrors the Python tldv title parser.

// Minimal list card. The full prep (executive briefing, stakeholders, game
// plan, competitive, sources, actions) lives on the meeting detail page at
// /meetings/:id — this card is just a clickable summary so the list stays
// scannable. Same name + props as before so the parent render is unchanged;
// the now-unused props are accepted for API compatibility.
function MeetingIntelCard({
  meeting,
  company,
  assigneeName,
  deal: _deal,
  lastActivity: _lastActivity,
  dealActivities: _dealActivities,
  allCompanies: _allCompanies,
  onRunIntel: _onRunIntel,
  onUpdateStatus: _onUpdateStatus,
  onUnlink: _onUnlink,
  runningIntel: _runningIntel,
  updatingStatus: _updatingStatus,
  unlinking: _unlinking,
}: {
  meeting: Meeting;
  company?: Company;
  deal?: Deal;
  lastActivity?: Activity;
  dealActivities: Activity[];
  assigneeName?: string;
  allCompanies: Company[];
  onRunIntel: (id: string) => void;
  onUpdateStatus: (id: string, status: "completed" | "cancelled") => void;
  onUnlink: (id: string) => void;
  runningIntel: string | null;
  updatingStatus: string | null;
  unlinking: string | null;
}) {
  const hours = hoursUntil(meeting.scheduled_at);
  const hasResearch = !!meeting.research_data;
  const hasIntelSent = !!(meeting as any).intel_email_sent_at;
  const needsReview = !meeting.company_id || !meeting.deal_id;
  const isCompleted = meeting.status === "completed";
  const isCancelled = meeting.status === "cancelled";
  const hoursPast = hours !== null ? -hours : null;
  const awayLabel =
    isCancelled ? "Cancelled"
    : isCompleted ? "Completed"
    : hours === null ? "Upcoming"
    : hours < 0 ? (hoursPast! >= 48 ? `${Math.round(hoursPast! / 24)}d overdue` : hoursPast! >= 1 ? `${hoursPast}h overdue` : "Just ended")
    : hours <= 2 ? "< 2 hrs"
    : `${hours}h away`;
  const awayTone =
    isCancelled ? { bg: "#f1f5f9", color: "#64748b", border: "#e2e8f0" }
    : isCompleted ? { bg: "#ecf8f0", color: "#15803d", border: "#c7e8d3" }
    : hours !== null && hours < 0 ? { bg: "#fdecec", color: "#b42336", border: "#f5c2c2" }
    : hours !== null && hours <= 2 ? { bg: "#fff2ec", color: colors.orange, border: "#ffd3be" }
    : { bg: "#f4f7ff", color: "#4b60cf", border: "#d7dffb" };
  const accent = needsReview
    ? colors.orange
    : hours !== null && hours < 0 && !isCompleted && !isCancelled
      ? "#b42336"
      : "transparent";

  return (
    <Link
      id={meeting.id}
      to={`/meetings/${meeting.id}`}
      style={{
        display: "block",
        textDecoration: "none",
        background: "#fff",
        border: `1px solid ${colors.border}`,
        borderLeft: accent === "transparent" ? `1px solid ${colors.border}` : `4px solid ${accent}`,
        borderRadius: 14,
        padding: "14px 18px",
        transition: "box-shadow 140ms ease",
      }}
      onMouseEnter={(e) => { e.currentTarget.style.boxShadow = "0 6px 18px -10px rgba(15,39,68,0.28)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.boxShadow = "none"; }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontSize: 14.5, fontWeight: 800, color: colors.text, display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{meeting.title}</span>
            <ExternalLink size={12} style={{ color: colors.faint, flexShrink: 0 }} />
          </div>
          <div style={{ marginTop: 4, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            {company && <span style={{ fontSize: 12, color: colors.sub, fontWeight: 600 }}>{company.name}</span>}
            {needsReview && (
              <span style={{ fontSize: 9.5, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.06em", padding: "2px 7px", borderRadius: 999, background: "#fff6ec", color: colors.orange, border: "1px solid #ffd3be" }}>
                Needs review
              </span>
            )}
            <span style={{ fontSize: 11, fontWeight: 700, textTransform: "capitalize", padding: "2px 8px", borderRadius: 999, background: "#f0f4fb", color: colors.sub, border: `1px solid ${colors.border}` }}>
              {meeting.meeting_type.replace(/_/g, " ")}
            </span>
            {assigneeName && (
              <span style={{ fontSize: 12, color: colors.faint, display: "inline-flex", alignItems: "center", gap: 4 }}>
                <User size={11} />{assigneeName}
              </span>
            )}
          </div>
          <div style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 6, color: colors.faint, fontSize: 12 }}>
            <CalendarDays size={12} />
            <span>{formatOptionalDate(meeting.scheduled_at)}</span>
          </div>
        </div>

        <div style={{ flexShrink: 0, display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8 }}>
          <span style={{ padding: "4px 9px", borderRadius: 999, background: awayTone.bg, color: awayTone.color, border: `1px solid ${awayTone.border}`, fontSize: 11, fontWeight: 800, display: "inline-flex", alignItems: "center", gap: 5 }}>
            <Clock3 size={11} />{awayLabel}
          </span>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, fontWeight: 700, color: hasResearch ? colors.green : colors.amber }}>
              {hasResearch ? <CheckCircle2 size={12} /> : <BrainCircuit size={12} />}{hasResearch ? "Intel ready" : "No intel"}
            </span>
            {hasIntelSent && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, fontWeight: 700, color: colors.primary }}>
                <MailCheck size={12} /> Sent
              </span>
            )}
          </div>
        </div>
      </div>
    </Link>
  );
}

type MultiSelectValue = string[];

function MultiSelectDropdown({
  label,
  options,
  selected,
  onChange,
  placeholder,
  selectionMode = "multi",
}: {
  label: string;
  options: { value: string; label: string }[];
  selected: string[];
  onChange: (values: string[]) => void;
  placeholder: string;
  selectionMode?: "multi" | "single";
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const filtered = useMemo(
    () =>
      query.trim()
        ? options.filter((opt) => opt.label.toLowerCase().includes(query.toLowerCase()))
        : options,
    [options, query],
  );

  function toggle(value: string) {
    if (selectionMode === "single") {
      onChange([value]);
      setOpen(false);
      setQuery("");
      return;
    }
    if (selected.includes(value)) {
      onChange(selected.filter((v) => v !== value));
    } else {
      onChange([...selected, value]);
    }
  }

  const displayText =
    selected.length === 0
      ? placeholder
      : selected.length === 1
        ? options.find((o) => o.value === selected[0])?.label ?? placeholder
        : `${selected.length} selected`;

  return (
    <div style={{ display: "grid", gap: 8, position: "relative", zIndex: open ? 200 : 1 }} ref={ref}>
      <label style={{ fontSize: 11, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.08em", color: "#7a8ca0" }}>
        {label}
      </label>
      <div style={{ position: "relative" }}>
        <button
          type="button"
          onClick={() => { setOpen((o) => !o); setQuery(""); }}
          style={{
            width: "100%",
            height: 40,
            borderRadius: 12,
            border: selected.length > 0 ? "1px solid #b8cff7" : `1px solid ${colors.border}`,
            background: selected.length > 0 ? "#eef4ff" : "#fff",
            color: selected.length > 0 ? "#2948b9" : colors.text,
            fontSize: 13,
            fontWeight: 700,
            padding: "0 12px",
            textAlign: "left",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 8,
          }}
        >
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
            {displayText}
          </span>
          <div style={{ display: "flex", alignItems: "center", gap: 4, flexShrink: 0 }}>
            {selected.length > 0 && (
              <span
                role="button"
                tabIndex={0}
                onClick={(e) => { e.stopPropagation(); onChange([]); }}
                onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); onChange([]); } }}
                style={{ display: "flex", alignItems: "center", color: "#5878be", cursor: "pointer" }}
              >
                <X size={13} />
              </span>
            )}
            <ChevronDown size={14} style={{ color: "#7a8ca0", transform: open ? "rotate(180deg)" : "none", transition: "transform 0.15s" }} />
          </div>
        </button>
        {open && (
          <div
            style={{
              position: "absolute",
              top: "calc(100% + 6px)",
              left: 0,
              right: 0,
              zIndex: 1000,
              background: "#fff",
              border: "1px solid #dde8f4",
              borderRadius: 14,
              boxShadow: "0 8px 28px rgba(20,50,80,0.12)",
              overflow: "hidden",
            }}
          >
            <div style={{ padding: "8px 10px", borderBottom: "1px solid #edf2f8", display: "flex", alignItems: "center", gap: 8 }}>
              <Search size={13} style={{ color: "#94a8be", flexShrink: 0 }} />
              <input
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search..."
                style={{
                  flex: 1,
                  border: "none",
                  outline: "none",
                  fontSize: 13,
                  color: colors.text,
                  background: "transparent",
                }}
              />
            </div>
            <div style={{ maxHeight: 220, overflowY: "auto" }}>
              {filtered.length === 0 ? (
                <p style={{ margin: 0, padding: "12px 14px", fontSize: 13, color: "#94a8be" }}>No results</p>
              ) : (
                filtered.map((opt) => {
                  const isSelected = selected.includes(opt.value);
                  return (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => toggle(opt.value)}
                      style={{
                        width: "100%",
                        padding: "10px 14px",
                        display: "flex",
                        alignItems: "center",
                        gap: 10,
                        border: "none",
                        background: isSelected ? "#f0f5ff" : "transparent",
                        cursor: "pointer",
                        textAlign: "left",
                        fontSize: 13,
                        fontWeight: isSelected ? 700 : 500,
                        color: isSelected ? "#2948b9" : "#2e4260",
                      }}
                    >
                      <span style={{
                        width: 18, height: 18, borderRadius: 6, border: isSelected ? "none" : "1.5px solid #c8d8ea",
                        background: isSelected ? "#3f5fd4" : "#fff",
                        display: "grid", placeItems: "center", flexShrink: 0,
                      }}>
                        {isSelected && <Check size={11} style={{ color: "#fff" }} />}
                      </span>
                      {opt.label}
                    </button>
                  );
                })
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function PreMeetingAssistance() {
  const { isAdmin, user } = useAuth();
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [deals, setDeals] = useState<Deal[]>([]);
  const [users, setUsers] = useState<UserType[]>([]);
  const [activities, setActivities] = useState<Activity[]>([]);
  const [loading, setLoading] = useState(true);
  const [runningIntel, setRunningIntel] = useState<string | null>(null);
  const [updatingStatus, setUpdatingStatus] = useState<string | null>(null);
  const [unlinking, setUnlinking] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<MultiSelectValue>(["scheduled"]);
  const [intelFilter, setIntelFilter] = useState<MultiSelectValue>([]);
  // Default to "my upcoming" — current user's scheduled meetings.  Can be
  // cleared from the filter bar to see all reps.  If the default scope
  // returns zero rows, we auto-broaden once per mount so the page is not
  // blank for users without personally-assigned meetings.
  const [assigneeFilter, setAssigneeFilter] = useState<MultiSelectValue>(user?.id ? [user.id] : []);
  const [autoFallbackApplied, setAutoFallbackApplied] = useState(false);
  // Internal-only meetings (attendees all @beacon.li) are hidden by default.
  const [showInternal, setShowInternal] = useState<boolean>(false);
  // Advanced filter disclosure — primary bar shows search / status / assignee /
  // show-internal; intel / type / link hide behind "More filters".
  const [showAdvancedFilters, setShowAdvancedFilters] = useState<boolean>(false);
  const [typeFilter, setTypeFilter] = useState<MultiSelectValue>([]);
  const [linkFilter, setLinkFilter] = useState<MultiSelectValue>([]);
  // Text search across title, company name, attendee JSON. Debounced.
  const [searchInput, setSearchInput] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [page, setPage] = useState(1);
  const [totalMeetings, setTotalMeetings] = useState(0);
  const [meetingPages, setMeetingPages] = useState(1);
  const [summary, setSummary] = useState({ total: 0, upcoming: 0, hasIntel: 0, noIntel: 0 });
  const [prepMonitor, setPrepMonitor] = useState<MeetingPrepMonitor | null>(null);
  const hideDeveloper = isDeveloperUser(user);

  useEffect(() => {
    if (!user?.id) return;
    setAssigneeFilter((current) => (current.length === 0 ? [user.id] : current));
  }, [user?.id]);

  const loadData = async () => {
    setLoading(true);
    try {
      const hasIntelFilter =
        intelFilter.length === 1
          ? intelFilter[0] === "has_intel"
          : undefined;

      // "Upcoming", "overdue", and "past" are virtual slices of scheduled meetings.
      // Send the temporal slice to the API so pagination and totals line up.
      const apiStatusFilter = statusFilter.some((status) => status === "overdue" || status === "past")
        ? Array.from(new Set(statusFilter.flatMap((s) => {
            if (s === "overdue") return ["scheduled"];
            if (s === "past") return ["scheduled", "completed"];
            return [s];
          })))
        : statusFilter;
      const temporalStatusFilter = Array.from(new Set(statusFilter.flatMap((status) => {
        if (status === "scheduled") return ["upcoming"];
        if (status === "overdue") return ["overdue"];
        if (status === "past") return ["overdue"];
        return [];
      })));

      // Prep view = upcoming/overdue scheduled meetings (not the past/completed
      // history). Only there do we hide closed/customer-account meetings so
      // e.g. a closed_won customer's daily syncs don't clutter the prep list.
      const isPrepView = !statusFilter.some((s) => s === "past" || s === "completed");

      const [pageResp, totalResp, upcomingResp, hasIntelResp, noIntelResp] = await Promise.all([
        meetingsApi.listPaginated({
          skip: (page - 1) * 25,
          limit: 25,
          status: apiStatusFilter,
          temporalStatus: temporalStatusFilter,
          meetingType: typeFilter,
          assigneeId: assigneeFilter,
          linkState: linkFilter,
          hasIntel: hasIntelFilter,
          order: statusFilter.length === 1 && (statusFilter[0] === "completed" || statusFilter[0] === "past") ? "desc" : "asc",
          q: debouncedSearch || undefined,
          internalScope: showInternal ? "only" : "exclude",
          excludeClosedPipeline: isPrepView,
        }),
        meetingsApi.listPaginated({ skip: 0, limit: 1, assigneeId: assigneeFilter, internalScope: showInternal ? "only" : "exclude" }),
        meetingsApi.listPaginated({ skip: 0, limit: 1, status: ["scheduled"], temporalStatus: ["upcoming"], assigneeId: assigneeFilter, internalScope: showInternal ? "only" : "exclude", excludeClosedPipeline: true }),
        meetingsApi.listPaginated({ skip: 0, limit: 1, status: ["scheduled"], temporalStatus: ["upcoming"], hasIntel: true, assigneeId: assigneeFilter, internalScope: showInternal ? "only" : "exclude", excludeClosedPipeline: true }),
        meetingsApi.listPaginated({ skip: 0, limit: 1, status: ["scheduled"], temporalStatus: ["upcoming"], hasIntel: false, assigneeId: assigneeFilter, internalScope: showInternal ? "only" : "exclude", excludeClosedPipeline: true }),
      ]);
      const ms = pageResp.items;

      // Empty-set fallback: if default "my upcoming" returns zero, broaden
      // to all reps once. ADMINS ONLY — reps are scoped to their own meetings
      // (server-enforced), so broadening would show nothing new and just
      // muddy the "my meetings" contract.
      if (
        isAdmin
        && !autoFallbackApplied
        && pageResp.total === 0
        && assigneeFilter.length === 1
        && user?.id
        && assigneeFilter[0] === user.id
        && !debouncedSearch
        && statusFilter.length === 1
        && (statusFilter[0] === "scheduled" || statusFilter[0] === "overdue")
      ) {
        setAutoFallbackApplied(true);
        setAssigneeFilter([]);
        return;
      }

      setMeetings(ms);
      setTotalMeetings(pageResp.total);
      setMeetingPages(pageResp.pages);
      setSummary({
        total: totalResp.total,
        upcoming: upcomingResp.total,
        hasIntel: hasIntelResp.total,
        noIntel: noIntelResp.total,
      });
      const monitor = await meetingsApi.prepMonitor(24).catch(() => null);
      setPrepMonitor(monitor);

      const companyIds = Array.from(new Set(ms.map((m) => m.company_id).filter(Boolean))) as string[];
      const dealIds = Array.from(new Set(ms.map((m) => m.deal_id).filter(Boolean))) as string[];

      const [companyResults, dealResults, activityResults] = await Promise.all([
        Promise.all(companyIds.map((id) => companiesApi.get(id).catch(() => null))),
        Promise.all(dealIds.map((id) => dealsApi.get(id).catch(() => null))),
        Promise.all(dealIds.map((id) => activitiesApi.list(id).catch(() => [] as Activity[]))),
      ]);

      setCompanies(companyResults.filter((item): item is Company => Boolean(item)));
      setDeals(dealResults.filter((item): item is Deal => Boolean(item)));
      setActivities(activityResults.flat());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!isAdmin) {
      setUsers([]);
      return;
    }
    getCachedUsers().then(setUsers).catch(() => setUsers([]));
  }, [isAdmin]);

  useEffect(() => {
    loadData();
  }, [page, statusFilter, intelFilter, assigneeFilter, typeFilter, linkFilter, debouncedSearch, showInternal]);

  useEffect(() => {
    setPage(1);
  }, [statusFilter, intelFilter, assigneeFilter, typeFilter, linkFilter, debouncedSearch, showInternal]);

  // Debounce the search input so typing doesn't hit the API on every
  // keystroke. 250ms feels fast enough to still be "live".
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(searchInput.trim()), 250);
    return () => clearTimeout(t);
  }, [searchInput]);

  const companyMap = useMemo(
    () => new Map(companies.map((c) => [c.id, c])),
    [companies]
  );

  const dealMap = useMemo(
    () => new Map(deals.map((d) => [d.id, d])),
    [deals]
  );

  // Latest activity per deal_id
  const latestActivityByDeal = useMemo(() => {
    const map = new Map<string, Activity>();
    for (const a of activities) {
      if (!a.deal_id) continue;
      const existing = map.get(a.deal_id);
      if (!existing || new Date(a.created_at) > new Date(existing.created_at)) {
        map.set(a.deal_id, a);
      }
    }
    return map;
  }, [activities]);

  const activitiesByDeal = useMemo(() => {
    const map = new Map<string, Activity[]>();
    for (const activity of activities) {
      if (!activity.deal_id) continue;
      const bucket = map.get(activity.deal_id) ?? [];
      bucket.push(activity);
      map.set(activity.deal_id, bucket);
    }
    return map;
  }, [activities]);

  const dealAssigneeMap = useMemo(() => {
    const userMap = new Map(users.map((u) => [u.id, u.name]));
    const map = new Map<string, { id: string; name: string }>();
    for (const d of deals) {
      if (d.assigned_to_id) {
        map.set(d.id, { id: d.assigned_to_id, name: userMap.get(d.assigned_to_id) ?? "Unknown" });
      }
    }
    return map;
  }, [deals, users]);

  // Who is this meeting assigned to? Prefer the deal owner, then the account
  // owner, then whoever owns/synced the calendar event — mirrors the backend's
  // pre-meeting recipient logic so the badge matches who actually gets the brief.
  const userNameById = useMemo(() => new Map(users.map((u) => [u.id, u.name])), [users]);
  const resolveMeetingAssignee = useCallback(
    (m: Meeting): string | undefined => {
      const ownerId =
        (m.deal_id ? dealMap.get(m.deal_id)?.assigned_to_id : undefined) ||
        (m.company_id ? companyMap.get(m.company_id)?.assigned_to_id : undefined) ||
        m.owner_user_id ||
        undefined;
      return ownerId ? userNameById.get(ownerId) ?? "Unknown" : undefined;
    },
    [dealMap, companyMap, userNameById],
  );

  const visibleUsers = useMemo(
    () => (hideDeveloper ? users.filter((teamUser) => !isDeveloperUser(teamUser)) : users),
    [hideDeveloper, users],
  );
  const handleRunIntel = async (meetingId: string) => {
    setRunningIntel(meetingId);
    try {
      await meetingsApi.runIntelligence(meetingId);
      await loadData();
    } catch {
      // swallow — user can retry
    } finally {
      setRunningIntel(null);
    }
  };

  // Manual close-out for overdue meetings when tl;dv / calendar didn't flip
  // status automatically. Reps need this to clear the red "Overdue" badge
  // without inventing a fake transcript.
  const handleUpdateStatus = async (meetingId: string, status: "completed" | "cancelled") => {
    setUpdatingStatus(meetingId);
    try {
      await meetingsApi.update(meetingId, { status } as Partial<Meeting>);
      await loadData();
    } catch {
      // swallow — user can retry
    } finally {
      setUpdatingStatus(null);
    }
  };

  // One-click unlink for meetings where the title names a different company
  // than the one auto-linked. Sending nulls + manually_linked=true locks the
  // choice so the next calendar sync cannot reattach the wrong account.
  const handleUnlinkMeeting = async (meetingId: string) => {
    setUnlinking(meetingId);
    try {
      await meetingsApi.update(meetingId, {
        company_id: null,
        deal_id: null,
        manually_linked: true,
      } as any);
      await loadData();
    } catch {
      // swallow — user can retry
    } finally {
      setUnlinking(null);
    }
  };

  const filtered = useMemo(() => {
    const now = Date.now();
    return meetings.filter((m) => {
      if (statusFilter.length > 0) {
        // Keep the client-side check aligned with the API's virtual status
        // slices for any locally refreshed rows.
        const scheduledTime = isValidDateValue(m.scheduled_at) ? new Date(m.scheduled_at as string).getTime() : null;
        const isOverdue = m.status === "scheduled" && scheduledTime !== null && scheduledTime < now;
        const isUpcoming = m.status === "scheduled" && (scheduledTime === null || scheduledTime >= now);
        const matches = statusFilter.some((s) => {
          if (s === "past") return m.status === "completed" || isOverdue;
          if (s === "overdue") return isOverdue;
          if (s === "scheduled") return isUpcoming;
          return s === m.status;
        });
        if (!matches) return false;
      }
      if (intelFilter.length > 0) {
        const intelState = m.research_data ? "has_intel" : "no_intel";
        if (!intelFilter.includes(intelState)) return false;
      }
      if (typeFilter.length > 0 && !typeFilter.includes(m.meeting_type)) return false;
      if (assigneeFilter.length > 0 && m.deal_id) {
        const assignee = dealAssigneeMap.get(m.deal_id);
        if (!assignee || !assigneeFilter.includes(assignee.id)) return false;
      } else if (assigneeFilter.length > 0 && !m.deal_id) {
        return false;
      }
      if (linkFilter.length > 0) {
        const linkState = !m.company_id || !m.deal_id ? "needs_review" : "linked";
        if (!linkFilter.includes(linkState)) return false;
      }
      return true;
    });
  }, [meetings, statusFilter, intelFilter, typeFilter, assigneeFilter, linkFilter, dealAssigneeMap]);

  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      const ta = isValidDateValue(a.scheduled_at) ? new Date(a.scheduled_at as string).getTime() : 0;
      const tb = isValidDateValue(b.scheduled_at) ? new Date(b.scheduled_at as string).getTime() : 0;
      return statusFilter.length === 1 && (statusFilter[0] === "completed" || statusFilter[0] === "past") ? tb - ta : ta - tb;
    });
  }, [filtered, statusFilter]);

  return (
    <div className="crm-page" style={{ display: "grid", gap: 18 }}>
      <style>{`
        @media (max-width: 768px) {
          .premeeting-filter-bar {
            flex-direction: column !important;
            gap: 8px !important;
            padding: 10px !important;
          }
          .premeeting-filter-bar > * {
            width: 100% !important;
          }
          .premeeting-card {
            padding: 12px !important;
          }
          .premeeting-card-grid {
            grid-template-columns: 1fr !important;
          }
        }
      `}</style>
      {/* Header */}
      <section className="crm-panel" style={{ padding: 22, display: "grid", gap: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
          <div style={{ minWidth: 0 }}>
            <h2 style={{ fontSize: 22, fontWeight: 800, color: colors.text, marginBottom: 4, letterSpacing: "-0.01em" }}>
              Meetings
            </h2>
            <p className="crm-muted" style={{ maxWidth: 620, lineHeight: 1.6, margin: 0, fontSize: 13 }}>
              Prep upcoming calls with account intel, stakeholder talk tracks, and recent activity. Use Past to review completed or overdue meetings in the same workspace.
            </p>
          </div>

          {/* Next-meeting spotlight (replaces generic counter when there's a clear
              next up). Clickable to jump straight to prep. */}
          {(() => {
            const now = Date.now();
            const next = sorted.find(
              (m) => m.scheduled_at && isValidDateValue(m.scheduled_at) && new Date(m.scheduled_at).getTime() >= now && m.status !== "cancelled",
            );
            if (!next) {
              return (
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "7px 12px", borderRadius: 999, background: "#f4f7ff", color: "#4b60cf", border: "1px solid #d7dffb", fontSize: 12, fontWeight: 700 }}>
                    <CalendarDays size={13} />
                    {summary.upcoming} upcoming
                  </span>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "7px 12px", borderRadius: 999, background: colors.greenSoft, color: colors.green, border: "1px solid #cfe8d7", fontSize: 12, fontWeight: 700 }}>
                    <CheckCircle2 size={13} />
                    {summary.hasIntel} intel ready
                  </span>
                  {summary.noIntel > 0 && (
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "7px 12px", borderRadius: 999, background: colors.amberSoft, color: colors.amber, border: "1px solid #ffe3b3", fontSize: 12, fontWeight: 700 }}>
                      <BrainCircuit size={13} />
                      {summary.noIntel} need intel
                    </span>
                  )}
                </div>
              );
            }
            const hrs = hoursUntil(next.scheduled_at);
            const imminent = hrs != null && hrs <= 2;
            const today = hrs != null && hrs <= 24;
            const countdown = hrs == null
              ? ""
              : hrs < 1
              ? "starts within the hour"
              : hrs < 24
              ? `in ${Math.round(hrs)}h`
              : `in ${Math.round(hrs / 24)}d`;
            const hasIntel = !!(next.research_data);
            return (
              <a
                href={`/meetings#${next.id}`}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 14,
                  padding: "12px 16px",
                  borderRadius: 14,
                  border: `1px solid ${imminent ? "#ffcdb8" : today ? "#c5d6ff" : "#d7dffb"}`,
                  background: imminent ? "#f3fbe3" : today ? "#eef4ff" : "#f6f8ff",
                  color: colors.text,
                  textDecoration: "none",
                  minWidth: 280,
                  maxWidth: 480,
                }}
                title="Jump to the next upcoming meeting"
              >
                <div style={{ width: 38, height: 38, borderRadius: 10, background: "#fff", border: "1px solid #e3ebf4", display: "grid", placeItems: "center", color: imminent ? "#4d7c0f" : "#3555c4" }}>
                  <CalendarDays size={18} />
                </div>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: "0.08em", textTransform: "uppercase", color: imminent ? "#4d7c0f" : "#3555c4" }}>
                    Next up · {countdown}
                  </div>
                  <div style={{ fontSize: 14, fontWeight: 700, color: colors.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {next.title}
                  </div>
                  <div style={{ fontSize: 11.5, color: colors.sub, marginTop: 2, display: "flex", gap: 8, alignItems: "center" }}>
                    {next.scheduled_at && <span>{formatOptionalDate(next.scheduled_at)}</span>}
                    <span style={{ width: 3, height: 3, borderRadius: 999, background: colors.faint }} />
                    {hasIntel
                      ? <span style={{ color: colors.green, fontWeight: 700 }}>Brief ready</span>
                      : <span style={{ color: colors.amber, fontWeight: 700 }}>Brief needed</span>}
                  </div>
                </div>
              </a>
            );
          })()}
        </div>
      </section>

      {prepMonitor && (
        <section className="crm-panel" style={{ padding: 18, display: "grid", gap: 14 }}>
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
            <div>
              <div style={{ fontSize: 12, fontWeight: 800, color: colors.primary, textTransform: "uppercase", letterSpacing: "0.06em" }}>
                Unlinked upcoming meetings
              </div>
              <div style={{ marginTop: 4, fontSize: 13, color: colors.sub }}>
                Next 24h: {prepMonitor.no_company_count} without account, {prepMonitor.no_deal_count} without deal, {prepMonitor.no_intel_count} without intel, {prepMonitor.no_recipient_count} without recipient.
              </div>
            </div>
          </div>
          {prepMonitor.unlinked.length === 0 ? (
            <div style={{ padding: "10px 12px", borderRadius: 10, background: colors.greenSoft, color: colors.green, fontSize: 13, fontWeight: 700 }}>
              No unlinked customer meetings in the next 24 hours.
            </div>
          ) : (
            <div style={{ display: "grid", gap: 8 }}>
              {prepMonitor.unlinked.slice(0, 8).map((meeting) => {
                const suggestedCompanyName = suggestCompanyNameFromMeetingTitle(meeting.title);
                const createAccountHref = `/account-sourcing?new=company&name=${encodeURIComponent(suggestedCompanyName)}&returnTo=${encodeURIComponent(`/meetings#${meeting.id}`)}`;
                return (
                  <div key={meeting.id} style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: 10, alignItems: "center", padding: "10px 12px", borderRadius: 10, border: `1px solid ${colors.border}`, background: "#fff" }}>
                    <div style={{ minWidth: 0 }}>
                      <Link to={`/meetings/${meeting.id}`} style={{ fontSize: 13, fontWeight: 800, color: colors.text, textDecoration: "none" }}>
                        {meeting.title}
                      </Link>
                      <div style={{ marginTop: 3, fontSize: 12, color: colors.sub }}>
                        {formatOptionalDate(meeting.scheduled_at)}
                        {!meeting.company_id ? " · needs account" : ""}
                        {!meeting.deal_id ? " · needs deal" : ""}
                      </div>
                    </div>
                    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <Link to={`/meetings/${meeting.id}`} style={{ fontSize: 12, color: colors.primary, fontWeight: 800, textDecoration: "none" }}>Review</Link>
                      {!meeting.company_id && (
                        <Link to={createAccountHref} style={{ fontSize: 12, color: colors.orange, fontWeight: 800, textDecoration: "none" }}>
                          Add account
                        </Link>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      )}

      {/* Filter bar — primary controls visible, advanced filters behind disclosure */}
      <section className="crm-panel premeeting-filter-bar" style={{ position: "relative", zIndex: 20, overflow: "visible", padding: 16, display: "grid", gap: 10 }}>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          <div style={{ position: "relative", minWidth: 280, flex: "1 1 280px", maxWidth: 380 }}>
            <Search size={14} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: colors.faint }} />
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search title, company, attendee…"
              style={{
                width: "100%",
                boxSizing: "border-box",
                height: 36,
                padding: "0 32px 0 30px",
                borderRadius: 10,
                border: `1px solid ${colors.border}`,
                fontSize: 13,
                color: colors.text,
                background: "#fff",
                outline: "none",
              }}
            />
            {searchInput && (
              <button
                type="button"
                onClick={() => setSearchInput("")}
                aria-label="Clear search"
                style={{ position: "absolute", right: 6, top: "50%", transform: "translateY(-50%)", border: "none", background: "transparent", color: colors.faint, cursor: "pointer", padding: 2, display: "inline-flex" }}
              >
                <X size={14} />
              </button>
            )}
          </div>
          <MultiSelectDropdown
            label="Status"
            options={[
              { value: "scheduled", label: "Upcoming" },
              { value: "past", label: "Past" },
              { value: "overdue", label: "Overdue" },
              { value: "completed", label: "Completed" },
              { value: "cancelled", label: "Cancelled" },
            ]}
            selected={statusFilter}
            onChange={setStatusFilter}
            placeholder="All statuses"
            selectionMode="single"
          />
          {isAdmin && visibleUsers.length > 0 && (
            <MultiSelectDropdown
              label="Rep"
              options={visibleUsers.map((u) => ({ value: u.id, label: u.name }))}
              selected={assigneeFilter}
              onChange={setAssigneeFilter}
              placeholder="All reps"
            />
          )}

          <button
            type="button"
            onClick={() => setShowInternal((v) => !v)}
            title={showInternal ? "Showing only internal meetings; click to return to customer meetings" : "Show only internal meetings"}
            style={{
              height: 38,
              padding: "0 12px",
              borderRadius: 10,
              border: showInternal ? "1px solid #c5b1ff" : "1px solid #d5e3ef",
              background: showInternal ? "#efebff" : "#fff",
              color: showInternal ? "#5b3bd4" : "#55657a",
              fontSize: 12,
              fontWeight: 700,
              cursor: "pointer",
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <span style={{ width: 8, height: 8, borderRadius: 999, background: showInternal ? "#7c3aed" : "#b8c4d4" }} />
            {showInternal ? "Internal only" : "Internal"}
          </button>

          <div style={{ flex: 1 }} />

          <button
            type="button"
            onClick={() => setShowAdvancedFilters((v) => !v)}
            style={{
              height: 38,
              padding: "0 12px",
              borderRadius: 10,
              border: (intelFilter.length || typeFilter.length || linkFilter.length) ? "1px solid #c5d6ff" : "1px solid #d5e3ef",
              background: (intelFilter.length || typeFilter.length || linkFilter.length) ? "#eef4ff" : "#fff",
              color: (intelFilter.length || typeFilter.length || linkFilter.length) ? "#3555c4" : "#55657a",
              fontSize: 12,
              fontWeight: 700,
              cursor: "pointer",
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <Filter size={12} />
            {showAdvancedFilters ? "Hide filters" : (intelFilter.length || typeFilter.length || linkFilter.length) ? `Filters (${(intelFilter.length ? 1 : 0) + (typeFilter.length ? 1 : 0) + (linkFilter.length ? 1 : 0)})` : "More filters"}
          </button>

          {(statusFilter.length !== 1 || statusFilter[0] !== "scheduled" || intelFilter.length > 0 || typeFilter.length > 0 || assigneeFilter.length > 0 || linkFilter.length > 0 || showInternal) && (
            <button
              type="button"
              onClick={() => { setStatusFilter(["scheduled"]); setIntelFilter([]); setTypeFilter([]); setAssigneeFilter([]); setLinkFilter([]); setShowInternal(false); }}
              style={{ height: 38, padding: "0 12px", borderRadius: 10, border: `1px solid #ffd0d8`, background: "#fff5f7", color: "#c55656", fontSize: 12, fontWeight: 700, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 5 }}
            >
              <RefreshCw size={11} />
              Reset
            </button>
          )}
        </div>

        {showAdvancedFilters && (
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", paddingTop: 10, borderTop: `1px dashed ${colors.border}` }}>
            <span style={{ fontSize: 11, fontWeight: 700, color: colors.faint, textTransform: "uppercase", letterSpacing: "0.06em" }}>Advanced</span>
            <MultiSelectDropdown
              label="Intel"
              options={[
                { value: "has_intel", label: "Intel ready" },
                { value: "no_intel", label: "No intel yet" },
              ]}
              selected={intelFilter}
              onChange={setIntelFilter}
              placeholder="All intel status"
            />
            <MultiSelectDropdown
              label="Type"
              options={["discovery", "demo", "poc", "qbr", "other"].map((t) => ({ value: t, label: t.replace(/_/g, " ") }))}
              selected={typeFilter}
              onChange={setTypeFilter}
              placeholder="All types"
            />
            <MultiSelectDropdown
              label="Link"
              options={[
                { value: "linked", label: "Linked" },
                { value: "needs_review", label: "Needs review" },
              ]}
              selected={linkFilter}
              onChange={setLinkFilter}
              placeholder="All links"
            />
          </div>
        )}
      </section>

      {/* Meeting cards */}
      {loading ? (
        <div className="crm-panel" style={{ padding: 18 }}>
          <SkeletonList rows={5} />
        </div>
      ) : sorted.length === 0 ? (
        <div className="crm-panel" style={{ padding: 40, textAlign: "center" }}>
          <CalendarDays size={36} style={{ color: colors.faint, margin: "0 auto 12px" }} />
          <div style={{ fontSize: 15, fontWeight: 700, color: colors.text, marginBottom: 6 }}>No meetings found</div>
          <div style={{ fontSize: 13, color: colors.faint, maxWidth: 400, margin: "0 auto" }}>
            {meetings.length === 0
              ? "Create a meeting from the Meetings page and link it to a deal. Beacon will generate a pre-meeting intel brief before the call."
              : "Try adjusting your filters — no meetings match the current selection."}
          </div>
        </div>
      ) : (
        <div style={{ display: "grid", gap: 12 }}>
          <div style={{ fontSize: 12, color: colors.faint, fontWeight: 600 }}>
            {totalMeetings} meeting{totalMeetings !== 1 ? "s" : ""} · {statusFilter.length === 1 && (statusFilter[0] === "completed" || statusFilter[0] === "past") ? "sorted by most recent" : "sorted by soonest first"}
          </div>
          {sorted.map((m) => {
            const deal = m.deal_id ? dealMap.get(m.deal_id) : undefined;
            const lastActivity = m.deal_id ? latestActivityByDeal.get(m.deal_id) : undefined;
            return (
              <MeetingIntelCard
                key={m.id}
                meeting={m}
                company={m.company_id ? companyMap.get(m.company_id) : undefined}
                deal={deal}
                lastActivity={lastActivity}
                dealActivities={m.deal_id ? activitiesByDeal.get(m.deal_id) ?? [] : []}
                assigneeName={isAdmin ? resolveMeetingAssignee(m) : undefined}
                allCompanies={companies}
                onRunIntel={handleRunIntel}
                onUpdateStatus={handleUpdateStatus}
                onUnlink={handleUnlinkMeeting}
                runningIntel={runningIntel}
                updatingStatus={updatingStatus}
                unlinking={unlinking}
              />
            );
          })}
        </div>
      )}

      {meetingPages > 1 && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
          <p style={{ margin: 0, fontSize: 12, color: colors.faint }}>
            Page {page} of {meetingPages}
          </p>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              style={{ height: 36, padding: "0 12px", borderRadius: 10, border: `1px solid ${colors.border}`, background: page <= 1 ? "#f7f9fc" : "#fff", color: page <= 1 ? colors.faint : colors.text, cursor: page <= 1 ? "not-allowed" : "pointer", fontSize: 12, fontWeight: 700 }}
            >
              Previous
            </button>
            <button
              type="button"
              disabled={page >= meetingPages}
              onClick={() => setPage((current) => Math.min(meetingPages, current + 1))}
              style={{ height: 36, padding: "0 12px", borderRadius: 10, border: `1px solid ${colors.border}`, background: page >= meetingPages ? "#f7f9fc" : "#fff", color: page >= meetingPages ? colors.faint : colors.text, cursor: page >= meetingPages ? "not-allowed" : "pointer", fontSize: 12, fontWeight: 700 }}
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

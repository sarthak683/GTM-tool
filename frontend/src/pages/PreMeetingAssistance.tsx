import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
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
import { activitiesApi, authApi, companiesApi, dealsApi, meetingsApi } from "../lib/api";
import { useAuth } from "../lib/AuthContext";
import type { Activity, Company, Deal, Meeting, MeetingPrepMonitor, User as UserType } from "../types/index";
import { formatOptionalDate, isValidDateValue } from "../lib/utils";
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

function suggestCompanyNameFromMeetingTitle(title: string): string {
  const cleaned = title
    .replace(/\b(introduction|intro|connect|sync|call|meeting|demo|walkthrough|discussion|cadence|kick off|clarification)\b/gi, " ")
    .replace(/\s+/g, " ")
    .trim();
  const parts = cleaned
    .split(/\s*(?:<>|<->| x | X | with | and |~|\||-|:|\/)\s*/g)
    .map((part) => part.replace(/\bbeacon(?:\.li)?\b/gi, "").replace(/\s+/g, " ").trim())
    .filter((part) => part.length >= 2);
  return parts.find((part) => !/^beacon$/i.test(part)) || title.trim();
}

function MeetingIntelCard({
  meeting,
  company,
  deal,
  lastActivity,
  dealActivities,
  assigneeName,
  allCompanies,
  onRunIntel,
  onUpdateStatus,
  onUnlink,
  runningIntel,
  updatingStatus,
  unlinking,
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
  const [expanded, setExpanded] = useState(false);
  const hours = hoursUntil(meeting.scheduled_at);
  const hasResearch = !!meeting.research_data;
  const hasIntelSent = !!(meeting as any).intel_email_sent_at;
  const isRunning = runningIntel === meeting.id;
  const isUnlinking = unlinking === meeting.id;
  const hasCompanyLink = !!meeting.company_id;
  const needsReview = !meeting.company_id || !meeting.deal_id;
  const suggestedCompanyName = useMemo(() => suggestCompanyNameFromMeetingTitle(meeting.title), [meeting.title]);
  const createAccountHref = `/account-sourcing?new=company&name=${encodeURIComponent(suggestedCompanyName)}&returnTo=${encodeURIComponent(`/pre-meeting-assistance#${meeting.id}`)}`;
  const titleMismatchCompany = detectTitleCompanyMismatch(
    meeting.title,
    meeting.company_id || undefined,
    allCompanies
  );

  // Classify each meeting across the full timeline:
  //   - "in_progress": started but not yet ended (within 90 min of scheduled_at)
  //   - "overdue":     scheduled_at has passed but status is still "scheduled"
  //                    (calendar / tl;dv didn't flip it to completed, or the
  //                    rep never logged an outcome)
  //   - "imminent":    within 2 hours
  //   - "soon":        within 12 hours
  //   - "upcoming":    further out
  //   - "completed":   already marked completed
  //   - "cancelled":   explicitly cancelled
  const hoursPast = hours !== null ? -hours : null; // positive = past
  const isCompleted = meeting.status === "completed";
  const isCancelled = meeting.status === "cancelled";
  const urgency: "completed" | "cancelled" | "in_progress" | "overdue" | "imminent" | "soon" | "upcoming" =
    isCancelled ? "cancelled"
    : isCompleted ? "completed"
    : hours === null ? "upcoming"
    : hours < 0 && hoursPast !== null && hoursPast * 60 <= 90 ? "in_progress"  // within 90 min after start
    : hours < 0 ? "overdue"
    : hours <= 2 ? "imminent"
    : hours <= 12 ? "soon"
    : "upcoming";

  const pastLabel = hoursPast !== null
    ? hoursPast >= 48 ? `${Math.round(hoursPast / 24)}d overdue`
      : hoursPast >= 1 ? `${hoursPast}h overdue`
      : "Just ended"
    : "Overdue";

  const urgencyStyle = {
    imminent: { bg: "#fff2ec", color: colors.orange, border: "#ffd3be", label: "< 2 hrs" },
    soon: { bg: colors.amberSoft, color: colors.amber, border: "#ffe3b3", label: hours !== null ? `${hours}h away` : "" },
    upcoming: { bg: "#f4f7ff", color: "#4b60cf", border: "#d7dffb", label: hours !== null ? `${hours}h away` : "Upcoming" },
    in_progress: { bg: "#fff5d9", color: "#9a6b00", border: "#f6dd9b", label: "In progress" },
    overdue: { bg: "#fdecec", color: "#b42336", border: "#f5c2c2", label: pastLabel },
    completed: { bg: "#ecf8f0", color: "#15803d", border: "#c7e8d3", label: "Completed" },
    cancelled: { bg: "#f1f5f9", color: "#64748b", border: "#e2e8f0", label: "Cancelled" },
  }[urgency];

  // ── Parse research_data ──────────────────────────────────────────────────
  const rd = (meeting.research_data ?? {}) as Record<string, any>;
  const execBriefing: string = rd.executive_briefing ?? "";
  const whyNow: Array<{ title: string; detail: string }> = rd.why_now_signals ?? [];
  const recommendations: string[] = rd.meeting_recommendations ?? [];
  const attendeeIntel = rd.attendee_intelligence ?? {};
  const crmSignals = rd.crm_signals ?? {};
  const meetingAttendees = normalizeMeetingAttendees(meeting.attendees);
  const stakeholders = normalizeStakeholderCards(attendeeIntel, meetingAttendees);
  const coverage: number | null = attendeeIntel.committee_coverage?.coverage_score ?? null;
  const competitive: Array<{ name?: string; competitor?: string; summary?: string }> = rd.competitive_landscape ?? [];
  const intentSignals = rd.intent_signals ?? {};
  const hiringRoles: string[] = intentSignals.hiring ?? [];
  const websiteAnalysis = rd.website_analysis ?? {};
  const techStack: string[] = websiteAnalysis.tech_stack ?? rd.company_snapshot?.tech_stack ?? [];
  const pricingModel: string = websiteAnalysis.pricing_model ?? "";
  const hunterCo = rd.hunter_company ?? {};
  const companySnapshot = rd.company_snapshot ?? {};
  const companyProfile = rd.company_profile ?? {};
  const companyBackground = rd.company_background ?? {};
  const newsItems: Array<{ title?: string; url?: string; published?: string }> = rd.recent_news ?? rd.news ?? [];
  const battlecards: Array<{ competitor?: string; win_reasons?: string[]; objection_handling?: string }> = rd.battlecards ?? [];
  const priorMeetings: Array<Record<string, any>> = Array.isArray(crmSignals.prior_meetings) ? crmSignals.prior_meetings : [];

  const attendeeContactIds = new Set(stakeholders.map((stakeholder) => stakeholder.contactId).filter(Boolean));
  const sortedDealActivities = [...dealActivities].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
  const attendeeActivities = sortedDealActivities.filter((activity) => activity.contact_id && attendeeContactIds.has(activity.contact_id));
  const relevantActivities = attendeeActivities.length > 0 ? attendeeActivities : sortedDealActivities;
  const activityCounts = relevantActivities.reduce(
    (acc, activity) => {
      const channel = activityChannel(activity);
      if (channel === "email") acc.emails += 1;
      else if (channel === "call") acc.calls += 1;
      else if (channel === "linkedin") acc.linkedin += 1;
      else if (channel === "meeting") acc.meetings += 1;
      return acc;
    },
    { emails: 0, calls: 0, linkedin: 0, meetings: priorMeetings.length || 0 },
  );
  activityCounts.meetings = Math.max(activityCounts.meetings, priorMeetings.length);

  const dominantChannel = ([
    { key: "emails", label: "email", value: activityCounts.emails },
    { key: "calls", label: "call", value: activityCounts.calls },
    { key: "linkedin", label: "LinkedIn", value: activityCounts.linkedin },
    { key: "meetings", label: "meeting", value: activityCounts.meetings },
  ].sort((a, b) => b.value - a.value)[0]);

  const likelyDecisionMaker =
    stakeholders.find((stakeholder) => stakeholder.role === "economic_buyer" && stakeholder.status === "attending")
    || stakeholders.find((stakeholder) => stakeholder.role === "economic_buyer")
    || stakeholders.find((stakeholder) => stakeholder.role === "champion" && stakeholder.status === "attending")
    || stakeholders.find((stakeholder) => stakeholder.role === "champion")
    || stakeholders[0];
  const championContact =
    stakeholders.find((stakeholder) => stakeholder.role === "champion" && stakeholder.status === "attending")
    || stakeholders.find((stakeholder) => stakeholder.role === "champion");
  const companySummary =
    companyBackground.description
    || companyBackground.extract
    || whyNow[0]?.detail
    || execBriefing.replace(/\*\*/g, "").replace(/##\s*/g, "").split("\n").filter(Boolean)[0]
    || "";
  const companyLinks = [
    company?.id ? { label: "Company record", href: `/companies/${company.id}`, internal: true } : null,
    domainUrl(company?.domain || companyProfile.domain) ? { label: "Website", href: domainUrl(company?.domain || companyProfile.domain)!, internal: false } : null,
    newsItems.find((item) => item.url)?.url ? { label: "Latest news", href: newsItems.find((item) => item.url)!.url!, internal: false } : null,
  ].filter((item): item is { label: string; href: string; internal: boolean } => Boolean(item));
  const activityScopeLabel = attendeeActivities.length > 0 ? "Activity with invited prospects" : "Deal-wide activity";

  // Risks
  const risks: string[] = [];
  if (hiringRoles.length) risks.push(`Hiring ${hiringRoles.length} role${hiringRoles.length > 1 ? "s" : ""}`);
  if (competitive.length) risks.push("Competitor activity");

  // Brief teaser (first non-empty line)
  const briefTeaser = execBriefing
    ? execBriefing.replace(/\*\*/g, "").replace(/##\s*/g, "").split("\n").filter(Boolean)[0]?.slice(0, 220)
    : null;

  // Sections that only show when expanded
  const hasFullBriefing = execBriefing.length > (briefTeaser?.length ?? 0) + 5;

  const roleColors: Record<string, { bg: string; color: string; border: string }> = {
    economic_buyer: { bg: "#fff4e6", color: "#b45309", border: "#fde68a" },
    champion: { bg: "#f0fdf4", color: "#15803d", border: "#bbf7d0" },
    technical_evaluator: { bg: "#eff6ff", color: "#1d4ed8", border: "#bfdbfe" },
    implementation_owner: { bg: "#faf5ff", color: "#7e22ce", border: "#e9d5ff" },
    unknown: { bg: "#f8fafc", color: "#64748b", border: "#e2e8f0" },
  };

  // Left-border accent by urgency so cards visually prioritize themselves
  // in a long list. Imminent = coral; in-progress/overdue = amber; soon = blue;
  // otherwise neutral.
  const accent =
    urgency === "imminent" ? "#ff6b35"
    : urgency === "in_progress" ? "#b56d00"
    : urgency === "overdue" ? "#c0392b"
    : urgency === "soon" ? "#4261d6"
    : "transparent";

  return (
    <div id={meeting.id}
      style={{
        background: "#fff",
        border: `1px solid ${urgency === "imminent" ? "#ffd3be" : colors.border}`,
        borderLeft: accent === "transparent" ? `1px solid ${colors.border}` : `4px solid ${accent}`,
        borderRadius: 16,
        overflow: "hidden",
      }}
    >
      {/* ── Top section (always visible) ─────────────────────────── */}
      <div style={{ padding: "18px 20px", display: "grid", gap: 12 }}>

        {/* Header row */}
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <Link
              to={`/meetings/${meeting.id}`}
              style={{ fontSize: 15, fontWeight: 800, color: colors.text, textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 6 }}
            >
              {meeting.title}
              <ExternalLink size={13} style={{ color: colors.faint, flexShrink: 0 }} />
            </Link>
            <div style={{ marginTop: 4, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              {company && (
                <span style={{ fontSize: 12, color: colors.sub, fontWeight: 600 }}>{company.name}</span>
              )}
              {needsReview && (
                <span
                  title="Beacon could not confidently link this meeting to a company and deal. Use Re-link before relying on the prep."
                  style={{
                    fontSize: 10,
                    fontWeight: 800,
                    textTransform: "uppercase",
                    letterSpacing: "0.06em",
                    padding: "3px 8px",
                    borderRadius: 999,
                    background: "#fff6ec",
                    color: colors.orange,
                    border: "1px solid #ffd3be",
                  }}
                >
                  Needs review
                </span>
              )}
              <span style={{ fontSize: 11, fontWeight: 700, textTransform: "capitalize", padding: "2px 8px", borderRadius: 999, background: "#f0f4fb", color: colors.sub, border: `1px solid ${colors.border}` }}>
                {meeting.meeting_type.replace(/_/g, " ")}
              </span>
              {assigneeName && (
                <span style={{ fontSize: 12, color: colors.faint, display: "inline-flex", alignItems: "center", gap: 4 }}>
                  <User size={11} />
                  {assigneeName}
                </span>
              )}
            </div>
          </div>

          <div style={{ flexShrink: 0, padding: "5px 10px", borderRadius: 999, background: urgencyStyle.bg, color: urgencyStyle.color, border: `1px solid ${urgencyStyle.border}`, fontSize: 11, fontWeight: 800, display: "inline-flex", alignItems: "center", gap: 5 }}>
            <Clock3 size={11} />
            {urgencyStyle.label}
          </div>
        </div>

        {/* Scheduled time */}
        <div style={{ display: "flex", alignItems: "center", gap: 6, color: colors.faint, fontSize: 12 }}>
          <CalendarDays size={13} />
          <span>{formatOptionalDate(meeting.scheduled_at)}</span>
          {meeting.scheduled_at && !isValidDateValue(meeting.scheduled_at) && (
            <span style={{ color: colors.orange, fontWeight: 700 }}>Fix date before prep</span>
          )}
        </div>

        {needsReview && (
          <div style={{ padding: "10px 12px", borderRadius: 10, background: "#fff8f1", border: "1px solid #ffe0bd", display: "flex", alignItems: "flex-start", gap: 10 }}>
            <AlertTriangle size={13} style={{ color: colors.orange, marginTop: 1, flexShrink: 0 }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12, color: "#7a5531", lineHeight: 1.5 }}>
                {!meeting.company_id
                  ? "No account is linked to this meeting. Link an existing account, or create the account from Account Sourcing before generating prep."
                  : "The account is linked, but the opportunity is missing. Open the meeting and link the right deal before relying on the prep."}
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
                <Link
                  to={`/meetings/${meeting.id}`}
                  style={{ padding: "5px 10px", borderRadius: 8, border: "1px solid #ffd3be", background: "#fff", color: "#7a3f1f", fontSize: 11, fontWeight: 800, textDecoration: "none" }}
                >
                  Link existing
                </Link>
                {!meeting.company_id && (
                  <Link
                    to={createAccountHref}
                    style={{ padding: "5px 10px", borderRadius: 8, border: "1px solid #ffd3be", background: "#fff", color: "#7a3f1f", fontSize: 11, fontWeight: 800, textDecoration: "none" }}
                  >
                    Add in Account Sourcing
                  </Link>
                )}
              </div>
            </div>
          </div>
        )}

        {titleMismatchCompany && (
          <div style={{ padding: "10px 12px", borderRadius: 10, background: "#fff2ec", border: "1px solid #ffc8a8", display: "flex", alignItems: "flex-start", gap: 10 }}>
            <AlertTriangle size={14} style={{ color: "#c2410c", marginTop: 1, flexShrink: 0 }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: "#7c2d12" }}>
                Possible company mismatch
              </div>
              <div style={{ fontSize: 12, color: "#7a3f1f", lineHeight: 1.5, marginTop: 2 }}>
                Title mentions <span style={{ fontWeight: 700 }}>{titleMismatchCompany.name}</span>, but this meeting is linked to <span style={{ fontWeight: 700 }}>{company?.name || "another company"}</span>. An attendee from the wrong account likely caused the auto-link. Open the meeting and use <span style={{ fontWeight: 700 }}>Re-link</span>, or unlink now.
              </div>
              <div style={{ marginTop: 6, display: "flex", gap: 8 }}>
                <button
                  onClick={(e) => { e.preventDefault(); e.stopPropagation(); onUnlink(meeting.id); }}
                  disabled={isUnlinking}
                  style={{ padding: "4px 10px", borderRadius: 8, border: "1px solid #ffc8a8", background: "#fff", color: "#7c2d12", fontSize: 11, fontWeight: 700, cursor: isUnlinking ? "wait" : "pointer" }}
                >
                  {isUnlinking ? "Unlinking…" : "Unlink company & deal"}
                </button>
                <Link
                  to={`/meetings/${meeting.id}`}
                  style={{ padding: "4px 10px", borderRadius: 8, border: "1px solid #ffc8a8", background: "#fff", color: "#7c2d12", fontSize: 11, fontWeight: 700, textDecoration: "none" }}
                >
                  Open meeting
                </Link>
              </div>
            </div>
          </div>
        )}

        {/* Status badges */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {hasResearch ? (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "5px 10px", borderRadius: 999, background: colors.greenSoft, color: colors.green, border: "1px solid #cfe8d7", fontSize: 12, fontWeight: 700 }}>
              <CheckCircle2 size={13} /> Intel ready
            </span>
          ) : (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "5px 10px", borderRadius: 999, background: colors.amberSoft, color: colors.amber, border: `1px solid #ffe3b3`, fontSize: 12, fontWeight: 700 }}>
              <BrainCircuit size={13} /> No intel yet
            </span>
          )}
          {hasIntelSent && (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "5px 10px", borderRadius: 999, background: colors.primarySoft, color: colors.primary, border: "1px solid #d5e5ff", fontSize: 12, fontWeight: 700 }}>
              <MailCheck size={13} /> Brief sent
            </span>
          )}
          {meeting.meeting_score != null && (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "5px 10px", borderRadius: 999, background: colors.violetSoft, color: colors.violet, border: "1px solid #eadbff", fontSize: 12, fontWeight: 700 }}>
              Score {meeting.meeting_score}/100
            </span>
          )}
          {coverage !== null && (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "5px 10px", borderRadius: 999, background: coverage >= 75 ? colors.greenSoft : colors.amberSoft, color: coverage >= 75 ? colors.green : colors.amber, border: `1px solid ${coverage >= 75 ? "#cfe8d7" : "#ffe3b3"}`, fontSize: 12, fontWeight: 700 }}>
              <Target size={11} /> {coverage}% coverage
            </span>
          )}
          {risks.map((r, i) => (
            <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "5px 10px", borderRadius: 999, background: "#fff5f0", color: colors.orange, border: "1px solid #ffd3be", fontSize: 12, fontWeight: 700 }}>
              <AlertTriangle size={11} /> {r}
            </span>
          ))}
        </div>

        {/* ── Deal context strip ─────────────────────────────────── */}
        {deal && (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", padding: "10px 14px", borderRadius: 12, background: "#f8fafc", border: "1px solid #e8edf5" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12 }}>
              <Briefcase size={12} style={{ color: colors.faint }} />
              <span style={{ color: colors.sub, fontWeight: 600 }}>{deal.stage.replace(/_/g, " ")}</span>
            </div>
            {deal.value != null && (
              <>
                <span style={{ color: colors.border }}>·</span>
                <span style={{ fontSize: 12, color: colors.sub, fontWeight: 600 }}>
                  ${deal.value.toLocaleString()}
                </span>
              </>
            )}
            <span style={{ color: colors.border }}>·</span>
            <span style={{ fontSize: 12, color: deal.days_in_stage > 30 ? colors.amber : colors.faint }}>
              {deal.days_in_stage}d in stage
            </span>
            {deal.health && (
              <>
                <span style={{ color: colors.border }}>·</span>
                <span style={{ fontSize: 12, color: deal.health === "at_risk" ? colors.red : deal.health === "needs_attention" ? colors.amber : colors.green, fontWeight: 700, textTransform: "capitalize" }}>
                  {deal.health.replace(/_/g, " ")}
                </span>
              </>
            )}
            {lastActivity && (
              <>
                <span style={{ color: colors.border }}>·</span>
                <span style={{ fontSize: 12, color: colors.faint, display: "inline-flex", alignItems: "center", gap: 4 }}>
                  <ActivityIcon size={11} />
                  Last touch: {timeAgo(lastActivity.created_at)}
                  {lastActivity.ai_summary ? ` — ${lastActivity.ai_summary.slice(0, 60)}${lastActivity.ai_summary.length > 60 ? "…" : ""}` : lastActivity.email_subject ? ` — ${lastActivity.email_subject.slice(0, 50)}` : ""}
                </span>
              </>
            )}
          </div>
        )}

        {/* Deal next step */}
        {deal?.next_step && (
          <div style={{ padding: "8px 14px", borderRadius: 10, background: "#f0fdf4", border: "1px solid #bbf7d0", display: "flex", alignItems: "flex-start", gap: 8 }}>
            <CheckCircle2 size={13} style={{ color: "#15803d", marginTop: 2, flexShrink: 0 }} />
            <div>
              <div style={{ fontSize: 10, fontWeight: 700, color: "#15803d", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 2 }}>Agreed Next Step</div>
              <div style={{ fontSize: 12, color: "#1e4032", lineHeight: 1.45 }}>{deal.next_step}</div>
            </div>
          </div>
        )}

        {/* ── Intel preview (briefing teaser + top action) ────────── */}
        {hasResearch && (
          <div style={{ display: "grid", gap: 8 }}>
            {briefTeaser && (
              <div style={{ padding: "10px 14px", borderRadius: 12, background: "#fff8f5", border: "1px solid #ffd5be" }}>
                <SectionHeader icon={Sparkles} label="Executive Briefing" color="#b05a2a" />
                <p style={{ fontSize: 12.5, color: "#3d5268", lineHeight: 1.55, margin: 0 }}>
                  {briefTeaser}{hasFullBriefing && !expanded ? "…" : ""}
                </p>
              </div>
            )}

            {/* Top recommendation */}
            {recommendations[0] && (
              <div style={{ padding: "9px 14px", borderRadius: 12, background: "#f0fdf4", border: "1px solid #bbf7d0", display: "flex", alignItems: "flex-start", gap: 8 }}>
                <TrendingUp size={13} style={{ color: "#15803d", marginTop: 2, flexShrink: 0 }} />
                <div>
                  <div style={{ fontSize: 10, fontWeight: 700, color: "#15803d", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 2 }}>Top Recommendation</div>
                  <div style={{ fontSize: 12, color: "#1e4032", lineHeight: 1.45 }}>{recommendations[0]}</div>
                </div>
              </div>
            )}

            {/* Stakeholder preview (collapsed — names only) */}
            {!expanded && stakeholders.length > 0 && (
              <div style={{ padding: "10px 14px", borderRadius: 12, background: "#f5f0ff", border: "1px solid #e0d3ff" }}>
                <SectionHeader icon={Users} label={`Stakeholders (${stakeholders.length})`} color="#5a1fa5" />
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {stakeholders.map((s, i) => {
                    const role = s.role ?? "unknown";
                    const rc = roleColors[role] ?? roleColors.unknown;
                    return (
                      <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: "3px 9px", borderRadius: 999, background: rc.bg, color: rc.color, border: `1px solid ${rc.border}`, fontSize: 11, fontWeight: 700 }}>
                        {s.name}{s.title ? ` · ${s.title}` : ""}{s.status === "recommended" ? " · not on invite" : ""}
                      </span>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── Expand / Collapse button ─────────────────────────────── */}
        {hasResearch && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12, fontWeight: 700, color: colors.primary, background: "none", border: "none", cursor: "pointer", padding: 0, alignSelf: "flex-start" }}
          >
            {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            {expanded ? "Collapse brief" : "Show full prep brief"}
          </button>
        )}
      </div>

      {/* ── Expanded full prep brief ──────────────────────────────────────── */}
      {expanded && hasResearch && (
        <div style={{ borderTop: `1px solid ${colors.border}`, padding: "18px 20px", display: "grid", gap: 16, background: "#fafbfd" }}>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 14 }}>
            <div style={{ padding: "14px 16px", borderRadius: 12, background: "#fff", border: "1px solid #e8edf5", display: "grid", gap: 10 }}>
              <SectionHeader icon={Building2} label="Account Brief" color={colors.sub} />
              <div style={{ fontSize: 13, color: "#3d5268", lineHeight: 1.65 }}>
                {companySummary || "Beacon gathered account research, but the concise company narrative is still thin. Use the sources below for the fuller context."}
              </div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {(companySnapshot.industry || companyProfile.industry) && <Pill label={String(companySnapshot.industry || companyProfile.industry)} color="#24567e" bg="#f3f8ff" border="#d5e5ff" />}
                {(hunterCo.employees || companySnapshot.employee_count) && <Pill label={`${hunterCo.employees ?? companySnapshot.employee_count} employees`} color="#1f3144" bg="#f8fafc" border="#e2e8f0" />}
                {pricingModel && <Pill label={pricingModel} color="#7c3f00" bg="#fff4df" border="#ffe3b3" />}
              </div>
              {companyLinks.length > 0 && (
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {companyLinks.map((link) =>
                    link.internal ? (
                      <Link
                        key={link.label}
                        to={link.href}
                        style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12, color: colors.primary, fontWeight: 700, textDecoration: "none" }}
                      >
                        {link.label} <ExternalLink size={11} />
                      </Link>
                    ) : (
                      <a
                        key={link.label}
                        href={link.href}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12, color: colors.primary, fontWeight: 700, textDecoration: "none" }}
                      >
                        {link.label} <ExternalLink size={11} />
                      </a>
                    )
                  )}
                </div>
              )}
            </div>

            <div style={{ padding: "14px 16px", borderRadius: 12, background: "#fff", border: "1px solid #e8edf5", display: "grid", gap: 10 }}>
              <SectionHeader icon={Users} label="Who To Hear Out" color="#5a1fa5" />
              {likelyDecisionMaker ? (
                <div style={{ display: "grid", gap: 10 }}>
                  <div style={{ padding: "10px 12px", borderRadius: 10, background: "#fff8f5", border: "1px solid #ffd5be" }}>
                    <div style={{ fontSize: 10, fontWeight: 700, color: "#b05a2a", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 4 }}>Likely decision maker</div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <span style={{ fontSize: 13, fontWeight: 800, color: colors.text }}>{likelyDecisionMaker.name}</span>
                      {likelyDecisionMaker.title && <span style={{ fontSize: 12, color: colors.sub }}>{likelyDecisionMaker.title}</span>}
                      <Pill
                        label={`${likelyDecisionMaker.roleLabel}${likelyDecisionMaker.status === "recommended" ? " · not on invite" : ""}`}
                        color="#b05a2a"
                        bg="#fff4e8"
                        border="#ffd5be"
                      />
                    </div>
                    {likelyDecisionMaker.likelyFocus && (
                      <div style={{ fontSize: 12, color: "#7a5531", lineHeight: 1.55, marginTop: 6 }}>
                        Hear them out on: {likelyDecisionMaker.likelyFocus}
                      </div>
                    )}
                    <div style={{ marginTop: 6, display: "flex", gap: 8, flexWrap: "wrap" }}>
                      {likelyDecisionMaker.contactId && (
                        <Link to={`/contacts/${likelyDecisionMaker.contactId}`} style={{ fontSize: 12, color: colors.primary, fontWeight: 700, textDecoration: "none" }}>
                          Prospect record
                        </Link>
                      )}
                      {likelyDecisionMaker.linkedinUrl && (
                        <a href={likelyDecisionMaker.linkedinUrl} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12, color: colors.primary, fontWeight: 700, textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 4 }}>
                          LinkedIn <ExternalLink size={11} />
                        </a>
                      )}
                    </div>
                  </div>

                  {championContact && championContact.name !== likelyDecisionMaker.name && (
                    <div style={{ padding: "10px 12px", borderRadius: 10, background: "#f0fdf4", border: "1px solid #bbf7d0" }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: "#15803d", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 4 }}>Likely champion</div>
                      <div style={{ fontSize: 13, fontWeight: 800, color: colors.text }}>{championContact.name}{championContact.title ? ` · ${championContact.title}` : ""}</div>
                      {championContact.likelyFocus && <div style={{ fontSize: 12, color: "#1e4032", lineHeight: 1.55, marginTop: 6 }}>Likely angle: {championContact.likelyFocus}</div>}
                    </div>
                  )}
                </div>
              ) : (
                <div style={{ fontSize: 12, color: colors.faint, lineHeight: 1.55 }}>
                  No clear buying-committee view yet. Run intel again after attendee links or contact mapping improves.
                </div>
              )}
            </div>
          </div>

          <div style={{ padding: "14px 16px", borderRadius: 12, background: "#fff", border: "1px solid #e8edf5", display: "grid", gap: 12 }}>
            <SectionHeader icon={ActivityIcon} label={activityScopeLabel} color={colors.sub} />
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <Pill label={`${activityCounts.emails} emails`} color="#2f8d5d" bg="#eefbf2" border="#cfe8d7" />
              <Pill label={`${activityCounts.calls} calls`} color="#445fd0" bg="#eef3ff" border="#d5e5ff" />
              <Pill label={`${activityCounts.linkedin} LinkedIn`} color="#0a66c2" bg="#eef4ff" border="#d7e2fb" />
              <Pill label={`${activityCounts.meetings} meetings`} color="#b05a2a" bg="#fff4e8" border="#ffd5be" />
            </div>
            <div style={{ fontSize: 12, color: colors.sub, lineHeight: 1.6 }}>
              {dominantChannel && dominantChannel.value > 0
                ? `Most of the motion so far has happened through ${dominantChannel.label}. Use the timeline below to see what topics actually moved the conversation.`
                : "Beacon does not yet have enough logged channel activity to show a conviction path for this deal."}
            </div>

            {relevantActivities.length > 0 && (
              <div style={{ display: "grid", gap: 8 }}>
                {relevantActivities.slice(0, 8).map((activity) => {
                  const channel = activityChannel(activity);
                  const actor = activity.user_name || activity.aircall_user_name || activity.email_from || "Beacon";
                  return (
                    <div key={activity.id} style={{ padding: "10px 12px", borderRadius: 10, background: "#f8fafc", border: "1px solid #e8edf5" }}>
                      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap", marginBottom: 4 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                          <Pill label={activityChannelLabel(channel)} color="#3856c8" bg="#eef4ff" border="#d7e2fb" />
                          <span style={{ fontSize: 11, color: colors.faint }}>{actor}</span>
                        </div>
                        <span style={{ fontSize: 11, color: colors.faint }}>{timeAgo(activity.created_at)}</span>
                      </div>
                      <div style={{ fontSize: 12, color: "#3d5268", lineHeight: 1.55 }}>
                        {activitySnippet(activity)}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {priorMeetings.length > 0 && (
              <div style={{ display: "grid", gap: 8 }}>
                <div style={{ fontSize: 10, fontWeight: 700, color: colors.faint, textTransform: "uppercase", letterSpacing: "0.06em" }}>Prior meeting takeaways</div>
                {priorMeetings.slice(0, 2).map((prior, index) => (
                  <div key={`${prior.title || "prior"}-${index}`} style={{ padding: "10px 12px", borderRadius: 10, background: "#fff8f5", border: "1px solid #ffd5be" }}>
                    <div style={{ fontSize: 12, fontWeight: 800, color: colors.text }}>{prior.title || "Previous meeting"}</div>
                    {prior.ai_summary && <div style={{ fontSize: 12, color: "#7a5531", lineHeight: 1.55, marginTop: 4 }}>{prior.ai_summary}</div>}
                    {prior.next_steps && <div style={{ fontSize: 11, color: colors.sub, marginTop: 4 }}>Next step then: {prior.next_steps}</div>}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Full executive briefing */}
          {execBriefing && (
            <div style={{ padding: "14px 16px", borderRadius: 12, background: "#fff8f5", border: "1px solid #ffd5be" }}>
              <SectionHeader icon={Sparkles} label="Full Executive Briefing" color="#b05a2a" />
              <div style={{ fontSize: 13, color: "#3d5268", lineHeight: 1.7, whiteSpace: "pre-wrap" }}>
                {execBriefing.replace(/\*\*/g, "").replace(/##\s*/g, "").replace(/^#+\s*/gm, "")}
              </div>
            </div>
          )}

          {/* Why-now signals — all of them */}
          {whyNow.length > 0 && (
            <div style={{ padding: "14px 16px", borderRadius: 12, background: "#f3f8ff", border: "1px solid #d5e5ff" }}>
              <SectionHeader icon={Zap} label={`Why Now — ${whyNow.length} Signal${whyNow.length > 1 ? "s" : ""}`} color="#24567e" />
              <div style={{ display: "grid", gap: 10 }}>
                {whyNow.map((s, i) => (
                  <div key={i} style={{ paddingLeft: 10, borderLeft: "3px solid #93c5fd" }}>
                    <div style={{ fontSize: 12, fontWeight: 700, color: "#1e3a5f", marginBottom: 2 }}>{s.title}</div>
                    <div style={{ fontSize: 12, color: "#4a6580", lineHeight: 1.5 }}>{s.detail}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Stakeholder cards — full detail */}
          {stakeholders.length > 0 && (
            <div>
              <SectionHeader icon={Users} label={`Stakeholder Prep (${stakeholders.length})`} color="#5a1fa5" />
              <div style={{ display: "grid", gap: 10 }}>
                {stakeholders.map((s, i) => {
                  const role = s.role ?? "unknown";
                  const rc = roleColors[role] ?? roleColors.unknown;
                  return (
                    <div key={i} style={{ padding: "12px 14px", borderRadius: 12, background: "#fff", border: `1px solid ${rc.border}` }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
                        <span style={{ fontSize: 13, fontWeight: 800, color: colors.text }}>{s.name}</span>
                        {s.title && <span style={{ fontSize: 12, color: colors.sub }}>{s.title}</span>}
                        <span style={{ padding: "2px 8px", borderRadius: 999, background: rc.bg, color: rc.color, border: `1px solid ${rc.border}`, fontSize: 10, fontWeight: 700, textTransform: "capitalize" }}>
                          {s.roleLabel}{s.status === "recommended" ? " · not on invite" : ""}
                        </span>
                        {s.contactId && (
                          <Link to={`/contacts/${s.contactId}`} style={{ fontSize: 11, color: colors.primary, textDecoration: "none" }}>
                            Prospect record
                          </Link>
                        )}
                        {s.linkedinUrl && (
                          <a href={s.linkedinUrl} target="_blank" rel="noopener noreferrer" style={{ fontSize: 11, color: colors.primary, display: "inline-flex", alignItems: "center", gap: 3 }}>
                            LinkedIn <ExternalLink size={10} />
                          </a>
                        )}
                      </div>
                      {s.likelyFocus && (
                        <div style={{ marginBottom: 8 }}>
                          <div style={{ fontSize: 10, fontWeight: 700, color: colors.faint, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 3 }}>Likely Focus</div>
                          <div style={{ fontSize: 12, color: "#3d5268", lineHeight: 1.5, padding: "8px 10px", background: "#f8fafc", borderRadius: 8, border: "1px solid #e8edf5" }}>{s.likelyFocus}</div>
                        </div>
                      )}
                      {s.talkTrack && (
                        <div style={{ marginBottom: 8 }}>
                          <div style={{ fontSize: 10, fontWeight: 700, color: colors.faint, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 3 }}>Talk Track</div>
                          <div style={{ fontSize: 12, color: "#3d5268", lineHeight: 1.5, padding: "8px 10px", background: "#f8fafc", borderRadius: 8, border: "1px solid #e8edf5" }}>{s.talkTrack}</div>
                        </div>
                      )}
                      {s.questions && s.questions.length > 0 && (
                        <div>
                          <div style={{ fontSize: 10, fontWeight: 700, color: colors.faint, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 4 }}>Discovery Questions</div>
                          <div style={{ display: "grid", gap: 4 }}>
                            {s.questions.map((q, qi) => (
                              <div key={qi} style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 12, color: "#3d5268", lineHeight: 1.45 }}>
                                <span style={{ fontWeight: 800, color: colors.primary, flexShrink: 0 }}>{qi + 1}.</span>
                                <span>{q}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* All recommendations as checklist */}
          {recommendations.length > 0 && (
            <div style={{ padding: "14px 16px", borderRadius: 12, background: "#f0fdf4", border: "1px solid #bbf7d0" }}>
              <SectionHeader icon={ListChecks} label="Meeting Checklist" color="#15803d" />
              <div style={{ display: "grid", gap: 6 }}>
                {recommendations.map((r, i) => (
                  <div key={i} style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 12, color: "#1e4032", lineHeight: 1.45 }}>
                    <span style={{ fontWeight: 800, color: "#15803d", flexShrink: 0 }}>✓</span>
                    <span>{r}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Competitive landscape */}
          {competitive.length > 0 && (
            <div style={{ padding: "14px 16px", borderRadius: 12, background: "#fff5f5", border: "1px solid #fecaca" }}>
              <SectionHeader icon={Swords} label="Competitive Landscape" color={colors.red} />
              <div style={{ display: "grid", gap: 8 }}>
                {competitive.map((c, i) => {
                  const name = c.name ?? c.competitor ?? "Unknown";
                  const bc = battlecards.find((b) => b.competitor?.toLowerCase() === name.toLowerCase());
                  return (
                    <div key={i} style={{ paddingLeft: 10, borderLeft: "3px solid #fca5a5" }}>
                      <div style={{ fontSize: 12, fontWeight: 700, color: colors.red, marginBottom: 2 }}>{name}</div>
                      {c.summary && <div style={{ fontSize: 12, color: "#7f1d1d", lineHeight: 1.45, marginBottom: bc?.win_reasons?.length ? 4 : 0 }}>{c.summary}</div>}
                      {bc?.win_reasons && bc.win_reasons.length > 0 && (
                        <div style={{ fontSize: 11, color: colors.green, fontWeight: 600 }}>
                          Win reason: {bc.win_reasons[0]}
                        </div>
                      )}
                      {bc?.objection_handling && (
                        <div style={{ fontSize: 11, color: colors.sub, marginTop: 2 }}>
                          Handle: {bc.objection_handling}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Intent signals */}
          {(hiringRoles.length > 0 || intentSignals.funding || intentSignals.growth) && (
            <div style={{ padding: "14px 16px", borderRadius: 12, background: colors.amberSoft, border: "1px solid #ffe3b3" }}>
              <SectionHeader icon={AlertCircle} label="Intent Signals" color={colors.amber} />
              <div style={{ display: "grid", gap: 8 }}>
                {hiringRoles.length > 0 && (
                  <div style={{ paddingLeft: 10, borderLeft: "3px solid #fcd34d" }}>
                    <div style={{ fontSize: 12, fontWeight: 700, color: "#92400e", marginBottom: 3 }}>Active Hiring ({hiringRoles.length} roles)</div>
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                      {hiringRoles.slice(0, 6).map((r: string, i: number) => (
                        <span key={i} style={{ padding: "2px 8px", borderRadius: 999, background: "#fef3c7", color: "#92400e", border: "1px solid #fde68a", fontSize: 11 }}>{r}</span>
                      ))}
                      {hiringRoles.length > 6 && <span style={{ fontSize: 11, color: colors.amber }}>+{hiringRoles.length - 6} more</span>}
                    </div>
                    <div style={{ fontSize: 11, color: "#7c6a3a", marginTop: 4 }}>They're feeling the pain Beacon solves — use this as a buying signal opener.</div>
                  </div>
                )}
                {intentSignals.funding && (
                  <div style={{ paddingLeft: 10, borderLeft: "3px solid #fcd34d", fontSize: 12, color: "#78350f" }}>
                    <span style={{ fontWeight: 700 }}>Funding: </span>{intentSignals.funding}
                  </div>
                )}
                {intentSignals.growth && (
                  <div style={{ paddingLeft: 10, borderLeft: "3px solid #fcd34d", fontSize: 12, color: "#78350f" }}>
                    <span style={{ fontWeight: 700 }}>Growth signal: </span>{intentSignals.growth}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Recent news */}
          {newsItems.length > 0 && (
            <div style={{ padding: "14px 16px", borderRadius: 12, background: "#f3f8ff", border: "1px solid #d5e5ff" }}>
              <SectionHeader icon={MessageSquare} label="Recent News — Open With This" color="#24567e" />
              <div style={{ display: "grid", gap: 6 }}>
                {newsItems.slice(0, 4).map((n, i) => (
                  <div key={i} style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                    <span style={{ color: "#93c5fd", fontWeight: 800, flexShrink: 0, fontSize: 12 }}>→</span>
                    <div>
                      {n.url ? (
                        <a href={n.url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12, color: colors.primary, fontWeight: 600, textDecoration: "none" }}>
                          {n.title ?? n.url}
                        </a>
                      ) : (
                        <span style={{ fontSize: 12, color: "#1e3a5f", fontWeight: 600 }}>{n.title}</span>
                      )}
                      {n.published && <span style={{ fontSize: 11, color: colors.faint, marginLeft: 6 }}>{n.published}</span>}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Company snapshot + tech stack */}
          {(techStack.length > 0 || pricingModel || hunterCo.employees || companySnapshot.icp_score != null) && (
            <div style={{ padding: "14px 16px", borderRadius: 12, background: "#f8fafc", border: "1px solid #e8edf5" }}>
              <SectionHeader icon={Building2} label="Company Profile" color={colors.sub} />
              <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                {companySnapshot.icp_score != null && (
                  <div>
                    <div style={{ fontSize: 10, color: colors.faint, fontWeight: 700, textTransform: "uppercase", marginBottom: 2 }}>ICP Score</div>
                    <div style={{ fontSize: 18, fontWeight: 800, color: companySnapshot.icp_score >= 70 ? colors.green : colors.amber }}>{companySnapshot.icp_score}</div>
                  </div>
                )}
                {(hunterCo.employees || companySnapshot.employee_count) && (
                  <div>
                    <div style={{ fontSize: 10, color: colors.faint, fontWeight: 700, textTransform: "uppercase", marginBottom: 2 }}>Employees</div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: colors.text }}>{hunterCo.employees ?? companySnapshot.employee_count}</div>
                  </div>
                )}
                {(hunterCo.industry || companySnapshot.industry) && (
                  <div>
                    <div style={{ fontSize: 10, color: colors.faint, fontWeight: 700, textTransform: "uppercase", marginBottom: 2 }}>Industry</div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: colors.text }}>{hunterCo.industry ?? companySnapshot.industry}</div>
                  </div>
                )}
                {pricingModel && (
                  <div>
                    <div style={{ fontSize: 10, color: colors.faint, fontWeight: 700, textTransform: "uppercase", marginBottom: 2 }}>Pricing Model</div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: colors.text }}>{pricingModel}</div>
                  </div>
                )}
              </div>
              {techStack.length > 0 && (
                <div style={{ marginTop: 10 }}>
                  <div style={{ fontSize: 10, color: colors.faint, fontWeight: 700, textTransform: "uppercase", marginBottom: 4 }}>Tech Stack</div>
                  <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
                    {techStack.map((t: string, i: number) => (
                      <span key={i} style={{ padding: "2px 8px", borderRadius: 999, background: "#eef2ff", color: "#3730a3", border: "1px solid #c7d2fe", fontSize: 11, fontWeight: 600 }}>{t}</span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── Action buttons ───────────────────────────────────────────────── */}
      <div style={{ padding: "12px 20px", borderTop: `1px solid ${colors.border}`, display: "flex", gap: 8, flexWrap: "wrap", background: expanded ? "#fafbfd" : "#fff" }}>
        <button
          type="button"
          disabled={isRunning || !hasCompanyLink}
          onClick={() => onRunIntel(meeting.id)}
          title={!hasCompanyLink ? "Link or add an account before generating meeting intel." : undefined}
          style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "7px 12px", borderRadius: 10, border: `1px solid ${colors.border}`, background: isRunning || !hasCompanyLink ? "#f5f8fe" : "#fff", color: isRunning || !hasCompanyLink ? colors.faint : colors.primary, fontSize: 12, fontWeight: 700, cursor: isRunning ? "wait" : !hasCompanyLink ? "not-allowed" : "pointer" }}
        >
          {isRunning ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
          {isRunning ? "Generating..." : hasResearch ? "Regenerate intel" : "Run intel now"}
        </button>

        {!hasCompanyLink && (
          <Link
            to={createAccountHref}
            style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "7px 12px", borderRadius: 10, border: "1px solid #ffd3be", background: "#fff8f1", color: "#7a3f1f", fontSize: 12, fontWeight: 700, textDecoration: "none" }}
          >
            <Building2 size={13} />
            Add account
          </Link>
        )}

        <Link
          to={`/meetings/${meeting.id}`}
          style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "7px 12px", borderRadius: 10, border: `1px solid ${colors.border}`, background: "#fff", color: colors.sub, fontSize: 12, fontWeight: 700, textDecoration: "none" }}
        >
          <ExternalLink size={13} />
          Open meeting
        </Link>

        {/* Show status-update actions when the meeting is past-but-unreviewed.
            Reps can't rely on tl;dv firing every time, so we give them a manual
            way to close out "overdue" meetings and clear the red badge. */}
        {(urgency === "overdue" || urgency === "in_progress") && (
          <>
            <button
              type="button"
              disabled={updatingStatus === meeting.id}
              onClick={() => onUpdateStatus(meeting.id, "completed")}
              style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "7px 12px", borderRadius: 10, border: "1px solid #c7e8d3", background: "#ecf8f0", color: "#15803d", fontSize: 12, fontWeight: 700, cursor: updatingStatus === meeting.id ? "wait" : "pointer" }}
            >
              <CheckCircle2 size={13} />
              Mark as done
            </button>
            <button
              type="button"
              disabled={updatingStatus === meeting.id}
              onClick={() => onUpdateStatus(meeting.id, "cancelled")}
              style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "7px 12px", borderRadius: 10, border: `1px solid ${colors.border}`, background: "#fff", color: colors.faint, fontSize: 12, fontWeight: 700, cursor: updatingStatus === meeting.id ? "wait" : "pointer" }}
            >
              Mark as cancelled
            </button>
          </>
        )}
      </div>
    </div>
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
        }),
        meetingsApi.listPaginated({ skip: 0, limit: 1, assigneeId: assigneeFilter, internalScope: showInternal ? "only" : "exclude" }),
        meetingsApi.listPaginated({ skip: 0, limit: 1, status: ["scheduled"], temporalStatus: ["upcoming"], assigneeId: assigneeFilter, internalScope: showInternal ? "only" : "exclude" }),
        meetingsApi.listPaginated({ skip: 0, limit: 1, status: ["scheduled"], temporalStatus: ["upcoming"], hasIntel: true, assigneeId: assigneeFilter, internalScope: showInternal ? "only" : "exclude" }),
        meetingsApi.listPaginated({ skip: 0, limit: 1, status: ["scheduled"], temporalStatus: ["upcoming"], hasIntel: false, assigneeId: assigneeFilter, internalScope: showInternal ? "only" : "exclude" }),
      ]);
      const ms = pageResp.items;

      // Empty-set fallback: if default "my upcoming" returns zero, broaden
      // to all reps once. Keeps the page useful for reps without owned
      // meetings (e.g. fresh users) without fighting the filter system.
      if (
        !autoFallbackApplied
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
    authApi.listAllUsers().then(setUsers).catch(() => setUsers([]));
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
                  background: imminent ? "#fff3ec" : today ? "#eef4ff" : "#f6f8ff",
                  color: colors.text,
                  textDecoration: "none",
                  minWidth: 280,
                  maxWidth: 480,
                }}
                title="Jump to the next upcoming meeting"
              >
                <div style={{ width: 38, height: 38, borderRadius: 10, background: "#fff", border: "1px solid #e3ebf4", display: "grid", placeItems: "center", color: imminent ? "#b85024" : "#3555c4" }}>
                  <CalendarDays size={18} />
                </div>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: "0.08em", textTransform: "uppercase", color: imminent ? "#b85024" : "#3555c4" }}>
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
      <section className="crm-panel" style={{ position: "relative", zIndex: 20, overflow: "visible", padding: 16, display: "grid", gap: 10 }}>
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
        <div className="crm-panel" style={{ padding: 32, display: "flex", alignItems: "center", gap: 10, color: colors.faint }}>
          <Loader2 size={18} className="animate-spin" />
          <span style={{ fontSize: 14 }}>Loading meetings...</span>
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
            const assignee = m.deal_id ? dealAssigneeMap.get(m.deal_id) : undefined;
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
                assigneeName={isAdmin && assignee ? assignee.name : undefined}
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

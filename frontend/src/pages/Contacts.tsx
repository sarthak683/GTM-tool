import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { accountSourcingApi, activitiesApi, angelMappingApi, authApi, companiesApi, contactsApi, dealsApi, outreachApi, settingsApi } from "../lib/api";
import type { PreCallBrief, SequenceLifecycle, LifecycleSummary, LifecycleStep, LifecycleStepState } from "../lib/api";
import type { Activity, Contact, AngelInvestor, AngelMapping, Company, RolePermissionsSettings, User } from "../types";
import { useAuth } from "../lib/AuthContext";
import { useToast } from "../lib/ToastContext";
import {
  Search, Users, CheckCircle2, XCircle, Sparkles, Trash2, AlertCircle, Loader2,
  Network, ChevronDown, ChevronRight, ExternalLink, Star, Plus, Link2,
  Building2, Target, Settings2, Phone, Upload, Download, MoreHorizontal,
  Mail, Clock, PhoneCall, Globe, X, AlertTriangle, ArrowLeftRight, EyeOff, GripVertical,
} from "lucide-react";
import { avatarColor, formatDomain, getInitials, gmailComposeUrl } from "../lib/utils";
import {
  getProspectTrackingScore,
  getProspectTrackingTone,
} from "../lib/prospectTracking";
import {
  CALL_DISPOSITION_OPTIONS,
  LINKEDIN_STATUS_OPTIONS,
  deriveSequenceStatusFromCallDisposition,
  deriveSequenceStatusFromLinkedinStatus,
  formatCallDisposition,
  getNextAction,
} from "../lib/prospectWorkflow";
import OutreachDrawer from "../components/outreach/OutreachDrawer";
import SequenceSettingsModal from "../components/outreach/SequenceSettingsModal";
import AssignDropdown from "../components/AssignDropdown";
import MultiSelectFilter from "../components/filters/MultiSelectFilter";
import TaskCenterModal from "../components/tasks/TaskCenterModal";
import AddProspectModal from "./contacts/AddProspectModal";
import SearchableCompanySelect from "../components/SearchableCompanySelect";
import { ANGEL_SURFACE, ANGEL_TEXT, PERSONA_LABEL, PERSONA_STYLE, STRENGTH_LABEL, STRENGTH_STYLE } from "./contacts/constants";
import { filterAngelMappings, getMissingCompanyKey, groupAngelMappingsByCompany } from "./contacts/utils";
import type { ProspectImportSummary, ProspectingTab } from "./contacts/types";

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

const EMAIL_FILTER_OPTIONS = [
  { value: "has_email", label: "Has email" },
  { value: "missing_email", label: "Missing email" },
  { value: "verified", label: "Verified" },
  { value: "unverified", label: "Unverified" },
];

const PROSPECT_PROGRESS_STAGES = [
  { key: "ready", label: "Ready" },
  { key: "email", label: "Email" },
  { key: "call", label: "Call" },
  { key: "linkedin", label: "LinkedIn" },
  { key: "reply", label: "Reply" },
  { key: "meeting", label: "Meeting" },
] as const;

type ProspectProgressStep = {
  key: string;
  label: string;
  state: "done" | "current" | "pending";
  detail: string;
};

const CHANNEL_LABEL: Record<string, string> = {
  email: "Email",
  call: "Call",
  linkedin: "LinkedIn",
  connector_request: "Connect",
  connector_follow_up: "Follow-up",
};

function getSequencePlanSteps(contact: Contact): ProspectProgressStep[] | null {
  // Only for pre-launch contacts (no campaign ID = not yet pushed to Instantly)
  if (contact.instantly_campaign_id) return null;
  if (!["ready", "research_needed", null, undefined, ""].includes(contact.sequence_status ?? "")) return null;

  const ed = contact.enrichment_data as Record<string, unknown> | null | undefined;
  const plan = ed?.sequence_plan as Record<string, unknown> | null | undefined;
  const rawSteps = plan?.steps;
  if (!Array.isArray(rawSteps) || rawSteps.length === 0) return null;

  const steps = rawSteps as Array<{ day_offset?: number; channel?: string; objective?: string }>;
  return steps.slice(0, 6).map((step, i) => {
    const ch = CHANNEL_LABEL[step.channel ?? ""] ?? (step.channel ?? "Touch");
    const day = step.day_offset ?? i;
    const label = `${ch} D${day}`;
    return {
      key: `step-${i}`,
      label,
      state: "pending" as const,
      detail: step.objective ? step.objective.slice(0, 80) : `${ch} on day ${day}`,
    };
  });
}

function getProspectProgressSteps(contact: Contact): ProspectProgressStep[] {
  // Pre-launch: show planned sequence step timeline if available, prefixed
  // with a Ready anchor so the track reads "Ready - Email D0 - Email D3 - ..."
  const plannedSteps = getSequencePlanSteps(contact);
  if (plannedSteps) {
    return [
      {
        key: "ready",
        label: "Ready",
        state: "current" as const,
        detail: contact.tracking_stage || "Ready for first touch",
      },
      ...plannedSteps.map((step) => ({ ...step, state: "pending" as const })),
    ];
  }

  // Live tracking: existing logic for initiated/active sequences
  const sequence = contact.sequence_status || "";
  const callStatus = contact.call_status || "";
  const callDisposition = contact.call_disposition || "";
  const linkedin = contact.linkedin_status || "";
  const emailOpened = (contact.email_open_count ?? 0) > 0;
  const emailSent = ["queued_instantly", "sent", "replied", "meeting_booked"].includes(sequence) || emailOpened;
  const callTouched = Boolean(callStatus && callStatus !== "none");
  const linkedinTouched = Boolean(linkedin && linkedin !== "none");
  const replied = sequence === "replied" || sequence === "meeting_booked" || linkedin === "replied" || ["interested", "working", "callback"].includes(callDisposition);
  const meetingBooked = sequence === "meeting_booked";

  const currentKey =
    meetingBooked ? "meeting" :
    replied ? "reply" :
    linkedinTouched ? "linkedin" :
    callTouched ? "call" :
    emailSent ? "email" :
    "ready";

  const reached = new Set<string>(["ready"]);
  if (emailSent) reached.add("email");
  if (callTouched) reached.add("call");
  if (linkedinTouched) reached.add("linkedin");
  if (replied) reached.add("reply");
  if (meetingBooked) reached.add("meeting");

  const detailByKey: Record<string, string> = {
    ready: contact.tracking_stage || "Ready for first touch",
    email: emailOpened
      ? `Opened ${contact.email_open_count} time${(contact.email_open_count ?? 0) === 1 ? "" : "s"}`
      : emailSent
        ? "Email touch sent"
        : "No email touch yet",
    call: callDisposition
      ? callDisposition.replace(/_/g, " ")
      : callTouched
        ? callStatus
        : "No call logged",
    linkedin: linkedinTouched ? linkedin : "No LinkedIn motion",
    reply: replied ? (contact.tracking_summary || "Engagement detected") : "Waiting for response",
    meeting: meetingBooked ? "Meeting booked" : "No meeting yet",
  };

  return PROSPECT_PROGRESS_STAGES.map((stage) => ({
    key: stage.key,
    label: stage.label,
    state: stage.key === currentKey ? "current" : reached.has(stage.key) ? "done" : "pending",
    detail: detailByKey[stage.key],
  }));
}

function parseSearchParamList(value: string | null): string[] {
  if (!value) return [];
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

const CONTACT_TABLE_COLUMNS: Array<{ key: string; label: string; required?: boolean }> = [
  { key: "name", label: "Name", required: true },
  { key: "company", label: "Company", required: true },
  { key: "title", label: "Title" },
  { key: "email", label: "Email", required: true },
  { key: "progress", label: "Progress", required: true },
  { key: "timezone", label: "Timezone" },
  { key: "next_action", label: "Next Action" },
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

function normalizeContactTableColumns(raw: string | null): ContactTableColumnKey[] {
  if (!raw) return DEFAULT_CONTACT_TABLE_COLUMNS;
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return DEFAULT_CONTACT_TABLE_COLUMNS;
    const allowed = new Set(CONTACT_TABLE_COLUMNS.map((column) => column.key));
    const next = parsed.filter((value): value is ContactTableColumnKey => typeof value === "string" && allowed.has(value as ContactTableColumnKey));
    for (const required of CONTACT_TABLE_COLUMNS.filter((column) => column.required).map((column) => column.key)) {
      if (!next.includes(required)) next.push(required);
    }
    return next.length ? next : DEFAULT_CONTACT_TABLE_COLUMNS;
  } catch {
    return DEFAULT_CONTACT_TABLE_COLUMNS;
  }
}

export default function Contacts() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const { isAdmin, user } = useAuth();
  const toast = useToast();
  const [tab, setTab] = useState<ProspectingTab>("contacts");
  const pageSize = 50;

  // ── Contacts state — initialised from URL so filters survive navigation ──
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const [personaFilter, setPersonaFilter] = useState<string[]>([]);
  const [sequenceFilter, setSequenceFilter] = useState<string[]>(() => parseSearchParamList(searchParams.get("seq")));
  const [callDispositionFilter, setCallDispositionFilter] = useState<string[]>(() => parseSearchParamList(searchParams.get("call")));
  const [emailFilter, setEmailFilter] = useState<string[]>([]);
  const [ownerScope, setOwnerScope] = useState<"all" | "mine">(() => (searchParams.get("owner") === "mine" ? "mine" : "all"));
  const [aeFilter, setAeFilter] = useState<string[]>(() => parseSearchParamList(searchParams.get("ae")));
  const [sdrFilter, setSdrFilter] = useState<string[]>(() => parseSearchParamList(searchParams.get("sdr")));
  // Owner filter — multi-select that matches AE OR SDR ownership for any
  // selected user. Different from ownerScope (binary "mine vs all") and from
  // aeFilter/sdrFilter (role-specific). Sent to backend via owner_id +
  // scope_any_match=true so a single user_id matches contacts they own as
  // either AE or SDR.
  const [ownerFilter, setOwnerFilter] = useState<string[]>(() => parseSearchParamList(searchParams.get("own")));
  // Timezone filter — values are short labels (IST, PST, etc.). When sent to
  // the backend they're expanded to include matching IANA names from
  // TIMEZONE_LABELS so a contact stored as "Asia/Kolkata" matches "IST".
  const [timezoneFilter, setTimezoneFilter] = useState<string[]>(() => parseSearchParamList(searchParams.get("tz")));
  // Company filter — optional narrowing to a single company's prospects.
  // Backend's contacts list already accepts `company_id`; this just wires a
  // dropdown to it. Value is a single company UUID (or "" for all).
  const [companyFilter, setCompanyFilter] = useState<string>(() => searchParams.get("co") ?? "");
  const [companyOptions, setCompanyOptions] = useState<Company[]>([]);
  const [teamUsers, setTeamUsers] = useState<User[]>([]);
  const [debouncedSearch, setDebouncedSearch] = useState(() => searchParams.get("q") ?? "");
  const [page, setPage] = useState(() => parseInt(searchParams.get("pg") ?? "1", 10) || 1);
  const [contactsTotal, setContactsTotal] = useState(0);
  const [contactsPages, setContactsPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [selectedContact, setSelectedContact] = useState<Contact | null>(null);
  const [resetting, setResetting] = useState(false);
  const [showSequenceSettings, setShowSequenceSettings] = useState(false);
  const [openActionsId, setOpenActionsId] = useState<string | null>(null);
  const [taskContact, setTaskContact] = useState<Contact | null>(null);
  const [callContact, setCallContact] = useState<Contact | null>(null);
  const [callDisposition, setCallDisposition] = useState("");
  const [callNotes, setCallNotes] = useState("");
  const [callStatus, setCallStatus] = useState("attempted");
  const [savingDisposition, setSavingDisposition] = useState(false);
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
  const [uploadingProspects, setUploadingProspects] = useState(false);
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
    const column = CONTACT_TABLE_COLUMNS.find((item) => item.key === key);
    if (column?.required) return;
    setTableColumns((current) => (
      current.includes(key)
        ? current.filter((item) => item !== key)
        : [...current, key]
    ));
  };

  const saveTimezone = async (contact: Contact, nextTimezone: string) => {
    const normalized = nextTimezone.trim();
    setSavingTimezoneId(contact.id);
    try {
      const updated = await contactsApi.update(contact.id, { timezone: normalized || undefined });
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
  const [aircallEnabled, setAircallEnabled] = useState<boolean>(() => localStorage.getItem("crm.aircall.enabled") !== "false");
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
    // Local-midnight ISO so the count matches what the rep "started today" —
    // not UTC midnight, which would give Indian reps a stale count after IST
    // 5:30am UTC rollover.
    const localMidnight = new Date();
    localMidnight.setHours(0, 0, 0, 0);
    try {
      const total = await activitiesApi.myCountSince(localMidnight.toISOString(), "call");
      setMyCallsTodayCount(total);
    } catch {
      setMyCallsTodayCount(0);
    }
  };

  const loadContacts = () => {
    setLoading(true);
    contactsApi.searchPaginated({
      skip: (page - 1) * pageSize,
      limit: pageSize,
      q: debouncedSearch || undefined,
      companyId: companyFilter || undefined,
      persona: personaFilter.length ? personaFilter : undefined,
      sequenceStatus: sequenceFilter.length ? sequenceFilter : undefined,
      callDisposition: callDispositionFilter.length ? callDispositionFilter : undefined,
      aeId: aeFilter.length ? aeFilter : undefined,
      sdrId: sdrFilter.length ? sdrFilter : undefined,
      // Owner filter: any selected user matches contacts they own as AE OR SDR.
      // ownerScope === "mine" still wins when set; otherwise the multi-select drives.
      ownerId: ownerScope === "mine"
        ? user?.id
        : (ownerFilter.length ? ownerFilter : undefined),
      timezone: timezoneFilter.length ? expandTimezoneFilter(timezoneFilter) : undefined,
      prospectOnly: true,
    }).then((result) => {
      setContacts(result.items);
      setContactsTotal(result.total);
      setContactsPages(result.pages);
    }).finally(() => setLoading(false));
  };

  const downloadProspectTemplate = () => {
    const template = [
      ["Company Name", "Domain", "First Name", "Last Name", "Title", "Email", "LinkedIn URL", "Phone"],
      ["BlackLine", "blackline.com", "Victoria", "Subbotina", "Director of Professional Services", "victoria.subbotina@blackline.com", "https://linkedin.com/in/victoriasubbotina", "+15135330040"],
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
    try {
      const result = await contactsApi.importCsv(file);
      setImportSummary(result);
      setPage(1);
      loadContacts();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Failed to import prospects");
    } finally {
      setUploadingProspects(false);
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

  useEffect(() => {
    loadAngels();
  }, []);

  useEffect(() => {
    settingsApi.getRolePermissions().then(setRolePermissions).catch(() => setRolePermissions(null));
  }, []);

  useEffect(() => {
    authApi
      .listAllUsers()
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
  useEffect(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      search.trim() ? next.set("q", search.trim()) : next.delete("q");
      ownerScope === "mine" ? next.set("owner", "mine") : next.delete("owner");
      sequenceFilter.length ? next.set("seq", sequenceFilter.join(",")) : next.delete("seq");
      callDispositionFilter.length ? next.set("call", callDispositionFilter.join(",")) : next.delete("call");
      aeFilter.length ? next.set("ae", aeFilter.join(",")) : next.delete("ae");
      sdrFilter.length ? next.set("sdr", sdrFilter.join(",")) : next.delete("sdr");
      ownerFilter.length ? next.set("own", ownerFilter.join(",")) : next.delete("own");
      timezoneFilter.length ? next.set("tz", timezoneFilter.join(",")) : next.delete("tz");
      companyFilter ? next.set("co", companyFilter) : next.delete("co");
      page > 1 ? next.set("pg", String(page)) : next.delete("pg");
      return next;
    }, { replace: true });
  }, [aeFilter, callDispositionFilter, companyFilter, ownerFilter, ownerScope, page, sdrFilter, search, sequenceFilter, timezoneFilter, setSearchParams]);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setDebouncedSearch(search.trim());
    }, 250);
    return () => window.clearTimeout(handle);
  }, [search]);

  useEffect(() => {
    setPage(1);
  }, [aeFilter, callDispositionFilter, companyFilter, debouncedSearch, ownerFilter, ownerScope, sdrFilter, sequenceFilter, timezoneFilter]);

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
  }, [aeFilter, callDispositionFilter, companyFilter, debouncedSearch, ownerFilter, ownerScope, page, sdrFilter, sequenceFilter, timezoneFilter, tab, user?.id]);

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
      .getSequence(linkedinContact.id)
      .then((seq) => {
        if (cancelled) return;
        setLinkedinSuggestion((seq.linkedin_message || "").trim() || null);
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

  const openCallSidebar = async (contact: Contact) => {
    setCallContact(contact);
    setCallStatus("attempted");
    setCallDisposition("");
    setCallNotes("");
    if (contact.phone) {
      if (window.__aircallDial) {
        window.__aircallDial(contact.phone, `${contact.first_name} ${contact.last_name}`.trim());
      }
    }
  };

  const handleCallDispositionChange = (value: string) => {
    setCallDisposition(value);
    const matched = CALL_DISPOSITION_OPTIONS.find((option) => option.value === value);
    if (matched?.suggestedCallStatus) {
      setCallStatus(matched.suggestedCallStatus);
    }
  };

  const saveCallDisposition = async () => {
    if (!callContact || !callDisposition) return;
    setSavingDisposition(true);
    try {
      const derivedSeqStatus = deriveSequenceStatusFromCallDisposition(
        callDisposition,
        callContact.sequence_status,
      );
      const nowIso = new Date().toISOString();
      await contactsApi.update(callContact.id, {
        call_status: callStatus,
        call_disposition: callDisposition,
        call_notes: callNotes || undefined,
        call_last_at: nowIso,
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
        } as Partial<Activity>);
      } catch {
        // Non-fatal — contact state already saved above; warn the rep so they
        // know to check the timeline manually.
        toast.error("Call logged but timeline write failed — check activity feed.", "Partial save");
      }

      toast.success(`Call logged for ${callContact.first_name}.`, "Call logged");
      setCallContact(null);
      loadContacts();
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
        ...(linkedinNotes ? { call_notes: linkedinNotes } : {}),
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
        replied: "Replied on LinkedIn",
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

      toast.success(`LinkedIn touch logged for ${linkedinContact.first_name}.`, "LinkedIn logged");
      setLinkedinContact(null);
      setLinkedinNotes("");
      loadContacts();
    } catch {
      toast.error("Failed to log LinkedIn touch.", "Error");
    } finally {
      setSavingLinkedin(false);
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

  const callsLoggedCount = contacts.filter((c) => c.call_status && c.call_status !== "none").length;
  const emailsOpenedCount = contacts.filter((c) => (c.email_open_count ?? 0) > 0).length;
  const linkedinActiveCount = contacts.filter((c) => c.linkedin_status && c.linkedin_status !== "none").length;
  const meetingsBookedCount = contacts.filter((c) => c.sequence_status === "meeting_booked").length;
  const hasNoSyncedEngagement =
    tab === "contacts" &&
    contactsTotal > 0 &&
    callsLoggedCount === 0 &&
    emailsOpenedCount === 0 &&
    linkedinActiveCount === 0 &&
    meetingsBookedCount === 0;

  return (
    <>
      <div className="crm-page contacts-page space-y-6">
        {/* ── Tab switcher + action bar ──────────────────────────────── */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>

          {/* Row 1 — tab cards */}
          {/* SDR Activity Cards — replace single large contacts count */}
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            {/* Prospects card */}
            <div style={{
              flex: "1 1 160px", display: "flex", alignItems: "center", gap: 12,
              padding: "14px 18px", borderRadius: 14,
              border: "1.5px solid #b8d0f0",
              background: "linear-gradient(135deg, #f0f6ff 0%, #e8f0fb 100%)",
              boxShadow: "0 4px 16px rgba(23, 80, 137, 0.08)",
            }}>
              <div style={{ width: 36, height: 36, borderRadius: 10, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", background: "#175089", color: "#fff" }}>
                <Users size={16} />
              </div>
              <div>
                <div style={{ fontSize: 22, fontWeight: 800, color: "#0f2744", lineHeight: 1 }}>{contactsTotal}</div>
                <div style={{ fontSize: 11, color: "#7a96b0", marginTop: 2, fontWeight: 600 }}>Prospects</div>
              </div>
            </div>
            {/* Calls made */}
            <div style={{
              flex: "1 1 160px", display: "flex", alignItems: "center", gap: 12,
              padding: "14px 18px", borderRadius: 14,
              border: "1.5px solid #bfdbfe",
              background: "linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%)",
            }}>
              <div style={{ width: 36, height: 36, borderRadius: 10, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", background: "#2563eb", color: "#fff" }}>
                <PhoneCall size={16} />
              </div>
              <div>
                <div style={{ fontSize: 22, fontWeight: 800, color: "#1e3a8a", lineHeight: 1 }}>
                  {myCallsTodayCount}
                </div>
                <div style={{ fontSize: 11, color: "#3b82f6", marginTop: 2, fontWeight: 600 }}>My Calls Today</div>
              </div>
            </div>
            {/* Emails opened */}
            <div style={{
              flex: "1 1 160px", display: "flex", alignItems: "center", gap: 12,
              padding: "14px 18px", borderRadius: 14,
              border: "1.5px solid #bbf7d0",
              background: "linear-gradient(135deg, #f0fdf4 0%, #dcfce7 100%)",
            }}>
              <div style={{ width: 36, height: 36, borderRadius: 10, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", background: "#16a34a", color: "#fff" }}>
                <Mail size={16} />
              </div>
              <div>
                <div style={{ fontSize: 22, fontWeight: 800, color: "#14532d", lineHeight: 1 }}>
                  {emailsOpenedCount}
                </div>
                <div style={{ fontSize: 11, color: "#16a34a", marginTop: 2, fontWeight: 600 }}>Emails Opened</div>
              </div>
            </div>
            {/* LinkedIn connected */}
            <div style={{
              flex: "1 1 160px", display: "flex", alignItems: "center", gap: 12,
              padding: "14px 18px", borderRadius: 14,
              border: "1.5px solid #e9d5ff",
              background: "linear-gradient(135deg, #fdf4ff 0%, #f3e8ff 100%)",
            }}>
              <div style={{ width: 36, height: 36, borderRadius: 10, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", background: "#7c3aed", color: "#fff" }}>
                <Link2 size={16} />
              </div>
              <div>
                <div style={{ fontSize: 22, fontWeight: 800, color: "#4c1d95", lineHeight: 1 }}>
                  {linkedinActiveCount}
                </div>
                <div style={{ fontSize: 11, color: "#7c3aed", marginTop: 2, fontWeight: 600 }}>LinkedIn Active</div>
              </div>
            </div>
            {/* Meeting booked */}
            <div style={{
              flex: "1 1 160px", display: "flex", alignItems: "center", gap: 12,
              padding: "14px 18px", borderRadius: 14,
              border: "1.5px solid #fde68a",
              background: "linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%)",
            }}>
              <div style={{ width: 36, height: 36, borderRadius: 10, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", background: "#d97706", color: "#fff" }}>
                <Clock size={16} />
              </div>
              <div>
                <div style={{ fontSize: 22, fontWeight: 800, color: "#78350f", lineHeight: 1 }}>
                  {meetingsBookedCount}
                </div>
                <div style={{ fontSize: 11, color: "#d97706", marginTop: 2, fontWeight: 600 }}>Meetings Booked</div>
              </div>
            </div>
            {hasNoSyncedEngagement && (
              <div style={{ flexBasis: "100%", border: "1px solid #f5ddaa", background: "#fff8e8", color: "#6f5a2d", borderRadius: 12, padding: "10px 12px", fontSize: 12.5, lineHeight: 1.55 }}>
                Engagement metrics are waiting for synced or logged activity. {aircallEnabled ? "Calls, email opens, LinkedIn touches, and booked meetings will populate as reps log activity or integrations sync." : "AirCall is currently off, so call counts will stay empty until it is enabled or calls are logged manually."}
              </div>
            )}
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
                {/* Search — left aligned, grows */}
                <div style={{ position: "relative", flex: 1, minWidth: 180 }}>
                  <Search size={14} style={{ position: "absolute", left: 11, top: "50%", transform: "translateY(-50%)", color: "#94a8bc", pointerEvents: "none" }} />
                  <input
                    style={{
                      width: "100%", height: 38, borderRadius: 10,
                      border: "1px solid #e0eaf4", background: "#f7fbff",
                      paddingLeft: 34, paddingRight: 12,
                      fontSize: 13, color: "#1e3a52", outline: "none",
                    }}
                    placeholder="Search people, title, email…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </div>

                {/* Sequence Timing */}
                <button
                  type="button"
                  title="Sequence timing settings"
                  onClick={() => setShowSequenceSettings(true)}
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 6,
                    height: 38, padding: "0 14px", borderRadius: 10,
                    border: "1px solid #d0dcea", background: "#f7fbff",
                    color: "#2c4a63", fontSize: 13, fontWeight: 600,
                    cursor: "pointer", whiteSpace: "nowrap", flexShrink: 0,
                  }}
                >
                  <Settings2 size={14} />
                  Sequence Timing
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
                    border: "1px solid #b8d0f0", background: "#eef5ff",
                    color: "#175089", fontSize: 13, fontWeight: 700,
                    cursor: uploadingProspects || !canMigrateProspects ? "default" : "pointer", whiteSpace: "nowrap", flexShrink: 0,
                    opacity: uploadingProspects || !canMigrateProspects ? 0.7 : 1,
                  }}
                >
                  {uploadingProspects ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
                  Migrate Prospects
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
                  title={aircallEnabled ? "AirCall calling is enabled for this browser." : "AirCall is disconnected/off for this browser. Click to enable when configured."}
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
                  {aircallEnabled ? "AirCall: Connected" : "AirCall: Disconnected"}
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
            <div
              style={{
                background: "#fff8e8",
                border: "1px solid #f5ddaa",
                borderRadius: 14,
                padding: "12px 16px",
                display: "flex",
                alignItems: "start",
                justifyContent: "space-between",
                gap: 12,
                flexWrap: "wrap",
              }}
            >
              <div style={{ display: "grid", gap: 4 }}>
                <div style={{ color: "#8a5b00", fontSize: 12, fontWeight: 800, letterSpacing: 0.3, textTransform: "uppercase" }}>
                  Prospect sourcing update
                </div>
                <div style={{ color: "#6c5a2f", fontSize: 13, lineHeight: 1.6, maxWidth: 860 }}>
                  Beacon is temporarily not pulling contacts during company research. Use this to migrate prospects into Beacon and map them onto sourced companies. If a company is missing, Beacon will ask whether you want to add and enrich that account now.
                </div>
              </div>
            </div>

            {/* Filters */}
            {(() => {
              const hasFilters = !!(
                ownerScope === "mine" ||
                sequenceFilter.length ||
                callDispositionFilter.length ||
                aeFilter.length ||
                sdrFilter.length ||
                ownerFilter.length ||
                timezoneFilter.length ||
                companyFilter ||
                search
              );
              const teamUserOptions = teamUsers.map((u) => ({
                value: u.id,
                label: u.name || u.email,
              }));
              return (
                <div style={{
                  display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
                  background: "#fff", borderRadius: 14,
                  border: "1px solid #e8eef5",
                  padding: "10px 14px",
                  boxShadow: "0 2px 8px rgba(17,34,68,0.04)",
                  position: "sticky",
                  top: 16,
                  zIndex: 5,
                }}>
                  {/* Company filter — narrow the prospecting list to one
                      account. Populated from the full company list on page
                      mount; for >500 accounts the list is still scannable
                      because entries are alphabetical. */}
                  <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                    <span style={{ fontSize: 10, fontWeight: 700, color: "#4a6580", textTransform: "uppercase", letterSpacing: 0.4 }}>View</span>
                    <select
                      value={ownerScope}
                      onChange={(e) => setOwnerScope(e.target.value === "mine" ? "mine" : "all")}
                      style={{
                        height: 34,
                        padding: "0 28px 0 10px",
                        borderRadius: 9,
                        border: ownerScope === "mine" ? "1.5px solid #ffc9b4" : "1px solid #c8d9e8",
                        fontSize: 13,
                        color: "#0f2744",
                        background: ownerScope === "mine" ? "#fff3ec" : "#fff",
                        outline: "none",
                        minWidth: 140,
                        cursor: "pointer",
                      }}
                    >
                      <option value="all">All prospects</option>
                      <option value="mine">My prospects</option>
                    </select>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                    <span style={{ fontSize: 10, fontWeight: 700, color: "#4a6580", textTransform: "uppercase", letterSpacing: 0.4 }}>Company</span>
                    <div style={{ width: 240 }}>
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
                  <MultiSelectFilter
                    label="Sequence"
                    values={sequenceFilter}
                    onChange={setSequenceFilter}
                    options={SEQUENCE_FILTER_OPTIONS}
                    allLabel="All sequence states"
                    minWidth={170}
                  />
                  <MultiSelectFilter
                    label="Call disposition"
                    values={callDispositionFilter}
                    onChange={setCallDispositionFilter}
                    options={CALL_DISPOSITION_FILTER_OPTIONS}
                    allLabel="All call outcomes"
                    minWidth={190}
                  />
                  {/* Owner filters */}
                  {teamUsers.length > 0 && (
                    <>
                      <MultiSelectFilter
                        label="Owner"
                        values={ownerFilter}
                        onChange={setOwnerFilter}
                        options={teamUserOptions}
                        allLabel="Owner: All"
                        minWidth={170}
                      />
                      <MultiSelectFilter
                        label="AE"
                        values={aeFilter}
                        onChange={setAeFilter}
                        options={teamUserOptions}
                        allLabel="AE: All"
                        minWidth={160}
                      />
                      <MultiSelectFilter
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
                    label="Timezone"
                    values={timezoneFilter}
                    onChange={setTimezoneFilter}
                    options={TIMEZONE_OPTIONS.map((tz) => ({ value: tz, label: tz }))}
                    allLabel="All timezones"
                    minWidth={170}
                  />

                  {/* Divider */}
                  <div style={{ flex: 1 }} />

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
                        position: "absolute", top: "calc(100% + 8px)", right: 0, zIndex: 30,
                        width: 280, borderRadius: 14, border: "1px solid #dbe6f2", background: "#fff",
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
                              {!column.required && (
                                <button type="button" onClick={() => toggleTableColumn(column.key)} style={{ border: "1px solid #dce8f4", background: active ? "#fff3ec" : "#fff", color: active ? "#b85024" : "#546679", borderRadius: 8, padding: "4px 8px", fontSize: 11, fontWeight: 700, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4 }}>
                                  <EyeOff size={11} />
                                  {active ? "Hide" : "Show"}
                                </button>
                              )}
                              <button type="button" onClick={() => moveTableColumn(column.key, 1)} disabled={!active} title="Move right" style={{ border: "none", background: "transparent", cursor: active ? "pointer" : "default", color: active ? "#7a8ea4" : "#c5d1de", display: "inline-flex" }}>
                                <ArrowLeftRight size={13} />
                              </button>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>

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
                        setOwnerScope("all");
                        setSequenceFilter([]); setCallDispositionFilter([]);
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
                </div>
              );
            })()}

            {/* Contacts Table */}
            {loading ? (
              <div className="crm-panel p-14 text-center crm-muted">Loading contacts...</div>
            ) : contacts.length === 0 ? (
              <div className="crm-panel p-14 text-center text-[#6f8297]">
                <Users className="h-12 w-12 mx-auto mb-4 opacity-30" />
                No contacts match your search.
              </div>
            ) : (
              <div className="crm-panel overflow-hidden contacts-table-panel">
                <div className="overflow-x-auto">
                  <table className="crm-table" style={{ minWidth: 1080 }}>
                    <thead>
                      <tr>
                        {visibleColumns.map((column) => (
                          <th key={column.key} style={{ position: "sticky", top: 0, zIndex: 2, background: "#f7faff" }}>{column.label}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {contacts.map((c) => (
                        <tr key={c.id} className="cursor-pointer" onClick={() => navigate(`/contacts/${c.id}`)}>
                          {visibleColumns.map((column) => {
                            switch (column.key) {
                              case "name":
                                return (
                                  <td key={column.key}>
                                    <div className="flex items-center gap-3 min-w-0">
                                      <div className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-full text-[13px] font-extrabold ${avatarColor(c.first_name + c.last_name)}`}>
                                        {getInitials(`${c.first_name} ${c.last_name}`)}
                                      </div>
                                      <div className="min-w-0">
                                        <p className="font-bold text-[#25384d] truncate">{c.first_name} {c.last_name}</p>
                                        <p className="text-[13px] text-[#7a8ea4] mt-0.5">{c.seniority ?? "-"}</p>
                                      </div>
                                    </div>
                                  </td>
                                );
                              case "company":
                                return (
                                  <td key={column.key}>
                                    {c.company_name ? (
                                      <button
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          if (c.company_id) navigate(`/account-sourcing/${c.company_id}`);
                                        }}
                                        className="text-[#2b6cb0] font-semibold text-[13px] hover:underline"
                                      >
                                        {c.company_name}
                                      </button>
                                    ) : (
                                      <span className="text-[#96a7ba]">-</span>
                                    )}
                                  </td>
                                );
                              case "title":
                                return <td key={column.key}>{c.title ?? <span className="text-[#96a7ba]">-</span>}</td>;
                              case "email":
                                return (
                                  <td key={column.key}>
                                    <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
                                      {c.email
                                        ? <span style={{ fontSize: 13, color: "#1e3a52", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.email}</span>
                                        : <span className="text-[#96a7ba]">-</span>
                                      }
                                      <div style={{ color: "#7a8ea4", fontSize: 11.5 }}>
                                        {c.phone && c.linkedin_url
                                          ? "Phone and LinkedIn ready"
                                          : c.phone
                                            ? "Phone ready"
                                            : c.linkedin_url
                                              ? "LinkedIn ready"
                                              : "No direct channel saved"}
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
                              case "next_action": {
                                const action = getNextAction(c);
                                if (action.channel === "none" && action.priority === "low") {
                                  return <td key={column.key}><span style={{ color: "#c0cdd8", fontSize: 12 }}>—</span></td>;
                                }
                                const toneMap = {
                                  high: { bg: "#fff4ed", border: "#fed7aa", color: "#c2410c" },
                                  medium: { bg: "#f0f9ff", border: "#bae6fd", color: "#0369a1" },
                                  low: { bg: "#f8fafc", border: "#e2e8f0", color: "#64748b" },
                                };
                                const tone = toneMap[action.priority];
                                return (
                                  <td key={column.key}>
                                    <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, fontWeight: 700, color: tone.color, background: tone.bg, border: `1px solid ${tone.border}`, borderRadius: 8, padding: "3px 8px", whiteSpace: "nowrap", maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis" }}>
                                      {action.label}
                                    </span>
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
                                    <div style={{ position: "relative", display: "inline-flex", alignItems: "center", gap: 8 }}>
                                      <button type="button" disabled={!c.phone} onClick={(e) => { e.stopPropagation(); if (c.phone) openCallSidebar(c); }} style={{ height: 38, borderRadius: 10, border: "1px solid #c8daf0", background: c.phone ? "#eaf2ff" : "#f6f8fb", color: c.phone ? "#175089" : "#9aa8b7", padding: "0 10px", display: "inline-flex", alignItems: "center", gap: 6, cursor: c.phone ? "pointer" : "default", fontSize: 12.5, fontWeight: 700 }} title={c.phone ? c.phone : "No phone number"}>
                                        <Phone size={13} /> Call
                                      </button>
                                      <a href={c.email ? gmailComposeUrl(c.email) : undefined} target="_blank" rel="noopener noreferrer" onClick={(e) => { e.stopPropagation(); if (!c.email) e.preventDefault(); }} style={{ height: 38, borderRadius: 10, border: "1px solid #bfd8c7", background: c.email ? "#ecfdf3" : "#f6f8fb", color: c.email ? "#1f7a4d" : "#9aa8b7", padding: "0 10px", display: "inline-flex", alignItems: "center", gap: 6, cursor: c.email ? "pointer" : "default", fontSize: 12.5, fontWeight: 700, textDecoration: "none" }} title={c.email ? `Email ${c.email} in Gmail` : "No email saved"}>
                                        <Mail size={13} /> Email
                                      </a>
                                      <a href={c.linkedin_url || undefined} target="_blank" rel="noopener noreferrer" onClick={(e) => { e.stopPropagation(); if (!c.linkedin_url) e.preventDefault(); }} style={{ height: 38, borderRadius: 10, border: "1px solid #b8d4f0", background: c.linkedin_url ? "#e8f2ff" : "#f6f8fb", color: c.linkedin_url ? "#0a66c2" : "#9aa8b7", padding: "0 10px", display: "inline-flex", alignItems: "center", gap: 6, cursor: c.linkedin_url ? "pointer" : "default", fontSize: 12.5, fontWeight: 700, textDecoration: "none" }} title={c.linkedin_url ? "Open LinkedIn profile" : "No LinkedIn profile"}>
                                        <Link2 size={13} /> LinkedIn
                                      </a>
                                      <button type="button" onClick={(e) => { e.stopPropagation(); setLinkedinContact(c); setLinkedinStatus(c.linkedin_status && c.linkedin_status !== "none" ? c.linkedin_status : "sent"); setLinkedinNotes(""); }} style={{ height: 38, borderRadius: 10, border: "1px solid #ddd6fe", background: "#f5f3ff", color: "#6d28d9", padding: "0 10px", display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 12.5, fontWeight: 700 }} title="Log LinkedIn touch">
                                        <Link2 size={13} /> Log
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
                <div style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: 12,
                  padding: "12px 16px",
                  borderTop: "1px solid #e8eef5",
                  background: "#fbfdff",
                  flexWrap: "wrap",
                }}>
                  <span style={{ color: "#71839a", fontSize: 12, fontWeight: 600 }}>
                    Page {page} of {Math.max(contactsPages, 1)}
                  </span>
                  <div style={{ display: "inline-flex", gap: 8 }}>
                    <button
                      type="button"
                      onClick={() => setPage((current) => Math.max(1, current - 1))}
                      disabled={page <= 1}
                      style={{
                        height: 34,
                        padding: "0 12px",
                        borderRadius: 9,
                        border: "1px solid #dce8f4",
                        background: page <= 1 ? "#f7f9fc" : "#ffffff",
                        color: page <= 1 ? "#9eb0c3" : "#4a6580",
                        fontSize: 12,
                        fontWeight: 600,
                        cursor: page <= 1 ? "not-allowed" : "pointer",
                      }}
                    >
                      Previous
                    </button>
                    <button
                      type="button"
                      onClick={() => setPage((current) => Math.min(Math.max(contactsPages, 1), current + 1))}
                      disabled={page >= contactsPages}
                      style={{
                        height: 34,
                        padding: "0 12px",
                        borderRadius: 9,
                        border: "1px solid #dce8f4",
                        background: page >= contactsPages ? "#f7f9fc" : "#ffffff",
                        color: page >= contactsPages ? "#9eb0c3" : "#4a6580",
                        fontSize: 12,
                        fontWeight: 600,
                        cursor: page >= contactsPages ? "not-allowed" : "pointer",
                      }}
                    >
                      Next
                    </button>
                  </div>
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

      {/* Global sequence timing settings */}
      <SequenceSettingsModal
        open={showSequenceSettings}
        onClose={() => setShowSequenceSettings(false)}
      />

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
      />

      {/* ── Call Disposition Sidebar ─────────────────────────────────── */}
      {callContact && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 200,
          display: "flex", justifyContent: "flex-end",
        }}>
          {/* Dim backdrop — clicking it does NOT close (must fill disposition first) */}
          <div style={{ flex: 1, background: "rgba(10,20,40,0.35)" }} />

          {/* Panel */}
          <div style={{
            width: 400, maxWidth: "100vw",
            background: "#fff",
            borderLeft: "1px solid #d5e3ef",
            boxShadow: "-24px 0 48px rgba(14,38,66,0.16)",
            display: "flex", flexDirection: "column",
            overflowY: "auto",
          }}>
            {/* Header */}
            <div style={{ padding: "20px 22px 16px", borderBottom: "1px solid #e8eef5" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <div style={{ width: 36, height: 36, borderRadius: 10, background: "linear-gradient(135deg,#0f2744,#175089)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                    <PhoneCall size={16} color="#fff" />
                  </div>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 700, color: "#0f2744" }}>Call in progress</div>
                    <div style={{ fontSize: 12, color: "#7a96b0" }}>{callContact.first_name} {callContact.last_name}</div>
                  </div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontSize: 11, background: "#fef3c7", color: "#92400e", border: "1px solid #fde68a", borderRadius: 999, padding: "3px 10px", fontWeight: 700 }}>
                    Disposition required
                  </span>
                  <button
                    type="button"
                    onClick={() => setCallContact(null)}
                    aria-label="Close call sidebar"
                    style={{
                      width: 30,
                      height: 30,
                      borderRadius: 8,
                      border: "1px solid #d5e3ef",
                      background: "#fff",
                      color: "#546679",
                      display: "inline-flex",
                      alignItems: "center",
                      justifyContent: "center",
                      cursor: "pointer",
                    }}
                  >
                    <X size={14} />
                  </button>
                </div>
              </div>
            </div>

            {/* Pre-call intel — rich, data-driven panel fetched from
                /contacts/:id/precall-brief. Gives the rep everything they
                need before the prospect picks up: last email opened? recent
                signals? talking points? objection handles? in ~300ms. */}
            <PreCallIntelPanel
              contact={callContact}
              brief={precallBrief}
              loading={precallLoading}
            />

            {/* Disposition form */}
            <div style={{ padding: "16px 22px", flex: 1 }}>
              <div style={{ fontSize: 11, fontWeight: 800, color: "#546679", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 12 }}>Log this call</div>

              <div style={{ marginBottom: 12 }}>
                <label style={{ fontSize: 12, fontWeight: 600, color: "#2c4a63", display: "block", marginBottom: 6 }}>Call outcome</label>
                <select
                  value={callStatus}
                  onChange={(e) => setCallStatus(e.target.value)}
                  style={{ width: "100%", border: "1px solid #c8d9e8", borderRadius: 10, padding: "9px 12px", fontSize: 13, color: "#0f2744", background: "#fff", outline: "none" }}
                >
                  <option value="attempted">Attempted — no answer</option>
                  <option value="voicemail">Left voicemail</option>
                  <option value="connected">Connected — spoke with prospect</option>
                  <option value="callback">Requested callback</option>
                </select>
              </div>

              <div style={{ marginBottom: 12 }}>
                <label style={{ fontSize: 12, fontWeight: 600, color: "#2c4a63", display: "block", marginBottom: 6 }}>Disposition *</label>
                <select
                  value={callDisposition}
                  onChange={(e) => handleCallDispositionChange(e.target.value)}
                  style={{ width: "100%", border: `1px solid ${callDisposition ? "#c8d9e8" : "#f87171"}`, borderRadius: 10, padding: "9px 12px", fontSize: 13, color: callDisposition ? "#0f2744" : "#7a96b0", background: "#fff", outline: "none" }}
                >
                  <option value="">— Select disposition —</option>
                  {CALL_DISPOSITION_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
                {!callDisposition && (
                  <div style={{ fontSize: 11, color: "#ef4444", marginTop: 4 }}>Required before closing</div>
                )}
              </div>

              <div style={{ marginBottom: 16 }}>
                <label style={{ fontSize: 12, fontWeight: 600, color: "#2c4a63", display: "block", marginBottom: 6 }}>Notes</label>
                <textarea
                  value={callNotes}
                  onChange={(e) => setCallNotes(e.target.value)}
                  placeholder="What came up on the call? Any objections or signals…"
                  rows={4}
                  style={{ width: "100%", border: "1px solid #c8d9e8", borderRadius: 10, padding: "9px 12px", fontSize: 13, color: "#0f2744", background: "#fff", outline: "none", resize: "vertical", fontFamily: "inherit" }}
                />
              </div>

              <button
                onClick={() => void saveCallDisposition()}
                disabled={!callDisposition || savingDisposition}
                style={{
                  width: "100%", padding: "11px 0", borderRadius: 12, border: "none",
                  background: callDisposition ? "linear-gradient(135deg,#0f2744,#175089)" : "#e8eef5",
                  color: callDisposition ? "#fff" : "#9aafbe",
                  fontSize: 14, fontWeight: 700,
                  cursor: callDisposition ? "pointer" : "not-allowed",
                  display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
                  opacity: savingDisposition ? 0.7 : 1,
                }}
              >
                {savingDisposition ? <Loader2 size={15} className="animate-spin" /> : <CheckCircle2 size={15} />}
                {savingDisposition ? "Saving…" : "Save & close"}
              </button>
            </div>
          </div>
        </div>
      )}

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
                  {linkedinStatus === "replied" && "They replied to your message. Follow up!"}
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
    </>
  );
}

// ── Progress cell — unified progress visualization ──────────────────────
// One component, two modes, visually consistent:
//   (1) Lifecycle mode — fires when the contact has an in-flight sequence.
//       Shows real cadence step dots (step 1..N) with actual reconciled
//       state from webhooks (sent/opened/replied/overdue/etc).
//   (2) Stage-fallback mode — fires when there's no sequence yet. Shows
//       the generic funnel stages (Ready → Email → Call → LinkedIn →
//       Reply → Meeting) so the row is never visually empty.
// Both modes open the same lifecycle drawer on click.

const LIFECYCLE_DOT_STYLE: Record<LifecycleStepState, { bg: string; ring: string; border: string; text: string }> = {
  sent:     { bg: "#22c55e", ring: "#dcfce7", border: "#16a34a", text: "#15803d" },
  opened:   { bg: "#14b8a6", ring: "#ccfbf1", border: "#0d9488", text: "#0f766e" },
  clicked:  { bg: "#0ea5e9", ring: "#e0f2fe", border: "#0284c7", text: "#0369a1" },
  replied:  { bg: "#7c3aed", ring: "#ede9fe", border: "#6d28d9", text: "#6d28d9" },
  done:     { bg: "#16a34a", ring: "#dcfce7", border: "#15803d", text: "#15803d" },
  overdue:  { bg: "#ef4444", ring: "#fee2e2", border: "#dc2626", text: "#b91c1c" },
  upcoming: { bg: "#ffffff", ring: "transparent", border: "#d6e0ea", text: "#8aa0b5" },
  skipped:  { bg: "#f1f5f9", ring: "transparent", border: "#cbd5e1", text: "#94a3b8" },
  failed:   { bg: "#f97316", ring: "#ffedd5", border: "#ea580c", text: "#c2410c" },
};

function ProgressCell({
  contact,
  lifecycle,
}: {
  contact: Contact;
  lifecycle: LifecycleSummary | undefined;
}) {
  const tone = getProspectTrackingTone(contact);
  const score = getProspectTrackingScore(contact);

  // Decide which mode to render.
  // Lifecycle mode when we have a real running sequence with steps.
  const hasLiveSequence =
    lifecycle &&
    lifecycle.total_steps > 0 &&
    !["never_launched"].includes(lifecycle.status);

  // ── Render path A: lifecycle mode ──────────────────────────────────────
  if (hasLiveSequence) {
    const total = lifecycle!.total_steps;
    const done = lifecycle!.done_count;
    const current = lifecycle!.current_step_index ?? -1;
    const overdueCount = lifecycle!.overdue_count;

    // Turn summary into per-step state for the rail.
    const stepStates: LifecycleStepState[] = [];
    for (let i = 0; i < total; i++) {
      if (i < done) stepStates.push("done");
      else if (i === current && overdueCount > 0) stepStates.push("overdue");
      else if (i === current) stepStates.push("upcoming");
      else stepStates.push("upcoming");
    }
    // Replied/booked → mark the current as its terminal color for clarity.
    if (lifecycle!.status === "replied" && current >= 0) stepStates[current] = "replied";
    if (lifecycle!.status === "booked" && current >= 0) stepStates[current] = "done";

    const statusLabel = (() => {
      switch (lifecycle!.status) {
        case "in_progress": return overdueCount > 0 ? "Overdue" : "In progress";
        case "replied":     return "Replied";
        case "booked":      return "Booked";
        case "stopped":     return "Stopped";
        case "stalled":     return "Stalled";
        case "completed":   return "Completed";
        case "ready":       return "Ready · Not launched";
        default:            return lifecycle!.status;
      }
    })();
    const statusColor = (() => {
      switch (lifecycle!.status) {
        case "replied":    return "#7c3aed";
        case "booked":     return "#16a34a";
        case "stalled":    return "#dc2626";
        case "stopped":    return "#64748b";
        case "completed":  return "#475569";
        case "ready":      return "#92400e";
        default:           return overdueCount > 0 ? "#dc2626" : "#175089";
      }
    })();

    return (
      <div
        style={{
          minWidth: 300,
          padding: "12px 14px",
          borderRadius: 16,
          background: "#ffffff",
          border: "1px solid #e5edf5",
          boxShadow: "inset 0 1px 0 rgba(255,255,255,0.9)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 10 }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "4px 9px", borderRadius: 999, background: tone.soft, border: `1px solid ${tone.border}`, color: tone.color, fontWeight: 800, fontSize: 11.5 }}>
            {score}
          </span>
          <span style={{ color: statusColor, fontSize: 11, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.05em" }}>
            {statusLabel}
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 0 }}>
          {stepStates.map((state, index) => {
            const s = LIFECYCLE_DOT_STYLE[state];
            const isCurrent = index === current;
            const label = state === "done" ? `Step ${index + 1}` :
                          state === "overdue" ? "Overdue" :
                          state === "replied" ? "Replied" :
                          isCurrent ? `Step ${index + 1}` : "";
            return (
              <div key={index} style={{ display: "flex", alignItems: "center", flex: 1, minWidth: 0 }}>
                <div
                  title={`Step ${index + 1}: ${state}`}
                  style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6, minWidth: 0 }}
                >
                  <div
                    style={{
                      width: 16, height: 16, borderRadius: 999,
                      border: `2px solid ${s.border}`,
                      background: s.bg,
                      boxShadow: isCurrent && s.ring !== "transparent" ? `0 0 0 5px ${s.ring}` : "none",
                      flexShrink: 0,
                    }}
                  />
                  <div
                    style={{
                      maxWidth: "100%",
                      color: s.text,
                      fontSize: 10,
                      fontWeight: isCurrent ? 800 : 700,
                      letterSpacing: 0.15,
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {label}
                  </div>
                </div>
                {index < stepStates.length - 1 ? (
                  <div
                    style={{
                      flex: 1, height: 2, borderRadius: 999,
                      background: index < done ? s.border : "#dbe5ef",
                      margin: "0 4px 18px",
                    }}
                  />
                ) : null}
              </div>
            );
          })}
        </div>
        <div style={{ marginTop: 10, color: "#8aa0b5", fontSize: 10.5, fontWeight: 700, textAlign: "right" }}>
          {lifecycle!.days_since_launch != null ? `Day ${lifecycle!.days_since_launch} · ` : ""}
          {done}/{total} done
          {overdueCount > 0 ? ` · ${overdueCount} overdue` : ""}
        </div>
      </div>
    );
  }

  // ── Render path B: stage-fallback mode ─────────────────────────────────
  // No generated sequence yet — show the generic funnel template so the
  // row isn't empty. Same visual grammar as lifecycle mode for continuity.
  const progressSteps = getProspectProgressSteps(contact);
  const currentStep = progressSteps.find((step) => step.state === "current") ?? progressSteps[0];
  const notGeneratedHint = lifecycle?.status === "never_launched"
    ? "No sequence yet — Generate to start"
    : lifecycle?.status === "ready"
      ? "Ready · Not launched"
      : currentStep.label;

  return (
    <div
      style={{
        minWidth: 300,
        padding: "12px 14px",
        borderRadius: 16,
        background: "#ffffff",
        border: "1px solid #e5edf5",
        boxShadow: "inset 0 1px 0 rgba(255,255,255,0.9)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 10 }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "4px 9px", borderRadius: 999, background: tone.soft, border: `1px solid ${tone.border}`, color: tone.color, fontWeight: 800, fontSize: 11.5 }}>
          {score}
        </span>
        <span style={{ color: "#7a8ea4", fontSize: 11, fontWeight: 700 }}>
          {notGeneratedHint}
        </span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 0 }}>
        {progressSteps.map((step, index) => {
          const stateStyle =
            step.state === "done"
              ? { fill: tone.color, border: tone.border, text: tone.color, line: tone.border, ring: "transparent" }
              : step.state === "current"
                ? { fill: "#ffffff", border: "#175089", text: "#175089", line: "#bfd7fb", ring: "rgba(23,80,137,0.12)" }
                : { fill: "#ffffff", border: "#d6e0ea", text: "#8aa0b5", line: "#dbe5ef", ring: "transparent" };
          return (
            <div key={step.key} style={{ display: "flex", alignItems: "center", flex: 1, minWidth: 0 }}>
              <div
                title={`${step.label}: ${step.detail}`}
                style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6, minWidth: 0 }}
              >
                <div
                  style={{
                    width: 16, height: 16, borderRadius: 999,
                    border: `2px solid ${stateStyle.border}`,
                    background: stateStyle.fill,
                    boxShadow: step.state === "current" ? `0 0 0 5px ${stateStyle.ring}` : "none",
                    flexShrink: 0,
                  }}
                />
                <div
                  style={{
                    maxWidth: "100%",
                    color: stateStyle.text,
                    fontSize: 10,
                    fontWeight: step.state === "current" ? 800 : 700,
                    letterSpacing: 0.15,
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {step.label}
                </div>
              </div>
              {index < progressSteps.length - 1 ? (
                <div
                  style={{
                    flex: 1, height: 2, borderRadius: 999,
                    background: stateStyle.line,
                    margin: "0 4px 18px",
                  }}
                />
              ) : null}
            </div>
          );
        })}
      </div>
      {contact.tracking_last_activity_at ? (
        <div style={{ marginTop: 10, color: "#8aa0b5", fontSize: 10.5, fontWeight: 700, textAlign: "right" }}>
          Updated {new Date(contact.tracking_last_activity_at).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
        </div>
      ) : null}
    </div>
  );
}

// ── Lifecycle drawer: full step-by-step cadence state ───────────────────
// Opens when rep clicks the mini bar. Shows every step's actual state,
// timestamps, and email/call/linkedin details. Issues banner at the top
// surfaces things like "stalled 10 days" or "campaign paused" so reps know
// what to check without digging through Instantly or activity logs.
const CHANNEL_ICON: Record<"email" | "call" | "linkedin", React.ReactNode> = {
  email: <Mail size={13} />,
  call: <PhoneCall size={13} />,
  linkedin: <Link2 size={13} />,
};

function formatLifecycleDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) +
    " " + d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

function LifecycleStepRow({ step }: { step: LifecycleStep }) {
  const style = LIFECYCLE_DOT_STYLE[step.state];
  const stateLabels: Record<LifecycleStepState, string> = {
    sent: "Sent", opened: "Opened", clicked: "Clicked", replied: "Replied",
    done: "Done", overdue: "Overdue", upcoming: "Upcoming",
    skipped: "Skipped", failed: "Failed",
  };
  const showFired = step.fired_at;
  return (
    <div style={{ display: "flex", gap: 12, padding: "12px 0", borderBottom: "1px solid #eef2f7" }}>
      {/* Rail dot */}
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", paddingTop: 4 }}>
        <div style={{ width: 14, height: 14, borderRadius: 999, background: style.bg, border: `2px solid ${style.border}`, boxShadow: style.ring !== "transparent" ? `0 0 0 3px ${style.ring}` : "none", flexShrink: 0 }} />
      </div>

      {/* Content */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12.5, fontWeight: 700, color: "#0f2744" }}>
            {CHANNEL_ICON[step.channel]}
            Step {step.index + 1} · {step.channel[0].toUpperCase() + step.channel.slice(1)}
          </span>
          <span style={{ fontSize: 11, color: "#64748b", fontWeight: 600 }}>
            day {step.day_offset}
          </span>
          <span style={{ fontSize: 10.5, fontWeight: 800, padding: "2px 8px", borderRadius: 999, background: style.ring !== "transparent" ? style.ring : "#f1f5f9", color: style.border, border: `1px solid ${style.border}`, textTransform: "uppercase", letterSpacing: "0.05em" }}>
            {stateLabels[step.state]}
          </span>
          {step.state === "overdue" && (
            <span style={{ fontSize: 10.5, color: "#dc2626", fontWeight: 700 }}>
              due {formatLifecycleDate(step.due_at)}
            </span>
          )}
        </div>

        {step.objective && (
          <div style={{ marginTop: 4, fontSize: 11.5, color: "#64748b" }}>{step.objective}</div>
        )}

        {/* Channel-specific details */}
        {step.channel === "email" && (
          <div style={{ marginTop: 6, display: "grid", gap: 3 }}>
            {step.subject && <div style={{ fontSize: 12, fontWeight: 600, color: "#24364b" }}>{step.subject}</div>}
            {showFired && <div style={{ fontSize: 11.5, color: "#334155" }}>Sent: {formatLifecycleDate(step.fired_at)}</div>}
            {step.opened_at && <div style={{ fontSize: 11.5, color: "#0d9488" }}>Opened: {formatLifecycleDate(step.opened_at)}</div>}
            {step.clicked_at && <div style={{ fontSize: 11.5, color: "#0284c7" }}>Clicked: {formatLifecycleDate(step.clicked_at)}</div>}
            {step.replied_at && <div style={{ fontSize: 11.5, color: "#7c3aed", fontWeight: 700 }}>Replied: {formatLifecycleDate(step.replied_at)}</div>}
            {step.bounced_at && <div style={{ fontSize: 11.5, color: "#ea580c", fontWeight: 700 }}>Bounced: {formatLifecycleDate(step.bounced_at)}</div>}
          </div>
        )}
        {step.channel === "call" && step.fired_at && (
          <div style={{ marginTop: 6, display: "grid", gap: 3 }}>
            <div style={{ fontSize: 11.5, color: "#334155" }}>
              Call logged: {formatLifecycleDate(step.fired_at)}
              {step.call_outcome && <> · {step.call_outcome}</>}
            </div>
            {step.note && <div style={{ fontSize: 11.5, color: "#64748b", lineHeight: 1.5 }}>{step.note}</div>}
          </div>
        )}
        {step.channel === "linkedin" && step.fired_at && (
          <div style={{ marginTop: 6, display: "grid", gap: 3 }}>
            <div style={{ fontSize: 11.5, color: "#334155" }}>Logged: {formatLifecycleDate(step.fired_at)}</div>
            {step.note && <div style={{ fontSize: 11.5, color: "#64748b", lineHeight: 1.5 }}>{step.note}</div>}
          </div>
        )}
        {step.state === "skipped" && step.skip_reason && (
          <div style={{ marginTop: 4, fontSize: 11.5, color: "#64748b", fontStyle: "italic" }}>
            Skipped — {step.skip_reason.replace(/_/g, " ")}
          </div>
        )}
        {step.state === "upcoming" && (
          <div style={{ marginTop: 4, fontSize: 11.5, color: "#64748b" }}>
            Due {formatLifecycleDate(step.due_at)}
          </div>
        )}
      </div>
    </div>
  );
}

function LifecycleDrawer({
  contactId, detail, loading, onClose,
}: {
  contactId: string | null;
  detail: SequenceLifecycle | null;
  loading: boolean;
  onClose: () => void;
}) {
  if (!contactId) return null;

  const statusChipTone = (status: string) => {
    switch (status) {
      case "in_progress": return { bg: "#eff6ff", color: "#1d4ed8", border: "#bfdbfe" };
      case "replied":     return { bg: "#faf5ff", color: "#7c3aed", border: "#e9d5ff" };
      case "booked":      return { bg: "#f0fdf4", color: "#15803d", border: "#bbf7d0" };
      case "stopped":     return { bg: "#f1f5f9", color: "#475569", border: "#cbd5e1" };
      case "stalled":     return { bg: "#fef2f2", color: "#dc2626", border: "#fecaca" };
      case "completed":   return { bg: "#f1f5f9", color: "#334155", border: "#cbd5e1" };
      case "ready":       return { bg: "#fef9e7", color: "#92400e", border: "#fde68a" };
      case "never_launched": return { bg: "#f1f5f9", color: "#64748b", border: "#cbd5e1" };
      default: return { bg: "#f1f5f9", color: "#475569", border: "#cbd5e1" };
    }
  };

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 220, display: "flex", justifyContent: "flex-end" }}>
      <div style={{ flex: 1, background: "rgba(10,20,40,0.35)" }} onClick={onClose} />
      <div style={{ width: 520, maxWidth: "100vw", background: "#fff", borderLeft: "1px solid #d5e3ef", boxShadow: "-24px 0 48px rgba(14,38,66,0.16)", display: "flex", flexDirection: "column", overflowY: "auto" }}>
        {/* Header */}
        <div style={{ padding: "18px 22px 14px", borderBottom: "1px solid #e8eef5", display: "flex", alignItems: "center", justifyContent: "space-between", position: "sticky", top: 0, background: "#fff", zIndex: 1 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 800, color: "#0f2744" }}>Sequence lifecycle</div>
            <div style={{ fontSize: 12, color: "#7a96b0" }}>
              {loading ? "Loading…" :
               detail ? `${detail.total_steps} steps · ${detail.days_since_launch != null ? `day ${detail.days_since_launch}` : "not launched"}` :
               "No data"}
            </div>
          </div>
          <button onClick={onClose} style={{ width: 30, height: 30, borderRadius: 8, border: "1px solid #d5e3ef", background: "#fff", color: "#546679", cursor: "pointer", display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
            <X size={14} />
          </button>
        </div>

        <div style={{ padding: "16px 22px" }}>
          {loading && (
            <div style={{ fontSize: 13, color: "#7a96b0" }}>Loading lifecycle…</div>
          )}

          {!loading && detail && (
            <>
              {/* Top-line status */}
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
                {(() => {
                  const tone = statusChipTone(detail.status);
                  return (
                    <span style={{ padding: "4px 10px", borderRadius: 999, background: tone.bg, color: tone.color, border: `1px solid ${tone.border}`, fontSize: 11.5, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.06em" }}>
                      {detail.status.replace(/_/g, " ")}
                    </span>
                  );
                })()}
                {detail.sequence?.instantly_campaign_status && (
                  <span style={{ padding: "4px 10px", borderRadius: 999, background: "#f1f5f9", color: "#475569", border: "1px solid #cbd5e1", fontSize: 11, fontWeight: 700 }}>
                    Instantly: {detail.sequence.instantly_campaign_status}
                  </span>
                )}
                {detail.launched_at && (
                  <span style={{ fontSize: 11.5, color: "#64748b" }}>
                    Launched {formatLifecycleDate(detail.launched_at)}
                  </span>
                )}
              </div>

              {/* Issues */}
              {detail.issues.length > 0 && (
                <div style={{ marginBottom: 14, display: "grid", gap: 8 }}>
                  {detail.issues.map((issue, i) => {
                    const toneBg = issue.severity === "error" ? "#fef2f2" :
                                   issue.severity === "warning" ? "#fffbeb" : "#eff6ff";
                    const toneColor = issue.severity === "error" ? "#b91c1c" :
                                      issue.severity === "warning" ? "#92400e" : "#1d4ed8";
                    const toneBorder = issue.severity === "error" ? "#fecaca" :
                                       issue.severity === "warning" ? "#fde68a" : "#bfdbfe";
                    return (
                      <div key={i} style={{ padding: "10px 12px", borderRadius: 10, background: toneBg, border: `1px solid ${toneBorder}`, color: toneColor, display: "flex", gap: 8, alignItems: "flex-start" }}>
                        <AlertTriangle size={14} style={{ marginTop: 2, flexShrink: 0 }} />
                        <div style={{ fontSize: 12.5, lineHeight: 1.5, fontWeight: 600 }}>
                          {issue.message}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Step timeline */}
              {detail.steps.length === 0 ? (
                <div style={{ fontSize: 13, color: "#7a96b0", fontStyle: "italic" }}>
                  No steps defined on this contact's sequence plan yet.
                </div>
              ) : (
                <div>
                  {detail.steps.map((step) => (
                    <LifecycleStepRow key={step.index} step={step} />
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Pre-call intel panel ─────────────────────────────────────────────────
// The most important rep surface in Prospecting: this is what they look at
// while the phone is ringing. Organized so the eye falls on the freshest
// signals first (last email + whether it was opened, then recent signals),
// then talking points, then objection handles, then deep context.
function PreCallIntelPanel({
  contact,
  brief,
  loading,
}: {
  contact: Contact;
  brief: PreCallBrief | null;
  loading: boolean;
}) {
  const SectionHeader = ({ children }: { children: React.ReactNode }) => (
    <div style={{ fontSize: 10, fontWeight: 800, color: "#546679", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 8 }}>
      {children}
    </div>
  );

  const emailAgeLabel = (iso: string) => {
    const sent = new Date(iso).getTime();
    const hrs = (Date.now() - sent) / 3_600_000;
    if (hrs < 1) return "just now";
    if (hrs < 24) return `${Math.round(hrs)}h ago`;
    const days = Math.round(hrs / 24);
    return `${days}d ago`;
  };

  const signalDotColor = (type: string) => {
    switch (type) {
      case "funding": return "#f59e0b";
      case "jobs": return "#10b981";
      case "pr": return "#3b82f6";
      case "news": return "#64748b";
      case "review": return "#8b5cf6";
      case "linkedin": return "#ec4899";
      default: return "#94a3b8";
    }
  };

  const titleLine = [
    contact.title,
    brief?.company?.name,
    brief?.company?.industry,
  ].filter(Boolean).join(" · ");

  return (
    <div style={{ padding: "16px 22px", borderBottom: "1px solid #e8eef5", display: "grid", gap: 16 }}>
      {/* Identity strip: who are we calling? */}
      <div style={{ display: "grid", gap: 4 }}>
        {titleLine && (
          <div style={{ fontSize: 12, color: "#2c4a63", fontWeight: 600 }}>{titleLine}</div>
        )}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, fontSize: 12, color: "#546679" }}>
          {contact.phone && (
            <span style={{ color: "#175089", fontWeight: 600 }}>
              {contact.phone}
            </span>
          )}
          {contact.timezone && <span>TZ: {contact.timezone}</span>}
          {contact.outreach_lane && (
            <span style={{ padding: "1px 8px", borderRadius: 999, background: "#eef2f7", color: "#24364b", fontWeight: 600 }}>
              {contact.outreach_lane.replace(/_/g, " ")}
            </span>
          )}
          {contact.sequence_status && (
            <span style={{ padding: "1px 8px", borderRadius: 999, background: "#eaf3ff", color: "#1a56db", fontWeight: 600 }}>
              {contact.sequence_status.replace(/_/g, " ")}
            </span>
          )}
        </div>
      </div>

      {loading && (
        <div style={{ fontSize: 12, color: "#7a96b0", fontStyle: "italic" }}>Loading pre-call brief…</div>
      )}

      {/* Last email sent — most decision-relevant signal. If the prospect
          opened it, the rep should reference it. If not, start fresh. */}
      {brief?.last_email_sent && (
        <div style={{ background: brief.last_email_sent.opened ? "#ecfdf5" : "#f8fafc", border: `1px solid ${brief.last_email_sent.opened ? "#a7f3d0" : "#dbe4ef"}`, borderRadius: 10, padding: "10px 12px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
            <SectionHeader>
              Last email · {emailAgeLabel(brief.last_email_sent.sent_at)}
            </SectionHeader>
            <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
              {brief.last_email_sent.opened && (
                <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 7px", borderRadius: 999, background: "#059669", color: "#fff" }}>OPENED</span>
              )}
              {brief.last_email_sent.clicked && (
                <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 7px", borderRadius: 999, background: "#1d4ed8", color: "#fff" }}>CLICKED</span>
              )}
            </div>
          </div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#0f2744", marginBottom: 4 }}>
            {brief.last_email_sent.subject}
          </div>
          {brief.last_email_sent.snippet && (
            <div style={{ fontSize: 12, color: "#3d5268", lineHeight: 1.5, whiteSpace: "pre-wrap" }}>
              {brief.last_email_sent.snippet}
            </div>
          )}
        </div>
      )}

      {/* Recent buying signals — warm the call with timely context */}
      {brief?.recent_signals && brief.recent_signals.length > 0 && (
        <div>
          <SectionHeader>Recent signals</SectionHeader>
          <div style={{ display: "grid", gap: 6 }}>
            {brief.recent_signals.map((s, i) => (
              <div key={i} style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 12.5, color: "#2c4a63", lineHeight: 1.5 }}>
                <span style={{ marginTop: 6, width: 6, height: 6, borderRadius: 999, background: signalDotColor(s.type), flexShrink: 0 }} />
                <span>
                  <span style={{ fontWeight: 600 }}>{s.title}</span>
                  {s.summary && <span style={{ color: "#546679" }}> — {s.summary}</span>}
                  {s.url && (
                    <>
                      {" "}
                      <a href={s.url} target="_blank" rel="noopener noreferrer" style={{ color: "#1a56db", fontSize: 11 }}>open</a>
                    </>
                  )}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Conversation starter & personalization — curated human signals
          that were written during account sourcing. Highest-trust content. */}
      {(brief?.conversation_starter || brief?.personalization_notes) && (
        <div style={{ display: "grid", gap: 8 }}>
          {brief.conversation_starter && (
            <div style={{ fontSize: 13, color: "#2c4a63", background: "#f0f6ff", border: "1px solid #c8daf0", borderRadius: 10, padding: "10px 12px", lineHeight: 1.5 }}>
              <div style={{ fontWeight: 700, marginBottom: 4, fontSize: 11, textTransform: "uppercase", color: "#546679", letterSpacing: "0.05em" }}>Conversation starter</div>
              {brief.conversation_starter}
            </div>
          )}
          {brief.personalization_notes && (
            <div style={{ fontSize: 13, color: "#2c4a63", background: "#f7fbff", border: "1px solid #dbe6f2", borderRadius: 10, padding: "10px 12px", lineHeight: 1.5 }}>
              <div style={{ fontWeight: 700, marginBottom: 4, fontSize: 11, textTransform: "uppercase", color: "#546679", letterSpacing: "0.05em" }}>Personalization notes</div>
              {brief.personalization_notes}
            </div>
          )}
        </div>
      )}

      {/* Talking points — always shown (persona fallback if not populated)
          so the rep is never staring at an empty sidebar. */}
      {brief?.talking_points && brief.talking_points.length > 0 && (
        <div>
          <SectionHeader>Talking points</SectionHeader>
          <ol style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 4 }}>
            {brief.talking_points.slice(0, 4).map((pt, i) => (
              <li key={i} style={{ fontSize: 12.5, color: "#2c4a63", lineHeight: 1.5 }}>{pt}</li>
            ))}
          </ol>
        </div>
      )}

      {/* Objection playbook — collapsed by default since we don't want to
          distract the rep before the call. Available when needed. */}
      {brief?.objection_playbook && brief.objection_playbook.length > 0 && (
        <details style={{ background: "#fef9e7", border: "1px solid #fde68a", borderRadius: 10, padding: "8px 12px" }}>
          <summary style={{ cursor: "pointer", fontSize: 11, fontWeight: 800, color: "#92400e", textTransform: "uppercase", letterSpacing: "0.06em" }}>
            Objection handles ({brief.objection_playbook.length})
          </summary>
          <div style={{ marginTop: 8, display: "grid", gap: 10 }}>
            {brief.objection_playbook.map((ob, i) => (
              <div key={i}>
                <div style={{ fontSize: 12, fontWeight: 700, color: "#7c2d12", marginBottom: 3 }}>{ob.objection}</div>
                <div style={{ fontSize: 12, color: "#3d5268", lineHeight: 1.5 }}>{ob.response}</div>
              </div>
            ))}
          </div>
        </details>
      )}

      {/* Recent activities — short trail of what's been happening on this
          contact across all channels. */}
      {brief?.recent_activities && brief.recent_activities.length > 0 && (
        <details style={{ fontSize: 12 }}>
          <summary style={{ cursor: "pointer", fontSize: 11, fontWeight: 800, color: "#546679", textTransform: "uppercase", letterSpacing: "0.06em" }}>
            Recent activity ({brief.recent_activities.length})
          </summary>
          <div style={{ marginTop: 8, display: "grid", gap: 6 }}>
            {brief.recent_activities.map((a, i) => (
              <div key={i} style={{ fontSize: 12, color: "#3d5268", lineHeight: 1.5 }}>
                <span style={{ fontWeight: 700, color: "#24364b" }}>{a.type}</span>
                {a.medium && <span style={{ color: "#7a96b0" }}> · {a.medium}</span>}
                {a.ai_summary || a.content ? <> — {a.ai_summary || a.content}</> : null}
              </div>
            ))}
          </div>
        </details>
      )}

      {!loading && !brief && (
        <div style={{ fontSize: 13, color: "#7a96b0", fontStyle: "italic" }}>
          No pre-call brief available. Check the contact has data.
        </div>
      )}
    </div>
  );
}

function ProspectingTabButton({
  active,
  icon,
  label,
  description,
  count,
  countLabel,
  accent,
  onClick,
}: {
  active: boolean;
  icon: ReactNode;
  label: string;
  description: string;
  count: number;
  countLabel: string;
  accent: "blue" | "teal";
  onClick: () => void;
}) {
  const accentStyles = active
    ? accent === "blue"
      ? {
          shell: {
            borderColor: "transparent",
            background: "linear-gradient(135deg, #1c4f93 0%, #1f6feb 100%)",
            boxShadow: "0 16px 32px rgba(31, 111, 235, 0.22)",
            color: "#ffffff",
          },
          icon: { background: "rgba(255,255,255,0.14)", color: "#ffffff" },
          badge: { background: "rgba(255,255,255,0.14)", color: "#ffffff" },
        }
      : {
          shell: {
            borderColor: "transparent",
            background: "linear-gradient(135deg, #124a4c 0%, #1b8a86 100%)",
            boxShadow: "0 16px 32px rgba(27, 138, 134, 0.22)",
            color: "#ffffff",
          },
          icon: { background: "rgba(255,255,255,0.14)", color: "#ffffff" },
          badge: { background: "rgba(255,255,255,0.14)", color: "#ffffff" },
        }
    : accent === "blue"
      ? {
          shell: {
            borderColor: "#d9e1ec",
            background: "linear-gradient(180deg, #ffffff 0%, #f8fbff 100%)",
            boxShadow: "0 8px 20px rgba(17, 34, 68, 0.04)",
            color: "#1d2b3c",
          },
          icon: { background: "#eaf2ff", color: "#1f6feb" },
          badge: { background: "#edf4ff", color: "#1f6feb" },
        }
      : {
          shell: {
            borderColor: "#d9e1ec",
            background: "linear-gradient(180deg, #ffffff 0%, #f8fbff 100%)",
            boxShadow: "0 8px 20px rgba(17, 34, 68, 0.04)",
            color: "#1d2b3c",
          },
          icon: { background: "#e7f7f5", color: "#177b75" },
          badge: { background: "#edf9f8", color: "#177b75" },
        };

  return (
    <button
      type="button"
      onClick={onClick}
      className="min-w-[250px] flex-1 border p-4 text-left transition-all"
      style={{ borderRadius: 22, ...accentStyles.shell }}
    >
      <div className="flex items-start gap-3">
        <div
          className="flex h-11 w-11 shrink-0 items-center justify-center"
          style={{ borderRadius: 18, ...accentStyles.icon }}
        >
          {icon}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-[15px] font-bold">{label}</p>
              <p
                className="mt-1 text-[12px] leading-5"
                style={{ color: active ? "rgba(255,255,255,0.78)" : "#6f8297" }}
              >
                {description}
              </p>
            </div>
            <span
              className="shrink-0 px-2.5 py-1 text-[11px] font-bold"
              style={{ borderRadius: 999, ...accentStyles.badge }}
            >
              {count}
            </span>
          </div>
          <p
            className="mt-4 text-[11px] font-semibold uppercase tracking-[0.14em]"
            style={{ color: active ? "rgba(255,255,255,0.62)" : "#90a3b8" }}
          >
            {countLabel}
          </p>
        </div>
      </div>
    </button>
  );
}

function AngelOverviewCard({
  icon,
  label,
  value,
  caption,
  tone,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  caption: string;
  tone: "blue" | "teal" | "amber" | "green";
}) {
  const toneStyles = {
    blue: { background: "#eef5ff", color: "#1f6feb" },
    teal: { background: "#e8f7f6", color: "#177b75" },
    amber: { background: "#fff5e6", color: "#b56d00" },
    green: { background: "#eaf8f0", color: "#1f8f5f" },
  }[tone];

  return (
    <div
      className="p-0"
      style={{
        padding: "20px 20px 18px",
        borderRadius: 22,
        border: "1px solid rgba(255,255,255,0.1)",
        background: "rgba(255,255,255,0.1)",
        backdropFilter: "blur(6px)",
      }}
    >
      <div className="flex items-start gap-3">
        <div
          className="flex h-10 w-10 shrink-0 items-center justify-center"
          style={{ borderRadius: 18, ...toneStyles }}
        >
          {icon}
        </div>
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em]" style={{ color: "rgba(255,255,255,0.62)" }}>{label}</p>
          <p className="mt-3 text-[30px] font-bold leading-none" style={{ color: "#ffffff" }}>{value}</p>
          <p className="mt-3 text-[12px] leading-6" style={{ color: "rgba(255,255,255,0.72)" }}>{caption}</p>
        </div>
      </div>
    </div>
  );
}

function SnapshotRow({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "blue" | "teal" | "green";
}) {
  const toneStyles = {
    blue: { background: "#eef5ff", color: "#235dc6" },
    teal: { background: "#edf9f8", color: "#177b75" },
    green: { background: "#eaf8f0", color: "#1f8f5f" },
  }[tone];

  return (
    <div
      className="flex items-center justify-between px-4 py-3"
      style={{ borderRadius: 18, border: "1px solid #e4edf5", background: "#fbfdff", padding: "14px 16px" }}
    >
      <span className="text-[13px] font-medium text-[#60758a]">{label}</span>
      <span className="px-2.5 py-1 text-[11px] font-bold" style={{ borderRadius: 999, ...toneStyles }}>{value}</span>
    </div>
  );
}

function StrengthBadge({
  strength,
  compact = false,
  labelPrefix,
}: {
  strength: number;
  compact?: boolean;
  labelPrefix?: string;
}) {
  return (
    <span
      className={`inline-flex items-center font-bold ${compact ? "px-2.5 py-1 text-[10px]" : "px-3 py-1.5 text-[11px]"}`}
      style={{ borderRadius: 999, ...(STRENGTH_STYLE[strength] || {}) }}
    >
      {labelPrefix ? `${labelPrefix}: ` : ""}
      {strength}/5
    </span>
  );
}

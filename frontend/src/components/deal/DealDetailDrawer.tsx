import { useEffect, useRef, useState } from "react";
import {
  X, ChevronDown, Building2, CalendarDays, UserCircle2,
  Send, Tag, Plus, Trash2, ArrowRight, Clock3, Globe, Zap, Navigation,
  Activity as ActivityIcon, Phone, Mail, Video, FileText, AlertTriangle, Search, Loader2, Sparkles,
  Shield, BarChart2, ClipboardList, Presentation,
} from "lucide-react";
import { ZippyDocDropdown } from "../zippy/ZippyDocDropdown";
import { accountSourcingApi, companiesApi, dealsApi, contactsApi, settingsApi, personalEmailSyncApi } from "../../lib/api";
import type { PersonalEmailThread } from "../../lib/api";
import { useAuth } from "../../lib/AuthContext";
import type { Activity, Company, Contact, Deal, DealContact, DealQualification, User } from "../../types";
import { avatarColor, formatCurrency, formatDate, getInitials } from "../../lib/utils";
import TaskCenterModal from "../tasks/TaskCenterModal";
import TranscriptPreview from "../activity/TranscriptPreview";
import ProvenanceBar from "../ProvenanceBar";

interface Props {
  deal: Deal;
  companies: Company[];
  users: User[];
  stages: { id: string; label: string; group: string }[];
  onClose: () => void;
  onDealUpdated: (deal: Deal) => void;
  onDealDeleted?: (dealId: string) => void;
  onConvert?: (deal: Deal) => void;
  onCompanyUpdated?: (company: Company) => void;
}

const PERSONA_STYLE: Record<string, { bg: string; color: string }> = {
  economic_buyer: { bg: "#ffe8de", color: "#7b3a1d" },
  champion: { bg: "#e4fbf3", color: "#1b6f53" },
  technical_evaluator: { bg: "#eaf4ff", color: "#24567e" },
};

const ACTIVITY_ICON: Record<string, typeof ActivityIcon> = {
  comment: ActivityIcon,
  call: Phone,
  email: Mail,
  meeting: Video,
  note: FileText,
  transcript: FileText,
  visit: Globe,
};

type DrawerTab = "overview" | "meddpicc" | "activity" | "tasks" | "emails";

const MEDDPICC_DIMENSIONS = [
  { key: "metrics", label: "Metrics", desc: "Quantified business impact of solving the problem" },
  { key: "economic_buyer", label: "Economic Buyer", desc: "Person with veto power and budget authority" },
  { key: "decision_criteria", label: "Decision Criteria", desc: "Technical, business, and legal requirements" },
  { key: "decision_process", label: "Decision Process", desc: "Steps, timeline, and approvals needed to close" },
  { key: "paper_process", label: "Paper Process", desc: "Legal, procurement, and security review steps" },
  { key: "identify_pain", label: "Identify Pain", desc: "The core business pain driving urgency" },
  { key: "champion", label: "Champion", desc: "Internal advocate who sells when you're not there" },
  { key: "competition", label: "Competition", desc: "Alternatives being evaluated, including status quo" },
] as const;

const MEDDPICC_LEVEL_LABELS = ["Not Started", "Identified", "Validated", "Confirmed"] as const;
const MEDDPICC_LEVEL_COLORS = ["#94a3b8", "#f59e0b", "#3b82f6", "#22c55e"] as const;

function engagementTone(timestamp?: string) {
  if (!timestamp) {
    return { label: "No signal", background: "#f8fafc", color: "#7a8ca1", border: "#d9e3ef", accent: "#cbd5e1" };
  }
  const ageMs = Date.now() - new Date(timestamp).getTime();
  const ageDays = ageMs / (1000 * 60 * 60 * 24);
  if (ageDays <= 3) {
    return { label: "Active", background: "#ecfdf3", color: "#15803d", border: "#bbf7d0", accent: "#22c55e" };
  }
  if (ageDays <= 7) {
    return { label: "Watch", background: "#fff7ed", color: "#c2410c", border: "#fed7aa", accent: "#f59e0b" };
  }
  return { label: "Stale", background: "#fff1f2", color: "#be123c", border: "#fecdd3", accent: "#f43f5e" };
}

function engagementStatusJustification(label: string, timestamp?: string, side: "rep" | "client" = "rep") {
  if (!timestamp) {
    return side === "rep"
      ? "No seller-side activity has been captured for this deal yet."
      : "No buyer-side activity has been captured for this deal yet.";
  }
  if (label === "Active") {
    return "Marked active because the latest relevant signal is very recent.";
  }
  if (label === "Watch") {
    return "Marked watch because the latest relevant signal is recent, but momentum may need attention.";
  }
  return "Marked stale because the latest relevant signal is older and momentum may be slowing down.";
}

function relativeTime(timestamp?: string): string {
  if (!timestamp) return "";
  const ageMs = Date.now() - new Date(timestamp).getTime();
  const ageHours = ageMs / (1000 * 60 * 60);
  if (ageHours < 1) return "just now";
  if (ageHours < 24) return `${Math.max(1, Math.floor(ageHours))}h ago`;
  const ageDays = (Date.now() - new Date(timestamp).getTime()) / (1000 * 60 * 60 * 24);
  if (ageDays < 1) return "today";
  if (ageDays < 2) return "yesterday";
  return `${Math.floor(ageDays)}d ago`;
}

type EngagementSignal = NonNullable<Deal["seller_engagement_signal"]>;

function engagementSummary(signal: EngagementSignal | undefined, side: "rep" | "client") {
  if (!signal) {
    return {
      title: side === "rep" ? "No rep touch yet" : "No client touch yet",
      detail: side === "rep" ? "Waiting for outreach activity" : "Waiting for buyer activity",
      Icon: Clock3,
    };
  }

  switch (signal.type) {
    case "email":
      return {
        title: side === "rep" ? "Rep email sent" : "Client email",
        detail: signal.label,
        Icon: Mail,
      };
    case "call":
      return {
        title: side === "rep" ? "Call logged" : "Client on call",
        detail: signal.label,
        Icon: Phone,
      };
    case "meeting":
      return {
        title: side === "rep" ? "Meeting touch" : "Client meeting",
        detail: signal.label,
        Icon: CalendarDays,
      };
    case "transcript":
      return {
        title: side === "rep" ? "Meeting intel" : "Conversation captured",
        detail: signal.label,
        Icon: FileText,
      };
    case "note":
      return {
        title: "Rep note added",
        detail: signal.label,
        Icon: FileText,
      };
    default:
      return {
        title: side === "rep" ? "Rep activity" : "Client activity",
        detail: signal.label,
        Icon: ActivityIcon,
      };
  }
}

function EngagementPanel({
  side,
  timestamp,
  signal,
  reason,
}: {
  side: "rep" | "client";
  timestamp?: string;
  signal?: EngagementSignal;
  reason?: string;
}) {
  const [showDetail, setShowDetail] = useState(false);
  const tone = engagementTone(timestamp);
  const summary = engagementSummary(signal, side);
  const Icon = summary.Icon;
  const primaryReason = (reason || signal?.reason || summary.title).trim();
  const secondaryLine = signal?.label && signal.label !== primaryReason ? signal.label : undefined;
  const basis = signal?.label || (side === "rep" ? "No seller-side source yet" : "No buyer-side source yet");
  const statusWhy = engagementStatusJustification(tone.label, timestamp, side);
  const tooltipText = [primaryReason, secondaryLine, timestamp ? `Last touch ${relativeTime(timestamp)}` : ""].filter(Boolean).join("\n");

  return (
    <div
      onMouseEnter={() => setShowDetail(true)}
      onMouseLeave={() => setShowDetail(false)}
      title={tooltipText}
      style={{
        flex: 1,
        minWidth: 180,
        position: "relative",
        borderRadius: 10,
        border: `1px solid ${tone.border}`,
        background: "#fbfdff",
        padding: "7px 9px",
        display: "flex",
        flexDirection: "column",
        gap: 4,
        cursor: "default",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <span
            style={{
              width: 16,
              height: 16,
              borderRadius: 999,
              border: `1px solid ${tone.border}`,
              background: tone.background,
              color: tone.color,
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
            }}
          >
            <Icon size={10} />
          </span>
          <span style={{ fontSize: 9, fontWeight: 800, color: "#5f6f84", textTransform: "uppercase", letterSpacing: "0.06em" }}>
            {side === "rep" ? "Rep" : "Client"}
          </span>
        </div>
        <span
          style={{
            fontSize: 9,
            fontWeight: 700,
            color: tone.color,
            background: tone.background,
            padding: "1px 6px",
            borderRadius: 999,
            flexShrink: 0,
          }}
        >
          {tone.label}
        </span>
      </div>
      <div style={{ fontSize: 12, fontWeight: 700, color: "#1f2d3d", lineHeight: 1.25, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        {primaryReason}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
        <span style={{ width: 7, height: 7, borderRadius: 999, background: tone.accent, flexShrink: 0 }} />
        <span style={{ fontSize: 11, color: "#62748a", lineHeight: 1.3, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {secondaryLine ?? summary.detail} {timestamp ? `· ${relativeTime(timestamp)}` : ""}
        </span>
      </div>
      {showDetail && (
        <div
          onClick={(event) => event.stopPropagation()}
          style={{
            position: "absolute",
            top: "calc(100% + 8px)",
            left: 0,
            zIndex: 20,
            width: 260,
            borderRadius: 12,
            border: "1px solid #dbe6f2",
            background: "#fff",
            boxShadow: "0 18px 40px rgba(15, 23, 42, 0.18)",
            padding: "11px 12px",
            display: "flex",
            flexDirection: "column",
            gap: 6,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: 10, fontWeight: 800, color: "#6f7f95", textTransform: "uppercase", letterSpacing: "0.06em" }}>
              {side === "rep" ? "Rep engagement" : "Client engagement"}
            </span>
            <span style={{ fontSize: 10, fontWeight: 700, color: tone.color }}>
              {tone.label}
            </span>
          </div>
          <div style={{ fontSize: 12, fontWeight: 700, color: "#1f2d3d", lineHeight: 1.4 }}>
            {primaryReason}
          </div>
          {secondaryLine && (
            <div style={{ fontSize: 11, color: "#62748a", lineHeight: 1.4 }}>
              {secondaryLine}
            </div>
          )}
          <div style={{ fontSize: 11, color: "#33485f", lineHeight: 1.45 }}>
            <span style={{ fontWeight: 700 }}>Based on:</span> {basis}
          </div>
          <div style={{ fontSize: 11, color: "#6f7f95", lineHeight: 1.45 }}>
            <span style={{ fontWeight: 700 }}>Why {tone.label.toLowerCase()}:</span> {statusWhy}
          </div>
          {timestamp && (
            <div style={{ fontSize: 10, color: "#8ca0b3" }}>
              Last touch {relativeTime(timestamp)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function DealDetailDrawer({ deal, companies, users, stages, onClose, onDealUpdated, onDealDeleted, onConvert, onCompanyUpdated }: Props) {
  const { isAdmin } = useAuth();
  const [activities, setActivities] = useState<Activity[]>([]);
  const [dealContacts, setDealContacts] = useState<DealContact[]>([]);
  const [activeTab, setActiveTab] = useState<DrawerTab>("overview");
  const [emailThreads, setEmailThreads] = useState<PersonalEmailThread[]>([]);
  const [loadingEmails, setLoadingEmails] = useState(false);
  const [expandedThreads, setExpandedThreads] = useState<Set<string>>(new Set());
  const [autoFillingMeddpicc, setAutoFillingMeddpicc] = useState(false);
  const [comment, setComment] = useState("");
  const [sendingComment, setSendingComment] = useState(false);
  const [sharedInbox, setSharedInbox] = useState("zippy@beacon.li");

  // Inline editing states
  const [editingName, setEditingName] = useState(false);
  const [nameVal, setNameVal] = useState(deal.name);
  const [showStageMenu, setShowStageMenu] = useState(false);

  // Link contact
  const [showLinkContact, setShowLinkContact] = useState(false);
  const [contactSearch, setContactSearch] = useState("");
  const [contactResults, setContactResults] = useState<Contact[]>([]);
  const [linkRole, setLinkRole] = useState("");

  // Tag input
  const [tagInput, setTagInput] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [localPriorityTag, setLocalPriorityTag] = useState<"P0" | "P1" | "P2" | null>(() => {
    const tag = companies.find((c) => c.id === deal.company_id)?.priority_tag ?? null;
    return tag as "P0" | "P1" | "P2" | null;
  });

  // Company searchable combobox
  const [companyDropdownOpen, setCompanyDropdownOpen] = useState(false);
  const [companySearch, setCompanySearch] = useState("");
  const [companyResults, setCompanyResults] = useState<Company[]>([]);
  const [loadingCompanyResults, setLoadingCompanyResults] = useState(false);
  const companyDropdownRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (!companyDropdownRef.current?.contains(e.target as Node)) {
        setCompanyDropdownOpen(false);
        setCompanySearch("");
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const [companyContacts, setCompanyContacts] = useState<Contact[]>([]);

  useEffect(() => {
    dealsApi.getActivities(deal.id).then(setActivities).catch(() => {});
    dealsApi.getContacts(deal.id).then(setDealContacts).catch(() => {});
    if (deal.company_id) {
      contactsApi.list(0, 50, deal.company_id).then(setCompanyContacts).catch(() => {});
    }
  }, [deal.id, deal.company_id]);

  useEffect(() => {
    if (activeTab !== "emails") return;
    setLoadingEmails(true);
    personalEmailSyncApi.getThreadsForDeal(deal.id)
      .then((res) => setEmailThreads(res.threads))
      .catch(() => setEmailThreads([]))
      .finally(() => setLoadingEmails(false));
  }, [activeTab, deal.id]);

  useEffect(() => {
    settingsApi.getGmailSync().then((data) => {
      if (data.inbox) setSharedInbox(data.inbox);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    setActiveTab("overview");
    setComment("");
  }, [deal.id]);

  useEffect(() => {
    if (!companyDropdownOpen) return;

    let cancelled = false;
    const handle = window.setTimeout(async () => {
      setLoadingCompanyResults(true);
      try {
        const response = await accountSourcingApi.listCompaniesPaginated({
          skip: 0,
          limit: 40,
          q: companySearch.trim() || undefined,
        });
        let next = response.items;
        const selectedCompany = companies.find((company) => company.id === deal.company_id);
        if (selectedCompany && !next.some((company) => company.id === selectedCompany.id)) {
          next = [selectedCompany, ...next];
        }
        if (!cancelled) {
          setCompanyResults(next);
        }
      } catch {
        if (!cancelled) {
          const needle = companySearch.trim().toLowerCase();
          setCompanyResults(
            companies
              .filter((company) => !needle || company.name.toLowerCase().includes(needle))
              .slice(0, 40),
          );
        }
      } finally {
        if (!cancelled) {
          setLoadingCompanyResults(false);
        }
      }
    }, 120);

    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
  }, [companyDropdownOpen, companySearch, companies, deal.company_id]);

  // ── Field updates ─────────────────────────────────────────────────────────

  const patchDeal = async (data: Partial<Deal>) => {
    const updated = await dealsApi.patch(deal.id, data);
    onDealUpdated(updated);
  };

  const handleMoveStage = async (newStage: string) => {
    setShowStageMenu(false);
    if (newStage === deal.stage) return;
    const updated = await dealsApi.moveStage(deal.id, newStage);
    onDealUpdated(updated);
    dealsApi.getActivities(deal.id).then(setActivities);
  };

  const handleNameSave = async () => {
    setEditingName(false);
    if (nameVal.trim() && nameVal !== deal.name) {
      await patchDeal({ name: nameVal.trim() });
    }
  };

  // ── Comments ──────────────────────────────────────────────────────────────

  const handleAddComment = async () => {
    if (!comment.trim()) return;
    setSendingComment(true);
    try {
      const act = await dealsApi.addComment(deal.id, comment.trim());
      setActivities((prev) => [act, ...prev]);
      setComment("");
    } finally { setSendingComment(false); }
  };

  const handleDeleteDeal = async () => {
    if (!isAdmin) return;
    const label = deal.pipeline_type === "prospect" ? "prospect" : "deal";
    if (!window.confirm(`Delete this ${label}? This cannot be undone.`)) return;
    await dealsApi.delete(deal.id);
    onDealDeleted?.(deal.id);
    onClose();
  };

  // ── Contact linking ───────────────────────────────────────────────────────

  const searchContacts = async (q: string) => {
    setContactSearch(q);
    if (q.length < 2) { setContactResults([]); return; }
    try {
      // If deal has a company, only show contacts from that company
      const all = await contactsApi.list(0, 200, deal.company_id ?? undefined);
      const lq = q.toLowerCase();
      setContactResults(
        all
          .filter((c) =>
            `${c.first_name} ${c.last_name} ${c.email ?? ""} ${c.title ?? ""}`.toLowerCase().includes(lq) &&
            !dealContacts.some((dc) => dc.contact_id === c.id)
          )
          .slice(0, 15)
      );
    } catch { setContactResults([]); }
  };

  const handleLinkContact = async (contactId: string) => {
    const dc = await dealsApi.addContact(deal.id, contactId, linkRole || undefined);
    setDealContacts((prev) => [dc, ...prev]);
    setShowLinkContact(false);
    setContactSearch("");
    setLinkRole("");
    dealsApi.getActivities(deal.id).then(setActivities);
  };

  const handleUnlinkContact = async (contactId: string) => {
    await dealsApi.removeContact(deal.id, contactId);
    setDealContacts((prev) => prev.filter((dc) => dc.contact_id !== contactId));
  };

  // ── Tags ──────────────────────────────────────────────────────────────────

  const handleAddTag = async () => {
    const tag = tagInput.trim();
    if (!tag || (deal.tags ?? []).includes(tag)) return;
    await patchDeal({ tags: [...(deal.tags ?? []), tag] } as Partial<Deal>);
    setTagInput("");
  };

  const handleRemoveTag = async (tag: string) => {
    await patchDeal({ tags: (deal.tags ?? []).filter((t) => t !== tag) } as Partial<Deal>);
  };

  const stageLabel = stages.find((s) => s.id === deal.stage)?.label ?? deal.stage;
  const emailSyncAddress = deal.email_cc_alias && sharedInbox.includes("@")
    ? (() => {
        const [local, domain] = sharedInbox.split("@");
        return `${local}+${deal.email_cc_alias}@${domain}`;
      })()
    : undefined;

  return (
    <>
      {/* Backdrop */}
      <div
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(15, 23, 42, 0.22)",
          backdropFilter: "blur(3px)",
          zIndex: 50,
        }}
        onClick={onClose}
      />

      {/* Drawer panel */}
      <div style={{
        position: "fixed",
        top: 12,
        right: 12,
        bottom: 12,
        width: "min(860px, calc(100vw - 24px))",
        maxWidth: "100%",
        zIndex: 51,
        background: "#fff",
        border: "1px solid #dfe8f2",
        borderRadius: 22,
        boxShadow: "-18px 0 60px rgba(15, 23, 42, 0.16)",
        display: "flex", flexDirection: "column",
        animation: "slideInRight 0.2s ease-out",
        overflow: "hidden",
      }}>

        {/* ── Header ───────────────────────────────────────────────── */}
        <div style={{
          padding: "22px 28px 18px", borderBottom: "1px solid #e8eef5",
          display: "flex", flexDirection: "column", gap: 12,
          background: "linear-gradient(180deg, #ffffff 0%, #fbfdff 100%)",
        }}>
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
            {/* Name */}
            {editingName ? (
              <input
                autoFocus
                value={nameVal}
                onChange={(e) => setNameVal(e.target.value)}
                onBlur={handleNameSave}
                onKeyDown={(e) => e.key === "Enter" && handleNameSave()}
                style={{
                  fontSize: 20, fontWeight: 700, color: "#1f2d3d", flex: 1,
                  border: "1px solid #b8d0f0", borderRadius: 8, padding: "4px 8px",
                  outline: "none",
                }}
              />
            ) : (
              <h2
                onClick={() => { setEditingName(true); setNameVal(deal.name); }}
                style={{
                  fontSize: 20, fontWeight: 700, color: "#1f2d3d", cursor: "pointer",
                  flex: 1, lineHeight: 1.3,
                }}
                title="Click to edit"
              >
                {deal.name}
              </h2>
            )}
            <button onClick={onClose} style={{ color: "#7a96b0", cursor: "pointer", background: "none", border: "none", marginLeft: 12 }}>
              <X size={20} />
            </button>
          </div>

          <ProvenanceBar
            source={deal.source}
            createdAt={deal.created_at}
            updatedAt={deal.updated_at}
          />

          {/* Stage + engagement badges */}
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            {/* Stage badge with dropdown */}
            <div style={{ position: "relative" }}>
              <button
                onClick={() => setShowStageMenu(!showStageMenu)}
                style={{
                  display: "flex", alignItems: "center", gap: 6,
                  padding: "4px 12px", borderRadius: 8, fontSize: 13, fontWeight: 600,
                  background: "#eaf2ff", color: "#175089", border: "1px solid #c8daf0",
                  cursor: "pointer",
                }}
              >
                {stageLabel}
                <ArrowRight size={12} />
              </button>
              {showStageMenu && (
                <div style={{
                  position: "absolute", top: "100%", left: 0, marginTop: 4,
                  background: "#fff", border: "1px solid #dbe6f2", borderRadius: 12,
                  boxShadow: "0 8px 24px rgba(0,0,0,0.12)", padding: 6, zIndex: 10,
                  minWidth: 200, maxHeight: 320, overflowY: "auto",
                }}>
                  {stages.map((s) => (
                    <button
                      key={s.id}
                      onClick={() => handleMoveStage(s.id)}
                      style={{
                        display: "block", width: "100%", textAlign: "left",
                        padding: "8px 12px", borderRadius: 8, fontSize: 13,
                        cursor: "pointer", border: "none",
                        background: s.id === deal.stage ? "#f0f6ff" : "transparent",
                        color: s.id === deal.stage ? "#175089" : "#2d4258",
                        fontWeight: s.id === deal.stage ? 600 : 400,
                      }}
                      onMouseEnter={(e) => { if (s.id !== deal.stage) e.currentTarget.style.background = "#f8fafc"; }}
                      onMouseLeave={(e) => { if (s.id !== deal.stage) e.currentTarget.style.background = "transparent"; }}
                    >
                      {s.label}
                    </button>
                  ))}
                </div>
              )}
            </div>

            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <EngagementPanel side="rep" timestamp={deal.seller_engagement_at} signal={deal.seller_engagement_signal} reason={deal.seller_engagement_reason} />
              <EngagementPanel side="client" timestamp={deal.client_engagement_at} signal={deal.client_engagement_signal} reason={deal.client_engagement_reason} />
            </div>

            {/* Convert button for prospects */}
            {onConvert && deal.stage === "in_progress" && (
              <button
                className="crm-button primary"
                onClick={() => onConvert(deal)}
                style={{ height: 30, fontSize: 12, marginLeft: "auto" }}
              >
                Convert to Deal
              </button>
            )}

            {/* Create with Zippy — right-aligned, inline with the stage row */}
            <div style={onConvert && deal.stage === "in_progress" ? undefined : { marginLeft: "auto" }}>
              <ZippyDocDropdown
                items={[
                  {
                    label: "Business Proposal",
                    icon: <FileText size={14} />,
                    message: `Create a business proposal for ${deal.company_name ?? "this account"}. Deal stage: ${stageLabel}. AE: ${deal.assigned_rep_name ?? "unassigned"}. Deal value: ${deal.value != null ? formatCurrency(deal.value) : "not set"}.`,
                  },
                  {
                    label: "NDA",
                    icon: <Shield size={14} />,
                    message: `Draft an NDA for ${deal.company_name ?? "this account"}, India jurisdiction. AE: ${deal.assigned_rep_name ?? "unassigned"}.`,
                  },
                  {
                    label: "ROI Analysis",
                    icon: <BarChart2 size={14} />,
                    message: `Generate an ROI analysis for ${deal.company_name ?? "this account"}. AE: ${deal.assigned_rep_name ?? "unassigned"}.`,
                  },
                  {
                    label: "PoC Kickoff",
                    icon: <ClipboardList size={14} />,
                    message: `Create a PoC Kickoff document for ${deal.company_name ?? "this account"}. AE: ${deal.assigned_rep_name ?? "unassigned"}.`,
                  },
                  {
                    label: "PoC Demo PPT",
                    icon: <Presentation size={14} />,
                    message: `Create a PoC Demo PPT for ${deal.company_name ?? "this account"}. AE: ${deal.assigned_rep_name ?? "unassigned"}.`,
                  },
                  {
                    label: "MOM",
                    icon: <FileText size={14} />,
                    message: `Create a MOM for ${deal.company_name ?? "this account"}. AE: ${deal.assigned_rep_name ?? "unassigned"}.`,
                    separatorBefore: true,
                  },
                ]}
              />
            </div>
          </div>
        </div>

        <div style={{ padding: "0 28px", borderBottom: "1px solid #e8eef5", background: "#fff" }}>
          <div style={{ display: "flex", gap: 8, padding: "12px 0 14px" }}>
            {[
              { id: "overview", label: "Overview" },
              { id: "meddpicc", label: `MEDDPICC${deal.meddpicc_score != null ? ` (${deal.meddpicc_score})` : ""}` },
              { id: "activity", label: `Activity (${activities.length})` },
              { id: "emails", label: `Emails${emailThreads.length > 0 ? ` (${emailThreads.length})` : ""}` },
              { id: "tasks", label: "Tasks" },
            ].map((item) => {
              const active = activeTab === item.id;
              return (
                <button
                  key={item.id}
                  onClick={() => setActiveTab(item.id as DrawerTab)}
                  style={{
                    border: active ? "1px solid #bfd6f3" : "1px solid transparent",
                    background: active ? "#f0f6ff" : "transparent",
                    color: active ? "#175089" : "#6f8399",
                    borderRadius: 10,
                    padding: "8px 12px",
                    fontSize: 13,
                    fontWeight: 700,
                    cursor: "pointer",
                  }}
                >
                  {item.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* ── Scrollable body ──────────────────────────────────────── */}
        <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "24px 28px 28px", display: "flex", flexDirection: "column", gap: 24, background: "#fcfdff" }}>
          {activeTab === "overview" ? (
            <>

          {/* ── Fields section ──────────────────────────────────────── */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
            {/* Company */}
            <FieldRow label="Company" icon={<Building2 size={13} />}>
              <div ref={companyDropdownRef} style={{ position: "relative", width: "100%" }}>
                <div
                  onClick={() => { setCompanyDropdownOpen(o => !o); setCompanySearch(""); }}
                  style={{ ...fieldInputStyle, display: "flex", alignItems: "center", justifyContent: "space-between", cursor: "pointer", userSelect: "none" }}
                >
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: (deal.company_id || deal.company_name) ? "#1a202c" : "#a0aec0" }}>
                    {companies.find(c => c.id === deal.company_id)?.name ?? companyResults.find(c => c.id === deal.company_id)?.name ?? deal.company_name ?? "None"}
                  </span>
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none" style={{ flexShrink: 0, marginLeft: 4 }}>
                    <path d="M2 4l4 4 4-4" stroke="#94a3b8" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                </div>
                {companyDropdownOpen && (
                  <div style={{ position: "absolute", top: "calc(100% + 4px)", left: 0, right: 0, background: "#fff", border: "1px solid #e2eaf2", borderRadius: 10, boxShadow: "0 4px 16px rgba(0,0,0,0.10)", zIndex: 200, overflow: "hidden" }}>
                    <div style={{ padding: "8px 8px 4px", borderBottom: "1px solid #f1f5f9" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 6, background: "#f8fafc", borderRadius: 7, padding: "0 8px" }}>
                        <Search size={12} color="#94a3b8" />
                        <input
                          autoFocus
                          value={companySearch}
                          onChange={e => setCompanySearch(e.target.value)}
                          placeholder="Search companies..."
                          style={{ border: "none", outline: "none", background: "transparent", fontSize: 12, padding: "6px 0", width: "100%" }}
                        />
                      </div>
                    </div>
                    <div style={{ maxHeight: 200, overflowY: "auto" }}>
                      <div
                        onClick={() => { patchDeal({ company_id: undefined } as Partial<Deal>); setCompanyDropdownOpen(false); }}
                        style={{ padding: "8px 12px", fontSize: 13, color: "#a0aec0", cursor: "pointer" }}
                        onMouseEnter={e => (e.currentTarget.style.background = "#f8fafc")}
                        onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
                      >
                        None
                      </div>
                      {loadingCompanyResults ? (
                        <div style={{ padding: "10px 12px", fontSize: 12, color: "#7a96b0" }}>
                          Searching accounts...
                        </div>
                      ) : companyResults.length === 0 ? (
                        <div style={{ padding: "10px 12px", fontSize: 12, color: "#7a96b0" }}>
                          No matching accounts found.
                        </div>
                      ) : companyResults.map(c => (
                          <div
                            key={c.id}
                            onClick={() => { patchDeal({ company_id: c.id } as Partial<Deal>); setCompanyDropdownOpen(false); }}
                            style={{ padding: "8px 12px", fontSize: 13, cursor: "pointer", background: deal.company_id === c.id ? "#eff6ff" : "transparent", color: deal.company_id === c.id ? "#2563eb" : "#1a202c", fontWeight: deal.company_id === c.id ? 500 : 400 }}
                            onMouseEnter={e => { if (deal.company_id !== c.id) e.currentTarget.style.background = "#f8fafc"; }}
                            onMouseLeave={e => { if (deal.company_id !== c.id) e.currentTarget.style.background = "transparent"; }}
                          >
                            {c.name}
                          </div>
                        ))}
                    </div>
                  </div>
                )}
              </div>
            </FieldRow>

            {/* Assigned rep */}
            <FieldRow label="Assigned" icon={<UserCircle2 size={13} />}>
              <select
                value={deal.assigned_to_id ?? ""}
                onChange={(e) => patchDeal({ assigned_to_id: e.target.value || undefined } as Partial<Deal>)}
                style={{ ...fieldInputStyle }}
              >
                <option value="">Unassigned</option>
                {users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
              </select>
            </FieldRow>

            {/* Amount */}
            <FieldRow label="Amount" icon={<span style={{ fontSize: 13, fontWeight: 700 }}>$</span>}>
              <input
                type="number"
                defaultValue={deal.value ?? ""}
                onBlur={(e) => patchDeal({ value: e.target.value ? Number(e.target.value) : undefined } as Partial<Deal>)}
                style={{ ...fieldInputStyle }}
                placeholder="0"
              />
            </FieldRow>

            {/* Close date */}
            <FieldRow label="Close Date" icon={<CalendarDays size={13} />}>
              <input
                type="date"
                defaultValue={deal.close_date_est ?? ""}
                onChange={(e) => patchDeal({ close_date_est: e.target.value || undefined } as Partial<Deal>)}
                style={{ ...fieldInputStyle }}
              />
            </FieldRow>

            {/* Health */}
            <FieldRow label="Health" icon={<span style={{ width: 10, height: 10, borderRadius: "50%", background: deal.health === "green" ? "#22c55e" : deal.health === "yellow" ? "#f59e0b" : "#ef4444" }} />}>
              <select
                value={deal.health}
                onChange={(e) => patchDeal({ health: e.target.value })}
                style={{ ...fieldInputStyle }}
              >
                <option value="green">Green</option>
                <option value="yellow">Yellow</option>
                <option value="red">Red</option>
              </select>
            </FieldRow>

            {/* Geography */}
            <FieldRow label="Geography" icon={<Globe size={13} />}>
              <select
                value={deal.geography ?? ""}
                onChange={(e) => patchDeal({ geography: e.target.value || undefined } as Partial<Deal>)}
                style={{ ...fieldInputStyle }}
              >
                <option value="">Select region</option>
                <option value="US">US</option>
                <option value="Americas">Americas</option>
                <option value="India">India</option>
                <option value="APAC">APAC</option>
                <option value="Rest of World">Rest of World</option>
              </select>
            </FieldRow>

            {/* Source */}
            <FieldRow label="Source" icon={<Zap size={13} />}>
              <select
                value={deal.source ?? ""}
                onChange={(e) => patchDeal({ source: e.target.value || undefined } as Partial<Deal>)}
                style={{ ...fieldInputStyle }}
              >
                <option value="">Select source</option>
                <option value="inbound">Inbound</option>
                <option value="outbound">Outbound</option>
                <option value="referral">Referral</option>
                <option value="partner">Partner</option>
                <option value="event">Event</option>
                <option value="cold_call">Cold Call</option>
                <option value="linkedin">LinkedIn</option>
              </select>
            </FieldRow>

            {/* Account Priority Tag */}
            {deal.company_id && (
              <FieldRow label="Account Priority" icon={<Tag size={13} />}>
                <select
                  value={localPriorityTag ?? ""}
                  onChange={(e) => {
                    const prev = localPriorityTag;
                    const next = (e.target.value || null) as "P0" | "P1" | "P2" | null;
                    setLocalPriorityTag(next);
                    console.log("[priority] patching company_id=", deal.company_id, "next=", next);
                    companiesApi.patch(deal.company_id!, { priority_tag: next } as Partial<Company>)
                      .then((updated) => { console.log("[priority] patch ok, tag=", updated.priority_tag); onCompanyUpdated?.(updated); })
                      .catch((err) => { console.error("[priority] patch failed:", err?.message ?? err); setLocalPriorityTag(prev); });
                  }}
                  style={{
                    ...fieldInputStyle,
                    color: localPriorityTag === "P0" ? "#be123c" : localPriorityTag === "P1" ? "#c2410c" : localPriorityTag === "P2" ? "#15803d" : undefined,
                    fontWeight: localPriorityTag ? 700 : undefined,
                  }}
                >
                  <option value="">No priority</option>
                  <option value="P0">P0</option>
                  <option value="P1">P1</option>
                  <option value="P2">P2</option>
                </select>
              </FieldRow>
            )}
            {/* Commit to Deal */}
            <FieldRow label="Commit to Deal" icon={<span style={{ fontSize: 13 }}>✓</span>}>
              <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", height: 36, padding: "0 10px", borderRadius: 8, border: deal.commit_to_deal ? "1.5px solid #bbf7d0" : "1px solid #dbe6f2", background: deal.commit_to_deal ? "#f0fdf4" : "#f8fafc" }}>
                <input
                  type="checkbox"
                  checked={deal.commit_to_deal ?? false}
                  onChange={(e) => patchDeal({ commit_to_deal: e.target.checked } as Partial<Deal>)}
                  style={{ width: 14, height: 14, accentColor: "#22c55e", cursor: "pointer" }}
                />
                <span style={{ fontSize: 12, fontWeight: 600, color: deal.commit_to_deal ? "#15803d" : "#7a96b0" }}>
                  {deal.commit_to_deal ? "Committed" : "Not committed"}
                </span>
              </label>
            </FieldRow>
          </div>

          {/* Next Step */}
          <div>
            <div style={{ fontSize: 12, fontWeight: 600, color: "#5e738b", marginBottom: 8, display: "flex", alignItems: "center", gap: 5 }}>
              <Navigation size={12} /> Next Step
            </div>
            <input
              type="text"
              defaultValue={deal.next_step ?? ""}
              onBlur={(e) => patchDeal({ next_step: e.target.value || undefined } as Partial<Deal>)}
              placeholder="e.g. Send pricing proposal by Friday"
              style={{
                width: "100%", height: 38, borderRadius: 10,
                border: "1px solid #dbe6f2", padding: "0 12px",
                fontSize: 13, outline: "none",
              }}
            />
          </div>

          <div>
            <div style={{ fontSize: 12, fontWeight: 600, color: "#5e738b", marginBottom: 8, display: "flex", alignItems: "center", gap: 5 }}>
              <Mail size={12} /> Email Sync CC
            </div>
            <div
              style={{
                width: "100%",
                minHeight: 42,
                borderRadius: 10,
                border: "1px solid #dbe6f2",
                padding: "10px 12px",
                fontSize: 13,
                background: "#f8fbff",
                color: "#2d4258",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 12,
                flexWrap: "wrap",
              }}
            >
              <span style={{ fontWeight: 700 }}>{emailSyncAddress ?? "Alias unavailable"}</span>
              {emailSyncAddress ? (
                <button
                  type="button"
                  onClick={() => navigator.clipboard?.writeText(emailSyncAddress)}
                  style={{
                    borderRadius: 8,
                    border: "1px solid #c8daf0",
                    background: "#eef5ff",
                    color: "#175089",
                    padding: "6px 10px",
                    fontSize: 12,
                    fontWeight: 700,
                    cursor: "pointer",
                  }}
                >
                  Copy
                </button>
              ) : null}
            </div>
            <div style={{ marginTop: 6, fontSize: 12, color: "#7a96b0", lineHeight: 1.5 }}>
              Ask reps to CC this exact address on client threads. Beacon uses the text after the <code>+</code> to map the email to this deal before any fallback matching.
            </div>
          </div>

          {/* Tags */}
          <div>
            <div style={{ fontSize: 12, fontWeight: 600, color: "#5e738b", marginBottom: 8, display: "flex", alignItems: "center", gap: 5 }}>
              <Tag size={12} /> Tags
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
              {(deal.tags ?? []).map((tag) => (
                <span key={tag} style={{
                  fontSize: 12, padding: "3px 10px", borderRadius: 8,
                  background: "#f8f0ff", color: "#6b46a0", border: "1px solid #e8d8f8",
                  display: "flex", alignItems: "center", gap: 4,
                }}>
                  {tag}
                  <button onClick={() => handleRemoveTag(tag)} style={{
                    background: "none", border: "none", cursor: "pointer", color: "#a78bfa",
                    padding: 0, display: "flex",
                  }}>
                    <X size={11} />
                  </button>
                </span>
              ))}
              <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <input
                  value={tagInput}
                  onChange={(e) => setTagInput(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleAddTag()}
                  placeholder="Add tag..."
                  style={{
                    width: 100, height: 28, borderRadius: 8, border: "1px solid #e2eaf2",
                    padding: "0 8px", fontSize: 12, outline: "none",
                  }}
                />
              </div>
            </div>
          </div>

          {/* Description */}
          <div>
            <div style={{ fontSize: 12, fontWeight: 600, color: "#5e738b", marginBottom: 8 }}>Description</div>
            <textarea
              defaultValue={deal.description ?? ""}
              onBlur={(e) => patchDeal({ description: e.target.value || undefined } as Partial<Deal>)}
              placeholder="Add notes about this deal..."
              style={{
                width: "100%", minHeight: 80, borderRadius: 12, border: "1px solid #dbe6f2",
                padding: 12, fontSize: 13, resize: "vertical", outline: "none",
                fontFamily: "inherit",
              }}
            />
          </div>

          {/* ── Contacts section ───────────────────────────────────── */}
          <div>
            <div style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              marginBottom: 12,
            }}>
              <span style={{ fontSize: 14, fontWeight: 700, color: "#1f2d3d" }}>
                People on this deal ({dealContacts.length})
              </span>
              <button
                onClick={() => setShowLinkContact(true)}
                style={{
                  display: "flex", alignItems: "center", gap: 4,
                  padding: "4px 10px", borderRadius: 8, fontSize: 12, fontWeight: 600,
                  background: "#f0f6ff", color: "#175089", border: "1px solid #c8daf0",
                  cursor: "pointer",
                }}
              >
                <Plus size={12} /> Link Contact
              </button>
            </div>

            {showLinkContact && (
              <div style={{
                marginBottom: 12, padding: 14, borderRadius: 12,
                border: "1px solid #dbe6f2", background: "#f9fbfe",
              }}>
                <input
                  autoFocus
                  placeholder="Search contacts..."
                  value={contactSearch}
                  onChange={(e) => searchContacts(e.target.value)}
                  style={{
                    width: "100%", height: 36, borderRadius: 10,
                    border: "1px solid #dbe6f2", padding: "0 12px", fontSize: 13, outline: "none",
                    marginBottom: 8,
                  }}
                />
                <select
                  value={linkRole}
                  onChange={(e) => setLinkRole(e.target.value)}
                  style={{
                    width: "100%", height: 32, borderRadius: 8, border: "1px solid #dbe6f2",
                    padding: "0 10px", fontSize: 12, background: "#fff", marginBottom: 8,
                  }}
                >
                  <option value="">No role</option>
                  <option value="champion">Champion</option>
                  <option value="economic_buyer">Economic Buyer</option>
                  <option value="technical_evaluator">Technical Evaluator</option>
                  <option value="blocker">Blocker</option>
                  <option value="influencer">Influencer</option>
                </select>
                {contactResults.length > 0 && (
                  <div style={{ maxHeight: 160, overflowY: "auto" }}>
                    {contactResults.map((c) => (
                      <button
                        key={c.id}
                        onClick={() => handleLinkContact(c.id)}
                        style={{
                          display: "flex", alignItems: "center", gap: 8, width: "100%",
                          padding: "8px 10px", borderRadius: 8, border: "none",
                          cursor: "pointer", background: "transparent", textAlign: "left",
                        }}
                        onMouseEnter={(e) => e.currentTarget.style.background = "#f0f6ff"}
                        onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
                      >
                        <div className={`flex items-center justify-center rounded-full text-[9px] font-bold ${avatarColor(c.first_name + c.last_name)}`}
                          style={{ width: 24, height: 24, flexShrink: 0 }}>
                          {getInitials(`${c.first_name} ${c.last_name}`)}
                        </div>
                        <div>
                          <div style={{ fontSize: 13, fontWeight: 600, color: "#1f2d3d" }}>{c.first_name} {c.last_name}</div>
                          <div style={{ fontSize: 11, color: "#7a96b0" }}>{c.title ?? c.email}</div>
                        </div>
                      </button>
                    ))}
                  </div>
                )}
                <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 4 }}>
                  <button
                    onClick={() => { setShowLinkContact(false); setContactSearch(""); setContactResults([]); }}
                    style={{ fontSize: 12, color: "#7a96b0", cursor: "pointer", background: "none", border: "none" }}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {dealContacts.length === 0 && !showLinkContact ? (
              <div style={{ fontSize: 13, color: "#94a3b8", padding: "12px 0" }}>No contacts linked yet.</div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {dealContacts.map((dc) => {
                  const name = `${dc.first_name ?? ""} ${dc.last_name ?? ""}`.trim();
                  const ps = PERSONA_STYLE[dc.persona ?? ""] ?? { bg: "#edf3f9", color: "#546679" };
                  return (
                    <div key={dc.contact_id} style={{
                      display: "flex", alignItems: "center", gap: 10, padding: "10px 12px",
                      borderRadius: 12, border: "1px solid #e8eef5", background: "#fff",
                    }}>
                      <div className={`flex items-center justify-center rounded-full text-[9px] font-bold ${avatarColor(name)}`}
                        style={{ width: 28, height: 28, flexShrink: 0 }}>
                        {getInitials(name || "?")}
                      </div>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 13, fontWeight: 600, color: "#1f2d3d" }}>{name}</div>
                        <div style={{ fontSize: 11, color: "#7a96b0" }}>{dc.title ?? dc.email}</div>
                      </div>
                      {dc.persona && (
                        <span style={{
                          fontSize: 10, fontWeight: 600, padding: "2px 8px", borderRadius: 6,
                          background: ps.bg, color: ps.color,
                        }}>
                          {dc.persona.replace(/_/g, " ")}
                        </span>
                      )}
                      {dc.role && (
                        <span style={{
                          fontSize: 10, fontWeight: 600, padding: "2px 8px", borderRadius: 6,
                          background: "#e8f0fb", color: "#175089",
                        }}>
                          {dc.role.replace(/_/g, " ")}
                        </span>
                      )}
                      <button
                        onClick={() => handleUnlinkContact(dc.contact_id)}
                        style={{ color: "#c8d2dd", cursor: "pointer", background: "none", border: "none" }}
                        title="Remove"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Company prospects not yet linked to this deal */}
            {(() => {
              const linkedIds = new Set(dealContacts.map((dc) => dc.contact_id));
              const unlinked = companyContacts.filter((c) => !linkedIds.has(c.id));
              if (unlinked.length === 0) return null;
              return (
                <div style={{ marginTop: 14 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: "#7a96b0", marginBottom: 8 }}>
                    Company Prospects ({unlinked.length})
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {unlinked.map((c) => {
                      const name = `${c.first_name ?? ""} ${c.last_name ?? ""}`.trim();
                      return (
                        <div key={c.id} style={{
                          display: "flex", alignItems: "center", gap: 10, padding: "8px 12px",
                          borderRadius: 12, border: "1px dashed #dbe6f2", background: "#fafcfe",
                        }}>
                          <div className={`flex items-center justify-center rounded-full text-[9px] font-bold ${avatarColor(name)}`}
                            style={{ width: 24, height: 24, flexShrink: 0 }}>
                            {getInitials(name || "?")}
                          </div>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontSize: 12, fontWeight: 600, color: "#475569" }}>{name}</div>
                            <div style={{ fontSize: 10, color: "#94a3b8" }}>{c.title ?? c.email}</div>
                          </div>
                          <button
                            onClick={async () => {
                              const dc = await dealsApi.addContact(deal.id, c.id, c.persona ?? undefined);
                              setDealContacts((prev) => [dc, ...prev]);
                            }}
                            style={{
                              fontSize: 10, fontWeight: 700, padding: "3px 8px", borderRadius: 6,
                              background: "#eef5ff", color: "#175089", border: "1px solid #c8daf0",
                              cursor: "pointer",
                            }}
                          >
                            Link
                          </button>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })()}
          </div>

          {/* ── Danger zone ──────────────────────────────────────── */}
          {isAdmin && (
            <div style={{ borderTop: "1px solid #fee2e2", paddingTop: 16, marginTop: 8 }}>
              {confirmDelete ? (
                <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 16px", borderRadius: 12, background: "#fff1f2", border: "1px solid #fecaca", flexWrap: "wrap" }}>
                  <AlertTriangle size={16} style={{ color: "#b42336", flexShrink: 0 }} />
                  <span style={{ fontSize: 13, color: "#7f1d1d", fontWeight: 600, flex: 1 }}>This will permanently delete the deal and all activity. Are you sure?</span>
                  <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
                    <button
                      type="button"
                      onClick={() => setConfirmDelete(false)}
                      style={{ height: 32, padding: "0 14px", borderRadius: 8, border: "1px solid #e2eaf2", background: "#fff", color: "#4d6178", fontSize: 13, cursor: "pointer", fontWeight: 500 }}
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      onClick={handleDeleteDeal}
                      style={{ height: 32, padding: "0 14px", borderRadius: 8, border: "none", background: "#b42336", color: "#fff", fontSize: 13, fontWeight: 700, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 6 }}
                    >
                      <Trash2 size={13} />
                      Yes, delete
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => setConfirmDelete(true)}
                  style={{ height: 32, padding: "0 14px", borderRadius: 8, border: "1px solid #fecaca", background: "#fff8f8", color: "#b42336", fontSize: 12, fontWeight: 600, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 6 }}
                >
                  <Trash2 size={13} />
                  Delete this deal
                </button>
              )}
            </div>
          )}

            </>
          ) : activeTab === "meddpicc" ? (
            <MeddpiccPanel
              qualification={deal.qualification}
              autoFilling={autoFillingMeddpicc}
              onAutoFill={async () => {
                setAutoFillingMeddpicc(true);
                try {
                  const updated = await dealsApi.autoFillMeddpicc(deal.id);
                  onDealUpdated(updated);
                  void dealsApi.getActivities(deal.id).then(setActivities).catch(() => {});
                } finally {
                  setAutoFillingMeddpicc(false);
                }
              }}
              onUpdate={async (meddpicc) => {
                const updated = { ...deal.qualification, meddpicc };
                await patchDeal({ qualification: updated } as Partial<Deal>);
              }}
            />
          ) : activeTab === "tasks" ? (
            <TaskCenterModal
              mode="inline"
              entityType="deal"
              entityId={deal.id}
              entityLabel={deal.name}
              onChanged={() => {
                void dealsApi.get(deal.id).then(onDealUpdated).catch(() => {});
                void dealsApi.getActivities(deal.id).then(setActivities).catch(() => {});
              }}
            />
          ) : activeTab === "emails" ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <div style={{ fontSize: 15, fontWeight: 800, color: "#182042", marginBottom: 4 }}>
                    Personal inbox threads
                  </div>
                  <p style={{ fontSize: 13, color: "#7c86a6", margin: 0 }}>
                    Email conversations from connected personal inboxes, mapped to this deal.
                  </p>
                </div>
                <button
                  className="crm-button soft"
                  style={{ fontSize: 13, padding: "6px 12px" }}
                  onClick={() => {
                    setLoadingEmails(true);
                    personalEmailSyncApi.getThreadsForDeal(deal.id)
                      .then((res) => setEmailThreads(res.threads))
                      .catch(() => {})
                      .finally(() => setLoadingEmails(false));
                  }}
                  disabled={loadingEmails}
                >
                  <Mail size={13} />
                  {loadingEmails ? "Loading…" : "Refresh"}
                </button>
              </div>

              {loadingEmails ? (
                <div style={{ textAlign: "center", padding: "32px 0", color: "#7c86a6" }}>
                  <Loader2 size={22} style={{ margin: "0 auto 8px", display: "block" }} className="animate-spin" />
                  Loading email threads…
                </div>
              ) : emailThreads.length === 0 ? (
                <div style={{ textAlign: "center", padding: "40px 0", color: "#7c86a6" }}>
                  <Mail size={28} style={{ margin: "0 auto 12px", display: "block", opacity: 0.35 }} />
                  <div style={{ fontWeight: 700, marginBottom: 6 }}>No email threads yet</div>
                  <p style={{ fontSize: 13, maxWidth: 320, margin: "0 auto", lineHeight: 1.6 }}>
                    Connect your personal Gmail in Settings → Email Sync to start syncing conversations with this account.
                  </p>
                </div>
              ) : (
                emailThreads.map((thread) => {
                  const isExpanded = expandedThreads.has(thread.thread_id);
                  return (
                    <div
                      key={thread.thread_id}
                      style={{ border: "1px solid #e7eaf5", borderRadius: 12, background: "#fff", overflow: "hidden" }}
                    >
                      <button
                        style={{
                          width: "100%", textAlign: "left", background: "none", border: "none",
                          padding: "14px 16px", cursor: "pointer", display: "flex",
                          justifyContent: "space-between", alignItems: "flex-start", gap: 12,
                        }}
                        onClick={() =>
                          setExpandedThreads((prev) => {
                            const next = new Set(prev);
                            if (next.has(thread.thread_id)) next.delete(thread.thread_id);
                            else next.add(thread.thread_id);
                            return next;
                          })
                        }
                      >
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontWeight: 700, fontSize: 14, color: "#182042", marginBottom: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {thread.subject || "(no subject)"}
                          </div>
                          <div style={{ fontSize: 12, color: "#7c86a6", display: "flex", gap: 10, flexWrap: "wrap" }}>
                            <span>{thread.message_count} message{thread.message_count !== 1 ? "s" : ""}</span>
                            <span>·</span>
                            <span>{thread.latest_at ? new Date(thread.latest_at).toLocaleDateString() : ""}</span>
                            {thread.synced_by_email && (
                              <>
                                <span>·</span>
                                <span style={{ color: "#4b56c7" }}>{thread.synced_by_email}</span>
                              </>
                            )}
                          </div>
                        </div>
                        <ChevronDown
                          size={16}
                          style={{ color: "#7c86a6", flexShrink: 0, transform: isExpanded ? "rotate(180deg)" : "none", transition: "transform 0.15s" }}
                        />
                      </button>

                      {isExpanded && (
                        <div style={{ borderTop: "1px solid #f0f2f8", display: "flex", flexDirection: "column", gap: 0 }}>
                          {thread.messages.map((msg, idx) => (
                            <div
                              key={msg.id}
                              style={{
                                padding: "14px 16px",
                                borderBottom: idx < thread.messages.length - 1 ? "1px solid #f0f2f8" : "none",
                                background: idx % 2 === 0 ? "#fafbff" : "#fff",
                              }}
                            >
                              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6, gap: 8 }}>
                                <div style={{ fontSize: 13, fontWeight: 700, color: "#182042" }}>
                                  {msg.from_addr}
                                </div>
                                <div style={{ fontSize: 12, color: "#7c86a6", flexShrink: 0 }}>
                                  {msg.created_at ? new Date(msg.created_at).toLocaleString() : ""}
                                </div>
                              </div>
                              {msg.ai_summary && (
                                <div style={{
                                  display: "flex", alignItems: "flex-start", gap: 8, marginBottom: 8,
                                  padding: "8px 10px", borderRadius: 8, background: "#f0f4ff", border: "1px solid #dde4f8",
                                }}>
                                  <Sparkles size={13} style={{ color: "#4b56c7", marginTop: 1, flexShrink: 0 }} />
                                  <span style={{ fontSize: 13, color: "#3b4dc8", lineHeight: 1.5 }}>{msg.ai_summary}</span>
                                </div>
                              )}
                              {msg.intent_detected && (
                                <div style={{
                                  display: "inline-flex", alignItems: "center", gap: 6, marginBottom: 8,
                                  padding: "3px 10px", borderRadius: 20, fontSize: 12, fontWeight: 700,
                                  background: "#e8f8ee", color: "#217a49", border: "1px solid #c3e8d4",
                                }}>
                                  <Zap size={11} />
                                  {msg.intent_detected.replace(/_/g, " ").replace("move deal stage:", "Stage: ").replace(":", " → ")}
                                </div>
                              )}
                              <p style={{ fontSize: 13, color: "#4a5568", lineHeight: 1.6, margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                                {msg.body_preview || "(empty)"}
                                {msg.body_preview && msg.body_preview.length >= 299 && <span style={{ color: "#7c86a6" }}>…</span>}
                              </p>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          ) : (
            <ActivityPanel
              activities={activities}
              comment={comment}
              sendingComment={sendingComment}
              onCommentChange={setComment}
              onAddComment={handleAddComment}
              onMoveToPoc={deal.stage !== "poc_agreed" && deal.stage !== "poc_wip" && deal.stage !== "poc_done" ? async () => {
                const updated = await dealsApi.moveStage(deal.id, "poc_agreed");
                onDealUpdated(updated);
                setActivities((current) => current);
              } : undefined}
              pocEligible={deal.stage !== "poc_agreed" && deal.stage !== "poc_wip" && deal.stage !== "poc_done"}
            />
          )}
        </div>
      </div>

      {/* Animation keyframes */}
      <style>{`
        @keyframes slideInRight {
          from { transform: translateX(100%); }
          to { transform: translateX(0); }
        }
      `}</style>
    </>
  );
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function FieldRow({ label, icon, children }: { label: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11, fontWeight: 600, color: "#7a96b0", marginBottom: 4 }}>
        {icon} {label}
      </div>
      {children}
    </div>
  );
}

// ── MEDDPICC Scorecard Panel ────────────────────────────────────────────────

function MeddpiccPanel({
  qualification,
  autoFilling,
  onAutoFill,
  onUpdate,
}: {
  qualification?: DealQualification;
  autoFilling: boolean;
  onAutoFill: () => Promise<void>;
  onUpdate: (meddpicc: Record<string, number>) => Promise<void>;
}) {
  const meddpicc = (qualification?.meddpicc ?? {}) as Record<string, number>;
  const aiDimensions = qualification?.meddpicc_ai?.dimensions ?? {};
  const aiGeneratedAt = qualification?.meddpicc_ai?.generated_at;
  const aiSignals = qualification?.meddpicc_ai?.signals_used;

  const handleChange = (key: string, value: number) => {
    onUpdate({ ...meddpicc, [key]: value });
  };

  const total = MEDDPICC_DIMENSIONS.reduce((sum, d) => sum + (meddpicc[d.key] ?? 0), 0);
  const filled = MEDDPICC_DIMENSIONS.filter((d) => (meddpicc[d.key] ?? 0) > 0).length;
  const pct = filled > 0 ? Math.round((total / 24) * 100) : 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Score summary bar */}
      <div style={{
        display: "flex", alignItems: "center", gap: 16,
        padding: "16px 20px", borderRadius: 14,
        background: "linear-gradient(135deg, #f8fafc 0%, #f0f6ff 100%)",
        border: "1px solid #e2eaf2",
      }}>
        <div style={{
          width: 52, height: 52, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 18, fontWeight: 800,
          background: pct >= 75 ? "#dcfce7" : pct >= 50 ? "#fef9c3" : pct > 0 ? "#fef2f2" : "#f1f5f9",
          color: pct >= 75 ? "#166534" : pct >= 50 ? "#854d0e" : pct > 0 ? "#991b1b" : "#94a3b8",
          border: `2px solid ${pct >= 75 ? "#bbf7d0" : pct >= 50 ? "#fde68a" : pct > 0 ? "#fecaca" : "#e2e8f0"}`,
        }}>
          {pct > 0 ? pct : "—"}
        </div>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, color: "#1f2d3d" }}>
            MEDDPICC Score
          </div>
          <div style={{ fontSize: 12, color: "#6b7f96", marginTop: 2 }}>
            {filled}/8 dimensions scored · {total}/24 points
          </div>
          {aiGeneratedAt && (
            <div style={{ fontSize: 11, color: "#7a8ca1", marginTop: 6 }}>
              Beacon AI used {aiSignals?.contacts ?? 0} contacts and {aiSignals?.activities ?? 0} recent signals to draft this score.
            </div>
          )}
        </div>
        <div style={{ marginLeft: "auto" }}>
          <button
            type="button"
            onClick={() => void onAutoFill()}
            disabled={autoFilling}
            style={{
              height: 36,
              padding: "0 14px",
              borderRadius: 10,
              border: "1px solid #ffc8b4",
              background: autoFilling ? "#fff7f2" : "#fff3ec",
              color: "#b85024",
              fontSize: 12,
              fontWeight: 800,
              cursor: autoFilling ? "default" : "pointer",
              display: "inline-flex",
              alignItems: "center",
              gap: 7,
            }}
          >
            {autoFilling ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
            {autoFilling ? "Refreshing..." : "Auto-fill with Beacon AI"}
          </button>
        </div>
      </div>

      {/* Dimension cards */}
      {MEDDPICC_DIMENSIONS.map((dim) => {
        const val = meddpicc[dim.key] ?? 0;
        const aiMeta = aiDimensions[dim.key];
        return (
          <div key={dim.key} style={{
            padding: "14px 18px", borderRadius: 12,
            border: "1px solid #e8eef5", background: "#fff",
          }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
              <div>
                <span style={{ fontSize: 13, fontWeight: 700, color: "#1f2d3d" }}>
                  {dim.label}
                </span>
                <span style={{
                  marginLeft: 8, fontSize: 10, fontWeight: 600,
                  padding: "2px 7px", borderRadius: 5,
                  background: `${MEDDPICC_LEVEL_COLORS[val]}18`,
                  color: MEDDPICC_LEVEL_COLORS[val],
                }}>
                  {MEDDPICC_LEVEL_LABELS[val]}
                </span>
              </div>
            </div>
            <div style={{ fontSize: 11, color: "#7a8ca1", marginBottom: 10 }}>
              {dim.desc}
            </div>
            {aiMeta?.reason && (
              <div style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                flexWrap: "wrap",
                marginBottom: 12,
                padding: "9px 10px",
                borderRadius: 10,
                background: "#f8fafc",
                border: "1px solid #e7eef6",
              }}>
                <span style={{
                  fontSize: 10,
                  fontWeight: 800,
                  padding: "2px 8px",
                  borderRadius: 999,
                  background: "#eef4ff",
                  color: "#3555c4",
                }}>
                  Beacon AI
                </span>
                {aiMeta.confidence && (
                  <span style={{
                    fontSize: 10,
                    fontWeight: 800,
                    padding: "2px 8px",
                    borderRadius: 999,
                    background: aiMeta.confidence === "high" ? "#e8fbf1" : aiMeta.confidence === "medium" ? "#fff8df" : "#f3f4f6",
                    color: aiMeta.confidence === "high" ? "#18794e" : aiMeta.confidence === "medium" ? "#946200" : "#667085",
                  }}>
                    {aiMeta.confidence} confidence
                  </span>
                )}
                <span style={{ fontSize: 11, color: "#55687d", lineHeight: 1.6 }}>
                  {aiMeta.reason}
                </span>
              </div>
            )}
            {/* Level selector buttons */}
            <div style={{ display: "flex", gap: 6 }}>
              {MEDDPICC_LEVEL_LABELS.map((label, idx) => (
                <button
                  key={idx}
                  onClick={() => handleChange(dim.key, idx)}
                  style={{
                    flex: 1, padding: "6px 0", borderRadius: 8, fontSize: 11, fontWeight: 600,
                    cursor: "pointer", transition: "all 0.15s",
                    border: idx === val ? `2px solid ${MEDDPICC_LEVEL_COLORS[idx]}` : "1px solid #e2e8f0",
                    background: idx === val ? `${MEDDPICC_LEVEL_COLORS[idx]}14` : "#fff",
                    color: idx === val ? MEDDPICC_LEVEL_COLORS[idx] : "#94a3b8",
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Activity Panel ──────────────────────────────────────────────────────────

function ActivityPanel({
  activities,
  comment,
  sendingComment,
  onCommentChange,
  onAddComment,
  onMoveToPoc,
  pocEligible,
}: {
  activities: Activity[];
  comment: string;
  sendingComment: boolean;
  onCommentChange: (value: string) => void;
  onAddComment: () => void;
  onMoveToPoc?: () => Promise<void>;
  pocEligible?: boolean;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, minHeight: "100%" }}>
      <div style={{
        padding: "18px 18px 16px",
        borderRadius: 16,
        border: "1px solid #dbe6f2",
        background: "linear-gradient(180deg, #fbfdff 0%, #f6faff 100%)",
      }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 12 }}>
          <div>
            <div style={{ fontSize: 16, fontWeight: 700, color: "#1f2d3d" }}>Activity</div>
            <div style={{ fontSize: 12, color: "#7a96b0", marginTop: 3 }}>
              Log manual notes and review the full deal timeline in one place.
            </div>
          </div>
          <span style={{
            fontSize: 11, fontWeight: 700, padding: "3px 10px", borderRadius: 999,
            background: "#eef4fb", color: "#4d6178", border: "1px solid #d7e2ee",
          }}>
            {activities.length} events
          </span>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <textarea
            value={comment}
            onChange={(e) => onCommentChange(e.target.value)}
            placeholder="Add a note, call outcome, next-step update, or stakeholder context..."
            rows={5}
            style={{
              width: "100%",
              borderRadius: 14,
              border: "1px solid #dbe6f2",
              padding: "12px 14px",
              fontSize: 14,
              resize: "vertical",
              outline: "none",
              fontFamily: "inherit",
              background: "#fff",
              lineHeight: 1.6,
            }}
          />
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button
              onClick={onAddComment}
              disabled={!comment.trim() || sendingComment}
              style={{
                minWidth: 120,
                height: 40,
                borderRadius: 10,
                border: "none",
                background: comment.trim() ? "#175089" : "#e8eef5",
                color: comment.trim() ? "#fff" : "#94a3b8",
                cursor: comment.trim() ? "pointer" : "default",
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 8,
                fontSize: 13,
                fontWeight: 700,
              }}
            >
              <Send size={14} />
              {sendingComment ? "Saving..." : "Add Activity"}
            </button>
          </div>
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {activities.map((act) => (
          <ActivityFeedItem key={act.id} activity={act} onMoveToPoc={onMoveToPoc} pocEligible={pocEligible} />
        ))}
        {activities.length === 0 && (
          <div style={{
            borderRadius: 16,
            border: "1px dashed #d7e2ee",
            background: "#fbfdff",
            padding: "36px 20px",
            textAlign: "center",
            color: "#94a3b8",
          }}>
            <ActivityIcon size={24} style={{ margin: "0 auto 10px", opacity: 0.5 }} />
            <div style={{ fontSize: 14, fontWeight: 600 }}>No activity yet</div>
            <div style={{ fontSize: 12, marginTop: 4 }}>Use the composer above to add the first update for this deal.</div>
          </div>
        )}
      </div>
    </div>
  );
}

function shouldSuggestPoc(activity: Activity) {
  const text = `${activity.ai_summary ?? ""} ${activity.content ?? ""} ${activity.email_subject ?? ""}`.toLowerCase();
  return text.includes("poc") && (
    text.includes("agree") ||
    text.includes("approved") ||
    text.includes("move forward") ||
    text.includes("green light") ||
    text.includes("let's do")
  );
}

function ActivityFeedItem({ activity, onMoveToPoc, pocEligible }: { activity: Activity; onMoveToPoc?: () => Promise<void>; pocEligible?: boolean }) {
  const Icon = ACTIVITY_ICON[activity.type] ?? ActivityIcon;
  const isSystem = activity.type !== "comment";
  const isEmail = activity.type === "email";
  const isTranscript = activity.type === "transcript";
  const isTldvMeeting = activity.source === "tldv" && activity.type === "meeting";
  const actor = activity.user_name || activity.aircall_user_name || activity.source || "System";
  const [expanded, setExpanded] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [movingPoc, setMovingPoc] = useState(false);
  const showPocSuggestion = Boolean(isEmail && pocEligible && !dismissed && shouldSuggestPoc(activity));
  const metadata = (activity.event_metadata ?? {}) as Record<string, unknown>;
  const transcriptText =
    typeof metadata.transcription === "string" && metadata.transcription.trim()
      ? metadata.transcription
      : activity.content ?? "";
  const transcriptTopics = Array.isArray(metadata.topics)
    ? metadata.topics.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    : [];
  const transcriptActionItems = Array.isArray(metadata.action_items)
    ? metadata.action_items.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    : [];
  const hidePlainContent = Boolean(
    isTranscript || (activity.source === "tldv" && activity.type === "meeting" && activity.ai_summary),
  );

  return (
      <div style={{
        padding: isTranscript || isTldvMeeting ? "18px 20px" : "14px 16px",
        borderRadius: 16,
        background: isEmail ? "#fefefe" : isSystem ? "#f7f9fc" : "#fff",
        border: isEmail ? "1px solid #d4e2f4" : "1px solid #e8eef5",
        boxShadow: "0 1px 3px rgba(17,34,68,0.04)",
      }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
        <div style={{
          width: 36,
          height: 36,
          borderRadius: 12,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: isEmail ? "#eef4fb" : isSystem ? "#eaf0f7" : "#eef4fb",
          color: isEmail ? "#175089" : isSystem ? "#60758b" : "#175089",
          flexShrink: 0,
        }}>
          <Icon size={16} />
        </div>
        <div style={{ minWidth: 0, flex: 1 }}>
          {/* Header row */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: "#2d4258", textTransform: "capitalize" }}>
                {activity.type.replace(/_/g, " ")}
              </span>
              <span style={{
                fontSize: 11,
                fontWeight: 600,
                padding: "2px 8px",
                borderRadius: 999,
                background: "#eef4fb",
                color: "#60758b",
              }}>
                {actor}
              </span>
            </div>
              <span style={{ fontSize: 11, color: "#94a3b8" }}>{formatDate(activity.created_at)}</span>
            </div>

            {/* Email-specific rendering */}
          {isEmail && activity.email_subject && (
            <div style={{ marginTop: 8 }}>
              {/* Subject line */}
              <div style={{ fontSize: 13, fontWeight: 600, color: "#1f2d3d", marginBottom: 6 }}>
                {activity.email_subject}
              </div>

              {/* From / To / CC badges */}
              <div style={{ display: "flex", flexDirection: "column", gap: 3, marginBottom: 6 }}>
                {activity.email_from && (
                  <div style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, color: "#5e738b" }}>
                    <span style={{ fontWeight: 600, color: "#7a96b0", minWidth: 30 }}>From</span>
                    <span style={{
                      padding: "1px 6px", borderRadius: 4,
                      background: "#f0f6ff", color: "#2d4258", fontSize: 11,
                    }}>
                      {activity.email_from}
                    </span>
                  </div>
                )}
                {activity.email_to && (
                  <div style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, color: "#5e738b", flexWrap: "wrap" }}>
                    <span style={{ fontWeight: 600, color: "#7a96b0", minWidth: 30 }}>To</span>
                    {activity.email_to.split(", ").map((addr) => (
                      <span key={addr} style={{
                        padding: "1px 6px", borderRadius: 4,
                        background: "#f4f7fa", color: "#48607b", fontSize: 11,
                      }}>
                        {addr}
                      </span>
                    ))}
                  </div>
                )}
                {activity.email_cc && (
                  <div style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, color: "#5e738b", flexWrap: "wrap" }}>
                    <span style={{ fontWeight: 600, color: "#7a96b0", minWidth: 30 }}>CC</span>
                    {activity.email_cc.split(", ").map((addr) => (
                      <span key={addr} style={{
                        padding: "1px 6px", borderRadius: 4,
                        background: "#f4f7fa", color: "#48607b", fontSize: 11,
                      }}>
                        {addr}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              {/* AI Summary (shown first, always visible) */}
              {activity.ai_summary && (
                <div style={{
                  padding: "8px 10px",
                  borderRadius: 8,
                  background: "#f0f6ff",
                  border: "1px solid #d4e2f4",
                  fontSize: 12,
                  color: "#175089",
                  fontWeight: 500,
                  marginBottom: 6,
                }}>
                  {activity.ai_summary}
                </div>
              )}

              {showPocSuggestion && (
                <div style={{
                  marginBottom: 8,
                  padding: "10px 12px",
                  borderRadius: 10,
                  border: "1px solid #bfdbfe",
                  background: "#eff6ff",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 12,
                  flexWrap: "wrap",
                }}>
                  <div style={{ fontSize: 12, color: "#1d4ed8", fontWeight: 700 }}>
                    Buyer sounds aligned on a POC. Move this deal to POC Agreed?
                  </div>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button
                      type="button"
                      onClick={() => setDismissed(true)}
                      style={{
                        borderRadius: 8,
                        border: "1px solid #cbd5e1",
                        background: "#fff",
                        color: "#475569",
                        padding: "6px 10px",
                        fontSize: 12,
                        fontWeight: 700,
                        cursor: "pointer",
                      }}
                    >
                      No
                    </button>
                    <button
                      type="button"
                      disabled={movingPoc}
                      onClick={async () => {
                        if (!onMoveToPoc) return;
                        setMovingPoc(true);
                        try {
                          await onMoveToPoc();
                          setDismissed(true);
                        } finally {
                          setMovingPoc(false);
                        }
                      }}
                      style={{
                        borderRadius: 8,
                        border: "1px solid #2563eb",
                        background: "#2563eb",
                        color: "#fff",
                        padding: "6px 10px",
                        fontSize: 12,
                        fontWeight: 700,
                        cursor: movingPoc ? "wait" : "pointer",
                      }}
                    >
                      {movingPoc ? "Moving..." : "Yes, move to POC"}
                    </button>
                  </div>
                </div>
              )}

              {/* Expandable body */}
              {activity.content && (
                <div>
                  <button
                    type="button"
                    onClick={() => setExpanded(!expanded)}
                    style={{
                      fontSize: 11, color: "#5e738b", fontWeight: 600,
                      background: "none", border: "none", cursor: "pointer",
                      padding: 0, display: "flex", alignItems: "center", gap: 4,
                    }}
                  >
                    <ChevronDown size={12} style={{
                      transform: expanded ? "rotate(180deg)" : "rotate(0deg)",
                      transition: "transform 0.15s ease",
                    }} />
                    {expanded ? "Hide" : "Show"} email body
                  </button>
                  {expanded && (
                    <div style={{
                      marginTop: 6, padding: "10px 12px",
                      borderRadius: 8, background: "#f8fafc",
                      border: "1px solid #e8eef5",
                      fontSize: 12, color: "#33485f",
                      lineHeight: 1.6, whiteSpace: "pre-wrap",
                      maxHeight: 300, overflowY: "auto",
                    }}>
                      {activity.content}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Non-email AI summary */}
          {!isEmail && activity.ai_summary && (
            <div style={{
              marginTop: 14,
              marginBottom: 10,
              padding: "16px 18px",
              borderRadius: 16,
              background: "#fff6ef",
              border: "1px solid #ffd9c2",
              fontSize: 13,
              color: "#b45309",
              lineHeight: 1.85,
            }}>
              {activity.ai_summary}
            </div>
          )}

          {isTranscript && transcriptText && (
            <TranscriptPreview
              transcript={transcriptText}
              topics={transcriptTopics}
              actionItems={transcriptActionItems}
            />
          )}

          {/* Non-email content */}
          {!isEmail && activity.content && !hidePlainContent && (
            <div style={{
              fontSize: 14,
              color: "#33485f",
              lineHeight: 1.65,
              marginTop: 8,
              whiteSpace: "pre-wrap",
            }}>
              {activity.content}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const fieldInputStyle: React.CSSProperties = {
  width: "100%", height: 34, borderRadius: 10,
  border: "1px solid #e2eaf2", padding: "0 10px",
  fontSize: 13, background: "#fff", outline: "none",
};

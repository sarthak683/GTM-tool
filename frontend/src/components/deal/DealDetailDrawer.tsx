import { memo, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useNavigate } from "react-router-dom";
import {
  X, ChevronDown, Building2, CalendarDays, UserCircle2,
  Send, Tag, Plus, Trash2, ArrowRight, Clock3, Globe, Zap, Navigation,
  Activity as ActivityIcon, Phone, Mail, Video, FileText, AlertTriangle, Search, Loader2, Sparkles,
  Shield, BarChart2, ClipboardList, Presentation,
} from "lucide-react";
import { ZippyDocDropdown } from "../zippy/ZippyDocDropdown";
import { accountSourcingApi, dealsApi, contactsApi, personalEmailSyncApi, tasksApi } from "../../lib/api";
import { getCachedGmailSync } from "../../lib/cachedFetch";
import type { PersonalEmailThread } from "../../lib/api";
import { useAuth } from "../../lib/AuthContext";
import type { Activity, Company, Contact, Deal, DealContact, DealQualification, MeddpiccFieldDetail, TaskItem, User } from "../../types";
import { avatarColor, formatCurrency, formatDate, getInitials } from "../../lib/utils";
import TaskCenterModal from "../tasks/TaskCenterModal";
import TranscriptPreview from "../activity/TranscriptPreview";
import ProvenanceBar from "../ProvenanceBar";
import UnifiedTimeline from "../UnifiedTimeline";
import ReplyComposer, { type ReplyContext } from "../ReplyComposer";
import DealCallLogger from "./DealCallLogger";

interface Props {
  deal: Deal;
  companies: Company[];
  users: User[];
  stages: { id: string; label: string; group: string }[];
  onClose: () => void;
  onDealUpdated: (deal: Deal) => void;
  onDealDeleted?: (dealId: string) => void;
  onConvert?: (deal: Deal) => void;
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

type DrawerTab = "overview" | "meddpicc" | "activity" | "timeline" | "tasks" | "emails";

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

function formatMeddpiccChangeReason(value?: string) {
  return value ? value.replace(/_/g, " ") : "";
}

// Engagement timestamps are naive-UTC (no trailing Z); a bare `new Date()` reads
// them as LOCAL, shifting Active/Watch/Stale + "Xh ago" by the UTC offset. Force UTC.
function utcMs(timestamp: string): number {
  return new Date(timestamp.endsWith("Z") ? timestamp : `${timestamp}Z`).getTime();
}

function engagementTone(timestamp?: string) {
  if (!timestamp) {
    return { label: "No signal", background: "#f8fafc", color: "#7a8ca1", border: "#d9e3ef", accent: "#cbd5e1" };
  }
  const ageMs = Date.now() - utcMs(timestamp);
  const ageDays = ageMs / (1000 * 60 * 60 * 24);
  if (ageDays <= 3) {
    return { label: "Active", background: "#ecfdf3", color: "#15803d", border: "#bbf7d0", accent: "#22c55e" };
  }
  if (ageDays <= 7) {
    return { label: "Watch", background: "#fff7ed", color: "#c2410c", border: "#fed7aa", accent: "#f59e0b" };
  }
  return { label: "Stale", background: "#fff1f2", color: "#be123c", border: "#fecdd3", accent: "#f43f5e" };
}

function relativeTime(timestamp?: string): string {
  if (!timestamp) return "";
  const ageMs = Date.now() - utcMs(timestamp);
  const ageHours = ageMs / (1000 * 60 * 60);
  if (ageHours < 1) return "just now";
  if (ageHours < 24) return `${Math.max(1, Math.floor(ageHours))}h ago`;
  const ageDays = ageMs / (1000 * 60 * 60 * 24);
  if (ageDays < 1) return "today";
  if (ageDays < 2) return "yesterday";
  return `${Math.floor(ageDays)}d ago`;
}

function formatEditableCurrency(value?: number | null): string {
  if (value == null || Number.isNaN(Number(value))) return "";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number(value));
}

function parseCurrencyInput(value: string): number | undefined {
  const normalized = value.replace(/[$,\s]/g, "");
  if (!normalized) return undefined;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : undefined;
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
            <span style={{ fontWeight: 700 }}>Based on:</span> {basis}{signal?.source_label ? ` · via ${signal.source_label}` : ""}
          </div>
          {signal?.detail ? (
            <div style={{ fontSize: 11, color: "#33485f", lineHeight: 1.45 }}>
              <span style={{ fontWeight: 700 }}>What happened:</span> {signal.detail}
            </div>
          ) : null}
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

/**
 * At-a-glance status strip for the deal Overview — surfaces the facts an AE
 * needs first (amount, close date, stage age, health, and the Next Step with an
 * overdue/due chip) so they don't have to scroll into the form or hop tabs.
 * Read-only summary; editing stays in the Deal Details form below.
 */
function DealAtAGlance({ deal, onPatch, qualificationDue }: { deal: Deal; onPatch: (data: Partial<Deal>) => void; qualificationDue: boolean }) {
  const [draft, setDraft] = useState("");
  const healthColor =
    deal.health === "green" ? "#15803d" : deal.health === "yellow" ? "#c2410c" : deal.health === "red" ? "#be123c" : "#64748b";
  const due = dueLabel(deal.next_step_due_at);
  const stat = (label: string, value: string, color?: string) => (
    <div style={{ minWidth: 78, flex: "1 1 78px" }}>
      <div style={{ fontSize: 9.5, fontWeight: 800, color: "#8295ab", textTransform: "uppercase", letterSpacing: "0.06em" }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 800, color: color || "#16273d", marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{value}</div>
    </div>
  );
  const addNextStep = () => {
    const text = draft.trim();
    if (!text) return;
    onPatch({ next_step: text } as Partial<Deal>);
    setDraft("");
  };
  return (
    <div style={{ border: "1px solid #e3ebf4", borderRadius: 14, background: "#fff", padding: "12px 14px", display: "grid", gap: 10, flexShrink: 0, boxShadow: "0 1px 3px rgba(17,34,68,0.04)" }}>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        {stat("Amount", deal.value != null ? formatCurrency(deal.value) : "—")}
        {stat("Close date", deal.close_date_est ? formatDate(deal.close_date_est) : "—")}
        {stat("Stage age", deal.days_in_stage != null ? `${deal.days_in_stage}d` : "—")}
        {stat("Health", deal.health ? deal.health[0].toUpperCase() + deal.health.slice(1) : "—", healthColor)}
      </div>
      <div style={{ borderTop: "1px solid #eef2f7", paddingTop: 9, display: "grid", gap: 5 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontSize: 9.5, fontWeight: 800, color: "#8295ab", textTransform: "uppercase", letterSpacing: "0.06em", flexShrink: 0 }}>Next step</span>
          {deal.next_step ? (
            <>
              <span style={{ minWidth: 0, flex: 1, fontSize: 12.5, fontWeight: 700, color: "#16273d" }}>{deal.next_step}</span>
              {due ? (
                <span style={{ flexShrink: 0, fontSize: 10.5, fontWeight: 800, borderRadius: 999, padding: "2px 9px", color: due.overdue ? "#be123c" : "#1d4ed8", background: due.overdue ? "#fff1f2" : "#eff6ff", border: `1px solid ${due.overdue ? "#fecdd3" : "#bfdbfe"}` }}>
                  {due.overdue ? "Overdue" : "Due"} · {due.text}
                </span>
              ) : null}
              <button
                type="button"
                onClick={() => onPatch({ next_step: null, next_step_due_at: null } as unknown as Partial<Deal>)}
                title="Mark this next step done"
                style={{ flexShrink: 0, fontSize: 11.5, fontWeight: 800, color: "#15803d", background: "#ecfdf3", border: "1px solid #bbf7d0", borderRadius: 8, padding: "7px 12px", minHeight: 34, cursor: "pointer" }}
              >
                ✓ Done
              </button>
            </>
          ) : (
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") addNextStep(); }}
              onBlur={addNextStep}
              placeholder="Add a next step…"
              style={{ minWidth: 0, flex: 1, fontSize: 12.5, color: "#16273d", border: "1px solid #d5e0ec", borderRadius: 8, padding: "8px 10px", minHeight: 36, outline: "none", fontFamily: "inherit" }}
            />
          )}
        </div>
        {/* Trust signal — only promise a reminder when one will actually fire:
            deal_reminders skips unassigned deals, so gate on assignment. */}
        {deal.next_step && due && deal.assigned_to_id ? (
          <div style={{ fontSize: 10.5, color: "#7a8ea4" }}>
            Beacon will remind {deal.assigned_rep_name || "the owner"} on {due.text}
          </div>
        ) : deal.next_step && due ? (
          <div style={{ fontSize: 10.5, color: "#b08400" }}>
            Assign an owner to get a reminder on {due.text}
          </div>
        ) : null}
      </div>
      {/* Qualification criteria — captured once a deal reaches demo_done.
          Always shown if a note already exists; otherwise only surfaced (and
          nudged) for demo_done-and-later deals. */}
      {(qualificationDue || deal.qualification_reason) ? (
        <div style={{ borderTop: "1px solid #eef2f7", paddingTop: 9, display: "grid", gap: 6 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: 9.5, fontWeight: 800, color: "#8295ab", textTransform: "uppercase", letterSpacing: "0.06em", flexShrink: 0 }}>Qualification criteria</span>
            {qualificationDue && !deal.qualification_reason ? (
              <span style={{ flexShrink: 0, fontSize: 10, fontWeight: 800, borderRadius: 999, padding: "2px 9px", color: "#b08400", background: "#fffbeb", border: "1px solid #fde68a" }}>
                Required after demo
              </span>
            ) : null}
          </div>
          <textarea
            key={`qual-${deal.id}-${deal.qualification_reason ?? ""}`}
            defaultValue={deal.qualification_reason ?? ""}
            onBlur={(e) => {
              const v = e.target.value.trim();
              if (v !== (deal.qualification_reason ?? "")) onPatch({ qualification_reason: v || null } as unknown as Partial<Deal>);
            }}
            placeholder="Why is this deal qualified? Capture the qualification criteria / reason…"
            rows={2}
            style={{ width: "100%", boxSizing: "border-box", fontSize: 12.5, color: "#16273d", border: `1px solid ${qualificationDue && !deal.qualification_reason ? "#fde68a" : "#d5e0ec"}`, borderRadius: 8, padding: "8px 10px", outline: "none", fontFamily: "inherit", resize: "vertical" }}
          />
        </div>
      ) : null}
    </div>
  );
}

/**
 * "Beacon suggests" — historically surfaced the top open AI next-actions
 * (T-STAGE / T-MEDPICC / T-CONTACT …) on the deal Overview. The product is now
 * "manual tasks only": system tasks are no longer generated (backend flag
 * ENABLE_SYSTEM_TASKS) and existing ones are dismissed, so this strip has
 * nothing to show. We keep the component wired (it still respects the same task
 * feed) but disable the fetch so it renders nothing and makes no dead network
 * call. To restore: re-enable system tasks on the backend and flip
 * SYSTEM_TASKS_ENABLED below.
 */
const SYSTEM_TASKS_ENABLED = false;

function BeaconSuggestions({ dealId, onChanged }: { dealId: string; onChanged: () => void }) {
  const [items, setItems] = useState<TaskItem[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = () => {
    if (!SYSTEM_TASKS_ENABLED) {
      setItems([]);
      return;
    }
    tasksApi
      .list("deal", dealId, false, "auto")
      .then((tasks) =>
        setItems(
          tasks
            .filter((t) => t.task_type === "system" && t.task_track === "sales_ai" && t.status === "open")
            .slice(0, 3),
        ),
      )
      .catch(() => setItems([]));
  };
  useEffect(load, [dealId]);

  if (items.length === 0) return null;

  const act = async (task: TaskItem, kind: "accept" | "dismiss") => {
    setBusyId(task.id);
    try {
      if (kind === "accept") await tasksApi.accept(task.id);
      else await tasksApi.update(task.id, { status: "dismissed" });
      load();
      onChanged(); // accept can move the stage / patch the deal → refresh drawer
    } catch {
      // A non-owner viewing someone else's deal gets a 403. Swallow it (don't
      // crash the page) and reload so the card reflects true server state
      // rather than being optimistically removed.
      load();
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div style={{ border: "1px solid #e7defb", borderRadius: 14, background: "linear-gradient(180deg,#faf8ff 0%,#ffffff 70%)", padding: "12px 14px", display: "grid", gap: 10, flexShrink: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
        <Sparkles size={14} color="#7c3aed" />
        <span style={{ fontSize: 11.5, fontWeight: 800, color: "#6d28d9", textTransform: "uppercase", letterSpacing: "0.06em" }}>Beacon suggests</span>
      </div>
      {items.map((task) => {
        const busy = busyId === task.id;
        const applyLabel = task.recommended_action ? "Let Beacon do it" : "Mark reviewed";
        const priorityLabel = String((task.action_payload as Record<string, unknown> | undefined)?.priority_label || "").trim();
        return (
          <div key={task.id} style={{ border: "1px solid #ece6fb", borderRadius: 11, background: "#fff", padding: "10px 12px", display: "grid", gap: 7 }}>
            <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
              <span style={{ minWidth: 0, flex: 1, fontSize: 13, fontWeight: 800, color: "#1f2d3d", lineHeight: 1.35 }}>{task.title}</span>
              {priorityLabel ? (
                <span style={{ flexShrink: 0, fontSize: 9.5, fontWeight: 800, color: "#6d28d9", background: "#f3ecff", border: "1px solid #e3d6fb", borderRadius: 999, padding: "2px 7px" }}>{priorityLabel}</span>
              ) : null}
            </div>
            {task.description ? (
              <div style={{ fontSize: 11.5, color: "#5a6b80", lineHeight: 1.45 }}>{task.description}</div>
            ) : null}
            <div style={{ display: "flex", gap: 8 }}>
              <button
                type="button"
                onClick={() => void act(task, "accept")}
                disabled={busy}
                style={{ flexShrink: 0, padding: "9px 14px", minHeight: 38, borderRadius: 9, border: "none", background: busy ? "#c7b8ee" : "#7c3aed", color: "#fff", fontSize: 12.5, fontWeight: 800, cursor: busy ? "default" : "pointer", display: "inline-flex", alignItems: "center", gap: 6 }}
              >
                {busy ? <Loader2 size={12} style={{ animation: "spin 1s linear infinite" }} /> : <Sparkles size={12} />}
                {applyLabel}
              </button>
              <button
                type="button"
                onClick={() => void act(task, "dismiss")}
                disabled={busy}
                style={{ flexShrink: 0, padding: "9px 14px", minHeight: 38, borderRadius: 9, border: "1px solid #dde5ef", background: "#fff", color: "#5a6b80", fontSize: 12.5, fontWeight: 700, cursor: busy ? "default" : "pointer" }}
              >
                Dismiss
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function DealDetailDrawer({ deal, companies, users, stages, onClose, onDealUpdated, onDealDeleted, onConvert }: Props) {
  const { isAdmin } = useAuth();
  const navigate = useNavigate();
  const [activities, setActivities] = useState<Activity[]>([]);
  const [dealContacts, setDealContacts] = useState<DealContact[]>([]);
  const [activeTab, setActiveTab] = useState<DrawerTab>("overview");
  const [emailThreads, setEmailThreads] = useState<PersonalEmailThread[]>([]);
  const [loadingEmails, setLoadingEmails] = useState(false);
  const [expandedThreads, setExpandedThreads] = useState<Set<string>>(new Set());
  const [replyCtx, setReplyCtx] = useState<ReplyContext | null>(null);
  // Whether the current user's connected Gmail has the send scope. null = unknown/not connected.
  // Drives the "Reconnect to reply" prompt so reps don't hit a send-time error.
  const [sendScopeOk, setSendScopeOk] = useState<boolean | null>(null);
  const [reconnectingGmail, setReconnectingGmail] = useState(false);
  const [stageHistory, setStageHistory] = useState<StageHistoryRow[]>([]);
  const [autoFillingMeddpicc, setAutoFillingMeddpicc] = useState(false);
  const [comment, setComment] = useState("");
  const [sendingComment, setSendingComment] = useState(false);
  const [sharedInbox, setSharedInbox] = useState("zippy@beacon.li");
  const [sharedEmailSyncConnected, setSharedEmailSyncConnected] = useState<boolean | null>(null);
  const [emailDraftTo, setEmailDraftTo] = useState("");
  const [emailDraftSubject, setEmailDraftSubject] = useState(`Following up on ${deal.name}`);
  const [emailDraftBody, setEmailDraftBody] = useState("");
  const [emailDraftCopied, setEmailDraftCopied] = useState(false);

  // Inline editing states
  const [editingName, setEditingName] = useState(false);
  const [nameVal, setNameVal] = useState(deal.name);
  const [amountInput, setAmountInput] = useState(formatEditableCurrency(deal.value));
  const [amountFocused, setAmountFocused] = useState(false);
  const [showStageMenu, setShowStageMenu] = useState(false);
  // Win/loss capture: moving to a closed stage opens a reason prompt before the move.
  const [closeStagePrompt, setCloseStagePrompt] = useState<string | null>(null);
  const [closeReasonDraft, setCloseReasonDraft] = useState("");
  const [closingDeal, setClosingDeal] = useState(false);

  // Link contact
  const [showLinkContact, setShowLinkContact] = useState(false);
  const [contactSearch, setContactSearch] = useState("");
  const [contactResults, setContactResults] = useState<Contact[]>([]);
  const [linkRole, setLinkRole] = useState("");

  // Tag input
  const [tagInput, setTagInput] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  // Per-deal priority tag (P0/P1/P2). Now lives on the deal itself, not the
  // company — one company can have several deals at different priorities.
  const [localPriorityTag, setLocalPriorityTag] = useState<"P0" | "P1" | "P2" | null>(
    (deal.priority_tag ?? null) as "P0" | "P1" | "P2" | null,
  );

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
    // Check whether the rep's Gmail can actually send, so Reply can prompt a
    // reconnect up front instead of failing after they've written the email.
    personalEmailSyncApi.getStatus()
      .then((s) => setSendScopeOk(Boolean(s.has_send_scope)))
      .catch(() => setSendScopeOk(null));
  }, [activeTab, deal.id]);

  const handleReconnectGmail = async () => {
    setReconnectingGmail(true);
    try {
      const { url } = await personalEmailSyncApi.getConnectUrl();
      window.location.assign(url);
    } catch {
      setReconnectingGmail(false);
    }
  };

  useEffect(() => {
    getCachedGmailSync().then((data) => {
      if (data.inbox) setSharedInbox(data.inbox);
      setSharedEmailSyncConnected(Boolean(data.configured));
    }).catch(() => {});
  }, []);

  // Stage journey — refetched when the stage changes so an in-drawer move
  // immediately reflects the new transition.
  useEffect(() => {
    dealsApi.getStageHistory(deal.id).then(setStageHistory).catch(() => setStageHistory([]));
  }, [deal.id, deal.stage]);

  useEffect(() => {
    setActiveTab("overview");
    setComment("");
    setAmountFocused(false);
    setAmountInput(formatEditableCurrency(deal.value));
    setEmailDraftSubject(`Following up on ${deal.name}`);
    setEmailDraftBody("");
    setEmailDraftTo("");
    setEmailDraftCopied(false);
  }, [deal.id]);

  useEffect(() => {
    if (!amountFocused) {
      setAmountInput(formatEditableCurrency(deal.value));
    }
  }, [deal.value, amountFocused]);

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
    dealsApi.getActivities(deal.id).then(setActivities).catch(() => {});
  };

  const handleMoveStage = async (newStage: string) => {
    setShowStageMenu(false);
    if (newStage === deal.stage) return;
    // Capture a win/loss reason at the decisive moment instead of nagging 48h later.
    if (stages.find((s) => s.id === newStage)?.group === "closed") {
      setCloseReasonDraft("");
      setCloseStagePrompt(newStage);
      return;
    }
    const updated = await dealsApi.moveStage(deal.id, newStage);
    onDealUpdated(updated);
    dealsApi.getActivities(deal.id).then(setActivities);
  };

  const confirmCloseMove = async () => {
    if (!closeStagePrompt) return;
    setClosingDeal(true);
    try {
      const reason = closeReasonDraft.trim();
      const outcome = closeStagePrompt === "closed_won" ? "won"
        : /lost|churn|not_a_fit/.test(closeStagePrompt) ? "lost" : "other";
      if (reason) {
        await patchDeal({
          qualification: {
            ...(deal.qualification || {}),
            close_reason: reason,
            close_outcome: outcome,
            closed_reason_at: new Date().toISOString().slice(0, -1),
          },
        } as Partial<Deal>);
      }
      const updated = await dealsApi.moveStage(deal.id, closeStagePrompt);
      onDealUpdated(updated);
      dealsApi.getActivities(deal.id).then(setActivities);
      setCloseStagePrompt(null);
    } finally {
      setClosingDeal(false);
    }
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

  const contactSearchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchContacts = (q: string) => {
    setContactSearch(q);
    if (contactSearchTimer.current) clearTimeout(contactSearchTimer.current);
    if (q.length < 2) { setContactResults([]); return; }
    // Debounce so a fast typist doesn't fire a 200-row fetch per keystroke.
    contactSearchTimer.current = setTimeout(async () => {
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
    }, 300);
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

  const handleLinkAllCompanyContacts = async (contactsToLink: Contact[]) => {
    if (!contactsToLink.length) return;
    const linked = await Promise.all(
      contactsToLink.map((contact) => dealsApi.addContact(deal.id, contact.id, contact.persona ?? undefined)),
    );
    setDealContacts((prev) => [...linked, ...prev]);
    dealsApi.getActivities(deal.id).then(setActivities).catch(() => {});
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
  // Qualification criteria become required once a deal reaches demo_done. Use
  // the configured stage order (the board columns) to decide "at or past
  // demo_done"; if demo_done isn't a configured stage, never force it.
  const demoDoneIdx = stages.findIndex((s) => s.id === "demo_done");
  const currentStageIdx = stages.findIndex((s) => s.id === deal.stage);
  const qualificationDue = demoDoneIdx >= 0 && currentStageIdx >= demoDoneIdx;
  const selectedCompanyName = companies.find(c => c.id === deal.company_id)?.name
    ?? companyResults.find(c => c.id === deal.company_id)?.name
    ?? deal.company_name
    ?? "None";
  const emailSyncAddress = deal.email_cc_alias && sharedInbox.includes("@")
    ? (() => {
        const [local, domain] = sharedInbox.split("@");
        return `${local}+${deal.email_cc_alias}@${domain}`;
      })()
    : undefined;
  const canUseSharedEmailSync = sharedEmailSyncConnected === true && Boolean(emailSyncAddress);
  const emailRecipients = useMemo(
    () => dealContacts.filter((contact) => Boolean(contact.email)).map((contact) => ({
      email: contact.email as string,
      label: `${contact.first_name ?? ""} ${contact.last_name ?? ""}`.trim() || (contact.email as string),
    })),
    [dealContacts],
  );

  const applyEmailTemplate = (kind: "followup" | "recap" | "pricing") => {
    const companyName = selectedCompanyName && selectedCompanyName !== "None" ? selectedCompanyName : deal.company_name || "your team";
    if (kind === "pricing") {
      setEmailDraftSubject(`Pricing next steps for ${companyName}`);
      setEmailDraftBody(`Hi,\n\nSharing the pricing and next-step context for ${companyName}.\n\nRecommended next step:\n\nOpen questions:\n\nBest,\n${users.find((u) => u.id === deal.assigned_to_id)?.name ?? ""}`.trim());
      return;
    }
    if (kind === "recap") {
      setEmailDraftSubject(`Recap and next steps: ${companyName}`);
      setEmailDraftBody(`Hi,\n\nQuick recap from our discussion:\n\n- Current priority:\n- Beacon fit:\n- Agreed next step:\n\nPlease confirm if I captured this correctly.\n\nBest,\n${users.find((u) => u.id === deal.assigned_to_id)?.name ?? ""}`.trim());
      return;
    }
    setEmailDraftSubject(`Following up on ${companyName}`);
    setEmailDraftBody(`Hi,\n\nFollowing up on our conversation around ${companyName}. The next best step from our side is:\n\n\nDoes this still work for you?\n\nBest,\n${users.find((u) => u.id === deal.assigned_to_id)?.name ?? ""}`.trim());
  };

  const copyEmailDraft = async () => {
    const ccLine = canUseSharedEmailSync && emailSyncAddress ? `CC: ${emailSyncAddress}\n` : "";
    const draft = `To: ${emailDraftTo}\n${ccLine}Subject: ${emailDraftSubject}\n\n${emailDraftBody}`;
    await navigator.clipboard?.writeText(draft);
    setEmailDraftCopied(true);
    window.setTimeout(() => setEmailDraftCopied(false), 1800);
  };

  return (
    <>
      {/* Win/loss reason capture — shown when moving to a closed stage. */}
      {closeStagePrompt && (
        <div
          onClick={() => { if (!closingDeal) setCloseStagePrompt(null); }}
          style={{ position: "fixed", inset: 0, zIndex: 9998, background: "rgba(15,23,42,0.5)", display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}
        >
          <div onClick={(e) => e.stopPropagation()} style={{ width: 440, maxWidth: "94vw", background: "#fff", borderRadius: 16, padding: "20px 22px", boxShadow: "0 24px 60px rgba(15,39,68,0.28)" }}>
            <div style={{ fontSize: 16, fontWeight: 800, color: "#0f2744" }}>
              Close · {stages.find((s) => s.id === closeStagePrompt)?.label ?? closeStagePrompt}
            </div>
            <div style={{ fontSize: 12.5, color: "#62748a", margin: "6px 0 12px", lineHeight: 1.5 }}>
              Capture why this deal is closing — it powers win/loss analysis. (Optional, but valuable.)
            </div>
            <textarea
              value={closeReasonDraft}
              onChange={(e) => setCloseReasonDraft(e.target.value)}
              autoFocus
              rows={4}
              placeholder="e.g. Lost to incumbent on price; champion left; chose us for security & TCO…"
              style={{ width: "100%", border: "1px solid #d5e0ec", borderRadius: 10, padding: "9px 11px", fontSize: 13, color: "#0f2744", outline: "none", resize: "vertical", fontFamily: "inherit", boxSizing: "border-box" }}
            />
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 14 }}>
              <button type="button" onClick={() => setCloseStagePrompt(null)} disabled={closingDeal} style={{ padding: "9px 15px", borderRadius: 10, border: "1px solid #d5e0ec", background: "#fff", color: "#3f5065", fontSize: 13, fontWeight: 700, cursor: "pointer" }}>Cancel</button>
              <button type="button" onClick={() => void confirmCloseMove()} disabled={closingDeal} style={{ padding: "9px 16px", borderRadius: 10, border: "none", background: "#1f6feb", color: "#fff", fontSize: 13, fontWeight: 800, cursor: closingDeal ? "default" : "pointer" }}>
                {closingDeal ? "Closing…" : "Confirm close"}
              </button>
            </div>
          </div>
        </div>
      )}
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
          padding: "22px 28px 18px", borderBottom: "1px solid #e4eecf",
          display: "flex", flexDirection: "column", gap: 12,
          background: "linear-gradient(180deg, #f4fbe6 0%, #ffffff 100%)",
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
                  fontSize: 23, fontWeight: 800, color: "#1f2d3d", flex: 1, letterSpacing: "-0.01em",
                  border: "1px solid #cfe89a", borderRadius: 8, padding: "4px 8px",
                  outline: "none",
                }}
              />
            ) : (
              <div style={{ display: "flex", alignItems: "center", gap: 10, flex: 1, minWidth: 0 }}>
                <h2
                  onClick={() => { setEditingName(true); setNameVal(deal.name); }}
                  style={{
                    fontSize: 23, fontWeight: 800, color: "#1f2d3d", cursor: "pointer", letterSpacing: "-0.01em",
                    lineHeight: 1.25, margin: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  }}
                  title="Click to edit"
                >
                  {deal.name}
                </h2>
                {deal.company_id && (
                  <button
                    type="button"
                    onClick={() => navigate(`/account-sourcing/${deal.company_id}`)}
                    title={`Open ${selectedCompanyName} account`}
                    style={{
                      border: "1px solid #cfe89a",
                      background: "#f3fbe3",
                      color: "#4d7c0f",
                      borderRadius: 8,
                      padding: "3px 8px",
                      fontSize: 11,
                      fontWeight: 800,
                      cursor: "pointer",
                      flexShrink: 0,
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 4,
                    }}
                  >
                    <Building2 size={11} />
                    Open
                  </button>
                )}
              </div>
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
                  background: "#f3fbe3", color: "#4d7c0f", border: "1px solid #cfe89a",
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
                        background: s.id === deal.stage ? "#f3fbe3" : "transparent",
                        color: s.id === deal.stage ? "#4d7c0f" : "#2d4258",
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
          {/* nowrap + horizontal scroll so the 6 tabs don't clip on narrow phones */}
          <div style={{ display: "flex", gap: 8, padding: "12px 0 14px", overflowX: "auto", flexWrap: "nowrap", scrollbarWidth: "none" }}>
            {[
              { id: "overview", label: "Overview" },
              { id: "meddpicc", label: `MEDDPICC${deal.meddpicc_score != null ? ` (${deal.meddpicc_score})` : ""}` },
              { id: "activity", label: `Activity (${activities.length})` },
              { id: "timeline", label: "Timeline" },
              { id: "emails", label: `Emails${emailThreads.length > 0 ? ` (${emailThreads.length})` : ""}` },
              { id: "tasks", label: "Tasks" },
            ].map((item) => {
              const active = activeTab === item.id;
              return (
                <button
                  key={item.id}
                  onClick={() => setActiveTab(item.id as DrawerTab)}
                  onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = "#f3f6fa"; }}
                  onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = "transparent"; }}
                  style={{
                    border: active ? "1px solid #cfe89a" : "1px solid transparent",
                    background: active ? "#f3fbe3" : "transparent",
                    color: active ? "#4d7c0f" : "#6f8399",
                    borderRadius: 10,
                    padding: "8px 13px",
                    fontSize: 13,
                    fontWeight: 700,
                    cursor: "pointer",
                    boxShadow: active ? "0 1px 3px rgba(111, 174, 39, 0.18)" : "none",
                    transition: "background 0.12s ease, color 0.12s ease",
                  }}
                >
                  {item.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* ── Scrollable body ──────────────────────────────────────── */}
        <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "24px 28px 28px", display: "flex", flexDirection: "column", gap: 22, background: "#f5f8fc" }}>
          {activeTab === "overview" ? (
            <>

          {/* At-a-glance status so the AE reads the deal in one look. */}
          <DealAtAGlance deal={deal} onPatch={patchDeal} qualificationDue={qualificationDue} />

          {/* Beacon's AI next-actions, surfaced inline so the AE acts without
              digging into the Tasks tab. */}
          <BeaconSuggestions
            dealId={deal.id}
            onChanged={() => {
              void dealsApi.get(deal.id).then(onDealUpdated).catch(() => {});
              dealsApi.getActivities(deal.id).then(setActivities).catch(() => {});
            }}
          />

          {/* Log a call — front and center on Overview so the AE flow is one
              click: open deal → Log a call → record (or type) → Save. */}
          <DealCallLogger
            deal={deal}
            dealContacts={dealContacts}
            onLogged={() => {
              dealsApi.getActivities(deal.id).then(setActivities).catch(() => {});
              // Also refetch the deal so the rep/client engagement cards recompute
              // their signal + "last touch" from the just-logged call.
              void dealsApi.get(deal.id).then(onDealUpdated).catch(() => {});
            }}
            onPatchDeal={patchDeal}
          />

          <SectionLabel>Deal Details</SectionLabel>
          <div style={{ border: "1px solid #e8eef5", borderRadius: 14, padding: "16px 16px 18px", background: "#fff", boxShadow: "0 1px 3px rgba(17,34,68,0.04)" }}>
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
                    {selectedCompanyName}
                  </span>
                  {deal.company_id && (
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        navigate(`/account-sourcing/${deal.company_id}`);
                      }}
                      title={`Open ${selectedCompanyName} account`}
                      style={{
                        marginLeft: "auto",
                        marginRight: 6,
                        border: "1px solid #cfe89a",
                        background: "#f3fbe3",
                        color: "#4d7c0f",
                        borderRadius: 8,
                        padding: "3px 7px",
                        fontSize: 10,
                        fontWeight: 800,
                        cursor: "pointer",
                        flexShrink: 0,
                      }}
                    >
                      Open
                    </button>
                  )}
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none" style={{ flexShrink: 0, marginLeft: 4 }}>
                    <path d="M2 4l4 4 4-4" stroke="#94a3b8" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                </div>
                {!deal.company_id && (
                  <div style={{ marginTop: 6, fontSize: 11, color: "#b7791f", display: "flex", alignItems: "center", gap: 4 }}>
                    <AlertTriangle size={11} /> No account linked — add this account in Account Sourcing and it will map automatically.
                  </div>
                )}
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
                            style={{ padding: "8px 12px", fontSize: 13, cursor: "pointer", background: deal.company_id === c.id ? "#f3fbe3" : "transparent", color: deal.company_id === c.id ? "#6fae27" : "#1a202c", fontWeight: deal.company_id === c.id ? 500 : 400 }}
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

            {/* Assigned AE */}
            <FieldRow label="Assigned AE" icon={<UserCircle2 size={13} />}>
              <select
                value={deal.assigned_to_id ?? ""}
                onChange={(e) => patchDeal({ assigned_to_id: e.target.value || null } as Partial<Deal>)}
                style={{ ...fieldInputStyle }}
              >
                <option value="">Unassigned</option>
                {users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
              </select>
            </FieldRow>

            {/* Assigned SDR — only visible for demo_scheduled, demo_done and qualified_lead */}
            {(deal.stage === "demo_scheduled" || deal.stage === "demo_done" || deal.stage === "qualified_lead") && (
              <FieldRow label="Assigned SDR" icon={<UserCircle2 size={13} />}>
                <select
                  value={deal.sdr_id ?? ""}
                  onChange={(e) => patchDeal({ sdr_id: e.target.value || null } as Partial<Deal>)}
                  style={{ ...fieldInputStyle }}
                >
                  <option value="">Unassigned</option>
                  {users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                </select>
              </FieldRow>
            )}

            {/* Amount */}
            <FieldRow label="Amount" icon={<span style={{ fontSize: 13, fontWeight: 700 }}>$</span>}>
              <input
                type="text"
                inputMode="decimal"
                value={amountInput}
                onFocus={() => {
                  setAmountFocused(true);
                  setAmountInput(deal.value == null ? "" : String(Number(deal.value)));
                }}
                onChange={(e) => setAmountInput(e.target.value)}
                onBlur={(e) => {
                  setAmountFocused(false);
                  const nextValue = parseCurrencyInput(e.target.value);
                  setAmountInput(formatEditableCurrency(nextValue));
                  patchDeal({ value: nextValue } as Partial<Deal>);
                }}
                style={{ ...fieldInputStyle }}
                placeholder="$0.00"
              />
            </FieldRow>

            {/* Close date */}
            <FieldRow label="Date of Meeting" icon={<CalendarDays size={13} />}>
              <input
                type="date"
                defaultValue={deal.close_date_est ?? ""}
                onChange={(e) => patchDeal({ close_date_est: e.target.value || null } as Partial<Deal>)}
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
                onChange={(e) => patchDeal({ geography: e.target.value || null } as Partial<Deal>)}
                style={{ ...fieldInputStyle }}
              >
                <option value="">Unassigned</option>
                <option value="India">India</option>
                <option value="America">America</option>
                <option value="Rest of the World">Rest of the World</option>
              </select>
            </FieldRow>

            {/* Source */}
            <FieldRow label="Source" icon={<Zap size={13} />}>
              <select
                value={deal.source ?? ""}
                onChange={(e) => patchDeal({ source: e.target.value || null } as Partial<Deal>)}
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

            {/* Deal Priority Tag (P0/P1/P2) — per deal, not per company */}
            <FieldRow label="Deal Priority" icon={<Tag size={13} />}>
              <select
                value={localPriorityTag ?? ""}
                onChange={(e) => {
                  const prev = localPriorityTag;
                  const next = (e.target.value || null) as "P0" | "P1" | "P2" | null;
                  setLocalPriorityTag(next);
                  dealsApi.patch(deal.id, { priority_tag: next } as Partial<Deal>)
                    .then((updated) => { onDealUpdated(updated); })
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
          </div>

          <SectionLabel>Next Step &amp; Notes</SectionLabel>
          <div style={{ border: "1px solid #e8eef5", borderRadius: 14, padding: "16px 16px 18px", background: "#fff", boxShadow: "0 1px 3px rgba(17,34,68,0.04)", display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Next Step */}
          <div>
            <div style={{ fontSize: 12, fontWeight: 600, color: "#5e738b", marginBottom: 8, display: "flex", alignItems: "center", gap: 5 }}>
              <Navigation size={12} /> Next Step
            </div>
            <input
              type="text"
              defaultValue={deal.next_step ?? ""}
              onBlur={(e) => patchDeal({ next_step: e.target.value || null } as Partial<Deal>)}
              placeholder="e.g. Send pricing proposal by Friday"
              style={{
                width: "100%", height: 38, borderRadius: 10,
                border: "1px solid #dbe6f2", padding: "0 12px",
                fontSize: 13, outline: "none",
              }}
            />
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8, flexWrap: "wrap" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <Clock3 size={12} style={{ color: "#7a96b0" }} />
                <input
                  type="datetime-local"
                  value={toLocalDatetimeInput(deal.next_step_due_at)}
                  onChange={(e) => patchDeal({ next_step_due_at: fromLocalDatetimeInput(e.target.value) ?? null } as unknown as Partial<Deal>)}
                  title="When the next step is due — Beacon reminds the owner when it passes"
                  style={{ height: 34, borderRadius: 9, border: "1px solid #dbe6f2", padding: "0 10px", fontSize: 12.5, color: "#2d4258", outline: "none" }}
                />
              </div>
              {deal.next_step_due_at && (() => {
                const d = dueLabel(deal.next_step_due_at);
                if (!d) return null;
                return (
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11.5, fontWeight: 700, padding: "3px 9px", borderRadius: 999, background: d.overdue ? "#fef2f2" : "#f3fbe3", color: d.overdue ? "#b91c1c" : "#4d7c0f", border: `1px solid ${d.overdue ? "#fecaca" : "#cfe89a"}` }}>
                    {d.overdue ? "Overdue" : "Due"} · {d.text}
                  </span>
                );
              })()}
              {deal.next_step_due_at && (
                <button type="button" onClick={() => patchDeal({ next_step_due_at: null } as unknown as Partial<Deal>)} style={{ fontSize: 11, fontWeight: 700, color: "#7a96b0", background: "none", border: "none", cursor: "pointer" }}>
                  Clear
                </button>
              )}
            </div>
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
                border: canUseSharedEmailSync ? "1px solid #dbe6f2" : "1px solid #ffd8b4",
                padding: "10px 12px",
                fontSize: 13,
                background: canUseSharedEmailSync ? "#f8fbff" : "#fff8f1",
                color: canUseSharedEmailSync ? "#2d4258" : "#9a4f16",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 12,
                flexWrap: "wrap",
              }}
            >
              <span style={{ fontWeight: 700 }}>
                {canUseSharedEmailSync ? emailSyncAddress : sharedEmailSyncConnected === false ? "Email sync not connected" : "Checking email sync..."}
              </span>
              {canUseSharedEmailSync ? (
                <button
                  type="button"
                  onClick={() => {
                    if (emailSyncAddress) navigator.clipboard?.writeText(emailSyncAddress);
                  }}
                  style={{
                    borderRadius: 8,
                    border: "1px solid #cfe89a",
                    background: "#f3fbe3",
                    color: "#4d7c0f",
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
              {canUseSharedEmailSync
                ? <>Ask reps to CC this exact address on client threads. Beacon uses the text after the <code>+</code> to map the email to this deal before any fallback matching.</>
                : "Connect the shared Gmail mailbox in Settings before asking reps to CC Beacon. Until then, emails will not be captured from this alias."}
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
              onBlur={(e) => patchDeal({ description: e.target.value || null } as Partial<Deal>)}
              placeholder="Add notes about this deal..."
              style={{
                width: "100%", minHeight: 80, borderRadius: 12, border: "1px solid #dbe6f2",
                padding: 12, fontSize: 13, resize: "vertical", outline: "none",
                fontFamily: "inherit",
              }}
            />
          </div>
          </div>

          <SectionLabel>People</SectionLabel>
          {/* ── Contacts section ───────────────────────────────────── */}
          <div style={{ border: "1px solid #e8eef5", borderRadius: 14, padding: "16px 16px 18px", background: "#fff", boxShadow: "0 1px 3px rgba(17,34,68,0.04)" }}>
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
                  background: "#f3fbe3", color: "#4d7c0f", border: "1px solid #cfe89a",
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
                        onMouseEnter={(e) => e.currentTarget.style.background = "#f3fbe3"}
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
                    <div
                      key={dc.contact_id}
                      onClick={() => navigate(`/contacts/${dc.contact_id}`)}
                      title="Open prospect detail"
                      style={{
                      display: "flex", alignItems: "center", gap: 10, padding: "10px 12px",
                      borderRadius: 12, border: "1px solid #e8eef5", background: "#fff",
                      cursor: "pointer",
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
                          background: "#f3fbe3", color: "#4d7c0f",
                        }}>
                          {dc.role.replace(/_/g, " ")}
                        </span>
                      )}
                      <button
                        onClick={(event) => {
                          event.stopPropagation();
                          handleUnlinkContact(dc.contact_id);
                        }}
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
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginBottom: 8 }}>
                    <div>
                      <div style={{ fontSize: 12, fontWeight: 700, color: "#7a96b0" }}>
                        Suggested people from this account ({unlinked.length})
                      </div>
                      <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 2 }}>
                        Auto-linking suggestion based on the deal company.
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => void handleLinkAllCompanyContacts(unlinked)}
                      style={{
                        fontSize: 10,
                        fontWeight: 800,
                        padding: "5px 9px",
                        borderRadius: 8,
                        background: "#f3fbe3",
                        color: "#4d7c0f",
                        border: "1px solid #cfe89a",
                        cursor: "pointer",
                        whiteSpace: "nowrap",
                      }}
                    >
                      Link all
                    </button>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {unlinked.map((c) => {
                      const name = `${c.first_name ?? ""} ${c.last_name ?? ""}`.trim();
                      return (
                        <div
                          key={c.id}
                          onClick={() => navigate(`/contacts/${c.id}`)}
                          title="Open prospect detail"
                          style={{
                          display: "flex", alignItems: "center", gap: 10, padding: "8px 12px",
                          borderRadius: 12, border: "1px dashed #dbe6f2", background: "#fafcfe",
                          cursor: "pointer",
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
                            onClick={async (event) => {
                              event.stopPropagation();
                              const dc = await dealsApi.addContact(deal.id, c.id, c.persona ?? undefined);
                              setDealContacts((prev) => [dc, ...prev]);
                            }}
                            style={{
                              fontSize: 10, fontWeight: 700, padding: "3px 8px", borderRadius: 6,
                              background: "#f3fbe3", color: "#4d7c0f", border: "1px solid #cfe89a",
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

          <SectionLabel>Stage Journey</SectionLabel>
          <div style={{ border: "1px solid #e8eef5", borderRadius: 14, padding: "16px 16px 18px", background: "#fff", boxShadow: "0 1px 3px rgba(17,34,68,0.04)" }}>
            <StageJourney history={stageHistory} deal={deal} stages={stages} />
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
              flags={deal.flags}
              forecastCategory={deal.forecast_category}
              flagCounts={{
                green: deal.flag_green_count,
                yellow: deal.flag_yellow_count,
                red: deal.flag_red_count,
              }}
              flagBlockers={deal.flag_blockers}
              flagYellows={deal.flag_yellows}
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
              onUpdateDetail={async (key, detail) => {
                const existingDetails = deal.qualification?.meddpicc_details ?? {};
                const updated = {
                  ...deal.qualification,
                  meddpicc_details: {
                    ...existingDetails,
                    [key]: detail,
                  },
                };
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
          ) : activeTab === "timeline" ? (
            <UnifiedTimeline scope={{ type: "deal", id: deal.id }} />
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

              <div style={{ border: "1px solid #dde8f4", borderRadius: 14, background: "linear-gradient(180deg, #fbfdff 0%, #ffffff 100%)", padding: 14, display: "grid", gap: 12 }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 800, color: "#182042" }}>Compose email</div>
                    <p style={{ margin: "3px 0 0", fontSize: 12, color: "#7c86a6" }}>
                      Template-based draft, opened in your mail client. No AI tokens used here.
                    </p>
                  </div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    <button type="button" onClick={() => applyEmailTemplate("followup")} className="crm-button soft" style={{ fontSize: 12, padding: "6px 9px" }}>Follow-up</button>
                    <button type="button" onClick={() => applyEmailTemplate("recap")} className="crm-button soft" style={{ fontSize: 12, padding: "6px 9px" }}>Recap</button>
                    <button type="button" onClick={() => applyEmailTemplate("pricing")} className="crm-button soft" style={{ fontSize: 12, padding: "6px 9px" }}>Pricing</button>
                  </div>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1.2fr", gap: 10 }}>
                  <label style={{ display: "grid", gap: 5, fontSize: 11, fontWeight: 800, color: "#6a7894" }}>
                    TO
                    <select
                      value={emailDraftTo}
                      onChange={(e) => setEmailDraftTo(e.target.value)}
                      style={{ height: 38, borderRadius: 10, border: "1px solid #dbe6f2", padding: "0 10px", background: "#fff", color: "#22334d", fontSize: 13 }}
                    >
                      <option value="">Select recipient</option>
                      {emailRecipients.map((recipient) => (
                        <option key={recipient.email} value={recipient.email}>{recipient.label} · {recipient.email}</option>
                      ))}
                    </select>
                  </label>
                  <label style={{ display: "grid", gap: 5, fontSize: 11, fontWeight: 800, color: "#6a7894" }}>
                    SUBJECT
                    <input
                      value={emailDraftSubject}
                      onChange={(e) => setEmailDraftSubject(e.target.value)}
                      style={{ height: 38, borderRadius: 10, border: "1px solid #dbe6f2", padding: "0 10px", color: "#22334d", fontSize: 13 }}
                    />
                  </label>
                </div>
                <textarea
                  value={emailDraftBody}
                  onChange={(e) => setEmailDraftBody(e.target.value)}
                  placeholder="Pick a template or write the message here..."
                  style={{ minHeight: 118, borderRadius: 12, border: "1px solid #dbe6f2", padding: "10px 12px", fontSize: 13, fontFamily: "inherit", lineHeight: 1.55, resize: "vertical" }}
                />
                {!canUseSharedEmailSync ? (
                  <div style={{ borderRadius: 10, border: "1px solid #ffd8b4", background: "#fff8f1", color: "#9a4f16", padding: "8px 10px", fontSize: 12 }}>
                    Shared email sync is not connected, so Beacon will not auto-capture this draft through the CC alias yet.
                  </div>
                ) : (
                  <div style={{ borderRadius: 10, border: "1px solid #cfe6d8", background: "#f1fbf5", color: "#1f7a4d", padding: "8px 10px", fontSize: 12 }}>
                    CC alias will be included: <strong>{emailSyncAddress}</strong>
                  </div>
                )}
                <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, flexWrap: "wrap" }}>
                  <button type="button" onClick={() => void copyEmailDraft()} disabled={!emailDraftSubject.trim() && !emailDraftBody.trim()} className="crm-button soft">
                    {emailDraftCopied ? "Copied" : "Copy draft"}
                  </button>
                  <a
                    className="crm-button primary"
                    href={`mailto:${encodeURIComponent(emailDraftTo)}?${new URLSearchParams({
                      subject: emailDraftSubject,
                      body: emailDraftBody,
                      ...(canUseSharedEmailSync && emailSyncAddress ? { cc: emailSyncAddress } : {}),
                    }).toString()}`}
                    style={{ pointerEvents: emailDraftTo && (emailDraftSubject || emailDraftBody) ? "auto" : "none", opacity: emailDraftTo && (emailDraftSubject || emailDraftBody) ? 1 : 0.55, textDecoration: "none" }}
                  >
                    <Mail size={13} />
                    Open composer
                  </a>
                </div>
              </div>

              {sendScopeOk === false && (
                <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", borderRadius: 12, border: "1px solid #ffd8b4", background: "#fff8f1", padding: "12px 14px" }}>
                  <div style={{ flex: 1, minWidth: 200 }}>
                    <div style={{ fontSize: 13, fontWeight: 800, color: "#9a4f16" }}>Reply needs Gmail send access</div>
                    <div style={{ fontSize: 12, color: "#a86b3c", marginTop: 2, lineHeight: 1.5 }}>
                      Your inbox is connected for reading but not sending. Reconnect to reply to threads directly from Beacon.
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => void handleReconnectGmail()}
                    disabled={reconnectingGmail}
                    style={{ fontSize: 12, fontWeight: 800, color: "#fff", background: "#6fae27", border: "1px solid #6fae27", borderRadius: 9, padding: "8px 14px", cursor: reconnectingGmail ? "wait" : "pointer", display: "inline-flex", alignItems: "center", gap: 6, whiteSpace: "nowrap" }}
                  >
                    <Mail size={13} /> {reconnectingGmail ? "Redirecting…" : "Reconnect Gmail"}
                  </button>
                </div>
              )}

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
                              <div style={{ marginTop: 10, display: "flex", justifyContent: "flex-end" }}>
                                {sendScopeOk === false ? (
                                  <button
                                    onClick={() => void handleReconnectGmail()}
                                    disabled={reconnectingGmail}
                                    title="Your connected Gmail can't send yet — reconnect to enable Reply from the CRM"
                                    style={{
                                      fontSize: 12, fontWeight: 700, color: "#9a4f16",
                                      background: "#fff8f1", border: "1px solid #ffd8b4",
                                      borderRadius: 8, padding: "5px 12px", cursor: reconnectingGmail ? "wait" : "pointer",
                                      display: "inline-flex", alignItems: "center", gap: 5,
                                    }}
                                  >
                                    <Mail size={11} /> {reconnectingGmail ? "Redirecting…" : "Reconnect Gmail to reply"}
                                  </button>
                                ) : (
                                <button
                                  onClick={() => {
                                    const replySubject = msg.subject?.toLowerCase().startsWith("re:")
                                      ? msg.subject
                                      : `Re: ${msg.subject || "(no subject)"}`;
                                    const quoted = msg.body_preview
                                      ? `\n\nOn ${msg.created_at ? new Date(msg.created_at).toLocaleString() : ""}, ${msg.from_addr} wrote:\n${msg.body_preview.split("\n").map((l) => `> ${l}`).join("\n")}`
                                      : "";
                                    setReplyCtx({
                                      to: msg.from_addr,
                                      cc: msg.cc_addrs || undefined,
                                      subject: replySubject,
                                      quotedBody: quoted,
                                      threadId: thread.thread_id,
                                      inReplyTo: msg.message_id,
                                      references: msg.message_id,
                                      dealId: deal.id,
                                    });
                                  }}
                                  style={{
                                    fontSize: 12, fontWeight: 700, color: "#4d7c0f",
                                    background: "#f3fbe3", border: "1px solid #cfe89a",
                                    borderRadius: 8, padding: "5px 12px", cursor: "pointer",
                                    display: "inline-flex", alignItems: "center", gap: 5,
                                  }}
                                >
                                  <Send size={11} /> Reply
                                </button>
                                )}
                              </div>
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

      <ReplyComposer
        open={replyCtx !== null}
        ctx={replyCtx}
        onClose={() => setReplyCtx(null)}
        onSent={() => {
          // Reload threads so the just-sent reply appears at the top of the list
          if (deal.id) {
            void personalEmailSyncApi.getThreadsForDeal(deal.id).then((r) => setEmailThreads(r.threads ?? [])).catch(() => {});
          }
        }}
      />
    </>
  );
}

export default memo(DealDetailDrawer);

// ── Helpers ─────────────────────────────────────────────────────────────────

function FieldRow({ label, icon, children }: { label: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 10.5, fontWeight: 700, color: "#8295a8", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 5 }}>
        {icon} {label}
      </div>
      {children}
    </div>
  );
}

// Lightweight section heading used to chunk the Overview tab into labeled
// groups (Deal details, Next step & notes, People). Pure presentation.
function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "8px 0 2px" }}>
      <span style={{ width: 4, height: 16, borderRadius: 3, background: "linear-gradient(180deg, #9ace3d 0%, #6fae27 100%)", flexShrink: 0 }} />
      <span style={{ fontSize: 12.5, fontWeight: 800, color: "#2d4258", textTransform: "uppercase", letterSpacing: "0.08em", whiteSpace: "nowrap" }}>{children}</span>
      <span style={{ flex: 1, height: 1, background: "linear-gradient(90deg, #e0e9f2, transparent)" }} />
    </div>
  );
}

type StageHistoryRow = { from_stage: string | null; to_stage: string; changed_at: string };

function _parseUtcMs(iso?: string | null): number {
  if (!iso) return NaN;
  return new Date(iso.endsWith("Z") ? iso : `${iso}Z`).getTime();
}
function _formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const mins = Math.floor(ms / 60000);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 48) return `${hrs}h`;
  return `${Math.floor(hrs / 24)}d`;
}

// Renders the deal's stage journey from recorded transitions, showing how long
// it spent in each stage. Surfaces data already captured in deal_stage_history.
function StageJourney({ history, deal, stages }: { history: StageHistoryRow[]; deal: Deal; stages: { id: string; label: string; color?: string }[] }) {
  const labelOf = (id: string) => stages.find((s) => s.id === id)?.label ?? id.replace(/_/g, " ");
  const colorOf = (id: string) => stages.find((s) => s.id === id)?.color ?? "#94a3b8";

  const points: { stage: string; at: number }[] = [];
  if (history.length > 0) {
    if (history[0].from_stage) points.push({ stage: history[0].from_stage, at: _parseUtcMs(deal.created_at) });
    for (const h of history) points.push({ stage: h.to_stage, at: _parseUtcMs(h.changed_at) });
  } else {
    points.push({ stage: deal.stage, at: _parseUtcMs(deal.stage_entered_at || deal.created_at) });
  }

  const now = Date.now();
  const segments = points.map((p, i) => {
    const end = i < points.length - 1 ? points[i + 1].at : now;
    return { stage: p.stage, duration: end - p.at, current: i === points.length - 1 };
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {segments.map((seg, i) => (
        <div key={`${seg.stage}-${i}`} style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ width: 9, height: 9, borderRadius: "50%", background: colorOf(seg.stage), flexShrink: 0 }} />
          <span style={{ fontSize: 13, fontWeight: seg.current ? 800 : 600, color: seg.current ? "#1f2d3d" : "#46586d", flex: 1, textTransform: "capitalize" }}>
            {labelOf(seg.stage)}
            {seg.current && <span style={{ marginLeft: 8, fontSize: 10.5, fontWeight: 800, color: "#4d7c0f", background: "#f3fbe3", border: "1px solid #cfe89a", borderRadius: 999, padding: "1px 7px" }}>current</span>}
          </span>
          <span style={{ fontSize: 12, fontWeight: 700, color: "#7a96b0", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>{_formatDuration(seg.duration)}</span>
        </div>
      ))}
    </div>
  );
}

// ── MEDDPICC Scorecard Panel ────────────────────────────────────────────────

type FlagColor = "green" | "yellow" | "red";
type ForecastCategory = "commit" | "best_case" | "pipeline";

const FLAG_PALETTE: Record<FlagColor, { dot: string; bg: string; border: string; fg: string; label: string }> = {
  green: { dot: "#22c55e", bg: "#ecfdf3", border: "#bbf7d0", fg: "#15803d", label: "GREEN" },
  yellow: { dot: "#f59e0b", bg: "#fffbeb", border: "#fde68a", fg: "#92400e", label: "YELLOW" },
  red: { dot: "#ef4444", bg: "#fef2f2", border: "#fecaca", fg: "#991b1b", label: "RED" },
};

const FORECAST_META: Record<ForecastCategory, { label: string; sub: string; color: string; bg: string; border: string }> = {
  commit: {
    label: "Commit",
    sub: "Every element defensible with evidence",
    color: "#15803d",
    bg: "#ecfdf3",
    border: "#bbf7d0",
  },
  best_case: {
    label: "Best Case",
    sub: "Yellows need an action + date to move to Green",
    color: "#92400e",
    bg: "#fffbeb",
    border: "#fde68a",
  },
  pipeline: {
    label: "Pipeline only",
    sub: "Reds are known gaps — resolve them or disqualify",
    color: "#991b1b",
    bg: "#fef2f2",
    border: "#fecaca",
  },
};

// Translate stored MEDDPICC level (0-3) into a flag color when the backend
// hasn't supplied one (older cached payloads).
function deriveFlagFromLevel(level: number): FlagColor {
  if (level <= 0) return "red";
  if (level >= 3) return "green";
  return "yellow";
}

function MeddpiccPanel({
  qualification,
  flags,
  forecastCategory,
  flagCounts,
  flagBlockers,
  flagYellows,
  autoFilling,
  onAutoFill,
  onUpdate,
  onUpdateDetail,
}: {
  qualification?: DealQualification;
  flags?: Record<string, "green" | "yellow" | "red">;
  forecastCategory?: ForecastCategory;
  flagCounts?: { green?: number; yellow?: number; red?: number };
  flagBlockers?: string[];
  flagYellows?: string[];
  autoFilling: boolean;
  onAutoFill: () => Promise<void>;
  onUpdate: (meddpicc: Record<string, number>) => Promise<void>;
  onUpdateDetail: (key: string, detail: MeddpiccFieldDetail) => Promise<void>;
}) {
  const meddpicc = (qualification?.meddpicc ?? {}) as Record<string, number>;
  const meddpiccDetails = qualification?.meddpicc_details ?? {};
  const aiDimensions = qualification?.meddpicc_ai?.dimensions ?? {};
  const aiGeneratedAt = qualification?.meddpicc_ai?.generated_at;
  const aiSignals = qualification?.meddpicc_ai?.signals_used;

  const handleChange = (key: string, value: number) => {
    onUpdate({ ...meddpicc, [key]: value });
  };

  const handleNotesSave = (key: string, detail: MeddpiccFieldDetail | undefined, notes: string) => {
    const trimmed = notes.trim();
    void onUpdateDetail(key, {
      ...(detail ?? {}),
      notes: trimmed || undefined,
      updated_at: new Date().toISOString(),
    });
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
        background: "linear-gradient(135deg, #f8fafc 0%, #f3fbe3 100%)",
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
              background: autoFilling ? "#fff7f2" : "#f3fbe3",
              color: "#4d7c0f",
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

      {/* Flag Matrix — forecast-call rubric */}
      <FlagMatrixCard
        meddpicc={meddpicc}
        flags={flags}
        forecastCategory={forecastCategory}
        flagCounts={flagCounts}
        flagBlockers={flagBlockers}
        flagYellows={flagYellows}
      />

      {/* Dimension cards */}
      {MEDDPICC_DIMENSIONS.map((dim) => {
        const val = meddpicc[dim.key] ?? 0;
        const aiMeta = aiDimensions[dim.key];
        const detail = meddpiccDetails[dim.key] as MeddpiccFieldDetail | undefined;
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
            {(detail?.summary || detail?.contact?.name || detail?.tags?.length || detail?.entities?.length) && (
              <div style={{
                marginBottom: 12,
                padding: "10px 12px",
                borderRadius: 10,
                background: "#fffaf5",
                border: "1px solid #fde8d8",
                display: "flex",
                flexDirection: "column",
                gap: 6,
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  <span style={{
                    fontSize: 10,
                    fontWeight: 800,
                    padding: "2px 8px",
                    borderRadius: 999,
                    background: "#ffe8de",
                    color: "#b45309",
                  }}>
                    Captured detail
                  </span>
                  {detail?.change_reason && (
                    <span style={{ fontSize: 10, color: "#3f6212", fontWeight: 700 }}>
                      {formatMeddpiccChangeReason(detail.change_reason)}
                    </span>
                  )}
                  {detail?.updated_at && (
                    <span style={{ fontSize: 10, color: "#7a8ca1" }}>
                      Updated {formatDate(detail.updated_at)}
                    </span>
                  )}
                </div>
                {detail?.summary && (
                  <div style={{ fontSize: 12, color: "#364152", lineHeight: 1.6 }}>
                    {detail.summary}
                  </div>
                )}
                {detail?.contact?.name && (
                  <div style={{ fontSize: 11, color: "#55687d" }}>
                    Stakeholder: {detail.contact.name}{detail.contact.title ? ` · ${detail.contact.title}` : ""}
                  </div>
                )}
                {detail?.entities && detail.entities.length > 0 && (
                  <div style={{ fontSize: 11, color: "#55687d" }}>
                    Named: {detail.entities.join(", ")}
                  </div>
                )}
                {detail?.tags && detail.tags.length > 0 && (
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {detail.tags.map((tag) => (
                      <span
                        key={tag}
                        style={{
                          fontSize: 10,
                          fontWeight: 700,
                          padding: "2px 7px",
                          borderRadius: 999,
                          background: "#fff",
                          color: "#3f6212",
                          border: "1px solid #fed7aa",
                        }}
                      >
                        {tag.replace(/_/g, " ")}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}
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
                  background: "#f3fbe3",
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
            <div style={{ marginTop: 12 }}>
              <label style={{ display: "block", fontSize: 11, fontWeight: 800, color: "#6b7f96", marginBottom: 6 }}>
                Rep notes and evidence
              </label>
              <textarea
                key={`${dim.key}-${detail?.updated_at ?? "empty"}`}
                defaultValue={detail?.notes ?? ""}
                onBlur={(event) => handleNotesSave(dim.key, detail, event.target.value)}
                placeholder={`Add notes for ${dim.label}: who/what did we identify, and what evidence supports it?`}
                rows={3}
                style={{
                  width: "100%",
                  borderRadius: 10,
                  border: "1px solid #dbe6f2",
                  background: "#fbfdff",
                  color: "#2d4258",
                  padding: "10px 12px",
                  fontSize: 12.5,
                  lineHeight: 1.5,
                  fontFamily: "inherit",
                  resize: "vertical",
                  outline: "none",
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Flag Matrix Card ────────────────────────────────────────────────────────
//
// Renders the deal review rubric described in the GTM playbook: each of the 8
// MEDDPICC elements gets a Green / Yellow / Red flag. The Green/Yellow/Red
// labels are derived server-side (see app/services/deal_flags.py) and fall
// back to a level-based mapping when the backend hasn't supplied them yet.

function FlagMatrixCard({
  meddpicc,
  flags,
  forecastCategory,
  flagCounts,
  flagBlockers,
  flagYellows,
}: {
  meddpicc: Record<string, number>;
  flags?: Record<string, "green" | "yellow" | "red">;
  forecastCategory?: ForecastCategory;
  flagCounts?: { green?: number; yellow?: number; red?: number };
  flagBlockers?: string[];
  flagYellows?: string[];
}) {
  const rows = MEDDPICC_DIMENSIONS.map((dim) => {
    const flag = (flags?.[dim.key] ?? deriveFlagFromLevel(meddpicc[dim.key] ?? 0)) as FlagColor;
    return { ...dim, flag };
  });

  // Forecast bucket header — when the backend hasn't sent one, derive it.
  const greens = flagCounts?.green ?? rows.filter((r) => r.flag === "green").length;
  const yellows = flagCounts?.yellow ?? rows.filter((r) => r.flag === "yellow").length;
  const reds = flagCounts?.red ?? rows.filter((r) => r.flag === "red").length;
  const bucket: ForecastCategory =
    forecastCategory ?? (reds > 0 ? "pipeline" : yellows === 0 ? "commit" : "best_case");
  const bucketMeta = FORECAST_META[bucket];

  return (
    <div
      style={{
        border: `1px solid ${bucketMeta.border}`,
        borderRadius: 14,
        overflow: "hidden",
        background: "#fff",
      }}
    >
      {/* Header — forecast bucket + counts */}
      <div
        style={{
          padding: "14px 18px",
          background: bucketMeta.bg,
          borderBottom: `1px solid ${bucketMeta.border}`,
          display: "flex",
          alignItems: "center",
          gap: 14,
          flexWrap: "wrap",
        }}
      >
        <div>
          <div style={{ fontSize: 10, fontWeight: 800, color: bucketMeta.color, letterSpacing: 0.6 }}>
            FORECAST CATEGORY
          </div>
          <div style={{ fontSize: 18, fontWeight: 800, color: bucketMeta.color, marginTop: 2 }}>
            {bucketMeta.label}
          </div>
          <div style={{ fontSize: 11, color: "#55687d", marginTop: 2 }}>{bucketMeta.sub}</div>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <FlagCountChip color="green" count={greens} />
          <FlagCountChip color="yellow" count={yellows} />
          <FlagCountChip color="red" count={reds} />
        </div>
      </div>

      {/* Matrix table */}
      <div style={{ padding: "12px 18px" }}>
        <div style={{ fontSize: 13, fontWeight: 800, color: "#1f2d3d", marginBottom: 8 }}>
          Flag Matrix
        </div>
        <div style={{ fontSize: 11, color: "#7a8ca1", marginBottom: 12, lineHeight: 1.6 }}>
          Use this on every deal review. Green = proven with written or recorded evidence. Yellow = assumed but
          not validated — needs a specific action and date. Red = missing or unknown.
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "minmax(140px, 1fr) repeat(3, 1fr)", gap: 0, border: "1px solid #e8eef5", borderRadius: 10, overflow: "hidden" }}>
          {/* Column headers */}
          <div style={cellStyle({ header: true })}>Element</div>
          <div style={cellStyle({ header: true, color: FLAG_PALETTE.green.fg, bg: FLAG_PALETTE.green.bg })}>● GREEN</div>
          <div style={cellStyle({ header: true, color: FLAG_PALETTE.yellow.fg, bg: FLAG_PALETTE.yellow.bg })}>● YELLOW</div>
          <div style={cellStyle({ header: true, color: FLAG_PALETTE.red.fg, bg: FLAG_PALETTE.red.bg })}>● RED</div>

          {rows.map((row) => (
            <FlagRow key={row.key} label={row.label} flag={row.flag} />
          ))}
        </div>

        {/* Action prompts */}
        {(flagBlockers?.length || flagYellows?.length) ? (
          <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 6 }}>
            {flagBlockers && flagBlockers.length > 0 && (
              <div style={{ fontSize: 11, color: "#991b1b" }}>
                <b>Reds to resolve:</b> {flagBlockers.join(", ")}
              </div>
            )}
            {flagYellows && flagYellows.length > 0 && (
              <div style={{ fontSize: 11, color: "#92400e" }}>
                <b>Yellows needing action + date:</b> {flagYellows.join(", ")}
              </div>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function FlagCountChip({ color, count }: { color: FlagColor; count: number }) {
  const p = FLAG_PALETTE[color];
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "4px 10px",
        borderRadius: 999,
        background: p.bg,
        border: `1px solid ${p.border}`,
        color: p.fg,
        fontSize: 11,
        fontWeight: 800,
      }}
    >
      <span style={{ width: 8, height: 8, borderRadius: "50%", background: p.dot }} />
      {count} {p.label}
    </span>
  );
}

function FlagRow({ label, flag }: { label: string; flag: FlagColor }) {
  const cells: FlagColor[] = ["green", "yellow", "red"];
  return (
    <>
      <div style={cellStyle({ label: true })}>{label}</div>
      {cells.map((cellColor) => {
        const active = cellColor === flag;
        const p = FLAG_PALETTE[cellColor];
        return (
          <div
            key={cellColor}
            style={cellStyle({
              bg: active ? p.bg : "#fff",
              color: active ? p.fg : "#cbd5e1",
              border: active ? `2px solid ${p.dot}` : undefined,
            })}
          >
            {active ? (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, fontWeight: 800 }}>
                <span style={{ width: 10, height: 10, borderRadius: "50%", background: p.dot }} />
                Current
              </span>
            ) : (
              <span style={{ fontSize: 16, opacity: 0.35 }}>○</span>
            )}
          </div>
        );
      })}
    </>
  );
}

function cellStyle({
  header,
  label,
  bg,
  color,
  border,
}: {
  header?: boolean;
  label?: boolean;
  bg?: string;
  color?: string;
  border?: string;
}): CSSProperties {
  return {
    padding: header ? "8px 10px" : "10px 12px",
    background: bg ?? (header ? "#f8fafc" : label ? "#fbfdff" : "#fff"),
    color: color ?? (header ? "#475569" : "#1f2d3d"),
    fontSize: header ? 10 : 12,
    fontWeight: header ? 800 : label ? 700 : 500,
    letterSpacing: header ? 0.4 : 0,
    borderBottom: "1px solid #eef2f7",
    borderRight: "1px solid #eef2f7",
    display: "flex",
    alignItems: "center",
    justifyContent: header || !label ? "center" : "flex-start",
    minHeight: header ? 32 : 44,
    outline: border,
    outlineOffset: -2,
  };
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
                background: comment.trim() ? "#4d7c0f" : "#e8eef5",
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
          color: isEmail ? "#4d7c0f" : isSystem ? "#60758b" : "#4d7c0f",
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
                      background: "#f3fbe3", color: "#2d4258", fontSize: 11,
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
                  background: "#f3fbe3",
                  border: "1px solid #d4e2f4",
                  fontSize: 12,
                  color: "#4d7c0f",
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
                  background: "#f3fbe3",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 12,
                  flexWrap: "wrap",
                }}>
                  <div style={{ fontSize: 12, color: "#4d7c0f", fontWeight: 700 }}>
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
                        border: "1px solid #6fae27",
                        background: "#6fae27",
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
  width: "100%", height: 38, borderRadius: 10,
  border: "1px solid #dbe5f0", padding: "0 12px",
  fontSize: 13, color: "#1f2d3d", background: "#fff", outline: "none",
  transition: "border-color 0.15s ease, box-shadow 0.15s ease",
};

// Backend stores next_step_due_at as a naive-UTC timestamp. These convert
// to/from the browser's local time for a <input type="datetime-local">.
function toLocalDatetimeInput(iso?: string | null): string {
  if (!iso) return "";
  const utc = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(utc.getTime())) return "";
  return new Date(utc.getTime() - utc.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}
function fromLocalDatetimeInput(local: string): string | undefined {
  if (!local) return undefined;
  const d = new Date(local); // interpreted as local wall-clock
  if (Number.isNaN(d.getTime())) return undefined;
  return d.toISOString().slice(0, -1); // drop trailing Z → naive UTC for the DB
}
function dueLabel(iso?: string | null): { text: string; overdue: boolean } | null {
  if (!iso) return null;
  const due = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(due.getTime())) return null;
  const overdue = due.getTime() < Date.now();
  const text = due.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  return { text, overdue };
}

import { useDeferredValue, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  ArrowRight,
  CalendarClock,
  CheckCircle2,
  Clock3,
  Filter,
  Loader2,
  MessageSquareMore,
  RefreshCw,
  Target,
} from "lucide-react";
import { authApi, executionTrackerApi } from "../lib/api";
import { useAuth } from "../lib/AuthContext";
import type { AssignmentUpdate, ExecutionTrackerItem, ExecutionTrackerSummary, User } from "../types";

const ENTITY_OPTIONS = [
  { value: "", label: "All work types" },
  { value: "company", label: "Accounts" },
  { value: "contact", label: "Prospects" },
  { value: "deal", label: "Deals" },
];

const PROGRESS_OPTIONS = [
  { value: "", label: "All progress states" },
  { value: "new", label: "New" },
  { value: "working", label: "Working" },
  { value: "waiting_on_buyer", label: "Waiting on buyer" },
  { value: "meeting_booked", label: "Meeting booked" },
  { value: "qualified", label: "Qualified" },
  { value: "deal_created", label: "Deal created" },
  { value: "blocked", label: "Blocked" },
  { value: "closed", label: "Closed" },
];

const CONFIDENCE_OPTIONS = [
  { value: "low", label: "Low confidence" },
  { value: "medium", label: "Medium confidence" },
  { value: "high", label: "High confidence" },
];

const BUYER_SIGNAL_OPTIONS = [
  { value: "none", label: "No buyer signal yet" },
  { value: "replied", label: "Replied" },
  { value: "interested", label: "Interested" },
  { value: "champion_identified", label: "Champion identified" },
  { value: "meeting_requested", label: "Meeting requested" },
  { value: "commercial_discussion", label: "Commercial discussion" },
  { value: "verbal_yes", label: "Verbal yes" },
];

const BLOCKER_OPTIONS = [
  { value: "none", label: "No blocker" },
  { value: "no_response", label: "No response" },
  { value: "wrong_person", label: "Wrong person" },
  { value: "timing", label: "Timing" },
  { value: "budget", label: "Budget" },
  { value: "competition", label: "Competition" },
  { value: "internal_dependency", label: "Internal dependency" },
  { value: "legal_security", label: "Legal or security" },
  { value: "other", label: "Other" },
];

const TOUCH_OPTIONS = [
  { value: "none", label: "No touch logged" },
  { value: "email", label: "Email" },
  { value: "call", label: "Call" },
  { value: "linkedin", label: "LinkedIn" },
  { value: "meeting", label: "Meeting" },
  { value: "research", label: "Research" },
  { value: "internal", label: "Internal" },
];

const ENTITY_LABEL: Record<string, string> = {
  company: "Account",
  contact: "Prospect",
  deal: "Deal",
};

const ROLE_LABEL: Record<string, string> = {
  owner: "Owner",
  ae: "AE",
  sdr: "SDR",
};

type UpdateDraft = {
  progress_state: string;
  confidence: string;
  buyer_signal: string;
  blocker_type: string;
  last_touch_type: string;
  summary: string;
  next_step: string;
  next_step_due_date: string;
  blocker_detail: string;
};

const EMPTY_DRAFT: UpdateDraft = {
  progress_state: "working",
  confidence: "medium",
  buyer_signal: "none",
  blocker_type: "none",
  last_touch_type: "none",
  summary: "",
  next_step: "",
  next_step_due_date: "",
  blocker_detail: "",
};

function itemKey(item: ExecutionTrackerItem | null | undefined) {
  if (!item) return "";
  return `${item.entity_type}:${item.entity_id}:${item.assignment_role}:${item.assignee_id}`;
}

function todayPlus(days: number) {
  const next = new Date();
  next.setDate(next.getDate() + days);
  return next.toISOString().slice(0, 10);
}

function formatDate(value?: string) {
  if (!value) return "No date";
  return new Date(value).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function formatRelativeDate(value?: string) {
  if (!value) return "No updates yet";
  const target = new Date(value);
  const now = new Date();
  const diffDays = Math.floor((now.getTime() - target.getTime()) / (1000 * 60 * 60 * 24));
  if (diffDays <= 0) return "Updated today";
  if (diffDays === 1) return "Updated 1 day ago";
  return `Updated ${diffDays} days ago`;
}

function toneForProgress(progress?: string) {
  if (!progress) return { background: "#f3f5fc", color: "#657392", border: "1px solid #dfe5f3" };
  if (progress === "blocked") return { background: "#fff1f1", color: "#b42336", border: "1px solid #f3c4cb" };
  if (progress === "deal_created" || progress === "closed") return { background: "#eaf8f0", color: "#1f8f5f", border: "1px solid #cfead9" };
  if (progress === "meeting_booked" || progress === "qualified") return { background: "#eef4ff", color: "#305fd0", border: "1px solid #d2ddfb" };
  return { background: "#f7f9fd", color: "#5e6f8a", border: "1px solid #e0e7f3" };
}

function toneForSummary(kind: "base" | "warning" | "success" | "danger") {
  if (kind === "warning") return { background: "#fff7ea", color: "#aa6700", border: "1px solid #f5ddb3" };
  if (kind === "success") return { background: "#eaf8f0", color: "#1f8f5f", border: "1px solid #cfead9" };
  if (kind === "danger") return { background: "#fff1f1", color: "#b42336", border: "1px solid #f3c4cb" };
  return { background: "#eef4ff", color: "#305fd0", border: "1px solid #d2ddfb" };
}

function humanizeToken(value?: string) {
  return (value || "").replace(/_/g, " ");
}

function buildDraft(item: ExecutionTrackerItem | null): UpdateDraft {
  const latest = item?.latest_update;
  return {
    progress_state: latest?.progress_state ?? "working",
    confidence: latest?.confidence ?? "medium",
    buyer_signal: latest?.buyer_signal ?? "none",
    blocker_type: latest?.blocker_type ?? "none",
    last_touch_type: latest?.last_touch_type ?? "none",
    summary: "",
    next_step: latest?.next_step ?? "",
    next_step_due_date: latest?.next_step_due_date ?? todayPlus(2),
    blocker_detail: latest?.blocker_detail ?? "",
  };
}

function SummaryCard({
  label,
  value,
  icon,
  tone,
  loading,
}: {
  label: string;
  value: number;
  icon: ReactNode;
  tone: "base" | "warning" | "success" | "danger";
  loading: boolean;
}) {
  const style = toneForSummary(tone);
  return (
    <div className="crm-panel" style={{ padding: "16px 18px" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <div>
          <p style={{ fontSize: 12, fontWeight: 700, color: "#6a7895", textTransform: "uppercase", letterSpacing: "0.06em" }}>{label}</p>
          <p style={{ marginTop: 10, fontSize: 28, fontWeight: 800, color: "#182042" }}>
            {loading ? "..." : value}
          </p>
        </div>
        <div style={{ ...style, width: 40, height: 40, borderRadius: 12, display: "grid", placeItems: "center" }}>
          {icon}
        </div>
      </div>
    </div>
  );
}

function FieldSelect({
  label,
  value,
  onChange,
  options,
  description,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: Array<{ value: string; label: string }>;
  description?: string;
}) {
  return (
    <label className="execution-update-field">
      <span className="execution-update-label">{label}</span>
      {description && <span className="execution-update-description">{description}</span>}
      <select value={value} onChange={(event) => onChange(event.target.value)} className="execution-update-input">
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export default function ExecutionTracker() {
  const { isAdmin } = useAuth();
  const [teamMembers, setTeamMembers] = useState<User[]>([]);
  const [items, setItems] = useState<ExecutionTrackerItem[]>([]);
  const [summary, setSummary] = useState<ExecutionTrackerSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [history, setHistory] = useState<AssignmentUpdate[]>([]);
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search.trim());
  const [assigneeFilter, setAssigneeFilter] = useState("");
  const [entityFilter, setEntityFilter] = useState("");
  const [progressFilter, setProgressFilter] = useState("");
  const [needsUpdateOnly, setNeedsUpdateOnly] = useState(false);
  const [dueDateFilter, setDueDateFilter] = useState<"" | "overdue" | "today" | "this_week">("");
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [total, setTotal] = useState(0);
  const [selectedItemKey, setSelectedItemKey] = useState("");
  const [draft, setDraft] = useState<UpdateDraft>(EMPTY_DRAFT);
  const pageSize = 25;

  const filteredItems = useMemo(() => {
    if (!dueDateFilter) return items;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const todayStr = today.toISOString().slice(0, 10);
    const weekEnd = new Date(today);
    weekEnd.setDate(today.getDate() + 7);
    const weekEndStr = weekEnd.toISOString().slice(0, 10);
    return items.filter((item) => {
      if (dueDateFilter === "overdue") return item.next_step_overdue;
      const due = item.latest_update?.next_step_due_date?.slice(0, 10);
      if (!due) return false;
      if (dueDateFilter === "today") return due === todayStr;
      if (dueDateFilter === "this_week") return due >= todayStr && due <= weekEndStr;
      return true;
    });
  }, [items, dueDateFilter]);

  const selectedItem = useMemo(
    () => items.find((item) => itemKey(item) === selectedItemKey) ?? null,
    [items, selectedItemKey]
  );

  useEffect(() => {
    if (!isAdmin) return;
    authApi.listUsers().then(setTeamMembers).catch(() => setTeamMembers([]));
  }, [isAdmin]);

  useEffect(() => {
    setPage(1);
  }, [deferredSearch, assigneeFilter, entityFilter, progressFilter, needsUpdateOnly, dueDateFilter]);

  useEffect(() => {
    const params = {
      skip: (page - 1) * pageSize,
      limit: pageSize,
      assigneeId: isAdmin ? assigneeFilter || undefined : undefined,
      entityType: (entityFilter || undefined) as "company" | "contact" | "deal" | undefined,
      progressState: progressFilter || undefined,
      needsUpdateOnly,
      q: deferredSearch || undefined,
    };

    setLoading(true);
    executionTrackerApi.listItems(params)
      .then((result) => {
        setItems(result.items);
        setTotal(result.total);
        setPages(result.pages);
      })
      .finally(() => setLoading(false));

    setSummaryLoading(true);
    executionTrackerApi.summary(params)
      .then(setSummary)
      .finally(() => setSummaryLoading(false));
  }, [page, deferredSearch, assigneeFilter, entityFilter, progressFilter, needsUpdateOnly, isAdmin]);

  useEffect(() => {
    if (!items.length) {
      setSelectedItemKey("");
      return;
    }
    const stillVisible = items.some((item) => itemKey(item) === selectedItemKey);
    if (!stillVisible) {
      setSelectedItemKey(itemKey(items[0]));
    }
  }, [items, selectedItemKey]);

  useEffect(() => {
    setDraft(buildDraft(selectedItem));
  }, [selectedItem]);

  useEffect(() => {
    if (!selectedItem) {
      setHistory([]);
      return;
    }
    setHistoryLoading(true);
    executionTrackerApi.getUpdates(selectedItem.entity_type, selectedItem.entity_id, selectedItem.assignment_role)
      .then(setHistory)
      .finally(() => setHistoryLoading(false));
  }, [selectedItemKey, selectedItem]);

  const refreshSelected = async () => {
    const params = {
      skip: (page - 1) * pageSize,
      limit: pageSize,
      assigneeId: isAdmin ? assigneeFilter || undefined : undefined,
      entityType: (entityFilter || undefined) as "company" | "contact" | "deal" | undefined,
      progressState: progressFilter || undefined,
      needsUpdateOnly,
      q: deferredSearch || undefined,
    };
    const [listResult, summaryResult] = await Promise.all([
      executionTrackerApi.listItems(params),
      executionTrackerApi.summary(params),
    ]);
    setItems(listResult.items);
    setTotal(listResult.total);
    setPages(listResult.pages);
    setSummary(summaryResult);
    if (selectedItem) {
      const updates = await executionTrackerApi.getUpdates(selectedItem.entity_type, selectedItem.entity_id, selectedItem.assignment_role);
      setHistory(updates);
    }
  };

  const handleSubmitUpdate = async () => {
    if (!selectedItem) return;
    if (!draft.summary.trim()) {
      window.alert("Add a short update summary so admins can quickly see what changed.");
      return;
    }
    if (!draft.next_step.trim()) {
      window.alert("Add the exact next step so this stays reportable later.");
      return;
    }
    if (!draft.next_step_due_date) {
      window.alert("Set a next step date so overdue follow-ups can be tracked.");
      return;
    }

    setSaving(true);
    try {
      await executionTrackerApi.createUpdate({
        entity_type: selectedItem.entity_type,
        entity_id: selectedItem.entity_id,
        assignment_role: selectedItem.assignment_role,
        progress_state: draft.progress_state,
        confidence: draft.confidence,
        buyer_signal: draft.buyer_signal,
        blocker_type: draft.blocker_type,
        last_touch_type: draft.last_touch_type,
        summary: draft.summary.trim(),
        next_step: draft.next_step.trim(),
        next_step_due_date: draft.next_step_due_date,
        blocker_detail: draft.blocker_detail.trim() || undefined,
      });
      setDraft((current) => ({
        ...current,
        summary: "",
      }));
      await refreshSelected();
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "Failed to save update");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="crm-page execution-tracker-page">
      <div
        className="crm-panel"
        style={{
          padding: "22px 24px",
          background: "linear-gradient(135deg, #ffffff 0%, #f6f8ff 100%)",
        }}
      >
        <div className="crm-toolbar" style={{ alignItems: "flex-start" }}>
          <div>
            <p style={{ fontSize: 18, fontWeight: 700, color: "#182042" }}>
              {isAdmin ? "Rep execution tracker" : "My assigned execution tracker"}
            </p>
            <p className="crm-muted" style={{ marginTop: 6, fontSize: 13, lineHeight: 1.6 }}>
              Reps log short structured updates with momentum, blockers, and next steps. Admins can scan freshness, risk, and positive buying signals without digging through notes.
            </p>
          </div>
          <div className="crm-chip">
            <MessageSquareMore size={14} />
            Updates stay append-only for cleaner reporting later
          </div>
        </div>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))",
          gap: 14,
        }}
      >
        <SummaryCard label="Assigned items" value={summary?.total_items ?? 0} icon={<Target size={16} />} tone="base" loading={summaryLoading} />
        <SummaryCard label="Need update" value={summary?.needs_update_items ?? 0} icon={<Clock3 size={16} />} tone="warning" loading={summaryLoading} />
        <SummaryCard label="Blocked" value={summary?.blocked_items ?? 0} icon={<AlertTriangle size={16} />} tone="danger" loading={summaryLoading} />
        <SummaryCard label="Next step overdue" value={summary?.overdue_next_steps ?? 0} icon={<CalendarClock size={16} />} tone="warning" loading={summaryLoading} />
        <SummaryCard label="Positive momentum" value={summary?.positive_momentum_items ?? 0} icon={<CheckCircle2 size={16} />} tone="success" loading={summaryLoading} />
      </div>

      <div className="crm-panel" style={{ padding: "18px 20px" }}>
        <div className="crm-toolbar" style={{ alignItems: "center" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", flex: 1 }}>
            <div style={{ position: "relative", minWidth: 220, flex: "1 1 260px" }}>
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search account, prospect, deal, rep, or latest update"
                style={{ width: "100%", height: 42, padding: "0 14px" }}
              />
            </div>
            {isAdmin && (
              <select
                value={assigneeFilter}
                onChange={(event) => setAssigneeFilter(event.target.value)}
                style={{ minWidth: 170, height: 42, padding: "0 12px" }}
              >
                <option value="">All reps</option>
                {teamMembers.map((member) => (
                  <option key={member.id} value={member.id}>
                    {member.name}
                  </option>
                ))}
              </select>
            )}
            <select value={entityFilter} onChange={(event) => setEntityFilter(event.target.value)} style={{ minWidth: 160, height: 42, padding: "0 12px" }}>
              {ENTITY_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <select value={progressFilter} onChange={(event) => setProgressFilter(event.target.value)} style={{ minWidth: 190, height: 42, padding: "0 12px" }}>
              {PROGRESS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <select value={dueDateFilter} onChange={(event) => setDueDateFilter(event.target.value as "" | "overdue" | "today" | "this_week")} style={{ minWidth: 170, height: 42, padding: "0 12px" }}>
              <option value="">All due dates</option>
              <option value="overdue">Overdue</option>
              <option value="today">Due today</option>
              <option value="this_week">Due this week</option>
            </select>
          </div>
          <div className="crm-toolbar-actions">
            <button
              type="button"
              className="crm-button soft"
              onClick={() => setNeedsUpdateOnly((value) => !value)}
              style={{
                background: needsUpdateOnly ? "#eef4ff" : "#fff",
                borderColor: needsUpdateOnly ? "#c8d6fb" : "#cfd5ef",
              }}
            >
              <Filter size={14} />
              {needsUpdateOnly ? "Showing only stale work" : "Needs update only"}
            </button>
          </div>
        </div>
      </div>

      <div className="execution-tracker-grid">
        <div className="crm-panel execution-tracker-list">
          <div style={{ padding: "18px 20px 12px", borderBottom: "1px solid #edf1f8" }}>
            <div className="crm-toolbar" style={{ alignItems: "center" }}>
              <div>
                <p style={{ fontSize: 15, fontWeight: 700, color: "#1c2745" }}>Assigned work</p>
                <p className="crm-muted" style={{ marginTop: 4, fontSize: 12 }}>
                  {loading ? "Loading assignments..." : `${total} matching assignment${total === 1 ? "" : "s"}`}
                </p>
              </div>
              <button type="button" className="crm-button soft" onClick={refreshSelected}>
                <RefreshCw size={14} />
                Refresh
              </button>
            </div>
          </div>

          <div style={{ overflow: "auto" }}>
            <table className="crm-table">
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Type</th>
                  {isAdmin && <th>Assignee</th>}
                  <th>System</th>
                  <th>Latest</th>
                  <th>Next step</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr>
                    <td colSpan={isAdmin ? 6 : 5} style={{ textAlign: "center", padding: 40, color: "#73829c" }}>
                      <Loader2 size={18} className="animate-spin" />
                    </td>
                  </tr>
                ) : filteredItems.length === 0 ? (
                  <tr>
                    <td colSpan={isAdmin ? 6 : 5} style={{ textAlign: "center", padding: 40, color: "#73829c" }}>
                      No assigned work matches these filters yet.
                    </td>
                  </tr>
                ) : (
                  filteredItems.map((item) => {
                    const selected = itemKey(item) === selectedItemKey;
                    const progressTone = toneForProgress(item.latest_update?.progress_state);
                    return (
                      <tr key={itemKey(item)} onClick={() => setSelectedItemKey(itemKey(item))} style={{ cursor: "pointer" }}>
                        <td style={{ background: selected ? "#f7f9ff" : undefined }}>
                          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                            <span style={{ fontWeight: 700 }}>{item.entity_name}</span>
                            <span className="crm-muted" style={{ fontSize: 12 }}>{item.entity_subtitle || "No secondary context yet"}</span>
                            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                              <span className="crm-chip" style={{ padding: "4px 8px", fontSize: 11 }}>
                                {ROLE_LABEL[item.assignment_role]}
                              </span>
                              {item.needs_update && (
                                <span style={{ ...toneForSummary("warning"), borderRadius: 999, padding: "4px 8px", fontSize: 11, fontWeight: 700 }}>
                                  Needs update
                                </span>
                              )}
                              {item.next_step_overdue && (
                                <span style={{ ...toneForSummary("danger"), borderRadius: 999, padding: "4px 8px", fontSize: 11, fontWeight: 700 }}>
                                  Overdue
                                </span>
                              )}
                            </div>
                          </div>
                        </td>
                        <td style={{ background: selected ? "#f7f9ff" : undefined }}>
                          <span style={{ fontSize: 12, fontWeight: 700, color: "#5f7192", textTransform: "uppercase", letterSpacing: "0.08em" }}>
                            {ENTITY_LABEL[item.entity_type]}
                          </span>
                        </td>
                        {isAdmin && (
                          <td style={{ background: selected ? "#f7f9ff" : undefined }}>
                            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                              <span style={{ fontWeight: 600 }}>{item.assignee_name || "Unassigned"}</span>
                              <span className="crm-muted" style={{ fontSize: 12 }}>{ROLE_LABEL[item.assignment_role]}</span>
                            </div>
                          </td>
                        )}
                        <td style={{ background: selected ? "#f7f9ff" : undefined }}>
                          <span style={{ fontSize: 13, color: "#51627d" }}>{item.system_status || "No system status"}</span>
                        </td>
                        <td style={{ background: selected ? "#f7f9ff" : undefined }}>
                          {item.latest_update ? (
                            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                              <span style={{ ...progressTone, borderRadius: 999, padding: "4px 8px", width: "fit-content", fontSize: 11, fontWeight: 700 }}>
                                {humanizeToken(item.latest_update.progress_state)}
                              </span>
                              <span style={{ fontSize: 12, lineHeight: 1.5, color: "#465976" }}>{item.latest_update.summary}</span>
                              <span className="crm-muted" style={{ fontSize: 11 }}>{formatRelativeDate(item.latest_update.created_at)}</span>
                            </div>
                          ) : (
                            <span className="crm-muted" style={{ fontSize: 12 }}>No structured update yet</span>
                          )}
                        </td>
                        <td style={{ background: selected ? "#f7f9ff" : undefined }}>
                          {item.latest_update?.next_step_due_date ? (
                            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                              <span style={{ fontWeight: 600 }}>{formatDate(item.latest_update.next_step_due_date)}</span>
                              <span className="crm-muted" style={{ fontSize: 12 }}>{item.latest_update.next_step}</span>
                            </div>
                          ) : (
                            <span className="crm-muted" style={{ fontSize: 12 }}>No next step date</span>
                          )}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 12,
              padding: "16px 20px",
              borderTop: "1px solid #edf1f8",
            }}
          >
            <p className="crm-muted" style={{ fontSize: 12 }}>
              Page {page} of {pages}
            </p>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="crm-button soft" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>
                Previous
              </button>
              <button className="crm-button soft" disabled={page >= pages} onClick={() => setPage((value) => Math.min(pages, value + 1))}>
                Next
              </button>
            </div>
          </div>
        </div>

        <div className="crm-panel execution-tracker-detail">
          {!selectedItem ? (
            <div style={{ padding: 28, color: "#73829c" }}>
              Select an assigned item to view the latest context and log a structured update.
            </div>
          ) : (
            <>
              <div style={{ padding: "22px 24px", borderBottom: "1px solid #edf1f8" }}>
                <div className="crm-toolbar" style={{ alignItems: "flex-start" }}>
                  <div style={{ minWidth: 0 }}>
                    <p style={{ fontSize: 18, fontWeight: 700, color: "#182042" }}>{selectedItem.entity_name}</p>
                    <p className="crm-muted" style={{ marginTop: 6, fontSize: 13 }}>
                      {selectedItem.entity_subtitle || "Assigned item"}
                    </p>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
                      <span className="crm-chip">{ENTITY_LABEL[selectedItem.entity_type]}</span>
                      <span className="crm-chip">{ROLE_LABEL[selectedItem.assignment_role]}</span>
                      {selectedItem.system_status && <span className="crm-chip">{selectedItem.system_status}</span>}
                      {selectedItem.assignee_name && <span className="crm-chip">{selectedItem.assignee_name}</span>}
                    </div>
                  </div>
                  <Link to={selectedItem.entity_link} className="crm-button soft">
                    Open record
                    <ArrowRight size={14} />
                  </Link>
                </div>
              </div>

              <div style={{ padding: "22px 24px", display: "grid", gap: 20 }}>
                <div
                  style={{
                    border: "1px solid #e4ebf6",
                    borderRadius: 14,
                    padding: 16,
                    background: "#fbfdff",
                  }}
                >
                  <p style={{ fontSize: 13, fontWeight: 700, color: "#243253" }}>Latest tracker snapshot</p>
                  {selectedItem.latest_update ? (
                    <div style={{ marginTop: 12, display: "grid", gap: 12 }}>
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        <span style={{ ...toneForProgress(selectedItem.latest_update.progress_state), borderRadius: 999, padding: "5px 10px", fontSize: 11, fontWeight: 700 }}>
                          {humanizeToken(selectedItem.latest_update.progress_state)}
                        </span>
                        <span className="crm-chip">{selectedItem.latest_update.confidence} confidence</span>
                        <span className="crm-chip">{humanizeToken(selectedItem.latest_update.buyer_signal)}</span>
                        <span className="crm-chip">{humanizeToken(selectedItem.latest_update.last_touch_type)}</span>
                      </div>
                      <div>
                        <p style={{ fontSize: 12, fontWeight: 700, color: "#637390", textTransform: "uppercase", letterSpacing: "0.06em" }}>What changed</p>
                        <p style={{ marginTop: 6, fontSize: 14, lineHeight: 1.7, color: "#233352" }}>{selectedItem.latest_update.summary}</p>
                      </div>
                      <div>
                        <p style={{ fontSize: 12, fontWeight: 700, color: "#637390", textTransform: "uppercase", letterSpacing: "0.06em" }}>Next step</p>
                        <p style={{ marginTop: 6, fontSize: 14, lineHeight: 1.7, color: "#233352" }}>{selectedItem.latest_update.next_step}</p>
                        <p className="crm-muted" style={{ marginTop: 6, fontSize: 12 }}>
                          Due {formatDate(selectedItem.latest_update.next_step_due_date)} by {selectedItem.latest_update.created_by_name || "team member"}
                        </p>
                      </div>
                      {selectedItem.latest_update.blocker_type !== "none" && (
                        <div>
                          <p style={{ fontSize: 12, fontWeight: 700, color: "#637390", textTransform: "uppercase", letterSpacing: "0.06em" }}>Blocker</p>
                          <p style={{ marginTop: 6, fontSize: 14, lineHeight: 1.7, color: "#233352" }}>
                            {humanizeToken(selectedItem.latest_update.blocker_type)}
                            {selectedItem.latest_update.blocker_detail ? ` - ${selectedItem.latest_update.blocker_detail}` : ""}
                          </p>
                        </div>
                      )}
                    </div>
                  ) : (
                    <p className="crm-muted" style={{ marginTop: 10, fontSize: 13, lineHeight: 1.6 }}>
                      No structured update has been logged yet. The first update should capture current momentum, the exact next step, and when that next step is due.
                    </p>
                  )}
                </div>

                <div
                  style={{
                    border: "1px solid #e4ebf6",
                    borderRadius: 14,
                    padding: 16,
                    background: "#fff",
                  }}
                >
                  <div className="crm-toolbar" style={{ alignItems: "center" }}>
                    <div>
                      <p style={{ fontSize: 13, fontWeight: 700, color: "#243253" }}>Update history</p>
                      <p className="crm-muted" style={{ marginTop: 4, fontSize: 12 }}>
                        Useful for manager reviews and future reporting.
                      </p>
                    </div>
                  </div>
                  <div style={{ marginTop: 14, display: "grid", gap: 12, maxHeight: 260, overflow: "auto", paddingRight: 4 }}>
                    {historyLoading ? (
                      <div className="crm-muted" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <Loader2 size={14} className="animate-spin" />
                        Loading update history...
                      </div>
                    ) : history.length === 0 ? (
                      <p className="crm-muted" style={{ fontSize: 13 }}>No update history yet.</p>
                    ) : (
                      history.map((update) => (
                        <div
                          key={update.id}
                          style={{
                            border: "1px solid #edf1f8",
                            borderRadius: 12,
                            padding: 14,
                            background: "#fbfdff",
                          }}
                        >
                          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                              <span style={{ ...toneForProgress(update.progress_state), borderRadius: 999, padding: "4px 8px", fontSize: 11, fontWeight: 700 }}>
                                {humanizeToken(update.progress_state)}
                              </span>
                              <span className="crm-chip" style={{ padding: "4px 8px", fontSize: 11 }}>{update.confidence}</span>
                              <span className="crm-chip" style={{ padding: "4px 8px", fontSize: 11 }}>{humanizeToken(update.buyer_signal)}</span>
                            </div>
                            <span className="crm-muted" style={{ fontSize: 11 }}>
                              {formatDate(update.created_at)} by {update.created_by_name || "team member"}
                            </span>
                          </div>
                          <p style={{ marginTop: 10, fontSize: 13, lineHeight: 1.6, color: "#233352" }}>{update.summary}</p>
                          <p className="crm-muted" style={{ marginTop: 8, fontSize: 12, lineHeight: 1.5 }}>
                            Next step: {update.next_step} {update.next_step_due_date ? `- due ${formatDate(update.next_step_due_date)}` : ""}
                          </p>
                        </div>
                      ))
                    )}
                  </div>
                </div>

                <div
                  style={{
                    border: "1px solid #dfe7f5",
                    borderRadius: 16,
                    padding: 18,
                    background: "linear-gradient(180deg, #ffffff 0%, #f8faff 100%)",
                  }}
                >
                  <div className="crm-toolbar" style={{ alignItems: "flex-start" }}>
                    <div>
                      <p style={{ fontSize: 15, fontWeight: 700, color: "#182042" }}>Log a new update</p>
                      <p className="crm-muted" style={{ marginTop: 4, fontSize: 12, lineHeight: 1.6 }}>
                        Keep it lightweight: momentum, blocker, what changed, and the exact next step with a due date.
                      </p>
                    </div>
                  </div>

                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                      gap: 12,
                      marginTop: 16,
                    }}
                  >
                    <FieldSelect label="Progress" value={draft.progress_state} onChange={(value) => setDraft((current) => ({ ...current, progress_state: value }))} options={PROGRESS_OPTIONS.slice(1)} />
                    <FieldSelect label="Confidence" value={draft.confidence} onChange={(value) => setDraft((current) => ({ ...current, confidence: value }))} options={CONFIDENCE_OPTIONS} />
                    <FieldSelect label="Buyer signal" value={draft.buyer_signal} onChange={(value) => setDraft((current) => ({ ...current, buyer_signal: value }))} options={BUYER_SIGNAL_OPTIONS} />
                    <FieldSelect label="Blocker" value={draft.blocker_type} onChange={(value) => setDraft((current) => ({ ...current, blocker_type: value }))} options={BLOCKER_OPTIONS} />
                    <FieldSelect label="Last touch" value={draft.last_touch_type} onChange={(value) => setDraft((current) => ({ ...current, last_touch_type: value }))} options={TOUCH_OPTIONS} />
                    <label style={{ display: "grid", gap: 6 }}>
                      <span style={{ fontSize: 12, fontWeight: 700, color: "#5a6884", textTransform: "uppercase", letterSpacing: "0.06em" }}>Next step date</span>
                      <input type="date" value={draft.next_step_due_date} onChange={(event) => setDraft((current) => ({ ...current, next_step_due_date: event.target.value }))} style={{ height: 42, padding: "0 12px" }} />
                    </label>
                  </div>

                  <div style={{ display: "grid", gap: 12, marginTop: 14 }}>
                    <label style={{ display: "grid", gap: 6 }}>
                      <span style={{ fontSize: 12, fontWeight: 700, color: "#5a6884", textTransform: "uppercase", letterSpacing: "0.06em" }}>What changed</span>
                      <textarea value={draft.summary} onChange={(event) => setDraft((current) => ({ ...current, summary: event.target.value }))} placeholder="Example: Prospect replied after the second follow-up and asked for a quick intro call next week." style={{ minHeight: 96, padding: "12px 14px", resize: "vertical" }} />
                    </label>
                    <label style={{ display: "grid", gap: 6 }}>
                      <span style={{ fontSize: 12, fontWeight: 700, color: "#5a6884", textTransform: "uppercase", letterSpacing: "0.06em" }}>Exact next step</span>
                      <textarea value={draft.next_step} onChange={(event) => setDraft((current) => ({ ...current, next_step: event.target.value }))} placeholder="Example: Confirm intro call slot and send agenda with two implementation outcomes to discuss." style={{ minHeight: 80, padding: "12px 14px", resize: "vertical" }} />
                    </label>
                    <label style={{ display: "grid", gap: 6 }}>
                      <span style={{ fontSize: 12, fontWeight: 700, color: "#5a6884", textTransform: "uppercase", letterSpacing: "0.06em" }}>Blocker detail</span>
                      <textarea value={draft.blocker_detail} onChange={(event) => setDraft((current) => ({ ...current, blocker_detail: event.target.value }))} placeholder="Only fill this when there is a blocker worth flagging to the admin." style={{ minHeight: 74, padding: "12px 14px", resize: "vertical" }} />
                    </label>
                  </div>

                  <div className="crm-toolbar" style={{ marginTop: 16, alignItems: "center" }}>
                    <div className="crm-muted" style={{ fontSize: 12, lineHeight: 1.6 }}>
                      This creates a new update entry instead of overwriting the old one, so progress and reporting stay traceable.
                    </div>
                    <button type="button" className="crm-button primary" onClick={handleSubmitUpdate} disabled={saving}>
                      {saving ? <Loader2 size={14} className="animate-spin" /> : <MessageSquareMore size={14} />}
                      Save update
                    </button>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

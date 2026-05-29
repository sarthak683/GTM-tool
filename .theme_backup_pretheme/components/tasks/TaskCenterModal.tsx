import { memo, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Clock3, MessageSquare, Plus, Sparkles, Trash2, Wrench, X } from "lucide-react";

import { authApi, tasksApi } from "../../lib/api";
import { useAuth } from "../../lib/AuthContext";
import type { TaskItem, User as UserType } from "../../types";
import { formatDate } from "../../lib/utils";
import { getSystemTaskGuidance } from "./systemTaskGuidance";

const colors = {
  border: "#d9e1ec",
  text: "#1d2b3c",
  sub: "#55657a",
  faint: "#7f8fa5",
  primary: "#1f6feb",
  primarySoft: "#eef5ff",
  green: "#1f8f5f",
  greenSoft: "#e8f8f0",
  amber: "#b56d00",
  amberSoft: "#fff4df",
  violet: "#7a2dd9",
  violetSoft: "#f3eaff",
  red: "#b42336",
  redSoft: "#ffecef",
};

const PRIORITY_STYLE = {
  urgent: { bg: colors.redSoft, border: "#ffb8c2", color: colors.red },
  high: { bg: colors.redSoft, border: "#ffd0d8", color: colors.red },
  medium: { bg: colors.amberSoft, border: "#ffe3b3", color: colors.amber },
  normal: { bg: "#eef2f7", border: colors.border, color: colors.sub },
  low: { bg: "#eef2f7", border: colors.border, color: colors.sub },
} as const;

const TYPE_STYLE = {
  manual: { bg: colors.primarySoft, border: "#d5e5ff", color: colors.primary },
  system: { bg: colors.violetSoft, border: "#eadbff", color: colors.violet },
} as const;

const PRIORITY_LABEL_STYLE = {
  P0: { bg: colors.redSoft, border: "#ffd0d8", color: colors.red },
  P1: { bg: colors.amberSoft, border: "#ffe3b3", color: colors.amber },
  P2: { bg: colors.primarySoft, border: "#d5e5ff", color: colors.primary },
} as const;

const TASK_CACHE_TTL_MS = 5 * 60 * 1000;
const taskListCache = new Map<string, { items: TaskItem[]; fetchedAt: number }>();

type TaskPriorityLabel = "P0" | "P1" | "P2";
type TaskRefreshMode = "auto" | "force" | "none";

function taskCacheKey(entityType: "company" | "contact" | "deal", entityId: string) {
  return `${entityType}:${entityId}`;
}

function getCachedTaskList(cacheKey: string) {
  const entry = taskListCache.get(cacheKey);
  if (!entry) return null;
  if (Date.now() - entry.fetchedAt > TASK_CACHE_TTL_MS) {
    taskListCache.delete(cacheKey);
    return null;
  }
  return entry;
}

function parseTaskMatrixMeta(payload?: Record<string, unknown>) {
  const priorityLabel: TaskPriorityLabel | null = payload?.priority_label === "P0" || payload?.priority_label === "P1" || payload?.priority_label === "P2"
    ? payload.priority_label
    : null;
  const ownerHint = typeof payload?.owner_hint === "string" ? payload.owner_hint.trim() : "";
  const escalationHint = typeof payload?.escalation_hint === "string" ? payload.escalation_hint.trim() : "";
  const slaLabel = typeof payload?.sla_label === "string" ? payload.sla_label.trim() : "";
  return { priorityLabel, ownerHint, escalationHint, slaLabel };
}

function parseMeetingPayload(payload?: Record<string, unknown>) {
  const meetingTitle = typeof payload?.meeting_title === "string" ? payload.meeting_title.trim() : "";
  const meetingSummary = typeof payload?.meeting_summary === "string" ? payload.meeting_summary.trim() : "";
  const followUpEmailDraft = typeof payload?.follow_up_email_draft === "string" ? payload.follow_up_email_draft.trim() : "";
  const actionItems = Array.isArray(payload?.action_items)
    ? payload.action_items.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    : [];

  return {
    meetingTitle,
    meetingSummary,
    followUpEmailDraft,
    actionItems,
    hasMeetingContent: Boolean(meetingTitle || meetingSummary || followUpEmailDraft || actionItems.length),
  };
}

function MeetingFollowUpBlock({ task }: { task: TaskItem }) {
  const [copied, setCopied] = useState(false);
  const { meetingTitle, meetingSummary, followUpEmailDraft, actionItems, hasMeetingContent } = parseMeetingPayload(task.action_payload);

  if (!hasMeetingContent) return null;

  const copyDraft = async () => {
    if (!followUpEmailDraft) return;
    try {
      await navigator.clipboard.writeText(followUpEmailDraft);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div style={{ display: "grid", gap: 12 }}>
      {meetingSummary ? (
        <div style={{ borderRadius: 14, border: "1px solid #ffd7b0", background: "#fff7ef", padding: "14px 15px", display: "grid", gap: 8 }}>
          <div style={{ color: colors.amber, fontSize: 11, fontWeight: 800, letterSpacing: 0.4 }}>MEETING SUMMARY</div>
          {meetingTitle ? <div style={{ color: colors.text, fontSize: 13, fontWeight: 700 }}>{meetingTitle}</div> : null}
          <div style={{ color: colors.text, fontSize: 13.5, lineHeight: 1.7 }}>{meetingSummary}</div>
        </div>
      ) : null}

      {actionItems.length > 0 ? (
        <div style={{ borderRadius: 14, border: `1px solid ${colors.border}`, background: "#fcfdff", padding: "14px 15px", display: "grid", gap: 10 }}>
          <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.4 }}>ACTION ITEMS</div>
          <div style={{ display: "grid", gap: 8 }}>
            {actionItems.map((item) => (
              <div key={item} style={{ color: colors.sub, fontSize: 13.5, lineHeight: 1.6, padding: "8px 10px", borderRadius: 10, background: "#fff", border: `1px solid ${colors.border}` }}>
                {item}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {followUpEmailDraft ? (
        <div style={{ borderRadius: 14, border: `1px solid ${colors.border}`, background: "#fff", padding: "14px 15px", display: "grid", gap: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.4 }}>FOLLOW-UP EMAIL DRAFT</div>
            <button
              type="button"
              onClick={() => void copyDraft()}
              style={{ borderRadius: 8, border: `1px solid ${colors.border}`, background: copied ? colors.greenSoft : "#fff", color: copied ? colors.green : colors.sub, padding: "6px 10px", fontSize: 12, fontWeight: 700, cursor: "pointer" }}
            >
              {copied ? "Copied" : "Copy draft"}
            </button>
          </div>
          <div style={{ whiteSpace: "pre-wrap", color: colors.text, fontSize: 13.5, lineHeight: 1.75, padding: "12px 13px", borderRadius: 12, background: "#fbfdff", border: `1px solid ${colors.border}` }}>
            {followUpEmailDraft}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function getTaskTemplates(task: TaskItem, entityLabel: string) {
  const label = entityLabel || "this record";
  const text = `${task.title} ${task.description ?? ""}`.toLowerCase();
  const templates = [
    {
      label: "Call outcome",
      body: `Outcome: Called ${label}.\nResult: Connected / voicemail / no answer\nNotes:\nNext step:`,
    },
    {
      label: "Email follow-up",
      body: `Outcome: Sent follow-up for ${label}.\nMessage angle:\nNext step:`,
    },
  ];
  if (text.includes("close date") || text.includes("forecast")) {
    templates.unshift({
      label: "Forecast hygiene",
      body: `Outcome: Reviewed forecast hygiene for ${label}.\nClose date update:\nConfidence:\nNext step:`,
    });
  } else if (text.includes("meeting") || text.includes("demo")) {
    templates.unshift({
      label: "Meeting follow-up",
      body: `Outcome: Followed up on ${label} meeting.\nCustomer ask:\nOwner:\nNext step/date:`,
    });
  } else if (task.entity_type === "deal") {
    templates.unshift({
      label: "Deal next step",
      body: `Outcome: Updated next step for ${label}.\nBuyer commitment:\nRep action:\nDue date:`,
    });
  }
  return templates.slice(0, 3);
}

function TaskCard({
  task,
  entityLabel,
  commentDraft,
  outcomeDraft,
  onCommentDraftChange,
  onOutcomeDraftChange,
  onAddComment,
  onComplete,
  onDismiss,
  onAccept,
  onManualTakeover,
  onDelete,
  canDelete,
}: {
  task: TaskItem;
  entityLabel: string;
  commentDraft: string;
  outcomeDraft: string;
  onCommentDraftChange: (value: string) => void;
  onOutcomeDraftChange: (value: string) => void;
  onAddComment: () => void;
  onComplete: () => void;
  onDismiss: () => void;
  onAccept: () => void;
  onManualTakeover: () => void;
  onDelete: () => void;
  canDelete: boolean;
}) {
  const priorityStyle = PRIORITY_STYLE[task.priority as keyof typeof PRIORITY_STYLE] ?? PRIORITY_STYLE.normal;
  const typeStyle = TYPE_STYLE[task.task_type];
  const isOpen = task.status === "open";
  const ownerText = task.assigned_to_name || "Unassigned";
  const systemGuidance = getSystemTaskGuidance(task);
  const matrixMeta = parseTaskMatrixMeta(task.action_payload);
  const priorityLabelStyle = matrixMeta.priorityLabel ? PRIORITY_LABEL_STYLE[matrixMeta.priorityLabel] : null;
  const templates = getTaskTemplates(task, entityLabel);

  return (
    <div style={{ border: `1px solid ${colors.border}`, background: "#fff", borderRadius: 16, padding: "14px 16px", display: "grid", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap", alignItems: "start" }}>
        <div style={{ display: "grid", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ borderRadius: 999, padding: "4px 9px", background: typeStyle.bg, border: `1px solid ${typeStyle.border}`, color: typeStyle.color, fontSize: 11, fontWeight: 800 }}>
              {task.task_type === "system" ? "System" : "Manual"}
            </span>
            <span style={{ borderRadius: 999, padding: "4px 9px", background: priorityStyle.bg, border: `1px solid ${priorityStyle.border}`, color: priorityStyle.color, fontSize: 11, fontWeight: 800 }}>
              {task.priority}
            </span>
            {matrixMeta.priorityLabel && priorityLabelStyle ? (
              <span style={{ borderRadius: 999, padding: "4px 9px", background: priorityLabelStyle.bg, border: `1px solid ${priorityLabelStyle.border}`, color: priorityLabelStyle.color, fontSize: 11, fontWeight: 800 }}>
                {matrixMeta.priorityLabel}
              </span>
            ) : null}
            <span style={{ borderRadius: 999, padding: "4px 9px", background: task.status === "completed" ? colors.greenSoft : task.status === "dismissed" ? "#eef2f7" : "#f8fbff", border: `1px solid ${task.status === "completed" ? "#cdeedc" : colors.border}`, color: task.status === "completed" ? colors.green : colors.sub, fontSize: 11, fontWeight: 800 }}>
              {task.status}
            </span>
          </div>
          <div style={{ color: colors.text, fontWeight: 800, fontSize: 15 }}>{task.title}</div>
          {task.description ? (
            <div style={{ color: colors.sub, fontSize: 13.5, lineHeight: 1.6 }}>{task.description}</div>
          ) : null}
        </div>
        <div style={{ display: "grid", gap: 4, justifyItems: "end", color: colors.faint, fontSize: 12 }}>
          <div>{formatDate(task.created_at)}</div>
          {task.due_at ? <div>Due {formatDate(task.due_at)}</div> : null}
          <div>{ownerText}</div>
          {matrixMeta.ownerHint ? <div>Owner {matrixMeta.ownerHint}</div> : null}
          {matrixMeta.slaLabel ? <div>SLA {matrixMeta.slaLabel}</div> : null}
          <div>{task.created_by_name ? `Created by ${task.created_by_name}` : task.source || "Beacon"}</div>
          {canDelete ? (
            <button
              type="button"
              onClick={onDelete}
              style={{ marginTop: 4, borderRadius: 8, border: `1px solid #ffd0d8`, background: "#fff5f7", color: colors.red, padding: "6px 10px", fontSize: 12, fontWeight: 700, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 6 }}
            >
              <Trash2 size={13} />
              Delete
            </button>
          ) : null}
        </div>
      </div>

      {matrixMeta.escalationHint ? (
        <div style={{ borderRadius: 12, border: `1px solid ${colors.border}`, background: "#fbfdff", padding: "10px 12px", color: colors.sub, fontSize: 12.5, lineHeight: 1.6 }}>
          Escalation: {matrixMeta.escalationHint}
        </div>
      ) : null}

      {task.task_type === "system" && isOpen ? (
        <div style={{ borderRadius: 12, background: "#fbf7ff", border: "1px solid #eadbff", padding: "12px 12px", display: "grid", gap: 10 }}>
          <div style={{ color: colors.violet, fontSize: 12.5, fontWeight: 700 }}>
            {systemGuidance?.intro ?? (task.recommended_action
              ? "Beacon can handle this automatically if you accept the recommendation."
              : "Beacon flagged this recommendation for the team. Mark it reviewed once you've handled it.")}
          </div>
          {systemGuidance?.steps?.length ? (
            <div style={{ display: "grid", gap: 7, padding: "0 2px" }}>
              {systemGuidance.steps.map((step) => (
                <div key={step} style={{ display: "flex", alignItems: "flex-start", gap: 8, color: colors.sub, fontSize: 12.5, lineHeight: 1.6 }}>
                  <span style={{ width: 18, height: 18, borderRadius: 999, background: "#efe5ff", color: colors.violet, fontSize: 11, fontWeight: 800, display: "inline-flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                    •
                  </span>
                  <span>{step}</span>
                </div>
              ))}
            </div>
          ) : null}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}>
            <button type="button" onClick={onManualTakeover} style={{ borderRadius: 8, border: "1px solid #d8c7ff", background: "#fff", color: colors.violet, padding: "6px 10px", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
              I'll do it myself
            </button>
            <button type="button" onClick={onDismiss} style={{ borderRadius: 8, border: `1px solid ${colors.border}`, background: "#fff", color: colors.sub, padding: "6px 10px", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
              Dismiss
            </button>
            <button type="button" onClick={onAccept} style={{ borderRadius: 8, border: `1px solid ${colors.primary}`, background: colors.primary, color: "#fff", padding: "6px 10px", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
              {task.recommended_action ? "Let Beacon do it" : "Mark reviewed"}
            </button>
          </div>
        </div>
      ) : null}

      {isOpen ? (
        <div style={{ borderRadius: 12, border: `1px solid ${colors.border}`, background: "#fbfdff", padding: "10px 12px", display: "grid", gap: 9 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
            <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.3 }}>OUTCOME LOG</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {templates.map((template) => (
                <button
                  key={template.label}
                  type="button"
                  onClick={() => onOutcomeDraftChange(template.body)}
                  style={{ borderRadius: 8, border: `1px solid ${colors.border}`, background: "#fff", color: colors.sub, padding: "5px 8px", fontSize: 11, fontWeight: 700, cursor: "pointer" }}
                >
                  {template.label}
                </button>
              ))}
            </div>
          </div>
          <textarea
            value={outcomeDraft}
            onChange={(e) => onOutcomeDraftChange(e.target.value)}
            placeholder="Log the result before completing..."
            style={{ width: "100%", minHeight: 66, borderRadius: 10, border: `1px solid ${colors.border}`, padding: "9px 10px", fontSize: 13, fontFamily: "inherit", outline: "none", resize: "vertical", background: "#fff" }}
          />
        </div>
      ) : null}

      {task.task_type === "manual" && isOpen ? (
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, flexWrap: "wrap" }}>
          <button type="button" onClick={onDismiss} style={{ borderRadius: 8, border: `1px solid ${colors.border}`, background: "#fff", color: colors.sub, padding: "6px 10px", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
            Dismiss
          </button>
          <button type="button" onClick={onComplete} style={{ borderRadius: 8, border: `1px solid ${colors.green}`, background: colors.green, color: "#fff", padding: "6px 10px", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
            Complete
          </button>
        </div>
      ) : null}

      <MeetingFollowUpBlock task={task} />

      <div style={{ display: "grid", gap: 8 }}>
        <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.3 }}>COMMENTS</div>
        {task.comments.length === 0 ? (
          <div style={{ color: colors.faint, fontSize: 12.5 }}>No comments yet.</div>
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            {task.comments.map((comment) => (
              <div key={comment.id} style={{ borderRadius: 12, background: "#fbfdff", border: `1px solid ${colors.border}`, padding: "10px 12px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
                  <div style={{ color: colors.text, fontSize: 12.5, fontWeight: 700 }}>{comment.created_by_name || "Beacon"}</div>
                  <div style={{ color: colors.faint, fontSize: 11 }}>{formatDate(comment.created_at)}</div>
                </div>
                <div style={{ color: colors.sub, fontSize: 12.5, lineHeight: 1.55, marginTop: 6 }}>{comment.body}</div>
              </div>
            ))}
          </div>
        )}
        <div style={{ display: "grid", gap: 6 }}>
          {templates.length > 0 ? (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {templates.map((template) => (
                <button
                  key={`comment-${template.label}`}
                  type="button"
                  onClick={() => onCommentDraftChange(template.body)}
                  style={{ borderRadius: 8, border: `1px solid ${colors.border}`, background: "#fff", color: colors.sub, padding: "5px 8px", fontSize: 11, fontWeight: 700, cursor: "pointer" }}
                >
                  Use {template.label.toLowerCase()}
                </button>
              ))}
            </div>
          ) : null}
          <textarea
            value={commentDraft}
            onChange={(e) => onCommentDraftChange(e.target.value)}
            placeholder="Add a quick comment or update..."
            style={{ width: "100%", minHeight: 68, borderRadius: 12, border: `1px solid ${colors.border}`, padding: "10px 12px", fontSize: 13, fontFamily: "inherit", outline: "none", resize: "vertical" }}
          />
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button type="button" onClick={onAddComment} disabled={!commentDraft.trim()} style={{ borderRadius: 8, border: "none", background: commentDraft.trim() ? colors.primary : "#e8eef5", color: commentDraft.trim() ? "#fff" : "#94a3b8", padding: "7px 10px", fontSize: 12, fontWeight: 700, cursor: commentDraft.trim() ? "pointer" : "default" }}>
              Add comment
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function isMeaningfulTaskTitle(value: string) {
  const title = value.trim();
  return title.length >= 3 && !/^[\d\s.,_-]+$/.test(title);
}

function TaskCenterModal({
  isOpen,
  onClose,
  entityType,
  entityId,
  entityLabel,
  onChanged,
  mode = "modal",
}: {
  isOpen?: boolean;
  onClose?: () => void;
  entityType: "company" | "contact" | "deal";
  entityId: string;
  entityLabel: string;
  onChanged?: () => void;
  mode?: "modal" | "inline";
}) {
  const { user } = useAuth();
  const cacheKey = taskCacheKey(entityType, entityId);
  const [tasks, setTasks] = useState<TaskItem[]>(() => getCachedTaskList(cacheKey)?.items ?? []);
  const [loading, setLoading] = useState(() => !getCachedTaskList(cacheKey));
  const [refreshing, setRefreshing] = useState(false);
  const [queuedRefresh, setQueuedRefresh] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState<"low" | "medium" | "high">("medium");
  const [dueAt, setDueAt] = useState("");
  const [assignedToId, setAssignedToId] = useState("");
  const [commentDrafts, setCommentDrafts] = useState<Record<string, string>>({});
  const [outcomeDrafts, setOutcomeDrafts] = useState<Record<string, string>>({});
  const [users, setUsers] = useState<UserType[]>([]);
  const isVisible = mode === "inline" ? true : Boolean(isOpen);
  const refreshPollRef = useRef<number | null>(null);

  const load = async (refreshMode: TaskRefreshMode = "auto", options?: { showSpinner?: boolean }) => {
    if (!isVisible) return;
    const cached = getCachedTaskList(cacheKey);
    const shouldBlock = options?.showSpinner ?? !cached;
    if (shouldBlock) {
      setLoading(true);
    } else {
      setRefreshing(true);
    }
    try {
      const result = await tasksApi.listDetailed(entityType, entityId, true, refreshMode);
      setTasks(result.items);
      taskListCache.set(cacheKey, { items: result.items, fetchedAt: Date.now() });
      const didQueueRefresh = result.refreshMode === "queued";
      setQueuedRefresh(didQueueRefresh);
      if (refreshPollRef.current) {
        window.clearTimeout(refreshPollRef.current);
        refreshPollRef.current = null;
      }
      if (didQueueRefresh) {
        refreshPollRef.current = window.setTimeout(() => {
          void load("none", { showSpinner: false });
        }, 1800);
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    const cached = getCachedTaskList(cacheKey);
    setTasks(cached?.items ?? []);
    setLoading(!cached);
    setQueuedRefresh(false);
    setRefreshing(false);
    if (refreshPollRef.current) {
      window.clearTimeout(refreshPollRef.current);
      refreshPollRef.current = null;
    }
    if (isVisible) {
      void load("auto", { showSpinner: !cached });
      authApi.listAllUsers().then(setUsers).catch(() => setUsers([]));
    }
  }, [cacheKey, isVisible, entityType, entityId]);

  useEffect(() => () => {
    if (refreshPollRef.current) {
      window.clearTimeout(refreshPollRef.current);
      refreshPollRef.current = null;
    }
  }, []);

  useEffect(() => {
    setAssignedToId(user?.id || "");
  }, [entityType, entityId, user?.id]);

  const openSystemTasks = useMemo(
    () => tasks.filter((task) => task.status === "open" && task.task_type === "system"),
    [tasks],
  );
  const openCriticalTasks = useMemo(
    () => openSystemTasks.filter((task) => task.task_track === "critical"),
    [openSystemTasks],
  );
  const openSalesAiTasks = useMemo(
    () => openSystemTasks.filter((task) => task.task_track === "sales_ai"),
    [openSystemTasks],
  );
  const openHygieneTasks = useMemo(
    // Catch-all for anything that isn't critical or sales_ai — covers the
    // explicit "hygiene" track plus any legacy rows that predate the migration.
    () => openSystemTasks.filter((task) => task.task_track !== "critical" && task.task_track !== "sales_ai"),
    [openSystemTasks],
  );
  const openManualTasks = useMemo(
    () => tasks.filter((task) => task.status === "open" && task.task_type === "manual"),
    [tasks],
  );
  const historyTasks = useMemo(() => tasks.filter((task) => task.status !== "open"), [tasks]);
  const [hygieneOpen, setHygieneOpen] = useState(false);

  const resetForm = () => {
    setTitle("");
    setDescription("");
    setPriority("medium");
    setDueAt("");
    setAssignedToId(user?.id || "");
    setShowCreate(false);
  };

  const assigneeOptions = useMemo(() => users, [users]);

  const createTask = async () => {
    if (!isMeaningfulTaskTitle(title)) return;
    await tasksApi.create({
      entity_type: entityType,
      entity_id: entityId,
      title: title.trim(),
      description: description.trim() || undefined,
      priority,
      due_at: dueAt || undefined,
      assigned_to_id: assignedToId || undefined,
    });
    resetForm();
    onChanged?.();
    await load("none", { showSpinner: false });
  };

  const patchTask = async (taskId: string, data: Parameters<typeof tasksApi.update>[1]) => {
    await tasksApi.update(taskId, data);
    onChanged?.();
    await load("none", { showSpinner: false });
  };

  const completeTask = async (task: TaskItem) => {
    const outcome = (outcomeDrafts[task.id] || "").trim();
    if (outcome) {
      await tasksApi.addComment(task.id, outcome.startsWith("Outcome:") ? outcome : `Outcome: ${outcome}`);
      setOutcomeDrafts((current) => ({ ...current, [task.id]: "" }));
    }
    await patchTask(task.id, { status: "completed" });
  };

  const addComment = async (taskId: string) => {
    const body = (commentDrafts[taskId] || "").trim();
    if (!body) return;
    await tasksApi.addComment(taskId, body);
    setCommentDrafts((current) => ({ ...current, [taskId]: "" }));
    onChanged?.();
    await load("none", { showSpinner: false });
  };

  const acceptTask = async (taskId: string) => {
    await tasksApi.accept(taskId);
    onChanged?.();
    await load("none", { showSpinner: false });
  };

  const takeManualOwnership = async (task: TaskItem) => {
    await tasksApi.create({
      entity_type: task.entity_type,
      entity_id: task.entity_id,
      title: task.title,
      description: task.description ? `${task.description}\n\nManual follow-up chosen by rep.` : "Manual follow-up chosen by rep.",
      priority: task.priority === "high" ? "high" : task.priority === "low" ? "low" : "medium",
      due_at: task.due_at,
      assigned_to_id: user?.id,
    });
    await tasksApi.update(task.id, { status: "dismissed" });
    onChanged?.();
    await load("none", { showSpinner: false });
  };

  const deleteTask = async (task: TaskItem) => {
    if (!window.confirm(`Delete "${task.title}"?`)) return;
    await tasksApi.remove(task.id);
    onChanged?.();
    await load("none", { showSpinner: false });
  };

  const refreshRecommendations = async () => {
    await load("force", { showSpinner: false });
  };

  if (!isVisible) return null;

  const content = (
    <div
      style={{
        overflowY: "auto",
        padding: mode === "inline" ? 0 : 20,
        background: mode === "inline" ? "transparent" : "#fbfdff",
        display: "grid",
        gap: 14,
      }}
    >
      <div style={{ display: "grid", gap: 12 }}>
        <div
          style={{
            border: `1px solid ${colors.border}`,
            background: "#fff",
            borderRadius: 16,
            padding: "14px 16px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
            flexWrap: "wrap",
          }}
        >
          <div style={{ display: "grid", gap: 6 }}>
            <div style={{ color: colors.text, fontWeight: 800, fontSize: 15 }}>Task framework</div>
            <div style={{ color: colors.sub, fontSize: 13, lineHeight: 1.55 }}>
              Manual tasks keep human follow-ups visible. System tasks are Beacon recommendations created from synced signals and record state.
            </div>
            {entityType === "deal" && (refreshing || queuedRefresh) ? (
              <div style={{ color: colors.faint, fontSize: 12.5, lineHeight: 1.5 }}>
                {queuedRefresh ? "Beacon is updating recommendations in the background. Stored tasks stay visible while it refreshes." : "Checking whether this deal has fresher recommendation inputs..."}
              </div>
            ) : null}
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {openCriticalTasks.length > 0 ? (
              <span style={{ borderRadius: 999, padding: "5px 10px", background: colors.redSoft, color: colors.red, border: "1px solid #ffd0d8", fontSize: 12, fontWeight: 800 }}>
                {openCriticalTasks.length} critical
              </span>
            ) : null}
            <span style={{ borderRadius: 999, padding: "5px 10px", background: colors.violetSoft, color: colors.violet, border: "1px solid #eadbff", fontSize: 12, fontWeight: 800 }}>
              {openSalesAiTasks.length} sales AI
            </span>
            <span style={{ borderRadius: 999, padding: "5px 10px", background: "#f4f7fb", color: colors.sub, border: `1px solid ${colors.border}`, fontSize: 12, fontWeight: 800 }}>
              {openHygieneTasks.length} hygiene
            </span>
            <span style={{ borderRadius: 999, padding: "5px 10px", background: colors.primarySoft, color: colors.primary, border: "1px solid #d5e5ff", fontSize: 12, fontWeight: 800 }}>
              {openManualTasks.length} manual
            </span>
            <span style={{ borderRadius: 999, padding: "5px 10px", background: "#f4f7fb", color: colors.sub, border: `1px solid ${colors.border}`, fontSize: 12, fontWeight: 800 }}>
              {historyTasks.length} history
            </span>
          </div>
        </div>

        {showCreate ? (
          <div style={{ border: `1px solid ${colors.border}`, background: "#fff", borderRadius: 16, padding: "14px 16px", display: "grid", gap: 10 }}>
            <div style={{ color: colors.text, fontWeight: 800 }}>Add manual task</div>
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title" style={{ height: 42, borderRadius: 12, border: `1px solid ${colors.border}`, padding: "0 12px", fontSize: 13, outline: "none" }} />
            <textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Description or context" style={{ minHeight: 84, borderRadius: 12, border: `1px solid ${colors.border}`, padding: "10px 12px", fontSize: 13, outline: "none", resize: "vertical", fontFamily: "inherit" }} />
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <select value={priority} onChange={(e) => setPriority(e.target.value as "low" | "medium" | "high")} style={{ height: 42, borderRadius: 12, border: `1px solid ${colors.border}`, padding: "0 12px", fontSize: 13, background: "#fff", outline: "none" }}>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
              <input type="datetime-local" value={dueAt} onChange={(e) => setDueAt(e.target.value)} style={{ height: 42, borderRadius: 12, border: `1px solid ${colors.border}`, padding: "0 12px", fontSize: 13, outline: "none" }} />
            </div>
            <div style={{ display: "grid", gap: 10 }}>
              <select value={assignedToId} onChange={(e) => setAssignedToId(e.target.value)} style={{ height: 42, borderRadius: 12, border: `1px solid ${colors.border}`, padding: "0 12px", fontSize: 13, background: "#fff", outline: "none" }}>
                <option value="">Unassigned</option>
                {assigneeOptions.map((user) => (
                  <option key={user.id} value={user.id}>{user.name} · {user.role.toUpperCase()}</option>
                ))}
              </select>
            </div>
            <div style={{ color: colors.faint, fontSize: 12, lineHeight: 1.5 }}>
              Assign the task to the teammate who owns it right now so it shows up in their personal task queue.
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button type="button" onClick={resetForm} style={{ borderRadius: 10, border: `1px solid ${colors.border}`, background: "#fff", color: colors.sub, padding: "8px 12px", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
                Cancel
              </button>
              <button type="button" onClick={createTask} disabled={!isMeaningfulTaskTitle(title)} style={{ borderRadius: 10, border: "none", background: isMeaningfulTaskTitle(title) ? colors.primary : "#e8eef5", color: isMeaningfulTaskTitle(title) ? "#fff" : "#94a3b8", padding: "8px 12px", fontSize: 12, fontWeight: 700, cursor: isMeaningfulTaskTitle(title) ? "pointer" : "default" }}>
                Save task
              </button>
            </div>
          </div>
        ) : null}

        {loading ? (
          <div style={{ color: colors.faint, padding: "8px 4px" }}>Loading tasks…</div>
        ) : (
          <>
            {openCriticalTasks.length > 0 ? (
              <div style={{ display: "grid", gap: 10 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, color: colors.red, fontWeight: 800 }}>
                  <AlertTriangle size={14} color={colors.red} />
                  <span>Critical — Action Required</span>
                </div>
                {openCriticalTasks.map((task) => (
                  <TaskCard
                    key={task.id}
                    task={task}
                    entityLabel={entityLabel}
                    commentDraft={commentDrafts[task.id] || ""}
                    outcomeDraft={outcomeDrafts[task.id] || ""}
                    onCommentDraftChange={(value) => setCommentDrafts((current) => ({ ...current, [task.id]: value }))}
                    onOutcomeDraftChange={(value) => setOutcomeDrafts((current) => ({ ...current, [task.id]: value }))}
                    onAddComment={() => addComment(task.id)}
                    onComplete={() => completeTask(task)}
                    onDismiss={() => patchTask(task.id, { status: "dismissed" })}
                    onAccept={() => acceptTask(task.id)}
                    onManualTakeover={() => takeManualOwnership(task)}
                    onDelete={() => deleteTask(task)}
                    canDelete={Boolean(user && (user.role === "admin" || user.id === task.created_by_id))}
                  />
                ))}
              </div>
            ) : null}

            <div style={{ display: "grid", gap: 10 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, color: colors.text, fontWeight: 800 }}>
                <Sparkles size={14} color={colors.violet} />
                <span>Sales AI — CRM Updates</span>
              </div>
              {openSalesAiTasks.length === 0 ? (
                <div style={{ border: `1px dashed ${colors.border}`, borderRadius: 16, background: "#fff", padding: "22px 18px", color: colors.faint }}>
                  No active CRM-update recommendations. When buyer signals imply a stage, amount, close-date, MEDDPICC, or stakeholder change, Beacon will surface it here.
                </div>
              ) : (
                openSalesAiTasks.map((task) => (
                  <TaskCard
                    key={task.id}
                    task={task}
                    entityLabel={entityLabel}
                    commentDraft={commentDrafts[task.id] || ""}
                    outcomeDraft={outcomeDrafts[task.id] || ""}
                    onCommentDraftChange={(value) => setCommentDrafts((current) => ({ ...current, [task.id]: value }))}
                    onOutcomeDraftChange={(value) => setOutcomeDrafts((current) => ({ ...current, [task.id]: value }))}
                    onAddComment={() => addComment(task.id)}
                    onComplete={() => completeTask(task)}
                    onDismiss={() => patchTask(task.id, { status: "dismissed" })}
                    onAccept={() => acceptTask(task.id)}
                    onManualTakeover={() => takeManualOwnership(task)}
                    onDelete={() => deleteTask(task)}
                    canDelete={Boolean(user && (user.role === "admin" || user.id === task.created_by_id))}
                  />
                ))
              )}
            </div>

            {openHygieneTasks.length > 0 ? (
              <div style={{ display: "grid", gap: 10 }}>
                <button
                  type="button"
                  onClick={() => setHygieneOpen((current) => !current)}
                  style={{ display: "flex", alignItems: "center", gap: 8, background: "transparent", border: "none", color: colors.sub, fontWeight: 800, cursor: "pointer", padding: 0, textAlign: "left" }}
                >
                  {hygieneOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  <Wrench size={13} color={colors.faint} />
                  <span>Hygiene ({openHygieneTasks.length})</span>
                </button>
                {hygieneOpen
                  ? openHygieneTasks.map((task) => (
                      <TaskCard
                        key={task.id}
                        task={task}
                        entityLabel={entityLabel}
                        commentDraft={commentDrafts[task.id] || ""}
                        outcomeDraft={outcomeDrafts[task.id] || ""}
                        onCommentDraftChange={(value) => setCommentDrafts((current) => ({ ...current, [task.id]: value }))}
                        onOutcomeDraftChange={(value) => setOutcomeDrafts((current) => ({ ...current, [task.id]: value }))}
                        onAddComment={() => addComment(task.id)}
                        onComplete={() => completeTask(task)}
                        onDismiss={() => patchTask(task.id, { status: "dismissed" })}
                        onAccept={() => acceptTask(task.id)}
                        onManualTakeover={() => takeManualOwnership(task)}
                        onDelete={() => deleteTask(task)}
                        canDelete={Boolean(user && (user.role === "admin" || user.id === task.created_by_id))}
                      />
                    ))
                  : null}
              </div>
            ) : null}

            <div style={{ display: "grid", gap: 10 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, color: colors.text, fontWeight: 800 }}>
                <MessageSquare size={14} color={colors.primary} />
                <span>Manual Tasks</span>
              </div>
              {openManualTasks.length === 0 ? (
                <div style={{ border: `1px dashed ${colors.border}`, borderRadius: 16, background: "#fff", padding: "22px 18px", color: colors.faint }}>
                  No open manual tasks. Add lightweight follow-ups here when the team wants to track work that Beacon cannot automate yet.
                </div>
              ) : (
                openManualTasks.map((task) => (
                  <TaskCard
                    key={task.id}
                    task={task}
                    entityLabel={entityLabel}
                    commentDraft={commentDrafts[task.id] || ""}
                    outcomeDraft={outcomeDrafts[task.id] || ""}
                    onCommentDraftChange={(value) => setCommentDrafts((current) => ({ ...current, [task.id]: value }))}
                    onOutcomeDraftChange={(value) => setOutcomeDrafts((current) => ({ ...current, [task.id]: value }))}
                    onAddComment={() => addComment(task.id)}
                    onComplete={() => completeTask(task)}
                    onDismiss={() => patchTask(task.id, { status: "dismissed" })}
                    onAccept={() => acceptTask(task.id)}
                    onManualTakeover={() => takeManualOwnership(task)}
                    onDelete={() => deleteTask(task)}
                    canDelete={Boolean(user && (user.role === "admin" || user.id === task.created_by_id))}
                  />
                ))
              )}
            </div>

            {historyTasks.length > 0 ? (
              <div style={{ display: "grid", gap: 10 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, color: colors.text, fontWeight: 800, marginTop: 4 }}>
                  <Clock3 size={14} color={colors.faint} />
                  <span>History</span>
                </div>
                {historyTasks.map((task) => (
                  <TaskCard
                    key={task.id}
                    task={task}
                    entityLabel={entityLabel}
                    commentDraft={commentDrafts[task.id] || ""}
                    outcomeDraft={outcomeDrafts[task.id] || ""}
                    onCommentDraftChange={(value) => setCommentDrafts((current) => ({ ...current, [task.id]: value }))}
                    onOutcomeDraftChange={(value) => setOutcomeDrafts((current) => ({ ...current, [task.id]: value }))}
                    onAddComment={() => addComment(task.id)}
                    onComplete={() => completeTask(task)}
                    onDismiss={() => patchTask(task.id, { status: "dismissed" })}
                    onAccept={() => acceptTask(task.id)}
                    onManualTakeover={() => takeManualOwnership(task)}
                    onDelete={() => deleteTask(task)}
                    canDelete={Boolean(user && (user.role === "admin" || user.id === task.created_by_id))}
                  />
                ))}
              </div>
            ) : null}
          </>
        )}
      </div>
    </div>
  );

  if (mode === "inline") {
    return (
      <div style={{ display: "grid", gap: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 16, alignItems: "start", flexWrap: "wrap" }}>
          <div style={{ display: "grid", gap: 8 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, color: colors.text, fontWeight: 800 }}>
              <CheckCircle2 size={16} color={colors.primary} />
              <span>Tasks</span>
            </div>
            <div style={{ color: colors.sub, fontSize: 13, lineHeight: 1.55 }}>
              Track manual follow-ups and review system-recommended actions for {entityLabel}.
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            {entityType === "deal" ? (
              <button type="button" onClick={() => void refreshRecommendations()} disabled={refreshing} style={{ borderRadius: 10, border: `1px solid ${colors.border}`, background: "#fff", color: colors.sub, padding: "9px 12px", fontSize: 12, fontWeight: 700, cursor: refreshing ? "default" : "pointer", display: "inline-flex", gap: 6, alignItems: "center", opacity: refreshing ? 0.7 : 1 }}>
                <Sparkles size={14} /> {refreshing ? "Refreshing..." : "Refresh AI"}
              </button>
            ) : null}
            <button type="button" onClick={() => setShowCreate((current) => !current)} style={{ borderRadius: 10, border: `1px solid ${colors.primary}`, background: colors.primary, color: "#fff", padding: "9px 12px", fontSize: 12, fontWeight: 700, cursor: "pointer", display: "inline-flex", gap: 6, alignItems: "center" }}>
              <Plus size={14} /> Manual task
            </button>
          </div>
        </div>
        {content}
      </div>
    );
  }

  return (
    <>
      <div onClick={() => onClose?.()} style={{ position: "fixed", inset: 0, background: "rgba(15, 23, 42, 0.36)", backdropFilter: "blur(3px)", zIndex: 1400 }} />
      <div style={{ position: "fixed", inset: 0, zIndex: 1401, display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
        <div style={{ width: "min(920px, 100%)", maxHeight: "84vh", display: "grid", gridTemplateRows: "auto minmax(0,1fr)", overflow: "hidden", background: "#fff", borderRadius: 20, border: `1px solid ${colors.border}`, boxShadow: "0 24px 60px rgba(15, 23, 42, 0.18)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 16, alignItems: "start", padding: "20px 22px 16px", borderBottom: `1px solid ${colors.border}` }}>
            <div style={{ display: "grid", gap: 8 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, color: colors.text, fontWeight: 800 }}>
                <CheckCircle2 size={16} color={colors.primary} />
                <span>Tasks</span>
              </div>
              <div style={{ color: colors.sub, fontSize: 13, lineHeight: 1.55 }}>
                Track manual follow-ups and review system-recommended actions for {entityLabel}.
              </div>
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              {entityType === "deal" ? (
                <button type="button" onClick={() => void refreshRecommendations()} disabled={refreshing} style={{ borderRadius: 10, border: `1px solid ${colors.border}`, background: "#fff", color: colors.sub, padding: "9px 12px", fontSize: 12, fontWeight: 700, cursor: refreshing ? "default" : "pointer", display: "inline-flex", gap: 6, alignItems: "center", opacity: refreshing ? 0.7 : 1 }}>
                  <Sparkles size={14} /> {refreshing ? "Refreshing..." : "Refresh AI"}
                </button>
              ) : null}
              <button type="button" onClick={() => setShowCreate((current) => !current)} style={{ borderRadius: 10, border: `1px solid ${colors.primary}`, background: colors.primary, color: "#fff", padding: "9px 12px", fontSize: 12, fontWeight: 700, cursor: "pointer", display: "inline-flex", gap: 6, alignItems: "center" }}>
                <Plus size={14} /> Manual task
              </button>
              <button type="button" onClick={() => onClose?.()} style={{ width: 36, height: 36, borderRadius: 10, border: `1px solid ${colors.border}`, background: "#fff", color: colors.sub, display: "grid", placeItems: "center", cursor: "pointer" }}>
                <X size={16} />
              </button>
            </div>
          </div>
          {content}
        </div>
      </div>
    </>
  );
}

export default memo(TaskCenterModal);

import { useEffect, useMemo, useState } from "react";
import { Calendar, CheckCircle2, Clock3, ExternalLink, Filter, MessageSquare, Plus, Sparkles, Trash2, X } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";

import { globalSearchApi, tasksApi } from "../lib/api";
import { useAuth } from "../lib/AuthContext";
import { formatDate, formatDomain, isPlaceholderDomain } from "../lib/utils";
import type { GlobalSearchItem, TaskWorkspaceItem } from "../types";
import { getSystemTaskGuidance } from "../components/tasks/systemTaskGuidance";
import { SkeletonList } from "../components/ui/Skeleton";

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

type TaskPriorityLabel = "P0" | "P1" | "P2";

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

function displayEntitySubtitle(value?: string | null) {
  return isPlaceholderDomain(value) ? formatDomain(value) : value;
}

function getTaskTemplates(task: TaskWorkspaceItem) {
  const entity = task.entity_name || "this account";
  const lowerText = `${task.title} ${task.description ?? ""}`.toLowerCase();
  const templates = [
    {
      label: "Call outcome",
      body: `Outcome: Called ${entity}.\nResult: Connected / voicemail / no answer\nNotes:\nNext step:`,
    },
    {
      label: "Email follow-up",
      body: `Outcome: Sent follow-up to ${entity}.\nMessage angle:\nNext step:`,
    },
  ];

  if (lowerText.includes("close date") || lowerText.includes("forecast")) {
    templates.unshift({
      label: "Forecast hygiene",
      body: `Outcome: Reviewed forecast hygiene for ${entity}.\nClose date update:\nConfidence:\nNext step:`,
    });
  } else if (lowerText.includes("meeting") || lowerText.includes("demo")) {
    templates.unshift({
      label: "Meeting follow-up",
      body: `Outcome: Followed up on ${entity} meeting.\nCustomer ask:\nOwner:\nNext step/date:`,
    });
  } else if (task.entity_type === "deal") {
    templates.unshift({
      label: "Deal next step",
      body: `Outcome: Updated deal next step for ${entity}.\nBuyer commitment:\nRep action:\nDue date:`,
    });
  }

  return templates.slice(0, 3);
}

function MeetingFollowUpBlock({ task }: { task: TaskWorkspaceItem }) {
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
              className="crm-button soft"
              style={{ background: copied ? colors.greenSoft : "#fff", color: copied ? colors.green : colors.sub, borderColor: copied ? "#cdeedc" : colors.border }}
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

type TaskStatusFilter = "open" | "completed" | "dismissed" | "all";
type TaskTypeFilter = "all" | "manual" | "system";
type EntityFilter = "all" | "company" | "contact" | "deal";
type CreateTaskEntityType = Exclude<EntityFilter, "all">;
type TaskPriority = "low" | "medium" | "high";
type QueueScope = "mine" | "team";
type DueDateFilter = "all" | "overdue" | "today" | "tomorrow" | "this_week" | "upcoming" | "unscheduled";

type DueBadge = {
  label: string;
  exactLabel: string;
  tone: {
    background: string;
    border: string;
    color: string;
  };
  bucket: Exclude<DueDateFilter, "all">;
};

function startOfLocalDay(date: Date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function diffInDays(from: Date, to: Date) {
  return Math.round((startOfLocalDay(to).getTime() - startOfLocalDay(from).getTime()) / 86_400_000);
}

function getDueBadge(dueAt?: string | null, status?: string): DueBadge | null {
  if (!dueAt) return null;

  const dueDate = new Date(dueAt);
  if (Number.isNaN(dueDate.getTime())) return null;

  const today = startOfLocalDay(new Date());
  const daysUntilDue = diffInDays(today, dueDate);
  const exactLabel = formatDate(dueAt);

  if (status === "completed") {
    return {
      label: `Completed · was due ${exactLabel}`,
      exactLabel,
      bucket: "upcoming",
      tone: { background: colors.greenSoft, border: "#cdeedc", color: colors.green },
    };
  }

  if (daysUntilDue < 0) {
    const overdueDays = Math.abs(daysUntilDue);
    return {
      label: overdueDays === 1 ? "Overdue by 1 day" : `Overdue by ${overdueDays} days`,
      exactLabel,
      bucket: "overdue",
      tone: { background: colors.redSoft, border: "#ffd0d8", color: colors.red },
    };
  }

  if (daysUntilDue === 0) {
    return {
      label: "Due today",
      exactLabel,
      bucket: "today",
      tone: { background: colors.amberSoft, border: "#ffe3b3", color: colors.amber },
    };
  }

  if (daysUntilDue === 1) {
    return {
      label: "Due tomorrow",
      exactLabel,
      bucket: "tomorrow",
      tone: { background: "#eef5ff", border: "#d5e5ff", color: colors.primary },
    };
  }

  if (daysUntilDue <= 7) {
    return {
      label: "Due this week",
      exactLabel,
      bucket: "this_week",
      tone: { background: "#eef5ff", border: "#d5e5ff", color: colors.primary },
    };
  }

  return {
    label: "Upcoming",
    exactLabel,
    bucket: "upcoming",
    tone: { background: "#f6f7fb", border: colors.border, color: colors.sub },
  };
}

function TaskWorkspaceCard({
  task,
  commentDraft,
  outcomeDraft,
  onCommentDraftChange,
  onOutcomeDraftChange,
  onAddComment,
  onAccept,
  onComplete,
  onDismiss,
  onManualTakeover,
  onDelete,
  onReschedule,
  canDelete,
}: {
  task: TaskWorkspaceItem;
  commentDraft: string;
  outcomeDraft: string;
  onCommentDraftChange: (value: string) => void;
  onOutcomeDraftChange: (value: string) => void;
  onAddComment: () => void;
  onAccept: () => void;
  onComplete: () => void;
  onDismiss: () => void;
  onManualTakeover: () => void;
  onDelete: () => void;
  onReschedule: (newDate: string) => void;
  canDelete: boolean;
}) {
  const priorityStyle = PRIORITY_STYLE[task.priority as keyof typeof PRIORITY_STYLE] ?? PRIORITY_STYLE.normal;
  const typeStyle = TYPE_STYLE[task.task_type];
  const isOpen = task.status === "open";
  const [showReschedule, setShowReschedule] = useState(false);
  const systemGuidance = getSystemTaskGuidance(task);
  const dueBadge = getDueBadge(task.due_at, task.status);
  const matrixMeta = parseTaskMatrixMeta(task.action_payload);
  const priorityLabelStyle = matrixMeta.priorityLabel ? PRIORITY_LABEL_STYLE[matrixMeta.priorityLabel] : null;
  const templates = getTaskTemplates(task);

  return (
    <div className="crm-panel crm-hover-lift" style={{ padding: 18, boxShadow: "none", display: "grid", gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap", alignItems: "start" }}>
        <div style={{ display: "flex", gap: 12, alignItems: "start" }}>
          {isOpen && (
            <input
              type="checkbox"
              checked={false}
              onChange={() => task.task_type === "manual" ? onComplete() : onAccept()}
              title={task.task_type === "manual" ? "Complete task" : task.recommended_action ? "Accept recommendation" : "Mark reviewed"}
              style={{ width: 20, height: 20, marginTop: 2, cursor: "pointer", accentColor: task.task_type === "system" ? "#7c3aed" : colors.green, flexShrink: 0 }}
            />
          )}
          {!isOpen && (
            <div style={{ width: 20, height: 20, marginTop: 2, borderRadius: 4, background: task.status === "completed" ? colors.greenSoft : "#eef2f7", border: `2px solid ${task.status === "completed" ? colors.green : "#cbd5e1"}`, display: "grid", placeItems: "center", flexShrink: 0 }}>
              {task.status === "completed" && <CheckCircle2 size={14} color={colors.green} />}
            </div>
          )}
        <div style={{ display: "grid", gap: 8 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
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
            <span className="crm-chip" style={{ background: "#f7f8fc", color: colors.sub, borderColor: colors.border }}>
              {task.entity_type}
            </span>
          </div>
          <div style={{ fontWeight: 800, fontSize: 16, color: colors.text }}>{task.title}</div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <Link to={task.entity_link} style={{ color: colors.primary, fontWeight: 700, fontSize: 14, textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 6 }}>
              {task.entity_name}
              <ExternalLink size={14} />
            </Link>
            {displayEntitySubtitle(task.entity_subtitle) ? <span style={{ color: colors.faint, fontSize: 13 }}>{displayEntitySubtitle(task.entity_subtitle)}</span> : null}
          </div>
          {task.description ? <div style={{ color: colors.sub, fontSize: 13.5, lineHeight: 1.6 }}>{task.description}</div> : null}
        </div>
        </div>
        <div style={{ display: "grid", gap: 4, justifyItems: "end", color: colors.faint, fontSize: 12 }}>
          <div>{formatDate(task.updated_at)}</div>
          {task.due_at ? <div>{dueBadge ? dueBadge.exactLabel : `Due ${formatDate(task.due_at)}`}</div> : null}
          {isOpen && (
            showReschedule ? (
              <input
                type="date"
                autoFocus
                defaultValue={task.due_at ? task.due_at.slice(0, 10) : ""}
                onBlur={(e) => { setShowReschedule(false); if (e.target.value) onReschedule(e.target.value); }}
                onKeyDown={(e) => { if (e.key === "Enter") { setShowReschedule(false); if ((e.target as HTMLInputElement).value) onReschedule((e.target as HTMLInputElement).value); } if (e.key === "Escape") setShowReschedule(false); }}
                style={{ fontSize: 12, border: `1px solid ${colors.border}`, borderRadius: 6, padding: "3px 6px" }}
              />
            ) : (
              <button type="button" onClick={() => setShowReschedule(true)} style={{ background: "none", border: `1px solid ${colors.border}`, borderRadius: 6, padding: "3px 8px", fontSize: 11, fontWeight: 700, color: colors.primary, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4 }}>
                <Calendar size={11} />Reschedule
              </button>
            )
          )}
          <div>{task.assigned_to_name || "Unassigned"}</div>
          {matrixMeta.ownerHint ? <div>Owner {matrixMeta.ownerHint}</div> : null}
          {matrixMeta.slaLabel ? <div>SLA {matrixMeta.slaLabel}</div> : null}
          <div>{task.created_by_name ? `Created by ${task.created_by_name}` : (task.source || "Beacon")}</div>
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
            <button type="button" onClick={onManualTakeover} className="crm-button soft" style={{ borderColor: "#d8c7ff", color: colors.violet }}>
              I'll do it myself
            </button>
            <button type="button" onClick={onDismiss} className="crm-button soft">Dismiss</button>
            <button type="button" onClick={onAccept} className="crm-button primary">
              {task.recommended_action ? "Let Beacon do it" : "Mark reviewed"}
            </button>
          </div>
        </div>
      ) : null}

      {isOpen ? (
        <div style={{ borderRadius: 14, border: `1px solid ${colors.border}`, background: "#fbfdff", padding: "12px 13px", display: "grid", gap: 10 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
            <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.3 }}>OUTCOME LOG</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {templates.map((template) => (
                <button
                  key={template.label}
                  type="button"
                  onClick={() => onOutcomeDraftChange(template.body)}
                  className="crm-button soft"
                  style={{ padding: "5px 8px", fontSize: 11 }}
                >
                  {template.label}
                </button>
              ))}
            </div>
          </div>
          <textarea
            value={outcomeDraft}
            onChange={(e) => onOutcomeDraftChange(e.target.value)}
            placeholder="Log the result before completing: what happened, buyer response, and next step..."
            style={{ width: "100%", minHeight: 76, borderRadius: 12, border: `1px solid ${colors.border}`, padding: "10px 12px", fontSize: 13, fontFamily: "inherit", outline: "none", resize: "vertical", background: "#fff" }}
          />
        </div>
      ) : null}

      {task.task_type === "manual" && isOpen ? (
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, flexWrap: "wrap" }}>
          <button type="button" onClick={onDismiss} className="crm-button soft">Dismiss</button>
          <button type="button" onClick={onComplete} className="crm-button primary" style={{ background: colors.green, borderColor: colors.green }}>
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
                  className="crm-button soft"
                  style={{ padding: "5px 8px", fontSize: 11 }}
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
            <button type="button" onClick={onAddComment} disabled={!commentDraft.trim()} className="crm-button soft">
              Add comment
            </button>
          </div>
        </div>
      </div>

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 10,
          flexWrap: "wrap",
          paddingTop: 8,
          borderTop: `1px solid ${colors.border}`,
        }}
      >
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {dueBadge ? (
            <span
              style={{
                borderRadius: 999,
                padding: "6px 10px",
                background: dueBadge.tone.background,
                border: `1px solid ${dueBadge.tone.border}`,
                color: dueBadge.tone.color,
                fontSize: 11.5,
                fontWeight: 800,
              }}
            >
              {dueBadge.label}
            </span>
          ) : (
            <span
              style={{
                borderRadius: 999,
                padding: "6px 10px",
                background: "#f6f7fb",
                border: `1px solid ${colors.border}`,
                color: colors.faint,
                fontSize: 11.5,
                fontWeight: 700,
              }}
            >
              No due date
            </span>
          )}
          <span
            style={{
              borderRadius: 999,
              padding: "6px 10px",
              background: "#f8fbff",
              border: `1px solid ${colors.border}`,
              color: colors.sub,
              fontSize: 11.5,
              fontWeight: 700,
            }}
          >
            {task.source || (task.task_type === "system" ? "Beacon system" : "Manual task")}
          </span>
        </div>
        {task.due_at ? (
          <div style={{ color: colors.faint, fontSize: 12.5 }}>
            Due on {formatDate(task.due_at)}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default function TasksPage() {
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [tasks, setTasks] = useState<TaskWorkspaceItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<TaskStatusFilter>(() => (searchParams.get("status") as TaskStatusFilter) ?? "open");
  const [typeFilter, setTypeFilter] = useState<TaskTypeFilter>(() => (searchParams.get("type") as TaskTypeFilter) ?? "all");
  const [entityFilter, setEntityFilter] = useState<EntityFilter>(() => (searchParams.get("entity") as EntityFilter) ?? "all");
  const [dueDateFilter, setDueDateFilter] = useState<DueDateFilter>(() => (searchParams.get("due") as DueDateFilter) ?? "all");
  const [queueScope, setQueueScope] = useState<QueueScope>(() => (searchParams.get("scope") as QueueScope) ?? "mine");
  // Client-side pagination for the task list. Filtering happens above; we
  // just slice the filtered set into pages so the workspace doesn't render
  // hundreds of cards at once. Reset to page 1 whenever any filter changes
  // (otherwise the rep ends up on page 7 of a 2-page filtered result).
  const [tasksPage, setTasksPage] = useState(1);
  const [commentDrafts, setCommentDrafts] = useState<Record<string, string>>({});
  const [outcomeDrafts, setOutcomeDrafts] = useState<Record<string, string>>({});
  const [showCreateTask, setShowCreateTask] = useState(() => searchParams.get("new") === "task");
  const [creatingTask, setCreatingTask] = useState(false);
  const [createTaskForm, setCreateTaskForm] = useState({
    title: "",
    description: "",
    entity_type: "deal" as CreateTaskEntityType,
    entity_id: "",
    priority: "medium" as TaskPriority,
    due_at: "",
  });
  const [entitySearch, setEntitySearch] = useState("");
  const [entityOptions, setEntityOptions] = useState<GlobalSearchItem[]>([]);
  const [entitySearching, setEntitySearching] = useState(false);
  const [dealFilter, setDealFilter] = useState(() => searchParams.get("deal") || "");
  const [dealFilterLabel, setDealFilterLabel] = useState("");
  const [dealSearch, setDealSearch] = useState("");
  const [dealOptions, setDealOptions] = useState<GlobalSearchItem[]>([]);
  const [dealSearching, setDealSearching] = useState(false);
  const isAdmin = user?.role === "admin";

  // Sync filter state to URL
  useEffect(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      statusFilter !== "open" ? next.set("status", statusFilter) : next.delete("status");
      typeFilter !== "all" ? next.set("type", typeFilter) : next.delete("type");
      entityFilter !== "all" ? next.set("entity", entityFilter) : next.delete("entity");
      dueDateFilter !== "all" ? next.set("due", dueDateFilter) : next.delete("due");
      queueScope !== "mine" ? next.set("scope", queueScope) : next.delete("scope");
      dealFilter ? next.set("deal", dealFilter) : next.delete("deal");
      showCreateTask ? next.set("new", "task") : next.delete("new");
      return next;
    }, { replace: true });
  }, [statusFilter, typeFilter, entityFilter, dueDateFilter, queueScope, dealFilter, showCreateTask]);

  useEffect(() => {
    if (searchParams.get("new") === "task") setShowCreateTask(true);
  }, [searchParams]);

  useEffect(() => {
    const dealParam = searchParams.get("deal") || "";
    if (dealParam && dealParam !== dealFilter) setDealFilter(dealParam);
  }, [searchParams, dealFilter]);

  useEffect(() => {
    if (!showCreateTask || entitySearch.trim().length < 2) {
      setEntityOptions([]);
      return;
    }
    let cancelled = false;
    const handle = window.setTimeout(async () => {
      setEntitySearching(true);
      try {
        const result = await globalSearchApi.search(entitySearch.trim());
        const options = result.sections
          .flatMap((section) => section.items)
          .filter((item) => item.kind === createTaskForm.entity_type)
          .slice(0, 6);
        if (!cancelled) setEntityOptions(options);
      } catch {
        if (!cancelled) setEntityOptions([]);
      } finally {
        if (!cancelled) setEntitySearching(false);
      }
    }, 220);
    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
  }, [createTaskForm.entity_type, entitySearch, showCreateTask]);

  useEffect(() => {
    if (dealSearch.trim().length < 2) {
      setDealOptions([]);
      return;
    }
    let cancelled = false;
    const handle = window.setTimeout(async () => {
      setDealSearching(true);
      try {
        const result = await globalSearchApi.search(dealSearch.trim());
        const options = result.sections
          .flatMap((section) => section.items)
          .filter((item) => item.kind === "deal")
          .slice(0, 8);
        if (!cancelled) setDealOptions(options);
      } catch {
        if (!cancelled) setDealOptions([]);
      } finally {
        if (!cancelled) setDealSearching(false);
      }
    }, 220);
    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
  }, [dealSearch]);

  const load = async () => {
    setLoading(true);
    try {
      const rows = await tasksApi.workspace({
        includeClosed: true,
        taskType: typeFilter === "all" ? undefined : typeFilter,
        entityType: entityFilter === "all" ? undefined : entityFilter,
        dealId: dealFilter || undefined,
        scope: isAdmin ? queueScope : "mine",
      });
      setTasks(rows);
      if (dealFilter && !dealFilterLabel) {
        const matched = rows.find((task) => task.entity_type === "deal" && task.entity_id === dealFilter);
        if (matched) setDealFilterLabel(matched.entity_name);
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [typeFilter, entityFilter, queueScope, dealFilter, isAdmin]);

  const visibleTasks = useMemo(() => {
    let filtered = statusFilter === "all" ? tasks : tasks.filter((task) => task.status === statusFilter);

    if (dueDateFilter === "all") return filtered;

    return filtered.filter((task) => {
      const dueBadge = getDueBadge(task.due_at, task.status);
      if (dueDateFilter === "unscheduled") return !dueBadge;
      return dueBadge?.bucket === dueDateFilter;
    });
  }, [tasks, statusFilter, dueDateFilter]);

  const TASKS_PAGE_SIZE = 25;
  const tasksPageCount = Math.max(1, Math.ceil(visibleTasks.length / TASKS_PAGE_SIZE));
  // Clamp the current page when the filtered set shrinks (e.g. rep narrows
  // the filter and the previous page no longer exists). useMemo so the slice
  // is stable across re-renders that don't change the filter result.
  const safeTasksPage = Math.min(tasksPage, tasksPageCount);
  const pagedTasks = useMemo(
    () => visibleTasks.slice((safeTasksPage - 1) * TASKS_PAGE_SIZE, safeTasksPage * TASKS_PAGE_SIZE),
    [visibleTasks, safeTasksPage],
  );

  // Reset to page 1 whenever the filter inputs change. Without this, the rep
  // could be stuck on page 4 looking at zero tasks because their filter
  // produced fewer than 4 pages of results.
  useEffect(() => {
    setTasksPage(1);
  }, [statusFilter, typeFilter, entityFilter, dueDateFilter, queueScope, dealFilter]);

  const summary = useMemo(() => ({
    open: tasks.filter((task) => task.status === "open").length,
    system: tasks.filter((task) => task.status === "open" && task.task_type === "system").length,
    manual: tasks.filter((task) => task.status === "open" && task.task_type === "manual").length,
    completed: tasks.filter((task) => task.status === "completed").length,
    overdue: tasks.filter((task) => task.status === "open" && getDueBadge(task.due_at, task.status)?.bucket === "overdue").length,
    dueToday: tasks.filter((task) => task.status === "open" && getDueBadge(task.due_at, task.status)?.bucket === "today").length,
  }), [tasks]);
  const summaryValue = (value: number) => loading && tasks.length === 0 ? "—" : value;

  const patchTask = async (taskId: string, data: Parameters<typeof tasksApi.update>[1]) => {
    await tasksApi.update(taskId, data);
    await load();
  };

  const acceptTask = async (taskId: string) => {
    await tasksApi.accept(taskId);
    await load();
  };

  const addComment = async (taskId: string) => {
    const body = (commentDrafts[taskId] || "").trim();
    if (!body) return;
    await tasksApi.addComment(taskId, body);
    setCommentDrafts((current) => ({ ...current, [taskId]: "" }));
    await load();
  };

  const completeTask = async (task: TaskWorkspaceItem) => {
    const outcome = (outcomeDrafts[task.id] || "").trim();
    if (outcome) {
      await tasksApi.addComment(task.id, outcome.startsWith("Outcome:") ? outcome : `Outcome: ${outcome}`);
      setOutcomeDrafts((current) => ({ ...current, [task.id]: "" }));
    }
    await patchTask(task.id, { status: "completed" });
  };

  const createManualTask = async () => {
    if (!createTaskForm.title.trim() || !createTaskForm.entity_id.trim()) return;
    setCreatingTask(true);
    try {
      await tasksApi.create({
        entity_type: createTaskForm.entity_type,
        entity_id: createTaskForm.entity_id.trim(),
        title: createTaskForm.title.trim(),
        description: createTaskForm.description.trim() || undefined,
        priority: createTaskForm.priority,
        due_at: createTaskForm.due_at ? new Date(createTaskForm.due_at).toISOString() : undefined,
        assigned_to_id: user?.id,
      });
      setShowCreateTask(false);
      setCreateTaskForm({ title: "", description: "", entity_type: "deal", entity_id: "", priority: "medium", due_at: "" });
      setEntitySearch("");
      setEntityOptions([]);
      await load();
    } finally {
      setCreatingTask(false);
    }
  };

  const deleteTask = async (task: TaskWorkspaceItem) => {
    if (!window.confirm(`Delete "${task.title}"?`)) return;
    await tasksApi.remove(task.id);
    await load();
  };

  const takeManualOwnership = async (task: TaskWorkspaceItem) => {
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
    await load();
  };

  // ── Mobile-only: a strict subset of the desktop workspace. Show only the
  // current user's open tasks, sorted with overdue first, then due-today,
  // then upcoming, then unscheduled. One CTA per card: Complete. Filters,
  // pagination, team queue, and task creation all stay desktop-only.
  const mobileTasks = useMemo(() => {
    const openTasks = tasks.filter((task) => task.status === "open");
    const bucketRank: Record<string, number> = { overdue: 0, today: 1, tomorrow: 2, this_week: 3, upcoming: 4 };
    return openTasks.slice().sort((a, b) => {
      const aBadge = getDueBadge(a.due_at, a.status);
      const bBadge = getDueBadge(b.due_at, b.status);
      const aRank = aBadge ? bucketRank[aBadge.bucket] ?? 5 : 6;
      const bRank = bBadge ? bucketRank[bBadge.bucket] ?? 5 : 6;
      if (aRank !== bRank) return aRank - bRank;
      const aDue = a.due_at ? new Date(a.due_at).getTime() : Number.MAX_SAFE_INTEGER;
      const bDue = b.due_at ? new Date(b.due_at).getTime() : Number.MAX_SAFE_INTEGER;
      return aDue - bDue;
    });
  }, [tasks]);

  return (
    <>
      <style>{`
        .tasks-mobile-only { display: none; }
        @media (max-width: 1080px) {
          .tasks-desktop-only {
            display: none !important;
            visibility: hidden !important;
            pointer-events: none !important;
          }
          .tasks-mobile-only {
            display: block !important;
            margin: -8px -8px 0;
            background: #f4f8fc;
            min-height: calc(100dvh - 56px);
            padding: 0 0 88px;
          }
          .tasks-mobile-header {
            position: sticky;
            top: 0;
            z-index: 5;
            padding: 14px 14px 12px;
            background: rgba(244, 248, 252, 0.96);
            backdrop-filter: blur(12px);
            border-bottom: 1px solid #dde8f4;
          }
          .tasks-mobile-list {
            display: grid;
            gap: 12px;
            padding: 14px 12px 0;
          }
          .tasks-mobile-card {
            background: #fff;
            border: 1px solid #dce8f4;
            border-radius: 16px;
            padding: 14px;
            box-shadow: 0 6px 18px rgba(16, 42, 67, 0.06);
            display: grid;
            gap: 10px;
          }
          .tasks-mobile-complete {
            min-height: 46px;
            border-radius: 12px;
            border: none;
            background: ${colors.green};
            color: #fff;
            font-size: 14px;
            font-weight: 800;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            box-shadow: 0 6px 14px rgba(31, 143, 95, 0.22);
          }
          .tasks-mobile-complete:disabled {
            background: #cfddd6;
            box-shadow: none;
          }
          .tasks-mobile-entity-link {
            color: ${colors.primary};
            text-decoration: none;
            font-weight: 700;
          }
        }
      `}</style>

      {/* ── Mobile: minimal open-task list ──────────────────────────────── */}
      <div className="tasks-mobile-only">
        <div className="tasks-mobile-header">
          <div style={{ fontSize: 18, fontWeight: 850, color: "#102a43", lineHeight: 1.1 }}>My tasks</div>
          <div style={{ fontSize: 12.5, color: "#6f8297", fontWeight: 650, marginTop: 3 }}>
            {loading
              ? "Loading…"
              : `${mobileTasks.length} open · ${summary.overdue} overdue · ${summary.dueToday} due today`}
          </div>
        </div>
        <div className="tasks-mobile-list">
          {loading ? (
            <div className="tasks-mobile-card" style={{ textAlign: "center", color: colors.faint, fontWeight: 700 }}>
              Loading tasks…
            </div>
          ) : mobileTasks.length === 0 ? (
            <div className="tasks-mobile-card" style={{ textAlign: "center" }}>
              <CheckCircle2 size={28} style={{ margin: "0 auto 6px", color: colors.green }} />
              <div style={{ fontWeight: 800, color: colors.text }}>You're all caught up.</div>
              <div style={{ fontSize: 12.5, color: colors.faint, marginTop: 4 }}>
                New recommendations and assignments will appear here.
              </div>
            </div>
          ) : (
            mobileTasks.map((task) => {
              const dueBadge = getDueBadge(task.due_at, task.status);
              const priorityStyle = PRIORITY_STYLE[task.priority] ?? PRIORITY_STYLE.normal;
              return (
                <div key={task.id} className="tasks-mobile-card">
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    {dueBadge && (
                      <span style={{
                        fontSize: 11, fontWeight: 800, padding: "3px 9px", borderRadius: 999,
                        background: dueBadge.tone.background, color: dueBadge.tone.color,
                        border: `1px solid ${dueBadge.tone.border}`,
                      }}>
                        {dueBadge.label}
                      </span>
                    )}
                    <span style={{
                      fontSize: 11, fontWeight: 800, padding: "3px 9px", borderRadius: 999,
                      background: priorityStyle.bg, color: priorityStyle.color,
                      border: `1px solid ${priorityStyle.border}`, textTransform: "uppercase", letterSpacing: 0.4,
                    }}>
                      {task.priority}
                    </span>
                  </div>
                  <div style={{ fontSize: 15, fontWeight: 800, color: colors.text, lineHeight: 1.35 }}>
                    {task.title}
                  </div>
                  {task.entity_name && (
                    <Link to={task.entity_link} className="tasks-mobile-entity-link" style={{ fontSize: 13 }}>
                      {task.entity_name}
                      {task.entity_subtitle ? ` · ${displayEntitySubtitle(task.entity_subtitle)}` : ""}
                    </Link>
                  )}
                  <button
                    type="button"
                    className="tasks-mobile-complete"
                    onClick={() => completeTask(task)}
                  >
                    <CheckCircle2 size={16} />
                    Mark complete
                  </button>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* ── Desktop: full task workspace ─────────────────────────────────── */}
      <div className="tasks-desktop-only crm-page" style={{ display: "grid", gap: 18 }}>
      <section className="crm-panel" style={{ padding: 24, display: "grid", gap: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
          <div>
            <h2 style={{ fontSize: 28, fontWeight: 800, color: colors.text, marginBottom: 8 }}>Tasks</h2>
            <p className="crm-muted" style={{ maxWidth: 760, lineHeight: 1.7 }}>
              {user?.role === "admin"
                ? "Use My Queue for daily execution and Team Queue for coaching. Beacon recommendations stay alongside manual follow-ups so you can drive the right next actions."
                : `Everything assigned to ${user?.name || "you"} in one place. Beacon recommendations stay alongside manual follow-ups so reps can triage work quickly and then jump into the right company, prospect, or deal.`}
            </p>
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <button type="button" onClick={() => setShowCreateTask(true)} className="crm-button primary">
              <Plus size={14} />
              New Task
            </button>
            <span className="crm-chip" style={{ background: colors.violetSoft, color: colors.violet, borderColor: "#eadbff" }}>
              <Sparkles size={14} />
              {summaryValue(summary.system)} recommendations
            </span>
            <span className="crm-chip" style={{ background: colors.primarySoft, color: colors.primary, borderColor: "#d5e5ff" }}>
              <MessageSquare size={14} />
              {summaryValue(summary.manual)} manual
            </span>
            <span className="crm-chip" style={{ background: "#eef7f1", color: colors.green, borderColor: "#cfe8d7" }}>
              <CheckCircle2 size={14} />
              {summaryValue(summary.completed)} completed
            </span>
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(6, minmax(0, 1fr))", gap: 12 }}>
          {[
            { label: "Open now", value: summaryValue(summary.open), tone: colors.primarySoft, color: colors.primary },
            { label: "Beacon recommendations", value: summaryValue(summary.system), tone: colors.violetSoft, color: colors.violet },
            { label: "Manual follow-ups", value: summaryValue(summary.manual), tone: "#eef7f1", color: colors.green },
            { label: "Completed", value: summaryValue(summary.completed), tone: "#f6f7fb", color: colors.sub },
            { label: "Overdue", value: summaryValue(summary.overdue), tone: colors.redSoft, color: colors.red },
            { label: "Due today", value: summaryValue(summary.dueToday), tone: colors.amberSoft, color: colors.amber },
          ].map((item) => (
            <div key={item.label} style={{ border: `1px solid ${colors.border}`, borderRadius: 16, padding: "16px 18px", background: "#fff" }}>
              <div style={{ color: colors.faint, fontSize: 12, textTransform: "uppercase", letterSpacing: "0.08em", fontWeight: 700, marginBottom: 8 }}>{item.label}</div>
              <div style={{ color: item.color, fontWeight: 800, fontSize: 28 }}>{item.value}</div>
            </div>
          ))}
        </div>
        <div style={{ border: `1px solid ${colors.border}`, background: "#fbfdff", borderRadius: 14, padding: "10px 12px", color: colors.sub, fontSize: 12.5, lineHeight: 1.55 }}>
          <span style={{ fontWeight: 800, color: colors.text }}>Priority guide:</span> P0/P1 = urgent customer or revenue blocker, P2 = important next action, P3/low = useful follow-up when higher-priority work is clear.
        </div>
      </section>

      {/* `crm-panel` ships with `overflow: hidden` for the rounded-corner card
          look — but the deal-search dropdown sits below the input and gets
          clipped at the panel's bottom edge. Override here so the dropdown
          can escape the card. The explicit zIndex bumps this whole section
          above the task cards section that follows, so the dropdown also
          paints over the cards rather than under them. */}
      <section className="crm-panel" style={{ padding: 20, display: "grid", gap: 14, overflow: "visible", position: "relative", zIndex: 5 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, color: colors.text, fontWeight: 800 }}>
          <Filter size={15} />
          <span>Filters</span>
        </div>
        {isAdmin ? (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              type="button"
              onClick={() => setQueueScope("mine")}
              className="crm-button soft"
              style={{
                borderColor: queueScope === "mine" ? "#d5e5ff" : colors.border,
                background: queueScope === "mine" ? colors.primarySoft : "#fff",
                color: queueScope === "mine" ? colors.primary : colors.sub,
              }}
            >
              My Queue
            </button>
            <button
              type="button"
              onClick={() => setQueueScope("team")}
              className="crm-button soft"
              style={{
                borderColor: queueScope === "team" ? "#eadbff" : colors.border,
                background: queueScope === "team" ? colors.violetSoft : "#fff",
                color: queueScope === "team" ? colors.violet : colors.sub,
              }}
            >
              Team Queue
            </button>
          </div>
        ) : null}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 12, alignItems: "start" }}>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as TaskStatusFilter)} style={{ height: 44, borderRadius: 12, border: `1px solid ${colors.border}`, padding: "0 12px", fontSize: 13, background: "#fff" }}>
            <option value="open">Open only</option>
            <option value="completed">Completed</option>
            <option value="dismissed">Dismissed</option>
            <option value="all">All statuses</option>
          </select>
          <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value as TaskTypeFilter)} style={{ height: 44, borderRadius: 12, border: `1px solid ${colors.border}`, padding: "0 12px", fontSize: 13, background: "#fff" }}>
            <option value="all">All task types</option>
            <option value="system">System recommendations</option>
            <option value="manual">Manual tasks</option>
          </select>
          <select value={entityFilter} onChange={(e) => setEntityFilter(e.target.value as EntityFilter)} style={{ height: 44, borderRadius: 12, border: `1px solid ${colors.border}`, padding: "0 12px", fontSize: 13, background: "#fff" }}>
            <option value="all">All record types</option>
            <option value="deal">Deals</option>
            <option value="contact">Prospects</option>
            <option value="company">Companies</option>
          </select>
          <select value={dueDateFilter} onChange={(e) => setDueDateFilter(e.target.value as DueDateFilter)} style={{ height: 44, borderRadius: 12, border: `1px solid ${colors.border}`, padding: "0 12px", fontSize: 13, background: "#fff" }}>
            <option value="all">All due dates</option>
            <option value="overdue">Overdue</option>
            <option value="today">Due today</option>
            <option value="tomorrow">Due tomorrow</option>
            <option value="this_week">Due this week</option>
            <option value="upcoming">Upcoming later</option>
            <option value="unscheduled">No due date</option>
          </select>
          <div style={{ position: "relative" }}>
            <div style={{ position: "relative" }}>
              <input
                value={dealFilter ? (dealFilterLabel || "Selected deal") : dealSearch}
                onChange={(event) => {
                  setDealFilter("");
                  setDealFilterLabel("");
                  setDealSearch(event.target.value);
                }}
                placeholder="Filter by deal"
                style={{ width: "100%", height: 44, borderRadius: 12, border: `1px solid ${dealFilter ? "#bfe4cf" : colors.border}`, padding: "0 36px 0 12px", fontSize: 13, background: "#fff", boxSizing: "border-box" }}
              />
              {(dealFilter || dealSearch) && (
                <button
                  type="button"
                  onClick={() => { setDealFilter(""); setDealFilterLabel(""); setDealSearch(""); setDealOptions([]); }}
                  aria-label="Clear deal filter"
                  style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", border: "none", background: "transparent", color: colors.faint, cursor: "pointer", display: "inline-flex", padding: 4 }}
                >
                  <X size={14} />
                </button>
              )}
            </div>
            {(dealOptions.length > 0 || dealSearching) && !dealFilter ? (
              <div style={{ position: "absolute", top: "calc(100% + 4px)", left: 0, right: 0, zIndex: 30, border: `1px solid ${colors.border}`, borderRadius: 12, background: "#fff", boxShadow: "0 14px 30px rgba(15,23,42,0.12)", overflow: "hidden" }}>
                {dealSearching ? (
                  <div style={{ padding: 12, color: colors.faint, fontSize: 12 }}>Searching deals...</div>
                ) : dealOptions.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => { setDealFilter(item.id); setDealFilterLabel(item.title); setDealSearch(""); setDealOptions([]); setEntityFilter("all"); }}
                    style={{ width: "100%", border: "none", background: "transparent", padding: "10px 12px", textAlign: "left", cursor: "pointer", display: "grid", gap: 2 }}
                  >
                    <span style={{ color: colors.text, fontSize: 13, fontWeight: 800 }}>{item.title}</span>
                    {item.subtitle && <span style={{ color: colors.faint, fontSize: 11.5 }}>{item.subtitle}</span>}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      </section>

      <section style={{ display: "grid", gap: 14 }}>
        {loading ? (
          <div className="crm-panel" style={{ padding: 18 }}><SkeletonList rows={6} /></div>
        ) : visibleTasks.length === 0 ? (
          <div className="crm-panel" style={{ padding: 28, color: colors.faint, display: "grid", gap: 8 }}>
            <div style={{ fontWeight: 800, color: colors.text }}>No tasks match these filters</div>
            <div>When Beacon recommends something or a teammate assigns you a manual follow-up, it will appear here.</div>
          </div>
        ) : (
          <>
            {pagedTasks.map((task) => (
              <TaskWorkspaceCard
                key={task.id}
                task={task}
                commentDraft={commentDrafts[task.id] || ""}
                outcomeDraft={outcomeDrafts[task.id] || ""}
                onCommentDraftChange={(value) => setCommentDrafts((current) => ({ ...current, [task.id]: value }))}
                onOutcomeDraftChange={(value) => setOutcomeDrafts((current) => ({ ...current, [task.id]: value }))}
                onAddComment={() => addComment(task.id)}
                onAccept={() => acceptTask(task.id)}
                onComplete={() => completeTask(task)}
                onDismiss={() => patchTask(task.id, { status: "dismissed" })}
                onManualTakeover={() => takeManualOwnership(task)}
                onReschedule={(newDate) => patchTask(task.id, { due_at: new Date(newDate).toISOString() })}
                onDelete={() => deleteTask(task)}
                canDelete={Boolean(user && (user.role === "admin" || user.id === task.created_by_id))}
              />
            ))}
            {tasksPageCount > 1 ? (
              <div style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 12,
                padding: "12px 18px",
                borderRadius: 14,
                border: `1px solid ${colors.border}`,
                background: "#fff",
              }}>
                <span style={{ fontSize: 12, color: colors.sub, fontWeight: 600 }}>
                  Showing {(safeTasksPage - 1) * TASKS_PAGE_SIZE + 1}–
                  {Math.min(safeTasksPage * TASKS_PAGE_SIZE, visibleTasks.length)} of {visibleTasks.length}
                </span>
                <div style={{ display: "inline-flex", gap: 8 }}>
                  <button
                    type="button"
                    className="crm-button soft"
                    onClick={() => setTasksPage(Math.max(1, safeTasksPage - 1))}
                    disabled={safeTasksPage <= 1}
                    style={{ height: 34, padding: "0 12px", opacity: safeTasksPage <= 1 ? 0.45 : 1 }}
                  >
                    Previous
                  </button>
                  <span style={{ fontSize: 12, color: colors.text, fontWeight: 700, alignSelf: "center" }}>
                    Page {safeTasksPage} of {tasksPageCount}
                  </span>
                  <button
                    type="button"
                    className="crm-button soft"
                    onClick={() => setTasksPage(Math.min(tasksPageCount, safeTasksPage + 1))}
                    disabled={safeTasksPage >= tasksPageCount}
                    style={{ height: 34, padding: "0 12px", opacity: safeTasksPage >= tasksPageCount ? 0.45 : 1 }}
                  >
                    Next
                  </button>
                </div>
              </div>
            ) : null}
          </>
        )}
      </section>

      {showCreateTask ? (
        <div style={{ position: "fixed", inset: 0, zIndex: 120, background: "rgba(15,23,42,0.42)", display: "grid", placeItems: "center", padding: 18 }} onClick={() => setShowCreateTask(false)}>
          <div className="crm-panel" style={{ width: "min(560px, 100%)", padding: 22, display: "grid", gap: 14 }} onClick={(event) => event.stopPropagation()}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
              <div>
                <h3 style={{ margin: 0, color: colors.text, fontSize: 20, fontWeight: 800 }}>New task</h3>
                <p style={{ margin: "4px 0 0", color: colors.sub, fontSize: 13 }}>Fast manual task entry. No AI generation, just a clean follow-up record.</p>
              </div>
              <button type="button" onClick={() => setShowCreateTask(false)} className="crm-button soft" aria-label="Close">
                <X size={14} />
              </button>
            </div>
            <label style={{ display: "grid", gap: 6, color: colors.sub, fontSize: 12, fontWeight: 800 }}>
              TITLE
              <input
                value={createTaskForm.title}
                onChange={(event) => setCreateTaskForm((current) => ({ ...current, title: event.target.value }))}
                placeholder="e.g. Update next step after discovery call"
                style={{ height: 42, borderRadius: 12, border: `1px solid ${colors.border}`, padding: "0 12px", fontSize: 14 }}
              />
            </label>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1.4fr", gap: 10 }}>
              <label style={{ display: "grid", gap: 6, color: colors.sub, fontSize: 12, fontWeight: 800 }}>
                RECORD TYPE
                <select
                  value={createTaskForm.entity_type}
                  onChange={(event) => {
                    setCreateTaskForm((current) => ({ ...current, entity_type: event.target.value as CreateTaskEntityType, entity_id: "" }));
                    setEntitySearch("");
                    setEntityOptions([]);
                  }}
                  style={{ height: 42, borderRadius: 12, border: `1px solid ${colors.border}`, padding: "0 12px", fontSize: 14, background: "#fff" }}
                >
                  <option value="deal">Deal</option>
                  <option value="contact">Prospect</option>
                  <option value="company">Company</option>
                </select>
              </label>
              <label style={{ display: "grid", gap: 6, color: colors.sub, fontSize: 12, fontWeight: 800 }}>
                RECORD
                <div style={{ position: "relative" }}>
                  <input
                    value={entitySearch}
                    onChange={(event) => {
                      setEntitySearch(event.target.value);
                      setCreateTaskForm((current) => ({ ...current, entity_id: "" }));
                    }}
                    placeholder={`Search ${createTaskForm.entity_type}`}
                    style={{ width: "100%", height: 42, borderRadius: 12, border: `1px solid ${createTaskForm.entity_id ? "#bfe4cf" : colors.border}`, padding: "0 12px", fontSize: 14, boxSizing: "border-box" }}
                  />
                  {(entityOptions.length > 0 || entitySearching) && !createTaskForm.entity_id ? (
                    <div style={{ position: "absolute", top: "calc(100% + 4px)", left: 0, right: 0, zIndex: 125, border: `1px solid ${colors.border}`, borderRadius: 12, background: "#fff", boxShadow: "0 14px 30px rgba(15,23,42,0.12)", overflow: "hidden" }}>
                      {entitySearching ? (
                        <div style={{ padding: "10px 12px", color: colors.faint, fontSize: 12 }}>Searching...</div>
                      ) : entityOptions.map((item) => (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() => {
                            setCreateTaskForm((current) => ({ ...current, entity_id: item.id }));
                            setEntitySearch(item.subtitle ? `${item.title} · ${item.subtitle}` : item.title);
                            setEntityOptions([]);
                          }}
                          style={{ width: "100%", border: "none", background: "transparent", padding: "10px 12px", textAlign: "left", cursor: "pointer", display: "grid", gap: 2 }}
                        >
                          <span style={{ color: colors.text, fontSize: 13, fontWeight: 800 }}>{item.title}</span>
                          <span style={{ color: colors.faint, fontSize: 11 }}>{item.subtitle || item.meta || item.kind}</span>
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              </label>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <label style={{ display: "grid", gap: 6, color: colors.sub, fontSize: 12, fontWeight: 800 }}>
                PRIORITY
                <select
                  value={createTaskForm.priority}
                  onChange={(event) => setCreateTaskForm((current) => ({ ...current, priority: event.target.value as TaskPriority }))}
                  style={{ height: 42, borderRadius: 12, border: `1px solid ${colors.border}`, padding: "0 12px", fontSize: 14, background: "#fff" }}
                >
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                  <option value="low">Low</option>
                </select>
              </label>
              <label style={{ display: "grid", gap: 6, color: colors.sub, fontSize: 12, fontWeight: 800 }}>
                DUE DATE
                <input
                  type="date"
                  value={createTaskForm.due_at}
                  onChange={(event) => setCreateTaskForm((current) => ({ ...current, due_at: event.target.value }))}
                  style={{ height: 42, borderRadius: 12, border: `1px solid ${colors.border}`, padding: "0 12px", fontSize: 14 }}
                />
              </label>
            </div>
            <label style={{ display: "grid", gap: 6, color: colors.sub, fontSize: 12, fontWeight: 800 }}>
              DESCRIPTION
              <textarea
                value={createTaskForm.description}
                onChange={(event) => setCreateTaskForm((current) => ({ ...current, description: event.target.value }))}
                placeholder="Context, desired outcome, and any next-step note."
                style={{ minHeight: 96, borderRadius: 12, border: `1px solid ${colors.border}`, padding: "10px 12px", fontSize: 14, fontFamily: "inherit", resize: "vertical" }}
              />
            </label>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button type="button" onClick={() => setShowCreateTask(false)} className="crm-button soft">Cancel</button>
              <button type="button" onClick={() => void createManualTask()} disabled={creatingTask || !createTaskForm.title.trim() || !createTaskForm.entity_id.trim()} className="crm-button primary">
                {creatingTask ? "Creating..." : "Create task"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
      </div>
    </>
  );
}

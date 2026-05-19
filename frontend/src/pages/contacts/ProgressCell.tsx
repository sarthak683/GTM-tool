import type { Contact } from "../../types";
import type { LifecycleSummary, LifecycleStepState } from "../../lib/api";
import { getProspectTrackingScore, getProspectTrackingTone } from "../../lib/prospectTracking";
import { LIFECYCLE_DOT_STYLE } from "./LifecycleStepRow";

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

export function ProgressCell({
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

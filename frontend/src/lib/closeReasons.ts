// Win/loss close reasons — MIRROR of the backend enum in
// app/services/deal_stage_history.py (CLOSE_REASONS). The values land in
// DealStageHistory.reason and Deal.qualification.close_reason, and the
// win/loss analytics rollup matches on exactly these strings — keep the two
// lists in lock-step.

export const CLOSE_REASONS = [
  { value: "budget", label: "Budget" },
  { value: "timing", label: "Timing" },
  { value: "lost_to_competitor", label: "Lost to competitor" },
  { value: "no_response", label: "No response" },
  { value: "not_a_fit", label: "Not a fit" },
  { value: "pricing", label: "Pricing" },
  { value: "champion_left", label: "Champion left" },
  { value: "other", label: "Other" },
] as const;

export type CloseReason = (typeof CLOSE_REASONS)[number]["value"];

/** Stages whose moves carry a close reason. Required for closed_lost,
 * optional for closed_won; the backend ignores reasons on any other stage. */
export const CLOSE_REASON_STAGES = ["closed_won", "closed_lost"] as const;

export function isCloseReasonStage(stageId: string): boolean {
  return stageId === "closed_won" || stageId === "closed_lost";
}

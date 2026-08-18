// Win/loss close reasons — MIRROR of the backend enum in
// app/services/deal_stage_history.py (CLOSE_REASONS). The values land in
// DealStageHistory.reason and Deal.qualification.close_reason, and the
// win/loss analytics rollup matches on exactly these strings — keep the two
// lists in lock-step.

export const CLOSE_REASONS = [
  { value: "budget", label: "Budget" },
  { value: "timing", label: "Timing" },
  // Distinct from "Lost to competitor", which means we lost to a vendor. This
  // is the largest real loss mode in the pipeline and has its own counter-play
  // (the Build vs Buy deck), so it needs its own number.
  { value: "built_in_house", label: "Built in-house" },
  { value: "lost_to_competitor", label: "Lost to competitor" },
  { value: "no_response", label: "No response" },
  { value: "not_a_fit", label: "Not a fit" },
  { value: "pricing", label: "Pricing" },
  { value: "champion_left", label: "Champion left" },
  // The Sales Lifecycle SOP's two "RCA-relevant exit points".
  { value: "poc_failed", label: "POC unsuccessful" },
  { value: "terms_not_agreed", label: "Terms not agreed" },
  { value: "other", label: "Other" },
] as const;

export function isCloseReasonStage(stageId: string): boolean {
  return stageId === "closed_won" || stageId === "closed_lost";
}

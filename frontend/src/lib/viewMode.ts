export type ViewMode = "board" | "table";

export type ViewModeAction =
  | { type: "hydrate"; value: string | null; defaultMode: ViewMode }
  | { type: "select"; value: ViewMode };

export function reduceViewMode(current: ViewMode, action: ViewModeAction): ViewMode {
  if (action.type === "select") return action.value;
  return action.value === "board" || action.value === "table"
    ? action.value
    : action.defaultMode ?? current;
}

export function withViewMode(
  current: URLSearchParams,
  value: ViewMode,
  defaultMode: ViewMode,
): URLSearchParams {
  const next = new URLSearchParams(current);
  if (value === defaultMode) next.delete("view");
  else next.set("view", value);
  return next;
}

import { useEffect, useState } from "react";
import { settingsApi } from "./api";
import { createCachedFetch } from "./cachedFetch";

// Pipeline stage ids are internal ("new_stage_18"); people should only ever see
// the label configured in Settings → Deal stages ("Marketing Lead (MQL)").
// Used wherever a stage id would otherwise leak into the UI — the activity
// timeline, analytics tables, search results — without each screen having to
// load and thread the stage list itself.

const stagesFetch = createCachedFetch(() => settingsApi.getDealStages(), 5 * 60 * 1000);

let labelsById: Record<string, string> = {};
const listeners = new Set<() => void>();
let started = false;

function load() {
  if (started) return;
  started = true;
  stagesFetch
    .get()
    .then((data) => {
      labelsById = Object.fromEntries((data.stages ?? []).map((s) => [s.id, s.label]));
      listeners.forEach((fn) => fn());
    })
    .catch(() => {
      started = false; // retry on next mount; ids fall back to readable text meanwhile
    });
}

function fallbackLabel(stageId: string): string {
  return stageId.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Configured label for a stage id; readable fallback before the config loads. */
export function stageLabel(stageId: string | null | undefined): string {
  if (!stageId) return "";
  return labelsById[stageId] ?? fallbackLabel(stageId);
}

/** Replace any stage ids inside free text ("Stage moved from a to b") with labels. */
export function humanizeStageText(text: string | null | undefined): string {
  if (!text) return text ?? "";
  const ids = Object.keys(labelsById);
  if (!ids.length) return text.replace(/\bnew_stage_\d+\b/g, (m) => labelsById[m] ?? fallbackLabel(m));
  return text.replace(/\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/g, (m) => labelsById[m] ?? m);
}

/** Subscribe a component so it re-renders once stage labels have loaded. */
export function useStageLabels() {
  const [, bump] = useState(0);
  useEffect(() => {
    const fn = () => bump((n) => n + 1);
    listeners.add(fn);
    load();
    return () => {
      listeners.delete(fn);
    };
  }, []);
  return { stageLabel, humanizeStageText };
}

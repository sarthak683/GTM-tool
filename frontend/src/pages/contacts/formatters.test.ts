import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CONTACT_TABLE_COLUMNS,
  dayEndIso,
  dayStartIso,
  normalizeContactTableColumns,
  parseSearchParamList,
  personaChipStyle,
  relativeTimeShort,
} from "./formatters";


afterEach(() => {
  vi.useRealTimers();
});

describe("contact formatters", () => {
  it("parses URL list values without empty entries", () => {
    expect(parseSearchParamList(" champion, buyer ,, evaluator ")).toEqual([
      "champion",
      "buyer",
      "evaluator",
    ]);
    expect(parseSearchParamList(null)).toEqual([]);
  });

  it("converts the rep's local date into full-day UTC bounds", () => {
    expect(dayStartIso("2026-08-24")).toBe(
      new Date("2026-08-24T00:00:00").toISOString(),
    );
    expect(dayEndIso("2026-08-24")).toBe(
      new Date("2026-08-24T23:59:59.999").toISOString(),
    );
    expect(dayStartIso("not-a-date")).toBeUndefined();
  });

  it("preserves saved column order and inserts newly-added columns before actions", () => {
    const savedBeforeComments = JSON.stringify([
      "name",
      "company",
      "title",
      "email",
      "progress",
      "action",
      "timezone",
      "ae",
      "sdr",
    ]);
    const normalized = normalizeContactTableColumns(savedBeforeComments);

    expect(normalized.indexOf("comments")).toBeLessThan(normalized.indexOf("action"));
    expect(new Set(normalized)).toEqual(
      new Set(CONTACT_TABLE_COLUMNS.map((column) => column.key)),
    );
  });

  it("formats relative age and persona fallbacks", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-24T12:00:00.000Z"));

    expect(relativeTimeShort("2026-08-24T11:58:00.000Z")).toBe("2m ago");
    expect(relativeTimeShort("2026-08-22T12:00:00.000Z")).toBe("2d ago");
    expect(personaChipStyle("champion").label).toBe("Champion");
    expect(personaChipStyle("Coach").label).toBe("Coach");
  });
});

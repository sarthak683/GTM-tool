import { describe, expect, it } from "vitest";

import { reduceViewMode, withViewMode } from "./viewMode";

describe("view-mode reducer", () => {
  it("keeps each page default while accepting a persisted alternate view", () => {
    expect(reduceViewMode("table", { type: "hydrate", value: null, defaultMode: "table" })).toBe("table");
    expect(reduceViewMode("board", { type: "hydrate", value: null, defaultMode: "board" })).toBe("board");
    expect(reduceViewMode("table", { type: "hydrate", value: "board", defaultMode: "table" })).toBe("board");
    expect(reduceViewMode("board", { type: "hydrate", value: "invalid", defaultMode: "board" })).toBe("board");
  });

  it("preserves unrelated filters and omits the default mode from the URL", () => {
    const alternate = withViewMode(new URLSearchParams("stage=demo_done"), "table", "board");
    expect(alternate.get("stage")).toBe("demo_done");
    expect(alternate.get("view")).toBe("table");

    const restored = withViewMode(alternate, "board", "board");
    expect(restored.get("stage")).toBe("demo_done");
    expect(restored.has("view")).toBe(false);
  });
});

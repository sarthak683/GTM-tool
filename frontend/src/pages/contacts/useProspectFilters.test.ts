import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useProspectFilters } from "./useProspectFilters";


afterEach(() => {
  localStorage.clear();
});

describe("useProspectFilters", () => {
  it("hydrates URL state, serializes updates, and resets as one snapshot", () => {
    const { result } = renderHook(() => useProspectFilters({
      searchParams: new URLSearchParams("q=Beacon&pe=champion,buyer&fcmin=2&pg=4&view=board"),
      isSdrLocked: false,
    }));

    expect(result.current.search).toBe("Beacon");
    expect(result.current.personaFilter).toEqual(["champion", "buyer"]);
    expect(result.current.followupCountMin).toBe(2);
    expect(result.current.page).toBe(4);
    expect(result.current.viewMode).toBe("board");

    act(() => {
      result.current.setCompanyFilter("company-123");
      result.current.setSearchScope("name");
      result.current.setSearchMatch("exact");
    });
    const params = result.current.toParams();
    expect(params.get("co")).toBe("company-123");
    expect(params.get("qm")).toBe("exact");

    act(() => result.current.reset());
    expect(result.current.search).toBe("");
    expect(result.current.page).toBe(1);
    expect(result.current.viewMode).toBe("board");
    expect(result.current.hasActiveFilters).toBe(false);
  });

  it("uses a saved bare-path snapshot and keeps SDR ownership locked", () => {
    localStorage.setItem("crm.prospecting.filters", "q=saved&owner=all&sdr=rep-1");
    const { result } = renderHook(() => useProspectFilters({
      searchParams: new URLSearchParams(),
      isSdrLocked: true,
    }));

    expect(result.current.search).toBe("saved");
    expect(result.current.sdrFilter).toEqual(["rep-1"]);
    expect(result.current.ownerScope).toBe("mine");
    expect(result.current.initiallyShowFilters).toBe(true);
  });
});

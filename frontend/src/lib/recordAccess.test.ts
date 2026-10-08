import { describe, expect, it } from "vitest";
import type { User } from "../types";
import { canEditRecord, canDeleteProspects } from "./recordAccess";

describe("shared record access", () => {
  for (const role of ["admin", "superadmin", "ae", "sdr", "marketing"] as const) {
    const user = { id: "me", role } as User;
    it(`${role}: viewing another rep's record does not grant editing`, () => {
      expect(canEditRecord(user, { assigned_to_id: "other", sdr_id: "other" })).toBe(["admin", "superadmin"].includes(role));
      expect(canEditRecord(user, {})).toBe(["admin", "superadmin"].includes(role));
      expect(canEditRecord(user, { assigned_to_id: "me" })).toBe(true);
      expect(canEditRecord(user, { sdr_id: "me" })).toBe(true);
      expect(canDeleteProspects(user)).toBe(role !== "marketing");
    });
  }
  it("does not match missing user IDs to unassigned slots", () => {
    expect(canEditRecord(null, {})).toBe(false);
    expect(canDeleteProspects(null)).toBe(false);
  });
});

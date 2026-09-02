import { describe, expect, it } from "vitest";

import { shouldSyncContactStatusToAccount } from "./contactStatusSync";
import {
  deriveAccountStatusFromCallDisposition,
  deriveSequenceStatusFromCallDisposition,
  deriveSequenceStatusFromLinkedinStatus,
  linkedinOutcomeColor,
} from "./prospectWorkflow";


describe("prospect workflow derivation", () => {
  it("never downgrades a booked meeting from later call or LinkedIn activity", () => {
    expect(
      deriveSequenceStatusFromCallDisposition(
        "connected_not_interested",
        "meeting_booked",
      ),
    ).toBe("meeting_booked");
    expect(
      deriveSequenceStatusFromLinkedinStatus("meeting_rejected", "meeting_booked"),
    ).toBe("meeting_booked");
  });

  it("keeps hard-negative account states until a booked meeting", () => {
    expect(
      deriveAccountStatusFromCallDisposition("referral", "dnd"),
    ).toBe("dnd");
    expect(
      deriveAccountStatusFromCallDisposition("demo_scheduled_booked", "dnd"),
    ).toBe("meeting_booked");
  });

  it("maps channel outcomes and only syncs lifecycle milestones to the account", () => {
    expect(linkedinOutcomeColor("accepted")).toBe("blue");
    expect(linkedinOutcomeColor("meeting_rejected")).toBe("red");
    expect(shouldSyncContactStatusToAccount("meeting_booked")).toBe(true);
    expect(shouldSyncContactStatusToAccount("in_progress")).toBe(false);
  });
});

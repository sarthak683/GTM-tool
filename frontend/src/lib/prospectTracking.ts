import type { Contact } from "../types";

type TrackingTone = {
  background: string;
  border: string;
  color: string;
  soft: string;
};

const TRACKING_TONES: Record<string, TrackingTone> = {
  good: {
    background: "#e9f8ef",
    border: "#c7ead3",
    color: "#1f8f5f",
    soft: "#f5fcf8",
  },
  watch: {
    background: "#fff6e7",
    border: "#f5e0b8",
    color: "#b56d00",
    soft: "#fffaf1",
  },
  at_risk: {
    background: "#edf3ff",
    border: "#d4e0fb",
    color: "#2d5fbd",
    soft: "#f7faff",
  },
  blocked: {
    background: "#ffedf0",
    border: "#f6cdd5",
    color: "#b42336",
    soft: "#fff7f8",
  },
};

export function getProspectTrackingTone(contact?: Pick<Contact, "tracking_label"> | null): TrackingTone {
  return TRACKING_TONES[contact?.tracking_label || "at_risk"] || TRACKING_TONES.at_risk;
}

export function getProspectTrackingStage(contact?: Pick<Contact, "tracking_stage"> | null): string {
  return contact?.tracking_stage || "No stage yet";
}

export function getProspectTrackingSummary(contact?: Pick<Contact, "tracking_summary"> | null): string {
  return contact?.tracking_summary || "No automated signal has been captured yet.";
}

export function getProspectTrackingScore(contact?: Pick<Contact, "tracking_score"> | null): string {
  return typeof contact?.tracking_score === "number" ? `${contact.tracking_score}/100` : "--";
}

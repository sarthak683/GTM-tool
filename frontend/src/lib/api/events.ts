import { request } from "./core";

export type EventTagResult = { event: string; contacts_changed: number; companies_changed: number };

export const eventsApi = {
  /** Every event name currently tagged on any prospect or account. */
  list: () => request<string[]>("/api/v1/events"),
  /** Add (or remove) one event on selected prospects/accounts. Adding to
   *  prospects also tags their accounts unless includeCompanies is false. */
  tag: (data: { event: string; contactIds?: string[]; companyIds?: string[]; action?: "add" | "remove"; includeCompanies?: boolean }) =>
    request<EventTagResult>("/api/v1/events/tag", {
      method: "POST",
      body: JSON.stringify({
        event: data.event,
        contact_ids: data.contactIds ?? [],
        company_ids: data.companyIds ?? [],
        action: data.action ?? "add",
        include_companies: data.includeCompanies ?? true,
      }),
    }),
};

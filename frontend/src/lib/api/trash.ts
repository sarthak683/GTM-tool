/**
 * Trash & restore — soft-deleted companies and deals.
 *
 * Soft-delete (deleted_at on Company/Deal) used to be a one-way door: nothing
 * listed what had been deleted, nothing brought it back, and a company that had
 * been MERGED away 404'd with no hint that its data now lives on another
 * account. These are the endpoints that close that loop.
 *
 * Kept in its own module (rather than folded into crm.ts) so the trash surface
 * — admin-only listing/restore plus the public tombstone lookup — stays one
 * self-contained thing.
 */
import { request } from "./core";

/** A soft-deleted account. `merged_into_*` is set only when the account left
 *  via a MERGE and the merge happened after migration 115 recorded the
 *  back-pointer; older merges read as a plain delete. */
export interface CompanyTrashRow {
  id: string;
  name: string;
  domain: string | null;
  deleted_at: string | null;
  merged_into_id: string | null;
  merged_into_name: string | null;
  /** Contacts still attached to the account — they kept their rows and their
   *  company_id through the delete, so this is what restore brings back. */
  prospect_count: number;
}

export interface DealTrashRow {
  id: string;
  name: string;
  stage: string | null;
  amount: number | null;
  company_name: string | null;
  deleted_at: string | null;
}

/** Why a company link dead-ended. `deleted: false` means the account is alive
 *  and the 404 came from something else (permissions, bad id). */
export interface CompanyTombstone {
  deleted: boolean;
  name: string | null;
  deleted_at: string | null;
  merged_into_id: string | null;
  merged_into_name: string | null;
}

export interface CompanyRestoreResult {
  id: string;
  name: string;
  deals_restored: number;
  tasks_reopened: number;
  was_merged_into_id: string | null;
}

export interface DealRestoreResult {
  id: string;
  name: string;
  tasks_reopened: number;
}

export const trashApi = {
  listCompanies: (limit = 50) =>
    request<CompanyTrashRow[]>(`/api/v1/companies/trash?limit=${limit}`),
  restoreCompany: (id: string) =>
    request<CompanyRestoreResult>(`/api/v1/companies/${id}/restore`, { method: "POST" }),
  listDeals: (limit = 50) =>
    request<DealTrashRow[]>(`/api/v1/deals/trash?limit=${limit}`),
  restoreDeal: (id: string) =>
    request<DealRestoreResult>(`/api/v1/deals/${id}/restore`, { method: "POST" }),
  /** Any authenticated role. 404s only when the id never existed. */
  companyTombstone: (id: string) =>
    request<CompanyTombstone>(`/api/v1/companies/${id}/tombstone`),
};

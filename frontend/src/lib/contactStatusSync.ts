import { accountSourcingApi } from "./api";

/** Prospect statuses that also update the parent account's manual status. */
export const ACCOUNT_SYNC_CONTACT_STATUSES = new Set(["meeting_booked", "meeting_done"]);

export function shouldSyncContactStatusToAccount(status: string | null | undefined): boolean {
  return Boolean(status && ACCOUNT_SYNC_CONTACT_STATUSES.has(status));
}

/**
 * Persist a prospect's manual status. When the status is Meeting Booked or
 * Meeting Done, also set the parent account to the same value (last write wins).
 */
export async function saveContactAccountStatus(
  contactId: string,
  companyId: string | null | undefined,
  status: string | null,
): Promise<void> {
  await accountSourcingApi.updateContact(contactId, { account_status: status });
  if (companyId && shouldSyncContactStatusToAccount(status)) {
    await accountSourcingApi.updateCompany(companyId, { account_status: status });
  }
}

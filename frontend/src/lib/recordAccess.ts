import type { User } from "../types";

type OwnedRecord = { assigned_to_id?: string | null; sdr_id?: string | null };
export function canEditRecord(user: User | null | undefined, record: OwnedRecord | null | undefined): boolean {
  return !!user && !!record && (user.role === "admin" || user.role === "superadmin" || record.assigned_to_id === user.id || record.sdr_id === user.id);
}
export function canDeleteProspects(user: User | null | undefined): boolean {
  return !!user && ["admin", "superadmin", "ae", "sdr"].includes(user.role);
}

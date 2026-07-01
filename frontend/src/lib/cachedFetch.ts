// Small TTL + in-flight-deduped fetch cache for rarely-changing global data.
//
// Generalizes the module-level cache pattern previously inlined in
// `components/tasks/TaskCenterModal.tsx` (taskListCache). A single shared
// request is reused by concurrent callers, and repeat calls within the TTL
// skip the network entirely. Surfaces that mutate the underlying data call
// `invalidate()` so the next read refetches instead of serving stale data.

import { authApi, settingsApi } from "./api";
import type { GmailSyncSettings, RolePermissionsSettings, User } from "../types";

export interface CachedFetch<T> {
  /** Resolves with a cached value when fresh, a shared in-flight request when
   *  one is already running, or a new request otherwise. */
  get: () => Promise<T>;
  /** Drop any cached value and pending request so the next `get()` refetches. */
  invalidate: () => void;
}

const DEFAULT_TTL_MS = 60 * 1000;

/**
 * Create a TTL-cached, in-flight-deduped wrapper around a zero-arg fetcher.
 * The returned `get()` resolves to exactly what `fetcher()` resolves to, so
 * call sites read the same shape they did when calling the raw API directly.
 */
export function createCachedFetch<T>(fetcher: () => Promise<T>, ttlMs: number = DEFAULT_TTL_MS): CachedFetch<T> {
  let value: T | undefined;
  let fetchedAt = 0;
  let inFlight: Promise<T> | null = null;

  const get = (): Promise<T> => {
    if (value !== undefined && Date.now() - fetchedAt <= ttlMs) {
      return Promise.resolve(value);
    }
    if (inFlight) {
      return inFlight;
    }
    inFlight = fetcher()
      .then((result) => {
        value = result;
        fetchedAt = Date.now();
        inFlight = null;
        return result;
      })
      .catch((err) => {
        // Do not cache failures: clear the in-flight pointer so the next caller
        // retries the network instead of being stuck with a rejected promise.
        inFlight = null;
        throw err;
      });
    return inFlight;
  };

  const invalidate = (): void => {
    value = undefined;
    fetchedAt = 0;
    inFlight = null;
  };

  return { get, invalidate };
}

// --- Shared caches for stable global data -----------------------------------

const usersCache = createCachedFetch<User[]>(() => authApi.listAllUsers());
const rolePermissionsCache = createCachedFetch<RolePermissionsSettings>(() => settingsApi.getRolePermissions());
const gmailSyncCache = createCachedFetch<GmailSyncSettings>(() => settingsApi.getGmailSync());

/** Cached `authApi.listAllUsers()` — returns the same `User[]` the raw API does. */
export const getCachedUsers = (): Promise<User[]> => usersCache.get();
/** Bust the users cache after editing/seeding/deleting users. */
export const invalidateUsersCache = (): void => usersCache.invalidate();

/** Cached `settingsApi.getRolePermissions()`. */
export const getCachedRolePermissions = (): Promise<RolePermissionsSettings> => rolePermissionsCache.get();
/** Bust the role-permissions cache after saving role permissions. */
export const invalidateRolePermissionsCache = (): void => rolePermissionsCache.invalidate();

/** Cached `settingsApi.getGmailSync()`. */
export const getCachedGmailSync = (): Promise<GmailSyncSettings> => gmailSyncCache.get();
/** Bust the Gmail-sync cache after changing the shared inbox or connection. */
export const invalidateGmailSyncCache = (): void => gmailSyncCache.invalidate();

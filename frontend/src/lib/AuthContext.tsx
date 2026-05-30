import { createContext, useContext, useEffect, useState, useCallback, useMemo } from "react";
import type { ReactNode } from "react";
import type { User } from "../types";
import { authApi } from "./api";

export type RoleView = "admin" | "ae" | "sdr";

// Superadmins can (a) preview the app as another ROLE, or (b) impersonate a
// specific PERSON to see the CRM from their exact perspective (their pipeline,
// their scoped meetings, their tasks). Person-impersonation is read-only and
// enforced server-side. Gated to these two by email — distinct from the `admin`
// role, which several people hold. Kept in sync with backend SUPERADMIN_EMAILS.
const SUPERADMIN_EMAILS = new Set(["sarthak@beacon.li", "rakesh@beacon.li"]);
const VIEW_AS_KEY = "beacon_view_as_role";
const TOKEN_KEY = "beacon_token";
// While impersonating, the active token is the target user's; we stash the real
// superadmin token here so "Exit" can restore it.
const IMPERSONATOR_TOKEN_KEY = "beacon_impersonator_token";

interface AuthState {
  user: User | null;        // effective user — impersonated person, or role-view clone of self
  realUser: User | null;    // the actual signed-in user (identity, superadmin check)
  loading: boolean;
  isAdmin: boolean;         // derived from the EFFECTIVE role
  isSuperAdmin: boolean;    // based on the real user — gates the switcher
  viewAsRole: RoleView | null;
  setViewAsRole: (role: RoleView | null) => void;
  isImpersonating: boolean;       // true while viewing as a specific person
  impersonatedUser: User | null;  // the person being viewed (for the banner)
  impersonate: (userId: string) => Promise<void>;
  stopImpersonating: () => void;
  login: (token: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthState>({
  user: null,
  realUser: null,
  loading: true,
  isAdmin: false,
  isSuperAdmin: false,
  viewAsRole: null,
  setViewAsRole: () => {},
  isImpersonating: false,
  impersonatedUser: null,
  impersonate: async () => {},
  stopImpersonating: () => {},
  login: () => {},
  logout: () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [realUser, setRealUser] = useState<User | null>(null);
  const [impersonatedUser, setImpersonatedUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [viewAsRole, setViewAsRoleState] = useState<RoleView | null>(() => {
    const v = localStorage.getItem(VIEW_AS_KEY);
    return v === "admin" || v === "ae" || v === "sdr" ? v : null;
  });

  const fetchMe = useCallback(async () => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) {
      setLoading(false);
      return;
    }
    const impToken = localStorage.getItem(IMPERSONATOR_TOKEN_KEY);
    try {
      if (impToken) {
        // Impersonating: the active token resolves to the target user, while the
        // stashed impersonator token resolves to the real superadmin.
        const [me, real] = await Promise.all([authApi.me(), authApi.meWithToken(impToken)]);
        setImpersonatedUser(me);
        setRealUser(real);
      } else {
        setRealUser(await authApi.me());
        setImpersonatedUser(null);
      }
    } catch {
      // Bad impersonation token → drop it and fall back to the active token.
      localStorage.removeItem(IMPERSONATOR_TOKEN_KEY);
      setImpersonatedUser(null);
      try {
        setRealUser(await authApi.me());
      } catch {
        localStorage.removeItem(TOKEN_KEY);
        setRealUser(null);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchMe();
  }, [fetchMe]);

  const login = useCallback((token: string) => {
    // A fresh login always clears any lingering impersonation.
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.removeItem(IMPERSONATOR_TOKEN_KEY);
    setImpersonatedUser(null);
    authApi.me().then(setRealUser).catch(() => {
      localStorage.removeItem(TOKEN_KEY);
      setRealUser(null);
    });
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(IMPERSONATOR_TOKEN_KEY);
    localStorage.removeItem(VIEW_AS_KEY);
    setViewAsRoleState(null);
    setImpersonatedUser(null);
    setRealUser(null);
  }, []);

  const isSuperAdmin = !!realUser && SUPERADMIN_EMAILS.has((realUser.email || "").trim().toLowerCase());
  const isImpersonating = !!impersonatedUser;

  // Switch into a specific teammate's view. Must be invoked with the real
  // (non-impersonation) token active. A full reload guarantees every screen
  // refetches as the target user with no stale superadmin data lingering.
  const impersonate = useCallback(async (userId: string) => {
    const myToken = localStorage.getItem(TOKEN_KEY);
    const resp = await authApi.impersonate(userId);
    if (myToken) localStorage.setItem(IMPERSONATOR_TOKEN_KEY, myToken);
    localStorage.setItem(TOKEN_KEY, resp.token);
    localStorage.removeItem(VIEW_AS_KEY); // role-view and impersonation are mutually exclusive
    window.location.assign("/");
  }, []);

  const stopImpersonating = useCallback(() => {
    const impToken = localStorage.getItem(IMPERSONATOR_TOKEN_KEY);
    if (impToken) localStorage.setItem(TOKEN_KEY, impToken);
    localStorage.removeItem(IMPERSONATOR_TOKEN_KEY);
    window.location.assign("/");
  }, []);

  const setViewAsRole = useCallback((role: RoleView | null) => {
    if (role) localStorage.setItem(VIEW_AS_KEY, role);
    else localStorage.removeItem(VIEW_AS_KEY);
    setViewAsRoleState(role);
  }, []);

  // Role-view only applies to superadmins who are NOT impersonating a person.
  const activeView = isSuperAdmin && !isImpersonating ? viewAsRole : null;

  // Effective user: the impersonated person if viewing one, else the real user
  // with an optional role swap. Every consumer of `user.role` / `isAdmin`
  // reflects the active perspective with no changes at the call sites.
  const user = useMemo<User | null>(() => {
    if (isImpersonating && impersonatedUser) return impersonatedUser;
    if (!realUser) return null;
    if (activeView && activeView !== realUser.role) return { ...realUser, role: activeView };
    return realUser;
  }, [isImpersonating, impersonatedUser, realUser, activeView]);

  return (
    <AuthContext.Provider
      value={{
        user,
        realUser,
        loading,
        isAdmin: user?.role === "admin",
        isSuperAdmin,
        viewAsRole: activeView,
        setViewAsRole,
        isImpersonating,
        impersonatedUser,
        impersonate,
        stopImpersonating,
        login,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}

import { createContext, useContext, useEffect, useState, useCallback, useMemo } from "react";
import type { ReactNode } from "react";
import type { User } from "../types";
import { authApi } from "./api";

export type RoleView = "admin" | "ae" | "sdr";

// Superadmins can preview the app as another role to understand each
// perspective. Gated to these two by email — distinct from the `admin` role,
// which several people hold. Identity is always the REAL user; only the
// effective `role` is swapped while viewing-as.
const SUPERADMIN_EMAILS = new Set(["sarthak@beacon.li", "rakesh@beacon.li"]);
const VIEW_AS_KEY = "beacon_view_as_role";

interface AuthState {
  user: User | null;        // effective user — role is swapped while viewing-as
  realUser: User | null;    // the actual signed-in user (identity, superadmin check)
  loading: boolean;
  isAdmin: boolean;         // derived from the EFFECTIVE role
  isSuperAdmin: boolean;    // based on the real user — gates the role switcher
  viewAsRole: RoleView | null;
  setViewAsRole: (role: RoleView | null) => void;
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
  login: () => {},
  logout: () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [realUser, setRealUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [viewAsRole, setViewAsRoleState] = useState<RoleView | null>(() => {
    const v = localStorage.getItem(VIEW_AS_KEY);
    return v === "admin" || v === "ae" || v === "sdr" ? v : null;
  });

  const fetchMe = useCallback(async () => {
    // Browser refreshes only persist the token, so we rehydrate the current
    // user from /auth/me on boot instead of storing a second copy of user data.
    const token = localStorage.getItem("beacon_token");
    if (!token) {
      setLoading(false);
      return;
    }
    try {
      setRealUser(await authApi.me());
    } catch {
      localStorage.removeItem("beacon_token");
      setRealUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchMe();
  }, [fetchMe]);

  const login = useCallback((token: string) => {
    localStorage.setItem("beacon_token", token);
    // Exchange the token for the canonical server-side user record immediately
    // so role-based UI can render without waiting for a page reload.
    authApi.me().then(setRealUser).catch(() => {
      localStorage.removeItem("beacon_token");
      setRealUser(null);
    });
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem("beacon_token");
    localStorage.removeItem(VIEW_AS_KEY);
    setViewAsRoleState(null);
    setRealUser(null);
  }, []);

  const isSuperAdmin = !!realUser && SUPERADMIN_EMAILS.has((realUser.email || "").trim().toLowerCase());

  const setViewAsRole = useCallback((role: RoleView | null) => {
    if (role) localStorage.setItem(VIEW_AS_KEY, role);
    else localStorage.removeItem(VIEW_AS_KEY);
    setViewAsRoleState(role);
  }, []);

  // Only superadmins can be viewing-as; everyone else is always themselves.
  const activeView = isSuperAdmin ? viewAsRole : null;

  // Effective user: clone the real user with the previewed role so EVERY
  // consumer of `user.role` / `isAdmin` reflects the perspective with no
  // changes at the call sites. Identity fields (id, email, name) are untouched.
  const user = useMemo<User | null>(() => {
    if (!realUser) return null;
    if (activeView && activeView !== realUser.role) return { ...realUser, role: activeView };
    return realUser;
  }, [realUser, activeView]);

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

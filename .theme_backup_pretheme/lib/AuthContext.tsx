import { createContext, useContext, useEffect, useState, useCallback } from "react";
import type { ReactNode } from "react";
import type { User } from "../types";
import { authApi } from "./api";

interface AuthState {
  user: User | null;
  loading: boolean;
  isAdmin: boolean;
  login: (token: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthState>({
  user: null,
  loading: true,
  isAdmin: false,
  login: () => {},
  logout: () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchMe = useCallback(async () => {
    // Browser refreshes only persist the token, so we rehydrate the current
    // user from /auth/me on boot instead of storing a second copy of user data.
    const token = localStorage.getItem("beacon_token");
    if (!token) {
      setLoading(false);
      return;
    }
    try {
      const me = await authApi.me();
      setUser(me);
    } catch {
      localStorage.removeItem("beacon_token");
      setUser(null);
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
    authApi.me().then(setUser).catch(() => {
      localStorage.removeItem("beacon_token");
      setUser(null);
    });
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem("beacon_token");
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        isAdmin: user?.role === "admin",
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

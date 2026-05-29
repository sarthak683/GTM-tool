import { memo, useEffect, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { Briefcase, CalendarDays, CheckSquare, ChevronDown, Eye, LogOut, Plus, Search, Shield, User, UserPlus } from "lucide-react";
import Sidebar from "./Sidebar";
import MobileNav from "./MobileNav";
import GlobalSearchModal from "./GlobalSearchModal";
import { NotificationBell } from "./NotificationBell";
import { ZippyLauncher } from "../zippy/ZippyLauncher";
import { ZippyProvider } from "../zippy/ZippyContext";
import { useAuth } from "../../lib/AuthContext";

const PAGE_META: Record<string, { title: string; subtitle: string }> = {
  "/pipeline": { title: "Pipeline", subtitle: "Track movement across every revenue stage" },
  "/account-sourcing": { title: "Account Sourcing", subtitle: "Source, import, and prioritize target accounts" },
  "/import": { title: "Account Sourcing", subtitle: "Upload target account CSVs and run bulk prospecting" },
  "/companies": { title: "Account Sourcing", subtitle: "Target accounts and ICP fit" },
  "/prospecting": { title: "Prospecting", subtitle: "Activate contacts, personas, and outreach readiness" },
  "/contacts": { title: "Prospecting", subtitle: "Stakeholders, personas, and outreach" },
  "/pre-meeting-assistance": { title: "Meetings", subtitle: "Prep upcoming calls and review past meeting intel" },
  "/meetings/manage": { title: "Meetings", subtitle: "Schedule calls and maintain meeting records" },
  "/meetings": { title: "Meetings", subtitle: "Prep upcoming calls and review past meeting intel" },
  "/sales-analytics": { title: "Sales Analytics", subtitle: "Rep performance, forecast visibility, and pipeline quality" },
  "/team": { title: "Team Management", subtitle: "Manage team members, roles, and permissions" },
  "/settings": { title: "Settings", subtitle: "Configure shared workflows, inboxes, and workspace defaults" },
};

function Layout() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { user, realUser, logout, isAdmin, isSuperAdmin, viewAsRole, setViewAsRole } = useAuth();
  const impersonating = isSuperAdmin && !!viewAsRole && viewAsRole !== realUser?.role;
  const [showUserMenu, setShowUserMenu] = useState(false);
  const [showNewMenu, setShowNewMenu] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [showGlobalSearch, setShowGlobalSearch] = useState(false);
  const matchedMeta = Object.entries(PAGE_META).find(([route]) => pathname === route || pathname.startsWith(`${route}/`));
  const meta = matchedMeta?.[1] ?? {
    title: "Beacon CRM",
    subtitle: "Enterprise GTM execution workspace",
  };
  const isPipelineRoute = pathname === "/pipeline";

  useEffect(() => {
    const saved = window.localStorage.getItem("crm.sidebar.collapsed");
    if (saved === "1") setSidebarCollapsed(true);
  }, []);

  useEffect(() => {
    window.localStorage.setItem("crm.sidebar.collapsed", sidebarCollapsed ? "1" : "0");
  }, [sidebarCollapsed]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setShowGlobalSearch(true);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  const handleNewAction = (path: string) => {
    setShowNewMenu(false);
    navigate(path);
  };

  return (
    <ZippyProvider>
    <div className={`crm-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
      <GlobalSearchModal open={showGlobalSearch} onClose={() => setShowGlobalSearch(false)} />
      <ZippyLauncher />
      <Sidebar collapsed={sidebarCollapsed} onToggle={() => setSidebarCollapsed((value) => !value)} />
      <main className="crm-main">
        <header className="crm-topbar">
          <div className="crm-topbar-left">
            <div className="crm-page-copy">
              <h1 className="crm-title">{meta.title}</h1>
              <p className="crm-subtitle">{meta.subtitle}</p>
            </div>
          </div>
          <div className="crm-top-actions">
            <div style={{ position: "relative" }}>
              <button
                type="button"
                onClick={() => {
                  setShowNewMenu((value) => !value);
                  setShowUserMenu(false);
                }}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 8,
                  height: 44,
                  padding: "0 14px",
                  borderRadius: 14,
                  border: "1px solid #ffcab8",
                  background: "linear-gradient(135deg, #a4d64a 0%, #7fb52f 100%)",
                  color: "#fff",
                  fontSize: 13,
                  fontWeight: 800,
                  cursor: "pointer",
                  boxShadow: "0 14px 28px rgba(154, 206, 61,0.18)",
                }}
              >
                <Plus size={15} />
                New
                <ChevronDown size={13} />
              </button>
              {showNewMenu && (
                <div
                  style={{
                    position: "absolute",
                    right: 0,
                    top: "calc(100% + 6px)",
                    width: 230,
                    background: "#ffffff",
                    border: "1px solid #dde6f0",
                    borderRadius: 16,
                    padding: 8,
                    zIndex: 105,
                    boxShadow: "0 18px 40px rgba(15,23,42,0.14)",
                  }}
                >
                  {[
                    { label: "Deal", hint: "Add a pipeline opportunity", path: "/pipeline?new=deal", icon: Briefcase },
                    { label: "Prospect", hint: "Create a person to work", path: "/prospecting?new=prospect", icon: UserPlus },
                    { label: "Meeting", hint: "Log or schedule a call", path: "/meetings?new=meeting", icon: CalendarDays },
                    { label: "Task", hint: "Assign a follow-up", path: "/tasks?new=task", icon: CheckSquare },
                  ].map((item) => {
                    const Icon = item.icon;
                    return (
                      <button
                        key={item.path}
                        type="button"
                        onClick={() => handleNewAction(item.path)}
                        style={{
                          width: "100%",
                          display: "flex",
                          alignItems: "center",
                          gap: 10,
                          padding: "10px 11px",
                          border: "none",
                          borderRadius: 12,
                          background: "transparent",
                          color: "#203244",
                          textAlign: "left",
                          cursor: "pointer",
                        }}
                        onMouseEnter={(e) => (e.currentTarget.style.background = "#f6f9fd")}
                        onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                      >
                        <span style={{ width: 28, height: 28, borderRadius: 10, background: "#f3fbe3", color: "#9ace3d", display: "grid", placeItems: "center", flexShrink: 0 }}>
                          <Icon size={14} />
                        </span>
                        <span style={{ display: "grid", gap: 2 }}>
                          <span style={{ fontSize: 13, fontWeight: 800 }}>{item.label}</span>
                          <span style={{ fontSize: 11, color: "#7b8ca2" }}>{item.hint}</span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
            <button type="button" className="crm-search-shell" onClick={() => setShowGlobalSearch(true)} style={{ cursor: "pointer" }}>
              <div className="relative">
                <Search size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-[#8f98bd]" />
                <div className="crm-search" style={{ display: "flex", alignItems: "center", color: "#8a99ad" }}>
                  Quick Search
                </div>
              </div>
              <span className="crm-search-kbd">Ctrl + K</span>
            </button>
            <NotificationBell />
            <div style={{ position: "relative" }}>
              <button
                type="button"
                onClick={() => setShowUserMenu((v) => !v)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "8px",
                  background: "rgba(255,255,255,0.94)",
                  border: "1px solid #dbe4ef",
                  cursor: "pointer",
                  padding: "6px 10px",
                  borderRadius: "14px",
                  boxShadow: "0 10px 24px rgba(15,23,42,0.05)",
                }}
              >
                {user?.avatar_url ? (
                  <img
                    src={user.avatar_url}
                    alt={user.name}
                    style={{ width: 28, height: 28, borderRadius: "50%", objectFit: "cover" }}
                    referrerPolicy="no-referrer"
                  />
                ) : (
                  <div className="crm-user-badge">{user?.name?.charAt(0) ?? "?"}</div>
                )}
                <span style={{ color: "#1d2f43", fontSize: "13px", fontWeight: 700 }}>
                  {user?.name?.split(" ")[0]}
                </span>
                {isAdmin && (
                  <span
                    style={{
                      fontSize: "10px",
                      padding: "1px 6px",
                      borderRadius: "999px",
                      background: "#eef4ff",
                      color: "#4561d5",
                      fontWeight: 600,
                      textTransform: "uppercase",
                    }}
                  >
                    Admin
                  </span>
                )}
                <ChevronDown size={14} style={{ color: "#7b8ca2" }} />
              </button>
              {showUserMenu && (
                <div
                  style={{
                    position: "absolute",
                    right: 0,
                    top: "calc(100% + 4px)",
                    background: "#ffffff",
                    border: "1px solid #dde6f0",
                    borderRadius: "16px",
                    padding: "8px",
                    minWidth: "200px",
                    zIndex: 100,
                    boxShadow: "0 18px 40px rgba(15,23,42,0.12)",
                  }}
                >
                  <div style={{ padding: "10px 12px", borderBottom: "1px solid #eef2f7", marginBottom: "4px" }}>
                    <div style={{ color: "#1c2d40", fontSize: "13px", fontWeight: 700 }}>{user?.name}</div>
                    <div style={{ color: "#6b7c92", fontSize: "11px" }}>{user?.email}</div>
                    <div style={{ display: "flex", alignItems: "center", gap: "4px", marginTop: "4px" }}>
                      {isAdmin ? <Shield size={11} color="#4561d5" /> : <User size={11} color="#7b8ca2" />}
                      <span style={{ color: isAdmin ? "#4561d5" : "#7b8ca2", fontSize: "11px", textTransform: "capitalize" }}>
                        {user?.role?.replace("_", " ")}
                        {impersonating ? " · viewing" : ""}
                      </span>
                    </div>
                  </div>
                  {isSuperAdmin && (
                    <div style={{ padding: "10px 12px", borderBottom: "1px solid #eef2f7", marginBottom: "4px" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "10px", fontWeight: 800, letterSpacing: "0.06em", textTransform: "uppercase", color: "#7b8ca2", marginBottom: "8px" }}>
                        <Eye size={11} /> View as role
                      </div>
                      <div style={{ display: "flex", gap: "6px" }}>
                        {(["admin", "ae", "sdr"] as const).map((r) => {
                          const active = (viewAsRole ?? realUser?.role) === r;
                          return (
                            <button
                              key={r}
                              type="button"
                              onClick={() => setViewAsRole(r === realUser?.role ? null : r)}
                              style={{
                                flex: 1, padding: "6px 0", borderRadius: "8px", fontSize: "11.5px", fontWeight: 800, textTransform: "uppercase",
                                border: active ? "1.5px solid #9ace3d" : "1px solid #dde6f0",
                                background: active ? "#f3fbe3" : "#fff",
                                color: active ? "#4d7c0f" : "#5d6f84",
                                cursor: "pointer",
                              }}
                            >
                              {r}
                            </button>
                          );
                        })}
                      </div>
                      <div style={{ fontSize: "10.5px", color: "#94a3b8", marginTop: "6px", lineHeight: 1.5 }}>
                        Preview the app from this role's perspective. Your real access and data don't change.
                      </div>
                    </div>
                  )}
                  <button
                    type="button"
                    onClick={() => { setShowUserMenu(false); logout(); }}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "8px",
                      width: "100%",
                      padding: "8px 12px",
                      background: "none",
                      border: "none",
                      borderRadius: "10px",
                      color: "#ef4444",
                      fontSize: "13px",
                      cursor: "pointer",
                      textAlign: "left",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(239,68,68,0.1)")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
                  >
                    <LogOut size={14} />
                    Sign out
                  </button>
                </div>
              )}
            </div>
          </div>
        </header>
        {impersonating && (
          <div style={{ position: "fixed", top: 10, left: "50%", transform: "translateX(-50%)", zIndex: 300, display: "inline-flex", alignItems: "center", gap: 10, padding: "7px 8px 7px 14px", borderRadius: 999, background: "#0b0c0e", color: "#fff", boxShadow: "0 12px 30px rgba(11,12,14,0.42)", border: "1px solid #23262b", fontSize: 12.5, fontWeight: 700 }}>
            <Eye size={13} style={{ color: "#9ace3d" }} />
            Viewing as <strong style={{ textTransform: "uppercase", color: "#9ace3d", letterSpacing: "0.04em" }}>{viewAsRole}</strong>
            <button type="button" onClick={() => setViewAsRole(null)} style={{ border: "none", background: "rgba(255,255,255,0.12)", color: "#fff", borderRadius: 999, padding: "4px 12px", fontSize: 11.5, fontWeight: 800, cursor: "pointer" }}>
              Exit
            </button>
          </div>
        )}
        <section className={`crm-content ${isPipelineRoute ? "crm-content--pipeline" : ""}`}>
          <div className={`crm-content-inner ${isPipelineRoute ? "crm-content-inner--pipeline" : ""}`}>
            <Outlet />
          </div>
        </section>
        <MobileNav />
      </main>
    </div>
    </ZippyProvider>
  );
}

export default memo(Layout);

import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Search, ChevronDown, LogOut, Shield, User } from "lucide-react";
import Sidebar from "./Sidebar";
import GlobalSearchModal from "./GlobalSearchModal";
import { ZippyLauncher } from "../zippy/ZippyLauncher";
import { ZippyProvider } from "../zippy/ZippyContext";
import { useAuth } from "../../lib/AuthContext";

const PAGE_META: Record<string, { title: string; subtitle: string }> = {
  "/sales-workspace": { title: "Sales Workspace", subtitle: "Unified GTM execution journey across all stages" },
  "/pipeline": { title: "Pipeline", subtitle: "Track movement across every revenue stage" },
  "/account-sourcing": { title: "Account Sourcing", subtitle: "Source, import, and prioritize target accounts" },
  "/import": { title: "Account Sourcing", subtitle: "Upload target account CSVs and run bulk prospecting" },
  "/companies": { title: "Account Sourcing", subtitle: "Target accounts and ICP fit" },
  "/prospecting": { title: "Prospecting", subtitle: "Activate contacts, personas, and outreach readiness" },
  "/contacts": { title: "Prospecting", subtitle: "Stakeholders, personas, and outreach" },
  "/pre-meeting-assistance": { title: "Pre-Meeting Assistance", subtitle: "Pre-brief and account context before every call" },
  "/custom-demo-assistance": { title: "Custom-Demo Assistance", subtitle: "Generate account-specific demo strategy" },
  "/meetings": { title: "Pre-Meeting Assistance", subtitle: "Pre-brief, debrief, and meeting quality scoring" },
  "/live-meeting-assistance": { title: "Live-Meeting Assistance", subtitle: "Live objection handling and competitive responses" },
  "/battlecards": { title: "Live-Meeting Assistance", subtitle: "Battlecards and talk-track support during calls" },
  "/sales-analytics": { title: "Sales Analytics", subtitle: "Rep performance, forecast visibility, and pipeline quality" },
  "/crm-insights-alerts": { title: "Sales Analytics", subtitle: "Rep performance, forecast visibility, and pipeline quality" },
  "/dashboard": { title: "Sales Analytics", subtitle: "Rep performance, forecast visibility, and pipeline quality" },
  "/workspace-insights": { title: "CRM- Insights and Alerts", subtitle: "Operational alerts and workspace readiness" },
  "/execution-tracker": { title: "Execution Tracker", subtitle: "Assigned work, rep updates, blockers, and next-step accountability" },
  "/angel-mapping": { title: "Prospecting", subtitle: "Activate contacts, personas, and outreach readiness" },
  "/team": { title: "Team Management", subtitle: "Manage team members, roles, and permissions" },
  "/settings": { title: "Settings", subtitle: "Configure shared workflows, inboxes, and workspace defaults" },
};

export default function Layout() {
  const { pathname } = useLocation();
  const { user, logout, isAdmin } = useAuth();
  const [showUserMenu, setShowUserMenu] = useState(false);
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

  return (
    <ZippyProvider>
    <div className={`crm-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
      <GlobalSearchModal open={showGlobalSearch} onClose={() => setShowGlobalSearch(false)} />
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
            <button type="button" className="crm-search-shell" onClick={() => setShowGlobalSearch(true)} style={{ cursor: "pointer" }}>
              <div className="relative">
                <Search size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-[#8f98bd]" />
                <div className="crm-search" style={{ display: "flex", alignItems: "center", color: "#8a99ad" }}>
                  Quick Search
                </div>
              </div>
              <span className="crm-search-kbd">Ctrl + K</span>
            </button>
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
                      </span>
                    </div>
                  </div>
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
        <section className={`crm-content ${isPipelineRoute ? "crm-content--pipeline" : ""}`}>
          <div className={`crm-content-inner ${isPipelineRoute ? "crm-content-inner--pipeline" : ""}`}>
            <Outlet />
          </div>
        </section>
      </main>
      <ZippyLauncher />
    </div>
    </ZippyProvider>
  );
}

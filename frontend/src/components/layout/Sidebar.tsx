import { memo, useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import {
  CalendarDays,
  ChartColumnBig,
  KanbanSquare,
  Radar,
  Search,
  CheckSquare,
  Settings,
  Users,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import { settingsApi, tasksApi } from "../../lib/api";
import { useAuth } from "../../lib/AuthContext";

const NAV = [
  { to: "/pipeline", label: "Pipeline", description: "Drag stages, manage forecast, and move revenue forward.", icon: KanbanSquare },
  { to: "/account-sourcing", label: "Account Sourcing", description: "Import, score, and prioritize target accounts.", icon: Search },
  { to: "/prospecting", label: "Prospecting", description: "Activate personas, ownership, and outreach readiness.", icon: Radar },
  { to: "/sales-analytics", label: "Sales Analytics", description: "See pipeline quality, activity, and forecast health.", icon: ChartColumnBig },
  { to: "/meetings", label: "Meetings", description: "Prep upcoming calls, review past meetings, and generate account intel.", icon: CalendarDays },
  { to: "/tasks", label: "Tasks", description: "Work the queue and accept Beacon recommendations.", icon: CheckSquare },
];

function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const { isAdmin, user } = useAuth();
  const [canManageTeam, setCanManageTeam] = useState(isAdmin);
  const [openTaskCount, setOpenTaskCount] = useState(0);

  useEffect(() => {
    if (isAdmin) {
      setCanManageTeam(true);
      return;
    }
    if (!user) {
      setCanManageTeam(false);
      return;
    }
    let cancelled = false;
    settingsApi
      .getRolePermissions()
      .then((permissions) => {
        if (!cancelled) {
          const permissionRole = user.role === "admin" ? null : user.role;
          setCanManageTeam(permissionRole ? Boolean(permissions[permissionRole]?.manage_team) : true);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setCanManageTeam(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [isAdmin, user]);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    const fetchCount = () => {
      tasksApi.countOpen().then((res) => {
        if (!cancelled) setOpenTaskCount(res.open);
      }).catch(() => {});
    };
    fetchCount();
    const interval = window.setInterval(fetchCount, 60_000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [user]);

  return (
    <aside className={`crm-sidebar ${collapsed ? "collapsed" : ""}`}>
      <div className="crm-brand">
        <div className="crm-brand-mark">
          <img
            src="/beacon-logo.jpg"
            alt="Beacon"
            style={{ width: "76%", height: "76%", objectFit: "contain", display: "block" }}
          />
        </div>
        <div className="crm-brand-copy">
          <p className="crm-brand-title">beacon.li</p>
          <p className="crm-brand-sub">Execution OS</p>
        </div>
        <button type="button" className="crm-sidebar-collapse-button" onClick={onToggle} aria-label={collapsed ? "Open sidebar" : "Collapse sidebar"}>
          {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
        </button>
      </div>

      <nav className="crm-nav">
        <p className="crm-nav-section-label">Workspace</p>
        {NAV.map((item) => {
          const Icon = item.icon;
          const isTasksItem = item.to === "/tasks";
          const showBadge = isTasksItem && openTaskCount > 0;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => `crm-nav-link ${isActive ? "active" : ""}`}
              title={collapsed ? item.label : undefined}
            >
              <span className="crm-nav-icon" style={{ position: "relative" }}>
                <Icon size={16} />
                {showBadge && collapsed && (
                  <span style={{
                    position: "absolute", top: -5, right: -5,
                    background: "#e53e3e", color: "#fff",
                    borderRadius: "50%", fontSize: 9, fontWeight: 700,
                    minWidth: 14, height: 14, display: "flex", alignItems: "center",
                    justifyContent: "center", padding: "0 3px", lineHeight: 1,
                  }}>
                    {openTaskCount > 99 ? "99+" : openTaskCount}
                  </span>
                )}
              </span>
              <span className="crm-nav-link-copy">
                <span className="crm-nav-link-label-row">
                  <span className="crm-nav-link-label">{item.label}</span>
                  {showBadge && !collapsed && (
                    <span style={{
                      background: "#e53e3e", color: "#fff",
                      borderRadius: 10, fontSize: 10, fontWeight: 700,
                      minWidth: 18, height: 18, display: "inline-flex", alignItems: "center",
                      justifyContent: "center", padding: "0 5px", lineHeight: 1,
                    }}>
                      {openTaskCount > 99 ? "99+" : openTaskCount}
                    </span>
                  )}
                </span>
                <span className="crm-nav-link-sub">{item.description}</span>
              </span>
            </NavLink>
          );
        })}
      </nav>

      <div className="crm-sidebar-footer">
        {canManageTeam && (
          <NavLink
            to="/team"
            className={({ isActive }) => `crm-nav-link ${isActive ? "active" : ""}`}
            style={{ marginBottom: 4 }}
            title={collapsed ? "Team" : undefined}
          >
            <span className="crm-nav-icon"><Users size={16} /></span>
            <span className="crm-nav-link-label">Team</span>
          </NavLink>
        )}
        <NavLink
          to="/settings"
          className={({ isActive }) => `crm-sidebar-settings ${isActive ? "active" : ""}`}
          title={collapsed ? "Settings" : undefined}
        >
          <Settings size={16} />
          <span className="crm-nav-link-label">Settings</span>
        </NavLink>
      </div>
    </aside>
  );
}

export default memo(Sidebar);

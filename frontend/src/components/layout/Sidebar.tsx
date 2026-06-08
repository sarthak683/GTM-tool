import { memo, useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import {
  CalendarDays,
  ChartColumnBig,
  KanbanSquare,
  UserSearch,
  Building2,
  ListChecks,
  Settings,
  Users,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import { tasksApi } from "../../lib/api";
import { getCachedRolePermissions } from "../../lib/cachedFetch";
import { useAuth } from "../../lib/AuthContext";

// Navigation is grouped so the rail reads as a focused command center rather
// than a flat list. "Workspace" = the build-the-pipeline surfaces; "Insights"
// = where reps review and plan their day. Descriptions live on the `title`
// tooltip (and are the label when collapsed) instead of a per-row sub-line.
const NAV_GROUPS = [
  {
    label: "Workspace",
    items: [
      { to: "/pipeline", label: "Pipeline", description: "Drag stages, manage forecast, and move revenue forward.", icon: KanbanSquare },
      { to: "/account-sourcing", label: "Account Sourcing", description: "Import, score, and prioritize target accounts.", icon: Building2 },
      { to: "/prospecting", label: "Prospecting", description: "Activate personas, ownership, and outreach readiness.", icon: UserSearch },
    ],
  },
  {
    label: "Insights",
    items: [
      { to: "/sales-analytics", label: "Sales Analytics", description: "See pipeline quality, activity, and forecast health.", icon: ChartColumnBig },
      { to: "/meetings", label: "Meetings", description: "Prep upcoming calls, review past meetings, and generate account intel.", icon: CalendarDays },
      { to: "/tasks", label: "Tasks", description: "Work your queue of manual follow-ups across deals, prospects, and accounts.", icon: ListChecks },
    ],
  },
];

// Open-task count badge — kept on-theme in the beacon green.
function TaskBadge({ count, collapsed }: { count: number; collapsed: boolean }) {
  const text = count > 99 ? "99+" : String(count);
  if (collapsed) {
    return (
      <span
        style={{
          position: "absolute", top: -5, right: -5,
          background: "#9ace3d", color: "#0a0b0c",
          borderRadius: "50%", fontSize: 9, fontWeight: 800,
          minWidth: 15, height: 15, display: "flex", alignItems: "center",
          justifyContent: "center", padding: "0 3px", lineHeight: 1,
          boxShadow: "0 0 8px rgba(154,206,61,0.6)",
        }}
      >
        {text}
      </span>
    );
  }
  return (
    <span
      style={{
        background: "rgba(154,206,61,0.18)", color: "#b6e85a",
        border: "1px solid rgba(154,206,61,0.34)",
        borderRadius: 999, fontSize: 10.5, fontWeight: 800,
        minWidth: 20, height: 19, display: "inline-flex", alignItems: "center",
        justifyContent: "center", padding: "0 7px", lineHeight: 1,
      }}
    >
      {text}
    </span>
  );
}

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
    getCachedRolePermissions()
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
            style={{ width: "78%", height: "78%", objectFit: "contain", display: "block" }}
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
        {NAV_GROUPS.map((group) => (
          <div className="crm-nav-group" key={group.label}>
            <p className="crm-nav-section-label">{group.label}</p>
            {group.items.map((item) => {
              const Icon = item.icon;
              const isTasksItem = item.to === "/tasks";
              const showBadge = isTasksItem && openTaskCount > 0;
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) => `crm-nav-link ${isActive ? "active" : ""}`}
                  title={collapsed ? item.label : item.description}
                >
                  <span className="crm-nav-icon" style={{ position: "relative" }}>
                    <Icon size={16} />
                    {showBadge && collapsed && <TaskBadge count={openTaskCount} collapsed />}
                  </span>
                  <span className="crm-nav-link-label">{item.label}</span>
                  {showBadge && !collapsed && <TaskBadge count={openTaskCount} collapsed={false} />}
                </NavLink>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="crm-sidebar-footer">
        {canManageTeam && (
          <NavLink
            to="/team"
            className={({ isActive }) => `crm-sidebar-settings ${isActive ? "active" : ""}`}
            title={collapsed ? "Team" : undefined}
          >
            <Users size={16} />
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

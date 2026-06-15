import { memo } from "react";
import { NavLink } from "react-router-dom";
import { Building2, CalendarDays, CheckSquare, KanbanSquare, Radar, Search } from "lucide-react";

const NAV = [
  { to: "/pipeline", label: "Pipeline", icon: KanbanSquare },
  { to: "/account-sourcing", label: "Sourcing", icon: Building2 },
  { to: "/prospecting", label: "Prospects", icon: Radar },
  { to: "/meetings", label: "Meetings", icon: CalendarDays },
  { to: "/tasks", label: "Tasks", icon: CheckSquare },
];

const itemStyle = (isActive: boolean): React.CSSProperties => ({
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  gap: 2,
  padding: "4px 6px",
  borderRadius: 10,
  textDecoration: "none",
  minWidth: 44,
  border: "none",
  background: isActive ? "#eef5ff" : "transparent",
  color: isActive ? "#1f6feb" : "#7a96b0",
  cursor: "pointer",
  fontFamily: "inherit",
  transition: "color 0.15s, background 0.15s",
});

function MobileNav({ onSearch }: { onSearch?: () => void }) {
  return (
    <nav className="mobile-nav" style={{
      display: "none",
      position: "fixed",
      bottom: 0,
      left: 0,
      right: 0,
      zIndex: 200,
      background: "#ffffff",
      borderTop: "1px solid #e8eef5",
      padding: "6px 4px max(6px, env(safe-area-inset-bottom))",
      boxShadow: "0 -4px 20px rgba(15,23,42,0.08)",
    }}>
      <div style={{ display: "flex", justifyContent: "space-around", alignItems: "center", maxWidth: 560, margin: "0 auto" }}>
        {NAV.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/pipeline" || item.to === "/tasks"}
              style={({ isActive }) => itemStyle(isActive)}
            >
              <Icon size={20} />
              <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.2 }}>{item.label}</span>
            </NavLink>
          );
        })}
        {/* Search docked in the bottom bar — within thumb reach on mobile,
            replacing the hard-to-reach top-right search. Opens the same
            global search modal (Ctrl+K) the desktop topbar uses. */}
        <button type="button" onClick={onSearch} style={itemStyle(false)} aria-label="Search">
          <Search size={20} />
          <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.2 }}>Search</span>
        </button>
      </div>
    </nav>
  );
}

export default memo(MobileNav);

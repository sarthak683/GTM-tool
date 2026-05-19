import { NavLink } from "react-router-dom";
import { CalendarDays, ChartColumnBig, CheckSquare, KanbanSquare, Radar, Search } from "lucide-react";

const NAV = [
  { to: "/pipeline", label: "Pipeline", icon: KanbanSquare },
  { to: "/account-sourcing", label: "Sourcing", icon: Search },
  { to: "/prospecting", label: "Prospects", icon: Radar },
  { to: "/meetings", label: "Meetings", icon: CalendarDays },
  { to: "/tasks", label: "Tasks", icon: CheckSquare },
];

export default function MobileNav() {
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
      <div style={{ display: "flex", justifyContent: "space-around", alignItems: "center", maxWidth: 500, margin: "0 auto" }}>
        {NAV.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/pipeline" || item.to === "/tasks"}
              style={({ isActive }) => ({
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 2,
                padding: "4px 8px",
                borderRadius: 10,
                textDecoration: "none",
                minWidth: 48,
                color: isActive ? "#1f6feb" : "#7a96b0",
                background: isActive ? "#eef5ff" : "transparent",
                transition: "color 0.15s, background 0.15s",
              })}
            >
              <Icon size={20} />
              <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.2 }}>{item.label}</span>
            </NavLink>
          );
        })}
      </div>
    </nav>
  );
}

import { useEffect, useState } from "react";
import { UserPlus } from "lucide-react";
import { assignmentsApi } from "../lib/api";
import { getCachedUsers } from "../lib/cachedFetch";
import { useAuth } from "../lib/AuthContext";
import type { User } from "../types";

function roleLabel(role: User["role"]) {
  if (role === "admin") return "Admin";
  if (role === "ae") return "AE";
  return "SDR";
}

interface Props {
  entityType: "company" | "contact";
  entityId: string;
  currentAssignedId?: string | null;
  currentAssignedName?: string | null;
  onAssigned?: (userId: string | null, userName: string | null) => void;
  compact?: boolean;
  role?: "ae" | "sdr";
  label?: string;
}

export default function AssignDropdown({
  entityType,
  entityId,
  currentAssignedId,
  currentAssignedName,
  onAssigned,
  compact = false,
  role = "ae",
  label,
}: Props) {
  const { isAdmin, user: currentUser } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);

  // AE/SDR slots are ownership labels, not a hard role gate — any active team
  // member can hold either. So admins (e.g. Shahruk) appear in both pickers, and
  // AEs show up in the SDR picker (and vice-versa). The selected user with the
  // matching role is sorted first so the common case stays one tap away.
  const ASSIGNABLE_ROLES = new Set(["ae", "sdr", "admin", "agency"]);
  const eligibleUsers = users
    .filter((user) => ASSIGNABLE_ROLES.has((user.role || "").toLowerCase()))
    .sort((a, b) => {
      const aMatch = (a.role || "").toLowerCase() === role ? 0 : 1;
      const bMatch = (b.role || "").toLowerCase() === role ? 0 : 1;
      if (aMatch !== bMatch) return aMatch - bMatch;
      return (a.name || "").localeCompare(b.name || "");
    });

  useEffect(() => {
    if (open && users.length === 0) {
      getCachedUsers().then(setUsers).catch(() => {});
    }
  }, [open, users.length]);

  // Per Annie 2026-06-17: any AE or SDR (not just admins) can assign the AE/SDR
  // for an account, so the full picker is shown to every sales-team member. The
  // backend enforces the same rule; roles outside the sales team stay read-only.
  const canAssign =
    isAdmin || currentUser?.role === "ae" || currentUser?.role === "sdr";

  const handleAssign = async (userId: string | null) => {
    setLoading(true);
    try {
      if (entityType === "company") {
        await assignmentsApi.assignCompany(entityId, userId, role);
      } else {
        await assignmentsApi.assignContact(entityId, userId, role);
      }
      const user = userId ? users.find((u) => u.id === userId) ?? null : null;
      onAssigned?.(userId, user?.name ?? (userId === currentUser?.id ? currentUser?.name ?? null : null));
      setOpen(false);
    } catch (err) {
      console.error("Assignment failed:", err);
    } finally {
      setLoading(false);
    }
  };

  if (!canAssign) {
    // Roles outside the sales team get a read-only label.
    return currentAssignedName ? (
      <span
        style={{
          fontSize: compact ? "11px" : "13px",
          color: "#1f6feb",
          fontWeight: 500,
        }}
      >
        {currentAssignedName}
      </span>
    ) : (
      <span style={{ fontSize: compact ? "11px" : "13px", color: "#7f8fa5" }}>
        Unassigned
      </span>
    );
  }

  return (
    <div style={{ position: "relative", display: "inline-block" }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={loading}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "6px",
          padding: compact ? "3px 8px" : "5px 12px",
          fontSize: compact ? "11px" : "13px",
          borderRadius: "6px",
          border: "1px solid #d9e1ec",
          background: currentAssignedId ? "#e8f0ff" : "#f8fafc",
          color: currentAssignedId ? "#1f6feb" : "#55657a",
          cursor: "pointer",
          fontWeight: 500,
          whiteSpace: "nowrap",
        }}
      >
        <UserPlus size={compact ? 12 : 14} />
        {loading ? "..." : currentAssignedName || (label ?? "Assign")}
      </button>

      {open && (
        <div
          className="beacon-pop"
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            left: 0,
            background: "#ffffff",
            border: "1px solid #d9e1ec",
            borderRadius: "10px",
            boxShadow: "0 8px 24px rgba(17,34,68,0.12)",
            minWidth: "200px",
            maxHeight: "260px",
            overflowY: "auto",
            zIndex: 50,
            padding: "4px",
          }}
        >
          {/* Unassign option */}
          {currentAssignedId && (
            <button
              type="button"
              onClick={() => handleAssign(null)}
              style={{
                display: "block",
                width: "100%",
                padding: "8px 12px",
                fontSize: "13px",
                color: "#b42336",
                background: "none",
                border: "none",
                borderRadius: "6px",
                cursor: "pointer",
                textAlign: "left",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "#ffecef")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
            >
              Unassign
            </button>
          )}

          {users.length === 0 && (
            <div style={{ padding: "12px", color: "#7f8fa5", fontSize: "12px", textAlign: "center" }}>
              Loading users...
            </div>
          )}

          {users.length > 0 && eligibleUsers.length === 0 && (
            <div style={{ padding: "12px", color: "#7f8fa5", fontSize: "12px", textAlign: "center" }}>
              No assignable team members available
            </div>
          )}

          {eligibleUsers.map((u) => (
            <button
              key={u.id}
              type="button"
              onClick={() => handleAssign(u.id)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "8px",
                width: "100%",
                padding: "8px 12px",
                fontSize: "13px",
                color: u.id === currentAssignedId ? "#1f6feb" : "#1d2b3c",
                fontWeight: u.id === currentAssignedId ? 600 : 400,
                background: u.id === currentAssignedId ? "#e8f0ff" : "none",
                border: "none",
                borderRadius: "6px",
                cursor: "pointer",
                textAlign: "left",
              }}
              onMouseEnter={(e) => {
                if (u.id !== currentAssignedId) e.currentTarget.style.background = "#f4f7fb";
              }}
              onMouseLeave={(e) => {
                if (u.id !== currentAssignedId) e.currentTarget.style.background = "none";
              }}
            >
              {u.avatar_url ? (
                <img
                  src={u.avatar_url}
                  alt={u.name}
                  style={{ width: 22, height: 22, borderRadius: "50%", flexShrink: 0 }}
                  referrerPolicy="no-referrer"
                />
              ) : (
                <div
                  style={{
                    width: 22,
                    height: 22,
                    borderRadius: "50%",
                    background: "#e8f0ff",
                    color: "#1f6feb",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: "11px",
                    fontWeight: 700,
                    flexShrink: 0,
                  }}
                >
                  {u.name.charAt(0)}
                </div>
              )}
              <div style={{ minWidth: 0 }}>
                <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{u.name}</div>
                <div style={{ fontSize: "11px", color: "#7f8fa5" }}>{roleLabel(u.role)}</div>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

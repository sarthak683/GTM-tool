import { useEffect, useState, type FormEvent } from "react";
import { Shield, User, UserPlus, Loader2, CheckCircle2, Mail, Save } from "lucide-react";
import { authApi } from "../lib/api";
import { getCachedRolePermissions, getCachedUsers, invalidateUsersCache } from "../lib/cachedFetch";
import { SkeletonList } from "../components/ui/Skeleton";
import { useAuth } from "../lib/AuthContext";
import type { User as UserType } from "../types";

function roleMeta(role: UserType["role"]) {
  if (role === "admin") {
    return {
      label: "Admin",
      icon: Shield,
      bg: "rgba(99, 132, 255, 0.1)",
      color: "#6384ff",
    };
  }
  if (role === "ae") {
    return {
      label: "AE",
      icon: UserPlus,
      bg: "rgba(14, 165, 233, 0.1)",
      color: "#0284c7",
    };
  }
  return {
    label: "SDR",
    icon: User,
    bg: "rgba(31, 143, 95, 0.1)",
    color: "#1f8f5f",
  };
}

export default function TeamManagement() {
  const { user: currentUser, isAdmin } = useAuth();
  const [canManageTeam, setCanManageTeam] = useState(isAdmin);
  const [users, setUsers] = useState<UserType[]>([]);
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState<string | null>(null);
  const [newUserEmail, setNewUserEmail] = useState("");
  const [newUserName, setNewUserName] = useState("");
  const [newUserRole, setNewUserRole] = useState<UserType["role"]>("sdr");
  const [creatingUser, setCreatingUser] = useState(false);

  useEffect(() => {
    if (isAdmin) {
      setCanManageTeam(true);
      return;
    }
    if (!currentUser) {
      setCanManageTeam(false);
      return;
    }
    getCachedRolePermissions()
      .then((permissions) =>
        setCanManageTeam(currentUser.role === "admin" ? true : Boolean(permissions[currentUser.role]?.manage_team))
      )
      .catch(() => setCanManageTeam(false));
  }, [currentUser, isAdmin]);

  useEffect(() => {
    setLoading(true);
    const loader = canManageTeam ? authApi.listUsers() : getCachedUsers();
    loader.then((u) => { setUsers(u); setLoading(false); }).catch(() => setLoading(false));
  }, [canManageTeam]);

  const handleRoleChange = async (userId: string, newRole: string) => {
    setUpdating(userId);
    try {
      const updated = await authApi.updateUser(userId, { role: newRole });
      invalidateUsersCache();
      setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to update role");
    } finally {
      setUpdating(null);
    }
  };

  const handleToggleActive = async (userId: string, isActive: boolean) => {
    setUpdating(userId);
    try {
      const updated = await authApi.updateUser(userId, { is_active: isActive });
      invalidateUsersCache();
      setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to update status");
    } finally {
      setUpdating(null);
    }
  };

  const handleDeleteUser = async (userId: string, userName: string) => {
    const ok = window.confirm(`Delete ${userName}? This permanently removes the account.`);
    if (!ok) return;

    setUpdating(userId);
    try {
      await authApi.deleteUser(userId);
      invalidateUsersCache();
      setUsers((prev) => prev.filter((u) => u.id !== userId));
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to delete user");
    } finally {
      setUpdating(null);
    }
  };

  const handleCreateUser = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const email = newUserEmail.trim().toLowerCase();
    const name = newUserName.trim();
    if (!email || !name) {
      alert("Name and email are required");
      return;
    }

    setCreatingUser(true);
    try {
      const created = await authApi.createUser({ email, name, role: newUserRole });
      invalidateUsersCache();
      setUsers((prev) => [...prev, created].sort((a, b) => a.name.localeCompare(b.name)));
      setNewUserEmail("");
      setNewUserName("");
      setNewUserRole("sdr");
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to add user");
    } finally {
      setCreatingUser(false);
    }
  };

  const [seeding, setSeeding] = useState(false);

  const BEACON_TEAM = [
    { email: "yash@beacon.li", name: "Yashveer Singh", role: "admin" },
    { email: "annie@beacon.li", name: "Annie Gupta", role: "ae" },
    { email: "bhavya@beacon.li", name: "Bhavya Mukkera", role: "ae" },
    { email: "dyuthith@beacon.li", name: "Dyuthith Din", role: "sdr" },
    { email: "mahesh@beacon.li", name: "Mahesh Pothula", role: "ae" },
    { email: "manognya@beacon.li", name: "Manognya Rangineni", role: "sdr" },
    { email: "pravalika@beacon.li", name: "Pravalika Jamalpur", role: "ae" },
    { email: "pulkit@beacon.li", name: "Pulkit Anand", role: "ae" },
    { email: "rakesh@beacon.li", name: "Rakesh Vaddadi", role: "sdr" },
    { email: "saher@beacon.li", name: "Saher Ghattas", role: "ae" },
    { email: "sandeep@beacon.li", name: "Sandeep Sinha", role: "ae" },
    { email: "sarthak@beacon.li", name: "Sarthak Aitha", role: "admin" },
    { email: "shahruk@beacon.li", name: "Shahruk", role: "ae" },
  ];

  const handleSeedTeam = async () => {
    setSeeding(true);
    try {
      const result = await authApi.seedUsers(BEACON_TEAM);
      invalidateUsersCache();
      alert(`Created ${result.created} new team members (${result.skipped} already existed)`);
      const loader = canManageTeam ? authApi.listUsers() : getCachedUsers();
      setUsers(await loader);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to seed team");
    } finally {
      setSeeding(false);
    }
  };

  const admins = users.filter((u) => u.role === "admin");
  const aes = users.filter((u) => u.role === "ae");
  const sdrs = users.filter((u) => u.role === "sdr");

  return (
    <div className="crm-page" style={{ padding: "0 0 40px" }}>
      <style>{`
        @media (max-width: 768px) {
          .team-mgmt-stats-grid {
            grid-template-columns: repeat(2, 1fr) !important;
            gap: 10px !important;
          }
          .team-mgmt-add-form {
            grid-template-columns: 1fr !important;
          }
        }
      `}</style>
      <div style={{ maxWidth: 1160, margin: "0 auto", width: "100%" }}>
        {/* Topbar already renders the "Team Management" title — only the
            permission context line lives in-page. */}
        <p style={{ fontSize: 13, color: "#68788d", margin: "0 0 14px" }}>
          {isAdmin
            ? "Manage your team members. Only admins can change someone else's role or access."
            : canManageTeam
              ? "You can manage teammate roles and access because your role has been granted team management permissions."
            : "View your team members."}
        </p>

        {canManageTeam && (
          <form
            className="team-mgmt-add-form"
            onSubmit={handleCreateUser}
            style={{
              background: "#fff",
              border: "1px solid #e3e9f2",
              borderRadius: 14,
              padding: 16,
              marginBottom: 16,
              display: "grid",
              gridTemplateColumns: "minmax(160px, 1fr) minmax(220px, 1.2fr) 120px auto",
              gap: 12,
              alignItems: "end",
            }}
          >
            <label style={{ display: "grid", gap: 6, minWidth: 0 }}>
              <span style={{ fontSize: 11, color: "#68788d", fontWeight: 700, textTransform: "uppercase" }}>Name</span>
              <input
                value={newUserName}
                onChange={(event) => setNewUserName(event.target.value)}
                placeholder="Jacob"
                style={{ width: "100%", height: 36, boxSizing: "border-box", border: "1px solid #d7e0ea", borderRadius: 8, padding: "0 10px", fontSize: 13, color: "#142335" }}
              />
            </label>
            <label style={{ display: "grid", gap: 6, minWidth: 0 }}>
              <span style={{ fontSize: 11, color: "#68788d", fontWeight: 700, textTransform: "uppercase" }}>Email</span>
              <div style={{ position: "relative" }}>
                <Mail size={14} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "#68788d" }} />
                <input
                  type="email"
                  value={newUserEmail}
                  onChange={(event) => setNewUserEmail(event.target.value)}
                  placeholder="name@beacon.li"
                  style={{ width: "100%", height: 36, boxSizing: "border-box", border: "1px solid #d7e0ea", borderRadius: 8, padding: "0 10px 0 30px", fontSize: 13, color: "#142335" }}
                />
              </div>
            </label>
            <label style={{ display: "grid", gap: 6 }}>
              <span style={{ fontSize: 11, color: "#68788d", fontWeight: 700, textTransform: "uppercase" }}>Role</span>
              <select
                value={newUserRole}
                onChange={(event) => setNewUserRole(event.target.value as UserType["role"])}
                style={{ height: 36, border: "1px solid #d7e0ea", borderRadius: 8, padding: "0 10px", fontSize: 13, color: "#142335", background: "#fff" }}
              >
                <option value="sdr">SDR</option>
                <option value="ae">AE</option>
                <option value="admin">Admin</option>
              </select>
            </label>
            <button
              type="submit"
              disabled={creatingUser}
              style={{ border: "1px solid #1f8f5f", background: "#1f8f5f", color: "#fff", borderRadius: 10, padding: "0 14px", fontSize: 13, fontWeight: 700, cursor: creatingUser ? "wait" : "pointer", display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 7, height: 36 }}
            >
              {creatingUser ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : <Save size={14} />}
              Add
            </button>
          </form>
        )}

        {isAdmin && (
          <button
            type="button"
            onClick={handleSeedTeam}
            disabled={seeding}
            style={{ marginBottom: 16, border: "1px solid #d8e0eb", background: "#fff", color: "#142335", borderRadius: 10, padding: "0 14px", height: 36, fontSize: 13, fontWeight: 700, cursor: seeding ? "wait" : "pointer", display: "inline-flex", alignItems: "center", gap: 8 }}
          >
            <UserPlus size={14} />
            {seeding ? "Seeding..." : "Seed missing Beacon teammates"}
          </button>
        )}

        {/* Stats */}
        <div className="team-mgmt-stats-grid" style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 16 }}>
          <div className="crm-hover-lift" style={{ background: "#fff", border: "1px solid #e3e9f2", borderRadius: 14, padding: "14px 16px" }}>
            <div style={{ fontSize: 11, color: "#68788d", fontWeight: 700, textTransform: "uppercase" }}>Total Members</div>
            <div style={{ fontSize: 24, fontWeight: 700, color: "#142335", marginTop: 4 }}>{users.length}</div>
          </div>
          <div style={{ background: "#fff", border: "1px solid #e3e9f2", borderRadius: 14, padding: "14px 16px" }}>
            <div style={{ fontSize: 11, color: "#68788d", fontWeight: 700, textTransform: "uppercase" }}>Admins</div>
            <div style={{ fontSize: 24, fontWeight: 700, color: "#6384ff", marginTop: 4 }}>{admins.length}</div>
          </div>
          <div style={{ background: "#fff", border: "1px solid #e3e9f2", borderRadius: 14, padding: "14px 16px" }}>
            <div style={{ fontSize: 11, color: "#68788d", fontWeight: 700, textTransform: "uppercase" }}>AEs</div>
            <div style={{ fontSize: 24, fontWeight: 700, color: "#0284c7", marginTop: 4 }}>{aes.length}</div>
          </div>
          <div style={{ background: "#fff", border: "1px solid #e3e9f2", borderRadius: 14, padding: "14px 16px" }}>
            <div style={{ fontSize: 11, color: "#68788d", fontWeight: 700, textTransform: "uppercase" }}>SDRs</div>
            <div style={{ fontSize: 24, fontWeight: 700, color: "#1f8f5f", marginTop: 4 }}>{sdrs.length}</div>
          </div>
        </div>

        {/* User List */}
        <div style={{ background: "#fff", border: "1px solid #e3e9f2", borderRadius: 16, overflow: "hidden" }}>
          {loading ? (
            <div style={{ padding: 16 }}>
              <SkeletonList rows={5} />
            </div>
          ) : (
            <table className="crm-table">
              <thead>
                <tr>
                  <th>Member</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Joined</th>
                  {canManageTeam && (
                    <th style={{ textAlign: "right" }}>Actions</th>
                  )}
                </tr>
              </thead>
              <tbody>
                {users.map((u) => {
                  const isMe = u.id === currentUser?.id;
                  const meta = roleMeta(u.role);
                  const RoleIcon = meta.icon;
                  return (
                    <tr key={u.id}>
                      <td data-label="Member">
                        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                          {u.avatar_url ? (
                            <img
                              src={u.avatar_url}
                              alt={u.name}
                              style={{ width: 30, height: 30, borderRadius: "50%", objectFit: "cover", flexShrink: 0 }}
                              referrerPolicy="no-referrer"
                            />
                          ) : (
                            <div style={{
                              width: 30, height: 30, borderRadius: "50%", background: "#e8f0ff", color: "#1f6feb", flexShrink: 0,
                              display: "flex", alignItems: "center", justifyContent: "center", fontSize: 13, fontWeight: 700,
                            }}>
                              {u.name.charAt(0)}
                            </div>
                          )}
                          <div style={{ minWidth: 0 }}>
                            <div style={{ fontSize: 13, fontWeight: 700, color: "#142335", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                              {u.name} {isMe && <span style={{ fontSize: 11, color: "#68788d", fontWeight: 400 }}>(you)</span>}
                            </div>
                            <div style={{ fontSize: 12, color: "#68788d", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{u.email}</div>
                          </div>
                        </div>
                      </td>
                      <td data-label="Role">
                        <span style={{
                          display: "inline-flex", alignItems: "center", gap: 4,
                          padding: "3px 9px", borderRadius: 999, fontSize: 11, fontWeight: 700,
                          background: meta.bg,
                          color: meta.color,
                        }}>
                          <RoleIcon size={12} />
                          {meta.label}
                        </span>
                      </td>
                      <td data-label="Status">
                        <span style={{
                          display: "inline-flex", alignItems: "center", gap: 4,
                          fontSize: 12, fontWeight: 600,
                          color: u.is_active ? "#1f8f5f" : "#b42336",
                        }}>
                          <CheckCircle2 size={12} />
                          {u.is_active ? "Active" : "Deactivated"}
                        </span>
                      </td>
                      <td data-label="Joined" style={{ fontSize: 12.5, color: "#68788d", whiteSpace: "nowrap" }}>
                        {new Date(u.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
                      </td>
                      {canManageTeam && (
                        <td data-label="Actions" style={{ textAlign: "right" }}>
                          {updating === u.id ? (
                            <Loader2 size={16} style={{ animation: "spin 1s linear infinite", color: "#68788d" }} />
                          ) : isMe ? (
                            <span style={{ fontSize: 12, color: "#68788d" }}>-</span>
                          ) : (
                            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", flexWrap: "wrap" }}>
                              {u.role !== "admin" && (
                                <button
                                  onClick={() => handleRoleChange(u.id, "admin")}
                                  style={{
                                    display: "flex", alignItems: "center", gap: 4,
                                    padding: "5px 10px", borderRadius: 8, fontSize: 12, fontWeight: 600,
                                    background: "rgba(99, 132, 255, 0.08)", color: "#6384ff",
                                    border: "1px solid rgba(99, 132, 255, 0.2)", cursor: "pointer",
                                  }}
                                >
                                  <Shield size={12} />
                                  Make Admin
                                </button>
                              )}
                              {u.role !== "ae" && (
                                <button
                                  onClick={() => handleRoleChange(u.id, "ae")}
                                  style={{
                                    display: "flex", alignItems: "center", gap: 4,
                                    padding: "5px 10px", borderRadius: 8, fontSize: 12, fontWeight: 600,
                                    background: "rgba(14, 165, 233, 0.08)", color: "#0284c7",
                                    border: "1px solid rgba(14, 165, 233, 0.2)", cursor: "pointer",
                                  }}
                                >
                                  <UserPlus size={12} />
                                  Make AE
                                </button>
                              )}
                              {u.role !== "sdr" && (
                                <button
                                  onClick={() => handleRoleChange(u.id, "sdr")}
                                  style={{
                                    display: "flex", alignItems: "center", gap: 4,
                                    padding: "5px 10px", borderRadius: 8, fontSize: 12, fontWeight: 600,
                                    background: "rgba(31, 143, 95, 0.08)", color: "#1f8f5f",
                                    border: "1px solid rgba(31, 143, 95, 0.2)", cursor: "pointer",
                                  }}
                                >
                                  <User size={12} />
                                  Make SDR
                                </button>
                              )}
                              <button
                                onClick={() => handleToggleActive(u.id, !u.is_active)}
                                style={{
                                  padding: "5px 10px", borderRadius: 8, fontSize: 12, fontWeight: 600,
                                  background: u.is_active ? "rgba(180, 35, 54, 0.06)" : "rgba(31, 143, 95, 0.06)",
                                  color: u.is_active ? "#b42336" : "#1f8f5f",
                                  border: `1px solid ${u.is_active ? "rgba(180, 35, 54, 0.15)" : "rgba(31, 143, 95, 0.15)"}`,
                                  cursor: "pointer",
                                }}
                              >
                                {u.is_active ? "Deactivate" : "Reactivate"}
                              </button>
                              {isAdmin && (
                                <button
                                  onClick={() => handleDeleteUser(u.id, u.name)}
                                  style={{
                                    padding: "5px 10px", borderRadius: 8, fontSize: 12, fontWeight: 600,
                                    background: "rgba(180, 35, 54, 0.08)",
                                    color: "#b42336",
                                    border: "1px solid rgba(180, 35, 54, 0.2)",
                                    cursor: "pointer",
                                  }}
                                >
                                  Delete
                                </button>
                              )}
                            </div>
                          )}
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        <p style={{ fontSize: 12, color: "#68788d", marginTop: 14, textAlign: "center" }}>
          Added members can sign in with Google using the same email.
        </p>
      </div>
    </div>
  );
}

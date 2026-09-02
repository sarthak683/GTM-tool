import { Mail, Phone, UserRound } from "lucide-react";

import type { Contact } from "../../types";
import { ACCOUNT_STATUS_OPTIONS, accountStatusOption } from "../../lib/accountStatus";
import { avatarColor, getInitials } from "../../lib/utils";

type ContactGroup = {
  key: string;
  label: string;
  color: string;
  background: string;
  records: Contact[];
};

export function groupContactsByAccountStatus(records: Contact[]): ContactGroup[] {
  const groups = new Map<string, Contact[]>();
  for (const contact of records) {
    const key = contact.company_account_status || "none";
    groups.set(key, [...(groups.get(key) ?? []), contact]);
  }

  const orderedKeys = [
    ...ACCOUNT_STATUS_OPTIONS.map((option) => option.value),
    "none",
    ...[...groups.keys()].filter((key) =>
      key !== "none" && !ACCOUNT_STATUS_OPTIONS.some((option) => option.value === key)),
  ];
  return orderedKeys
    .filter((key) => groups.has(key))
    .map((key) => {
      const option = accountStatusOption(key);
      return {
        key,
        label: option?.label ?? (key === "none" ? "No account status" : key.replace(/_/g, " ")),
        color: option?.color ?? "#52677d",
        background: option?.bg ?? "#eef3f8",
        records: groups.get(key) ?? [],
      };
    });
}

export default function ContactsBoardView({
  records,
  onOpen,
}: {
  records: Contact[];
  onOpen: (contact: Contact) => void;
}) {
  const groups = groupContactsByAccountStatus(records);

  if (!groups.length) {
    return <div className="crm-panel p-14 text-center text-[#6f8297] prospect-desktop-only">No prospects match this view.</div>;
  }

  return (
    <div className="prospect-desktop-only" style={{ overflowX: "auto", paddingBottom: 8 }}>
      <div style={{ display: "flex", gap: 12, minWidth: "max-content", alignItems: "flex-start" }}>
        {groups.map((group) => (
          <section
            key={group.key}
            aria-label={group.label}
            style={{ width: 286, maxHeight: "calc(100vh - 330px)", minHeight: 180, display: "flex", flexDirection: "column", border: "1px solid #dfe8f2", borderRadius: 14, background: "#f8fbfe", overflow: "hidden" }}
          >
            <header style={{ padding: "11px 12px", borderBottom: "1px solid #e4ecf4", background: group.background, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
              <span style={{ fontSize: 11.5, fontWeight: 850, color: group.color, textTransform: "uppercase", letterSpacing: 0.35 }}>{group.label}</span>
              <span style={{ minWidth: 24, height: 22, padding: "0 7px", borderRadius: 999, display: "inline-flex", alignItems: "center", justifyContent: "center", background: "rgba(255,255,255,0.72)", color: group.color, fontSize: 11, fontWeight: 850 }}>{group.records.length}</span>
            </header>
            <div style={{ padding: 9, display: "flex", flexDirection: "column", gap: 8, overflowY: "auto" }}>
              {group.records.map((contact) => {
                const name = `${contact.first_name || ""} ${contact.last_name || ""}`.trim() || contact.email || "Unnamed prospect";
                return (
                  <button
                    key={contact.id}
                    type="button"
                    onClick={() => onOpen(contact)}
                    style={{ width: "100%", padding: 11, border: "1px solid #dfe8f2", borderRadius: 12, background: "#fff", textAlign: "left", cursor: "pointer", boxShadow: "0 2px 7px rgba(25, 52, 79, 0.05)" }}
                  >
                    <div style={{ display: "flex", gap: 9, alignItems: "flex-start" }}>
                      <span className={`flex shrink-0 items-center justify-center rounded-full text-[10px] font-extrabold ${avatarColor(name)}`} style={{ width: 30, height: 30 }}>
                        {getInitials(name)}
                      </span>
                      <span style={{ minWidth: 0, flex: 1 }}>
                        <span style={{ display: "block", color: "#152a40", fontSize: 13, fontWeight: 800, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name}</span>
                        <span style={{ display: "block", marginTop: 2, color: "#70849a", fontSize: 11.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{contact.title || contact.company_name || "No title"}</span>
                      </span>
                    </div>
                    {contact.company_name && (
                      <div style={{ marginTop: 9, color: "#4d6278", fontSize: 11.5, fontWeight: 650, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{contact.company_name}</div>
                    )}
                    <div style={{ marginTop: 9, display: "flex", alignItems: "center", gap: 10, color: "#8597aa", fontSize: 10.5 }}>
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><Phone size={11} />{contact.phone ? "Callable" : "No phone"}</span>
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><Mail size={11} />{contact.email ? "Email" : "No email"}</span>
                      {(contact.sdr_name || contact.assigned_rep_email) && <span title={contact.sdr_name || contact.assigned_rep_email || ""} style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center" }}><UserRound size={12} /></span>}
                    </div>
                  </button>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

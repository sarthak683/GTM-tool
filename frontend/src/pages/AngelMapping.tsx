import { useEffect, useState } from "react";
import {
  Users,
  Link2,
  Loader2,
  Plus,
  Trash2,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Star,
  Upload,
} from "lucide-react";
import { angelMappingApi } from "../lib/api";
import type { AngelInvestor, AngelMapping as AngelMappingType } from "../types";

// ── Strength badge colors ──────────────────────────────────────────────────
const strengthColors: Record<number, string> = {
  5: "bg-green-100 text-green-800 border-green-300",
  4: "bg-blue-100 text-blue-800 border-blue-300",
  3: "bg-yellow-100 text-yellow-800 border-yellow-300",
  2: "bg-orange-100 text-orange-800 border-orange-300",
  1: "bg-red-100 text-red-800 border-red-300",
};

const strengthLabels: Record<number, string> = {
  5: "Direct fund overlap",
  4: "Same fund, both sides",
  3: "PE/VC peer community",
  2: "Domain/sector community",
  1: "Indirect connection",
};

// ── Main Page ──────────────────────────────────────────────────────────────

export default function AngelMapping() {
  const [tab, setTab] = useState<"mappings" | "investors">("mappings");
  const [mappings, setMappings] = useState<AngelMappingType[]>([]);
  const [investors, setInvestors] = useState<AngelInvestor[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedCompany, setExpandedCompany] = useState<string | null>(null);
  const [showAddInvestor, setShowAddInvestor] = useState(false);
  const [newInvestor, setNewInvestor] = useState({ name: "", current_role: "", current_company: "" });
  const [filterStrength, setFilterStrength] = useState<number>(0);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const [m, inv] = await Promise.all([
        angelMappingApi.listMappings(),
        angelMappingApi.listInvestors(),
      ]);
      setMappings(m);
      setInvestors(inv);
    } catch (err) {
      console.error("Failed to load angel mapping data:", err);
    } finally {
      setLoading(false);
    }
  };

  // Group mappings by company
  const grouped = mappings
    .filter((m) => !filterStrength || m.strength >= filterStrength)
    .reduce<Record<string, { company: string; mappings: AngelMappingType[] }>>((acc, m) => {
      const key = m.company_name || "Unknown Company";
      if (!acc[key]) acc[key] = { company: key, mappings: [] };
      acc[key].mappings.push(m);
      return acc;
    }, {});

  // Group by company, then by contact within each company
  const groupedByCompanyAndContact = Object.entries(grouped)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([companyName, { mappings: companyMappings }]) => {
      const byContact = companyMappings.reduce<
        Record<string, { contact: string; title: string; linkedin?: string; mappings: AngelMappingType[] }>
      >((acc, m) => {
        const contactKey = m.contact_name || "Unknown";
        if (!acc[contactKey])
          acc[contactKey] = {
            contact: contactKey,
            title: m.contact_title || "",
            linkedin: m.contact_linkedin,
            mappings: [],
          };
        acc[contactKey].mappings.push(m);
        return acc;
      }, {});
      return {
        companyName,
        contacts: Object.values(byContact).sort((a, b) => a.contact.localeCompare(b.contact)),
        totalMappings: companyMappings.length,
        maxStrength: Math.max(...companyMappings.map((m) => m.strength)),
      };
    });

  const handleAddInvestor = async () => {
    if (!newInvestor.name.trim()) return;
    try {
      const created = await angelMappingApi.createInvestor(newInvestor);
      setInvestors((prev) => [...prev, created]);
      setNewInvestor({ name: "", current_role: "", current_company: "" });
      setShowAddInvestor(false);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to create investor");
    }
  };

  const handleDeleteInvestor = async (id: string) => {
    if (!confirm("Delete this angel investor and all their mappings?")) return;
    try {
      await angelMappingApi.deleteInvestor(id);
      setInvestors((prev) => prev.filter((i) => i.id !== id));
      setMappings((prev) => prev.filter((m) => m.angel_investor_id !== id));
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to delete");
    }
  };

  const handleDeleteMapping = async (id: string) => {
    try {
      await angelMappingApi.deleteMapping(id);
      setMappings((prev) => prev.filter((m) => m.id !== id));
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to delete mapping");
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Angel / Investor Mapping</h1>
          <p className="text-sm text-zinc-400 mt-1">
            Map prospects to angel and VC investor connections for warm introductions
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowAddInvestor(true)}
            className="flex items-center gap-2 px-4 py-2 bg-zinc-800 text-zinc-200 rounded-lg hover:bg-zinc-700 text-sm border border-zinc-700"
          >
            <Plus className="w-4 h-4" />
            Add Investor
          </button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4">
        <StatCard
          label="Angel Investors"
          value={investors.length}
          icon={<Users className="w-5 h-5 text-purple-400" />}
        />
        <StatCard
          label="Total Mappings"
          value={mappings.length}
          icon={<Link2 className="w-5 h-5 text-blue-400" />}
        />
        <StatCard
          label="Companies Mapped"
          value={Object.keys(grouped).length}
          icon={<Star className="w-5 h-5 text-yellow-400" />}
        />
        <StatCard
          label="Strength 5 Connections"
          value={mappings.filter((m) => m.strength === 5).length}
          icon={<Star className="w-5 h-5 text-green-400" />}
        />
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-zinc-900 p-1 rounded-lg w-fit">
        <button
          onClick={() => setTab("mappings")}
          className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
            tab === "mappings" ? "bg-zinc-700 text-white" : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          Prospect Mappings ({mappings.length})
        </button>
        <button
          onClick={() => setTab("investors")}
          className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
            tab === "investors" ? "bg-zinc-700 text-white" : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          Angel Investors ({investors.length})
        </button>
      </div>

      {/* ── Mappings Tab ───────────────────────────────────────────────── */}
      {tab === "mappings" && (
        <div className="space-y-3">
          {/* Filter */}
          <div className="flex gap-2 items-center">
            <span className="text-sm text-zinc-400">Min strength:</span>
            {[0, 3, 4, 5].map((s) => (
              <button
                key={s}
                onClick={() => setFilterStrength(s)}
                className={`px-3 py-1 rounded-md text-xs font-medium border transition-colors ${
                  filterStrength === s
                    ? "bg-blue-600 text-white border-blue-500"
                    : "bg-zinc-800 text-zinc-300 border-zinc-700 hover:bg-zinc-700"
                }`}
              >
                {s === 0 ? "All" : `${s}+`}
              </button>
            ))}
          </div>

          {groupedByCompanyAndContact.length === 0 ? (
            <div className="text-center py-16 text-zinc-500">
              <Link2 className="w-12 h-12 mx-auto mb-4 opacity-50" />
              <p className="text-lg font-medium">No angel mappings yet</p>
              <p className="text-sm mt-1">
                Import mapping data or create mappings manually
              </p>
            </div>
          ) : (
            groupedByCompanyAndContact.map(({ companyName, contacts, totalMappings, maxStrength }) => (
              <div key={companyName} className="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
                {/* Company Header */}
                <button
                  onClick={() => setExpandedCompany(expandedCompany === companyName ? null : companyName)}
                  className="w-full flex items-center gap-3 px-4 py-3 hover:bg-zinc-800/50 transition-colors"
                >
                  {expandedCompany === companyName ? (
                    <ChevronDown className="w-4 h-4 text-zinc-500" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-zinc-500" />
                  )}
                  <span className="font-semibold text-white">{companyName}</span>
                  <span className="text-xs text-zinc-500">
                    {totalMappings} connection{totalMappings !== 1 ? "s" : ""}
                  </span>
                  <span
                    className={`ml-auto text-xs px-2 py-0.5 rounded border ${strengthColors[maxStrength] || "bg-zinc-800 text-zinc-400"}`}
                  >
                    Best: {maxStrength}/5
                  </span>
                </button>

                {/* Expanded Content */}
                {expandedCompany === companyName && (
                  <div className="border-t border-zinc-800">
                    {contacts.map(({ contact, title, linkedin, mappings: contactMappings }) => (
                      <div key={contact} className="border-b border-zinc-800/50 last:border-b-0">
                        {/* Contact Row */}
                        <div className="px-6 py-3 bg-zinc-800/30 flex items-center gap-3">
                          <div className="w-8 h-8 rounded-full bg-zinc-700 flex items-center justify-center text-xs font-bold text-zinc-300">
                            {contact.split(" ").map((n) => n[0]).join("").slice(0, 2)}
                          </div>
                          <div className="flex-1">
                            <div className="flex items-center gap-2">
                              <span className="text-sm font-medium text-white">{contact}</span>
                              {linkedin && (
                                <a
                                  href={linkedin}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="text-blue-400 hover:text-blue-300"
                                >
                                  <ExternalLink className="w-3 h-3" />
                                </a>
                              )}
                            </div>
                            {title && <p className="text-xs text-zinc-400">{title}</p>}
                          </div>
                        </div>

                        {/* Angel Connections */}
                        <div className="divide-y divide-zinc-800/50">
                          {contactMappings
                            .sort((a, b) => a.rank - b.rank)
                            .map((m) => (
                              <div key={m.id} className="px-6 py-3 flex gap-4 items-start group">
                                <div className="flex-shrink-0 w-6 text-center">
                                  <span className="text-xs font-mono text-zinc-500">#{m.rank}</span>
                                </div>
                                <div
                                  className={`flex-shrink-0 px-2 py-0.5 rounded border text-xs font-bold ${
                                    strengthColors[m.strength] || ""
                                  }`}
                                >
                                  {m.strength}/5
                                </div>
                                <div className="flex-1 min-w-0">
                                  <div className="flex items-center gap-2">
                                    <span className="text-sm font-medium text-blue-400">
                                      {m.angel_name}
                                    </span>
                                    {m.angel_current_company && (
                                      <span className="text-xs text-zinc-500">
                                        {m.angel_current_role ? `${m.angel_current_role} @ ` : ""}
                                        {m.angel_current_company}
                                      </span>
                                    )}
                                  </div>
                                  {m.connection_path && (
                                    <p className="text-xs text-zinc-400 mt-1 leading-relaxed">
                                      <span className="text-zinc-500 font-medium">Path: </span>
                                      {m.connection_path}
                                    </p>
                                  )}
                                  {m.why_it_works && (
                                    <p className="text-xs text-zinc-500 mt-1 leading-relaxed italic">
                                      {m.why_it_works}
                                    </p>
                                  )}
                                  {m.recommended_strategy && (
                                    <p className="text-xs text-purple-400 mt-1">
                                      <span className="font-medium">Strategy: </span>
                                      {m.recommended_strategy}
                                    </p>
                                  )}
                                </div>
                                <button
                                  onClick={() => handleDeleteMapping(m.id)}
                                  className="opacity-0 group-hover:opacity-100 text-zinc-600 hover:text-red-400 transition-all"
                                >
                                  <Trash2 className="w-3.5 h-3.5" />
                                </button>
                              </div>
                            ))}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {/* ── Investors Tab ──────────────────────────────────────────────── */}
      {tab === "investors" && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {investors.map((inv) => {
            const mappingCount = mappings.filter((m) => m.angel_investor_id === inv.id).length;
            return (
              <div
                key={inv.id}
                className="bg-zinc-900 border border-zinc-800 rounded-lg p-4 hover:border-zinc-700 transition-colors group"
              >
                <div className="flex items-start justify-between">
                  <div>
                    <h3 className="font-semibold text-white">{inv.name}</h3>
                    {inv.current_role && (
                      <p className="text-xs text-zinc-400 mt-0.5">
                        {inv.current_role}
                        {inv.current_company ? ` @ ${inv.current_company}` : ""}
                      </p>
                    )}
                  </div>
                  <button
                    onClick={() => handleDeleteInvestor(inv.id)}
                    className="opacity-0 group-hover:opacity-100 text-zinc-600 hover:text-red-400 transition-all"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>

                <div className="mt-3 flex items-center gap-2">
                  <span className="text-xs bg-zinc-800 text-zinc-300 px-2 py-0.5 rounded">
                    {mappingCount} mapping{mappingCount !== 1 ? "s" : ""}
                  </span>
                  {inv.linkedin_url && (
                    <a
                      href={inv.linkedin_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs text-blue-400 hover:text-blue-300 flex items-center gap-1"
                    >
                      LinkedIn <ExternalLink className="w-3 h-3" />
                    </a>
                  )}
                </div>

                {inv.pe_vc_connections && (
                  <p className="text-xs text-zinc-500 mt-2 line-clamp-2">{inv.pe_vc_connections}</p>
                )}
                {inv.sectors && (
                  <p className="text-xs text-zinc-500 mt-1">
                    <span className="text-zinc-400">Sectors:</span> {inv.sectors}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* ── Add Investor Modal ─────────────────────────────────────────── */}
      {showAddInvestor && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-zinc-900 border border-zinc-700 rounded-xl p-6 w-full max-w-md">
            <h2 className="text-lg font-semibold text-white mb-4">Add Angel Investor</h2>
            <div className="space-y-3">
              <input
                placeholder="Name *"
                value={newInvestor.name}
                onChange={(e) => setNewInvestor({ ...newInvestor, name: e.target.value })}
                className="w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-white placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <input
                placeholder="Current Role"
                value={newInvestor.current_role}
                onChange={(e) => setNewInvestor({ ...newInvestor, current_role: e.target.value })}
                className="w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-white placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <input
                placeholder="Current Company"
                value={newInvestor.current_company}
                onChange={(e) => setNewInvestor({ ...newInvestor, current_company: e.target.value })}
                className="w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-white placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button
                onClick={() => setShowAddInvestor(false)}
                className="px-4 py-2 text-sm text-zinc-400 hover:text-white"
              >
                Cancel
              </button>
              <button
                onClick={handleAddInvestor}
                className="px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-500"
              >
                Add Investor
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Strength Legend */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
        <h3 className="text-sm font-semibold text-zinc-300 mb-3">Connection Strength Scale</h3>
        <div className="flex flex-wrap gap-3">
          {[5, 4, 3, 2, 1].map((s) => (
            <div key={s} className="flex items-center gap-2">
              <span className={`px-2 py-0.5 rounded border text-xs font-bold ${strengthColors[s]}`}>
                {s}/5
              </span>
              <span className="text-xs text-zinc-400">{strengthLabels[s]}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Helper Components ──────────────────────────────────────────────────────

function StatCard({
  label,
  value,
  icon,
}: {
  label: string;
  value: number;
  icon: React.ReactNode;
}) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
      <div className="flex items-center gap-3">
        {icon}
        <div>
          <p className="text-2xl font-bold text-white">{value}</p>
          <p className="text-xs text-zinc-400">{label}</p>
        </div>
      </div>
    </div>
  );
}

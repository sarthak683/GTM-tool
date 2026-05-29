import { useEffect, useMemo, useRef, useState } from "react";
import { Building2, Check, ChevronDown, Loader2, Search } from "lucide-react";
import { accountSourcingApi, companiesApi } from "../lib/api";
import type { Company } from "../types";

type SearchableCompanySelectProps = {
  value?: string;
  companies?: Company[];
  onChange: (companyId?: string) => void;
  placeholder?: string;
  noneLabel?: string;
  allowNone?: boolean;
  disabled?: boolean;
};

export default function SearchableCompanySelect({
  value,
  companies = [],
  onChange,
  placeholder = "Select company",
  noneLabel = "No company",
  allowNone = true,
  disabled = false,
}: SearchableCompanySelectProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [catalog, setCatalog] = useState<Company[]>([]);
  const [loading, setLoading] = useState(false);
  const [catalogLoaded, setCatalogLoaded] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const selectedCompany = useMemo(
    () => [...catalog, ...companies].find((company) => company.id === value),
    [catalog, companies, value],
  );

  useEffect(() => {
    const handleClick = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) {
        setOpen(false);
        setQuery(selectedCompany?.name ?? "");
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [selectedCompany]);

  useEffect(() => {
    if (!open) {
      setQuery(selectedCompany?.name ?? "");
    }
  }, [open, selectedCompany]);

  useEffect(() => {
    if (open) {
      window.setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;

    let cancelled = false;
    if (catalogLoaded) return;

    const loadCatalog = async () => {
      setLoading(true);
      try {
        const [crmCompanies, sourcingCompanies] = await Promise.all([
          companiesApi.list(0, 1000).catch(() => []),
          accountSourcingApi.listCompanies(0, 1000).catch(() => []),
        ]);
        const merged = new Map<string, Company>();
        for (const company of [...crmCompanies, ...sourcingCompanies, ...companies]) {
          if (!company?.id) continue;
          merged.set(company.id, company);
        }
        if (!cancelled) {
          setCatalog(Array.from(merged.values()));
          setCatalogLoaded(true);
        }
      } catch {
        if (!cancelled) {
          setCatalog(companies);
          setCatalogLoaded(true);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void loadCatalog();

    return () => {
      cancelled = true;
    };
  }, [catalogLoaded, companies, open]);

  const results = useMemo(() => {
    const needle = query.trim().toLowerCase();
    let next = catalog.filter((company) => !needle || company.name.toLowerCase().includes(needle));
    if (selectedCompany && !next.some((company) => company.id === selectedCompany.id)) {
      next = [selectedCompany, ...next];
    }
    return next.slice(0, 50);
  }, [catalog, query, selectedCompany]);

  const selectCompany = (companyId?: string) => {
    const company = [...catalog, ...companies].find((entry) => entry.id === companyId);
    onChange(companyId);
    setQuery(company?.name ?? "");
    setOpen(false);
  };

  return (
    <div
      ref={ref}
      style={{ position: "relative" }}
    >
      <div
        style={{
          width: "100%",
          minHeight: 42,
          borderRadius: 12,
          border: open ? "1px solid #bfd7fb" : "1px solid #d7e2ee",
          background: disabled ? "#f8fafc" : "#fff",
          padding: "0 12px",
          display: "flex",
          alignItems: "center",
          gap: 10,
          fontSize: 14,
          color: "#1f2d3d",
          boxShadow: open ? "0 0 0 4px rgba(191,215,251,0.35)" : "none",
        }}
      >
        <Building2 size={14} color="#7a96b0" />
        <input
          ref={inputRef}
          type="text"
          value={open ? query : (selectedCompany?.name ?? query)}
          disabled={disabled}
          onFocus={() => {
            if (disabled) return;
            setOpen(true);
            setQuery(selectedCompany?.name ?? "");
          }}
          onChange={(event) => {
            setOpen(true);
            setQuery(event.target.value);
          }}
          placeholder={placeholder}
          style={{
            flex: 1,
            height: 40,
            border: "none",
            outline: "none",
            background: "transparent",
            fontSize: 14,
            color: selectedCompany || query ? "#1f2d3d" : "#94a3b8",
            minWidth: 0,
          }}
        />
        <button
          type="button"
          disabled={disabled}
          onClick={() => {
            if (disabled) return;
            setOpen((current) => !current);
            if (!open) {
              setQuery(selectedCompany?.name ?? "");
            }
          }}
          style={{
            border: "none",
            background: "transparent",
            width: 24,
            height: 24,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: disabled ? "default" : "pointer",
            flexShrink: 0,
          }}
        >
          <ChevronDown size={14} color="#7a96b0" />
        </button>
      </div>

      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 8px)",
            left: 0,
            right: 0,
            borderRadius: 14,
            border: "1px solid #dbe6f2",
            background: "#fff",
            boxShadow: "0 18px 36px rgba(15,23,42,0.14)",
            zIndex: 40,
            overflow: "hidden",
          }}
        >
          <div style={{ maxHeight: 220, overflowY: "auto" }}>
            {allowNone && (
              <button
                type="button"
                onClick={() => {
                  selectCompany(undefined);
                }}
                style={{
                  display: "block",
                  width: "100%",
                  textAlign: "left",
                  padding: "10px 12px",
                  border: "none",
                  background: !value ? "#f0f6ff" : "transparent",
                  color: !value ? "#175089" : "#60758b",
                  fontSize: 13,
                  fontWeight: !value ? 700 : 500,
                  cursor: "pointer",
                }}
              >
                {noneLabel}
              </button>
            )}

            {loading ? (
              <div style={{ padding: "12px 14px", fontSize: 13, color: "#7a96b0", display: "flex", alignItems: "center", gap: 8 }}>
                <Loader2 size={14} className="animate-spin" />
                Searching companies...
              </div>
            ) : results.length === 0 ? (
              <div style={{ padding: "12px 14px", fontSize: 13, color: "#7a96b0" }}>
                No matching companies found.
              </div>
            ) : (
              results.map((company) => (
                <button
                  key={company.id}
                  type="button"
                  onClick={() => {
                    selectCompany(company.id);
                  }}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    width: "100%",
                    textAlign: "left",
                    padding: "10px 12px",
                    border: "none",
                    background: value === company.id ? "#f0f6ff" : "transparent",
                    color: value === company.id ? "#175089" : "#1f2d3d",
                    fontSize: 13,
                    fontWeight: value === company.id ? 700 : 500,
                    cursor: "pointer",
                  }}
                >
                  <span>{company.name}</span>
                  {value === company.id ? <Check size={14} color="#175089" /> : null}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

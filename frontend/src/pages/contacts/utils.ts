import type { AngelMapping } from "../../types";
import type { ProspectImportMissingCompany } from "./types";

type GroupedCompanyContact = {
  name: string;
  title: string;
  linkedin?: string;
  mappings: AngelMapping[];
};

export type GroupedAngelCompany = {
  companyName: string;
  contacts: GroupedCompanyContact[];
  totalMappings: number;
  maxStrength: number;
};

export function getMissingCompanyKey(
  company: Pick<ProspectImportMissingCompany, "name" | "domain">
): string {
  return `${company.domain || ""}-${company.name}`;
}

export function filterAngelMappings(
  mappings: AngelMapping[],
  angelSearch: string,
  filterStrength: number
): AngelMapping[] {
  return mappings
    .filter((mapping) => !filterStrength || mapping.strength >= filterStrength)
    .filter((mapping) => {
      if (!angelSearch.trim()) return true;
      const query = angelSearch.toLowerCase();
      return (
        (mapping.company_name || "").toLowerCase().includes(query) ||
        (mapping.contact_name || "").toLowerCase().includes(query) ||
        (mapping.angel_name || "").toLowerCase().includes(query)
      );
    });
}

export function groupAngelMappingsByCompany(filteredMappings: AngelMapping[]): GroupedAngelCompany[] {
  return Object.entries(
    filteredMappings.reduce<Record<string, { mappings: AngelMapping[] }>>((acc, mapping) => {
      const key = mapping.company_name || "Unknown Company";
      if (!acc[key]) acc[key] = { mappings: [] };
      acc[key].mappings.push(mapping);
      return acc;
    }, {})
  )
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([companyName, companyGroup]) => {
      const byContact = companyGroup.mappings.reduce<Record<string, GroupedCompanyContact>>((acc, mapping) => {
        const key = mapping.contact_name || "Unknown";
        if (!acc[key]) {
          acc[key] = {
            name: key,
            title: mapping.contact_title || "",
            linkedin: mapping.contact_linkedin,
            mappings: [],
          };
        }
        acc[key].mappings.push(mapping);
        return acc;
      }, {});

      return {
        companyName,
        contacts: Object.values(byContact).sort((left, right) => left.name.localeCompare(right.name)),
        totalMappings: companyGroup.mappings.length,
        maxStrength: Math.max(...companyGroup.mappings.map((mapping) => mapping.strength)),
      };
    });
}

export type ProspectingTab = "contacts" | "angel-mapping";

export type ProspectImportMissingCompany = {
  name: string;
  domain?: string;
  contacts_count: number;
};

export type ProspectImportCreatedCompany = {
  id: string;
  name: string;
  domain?: string;
  contacts_count: number;
};

export type ProspectImportSummary = {
  imported_rows: number;
  created_count: number;
  updated_count: number;
  skipped_count: number;
  warning_count?: number;
  missing_company_count: number;
  missing_companies: ProspectImportMissingCompany[];
  created_company_count?: number;
  created_companies?: ProspectImportCreatedCompany[];
  message: string;
};

export type AddProspectFormState = {
  first_name: string;
  last_name: string;
  email: string;
  phone: string;
  title: string;
  company_id: string;
  linkedin_url: string;
};

export const EMPTY_ADD_PROSPECT_FORM: AddProspectFormState = {
  first_name: "",
  last_name: "",
  email: "",
  phone: "",
  title: "",
  company_id: "",
  linkedin_url: "",
};

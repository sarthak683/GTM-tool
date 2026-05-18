"""
Tabular upload parsing helpers for Account Sourcing.

This module isolates CSV/XLSX parsing and header alias detection so the core
account_sourcing service can focus on orchestration/business logic.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET

_XLSX_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def _normalize_header(value: str) -> str:
    normalized = (value or "").strip().lower()
    replacements = {
        "&": " and ",
        "/": " ",
        "\\": " ",
        "+": " plus ",
        ">=": " ge ",
        "<=": " le ",
        ">": " gt ",
        "<": " lt ",
    }
    for needle, replacement in replacements.items():
        normalized = normalized.replace(needle, replacement)
    normalized = normalized.replace("≥", " ge ").replace("≤", " le ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = re.sub(r"^(plus|minus)\s+", "", normalized)
    return normalized.strip()


_ALIASES_RAW: dict[str, list[str]] = {
    "name": ["company name", "company", "organization", "accounts", "account", "comapnies", "companies", "name"],
    "domain": ["domain", "domain name", "website", "url", "web"],
    "industry": [
        "industry",
        "sector",
        "sector (pratice area & feed)",
        "sector (practice area & feed)",
        "vertical",
        "category",
    ],
    "employee_count": ["employee_count", "total employee count", "employees", "headcount", "employee count", "no. of employees"],
    "funding_stage": ["funding_stage", "company stage", "stage", "funding stage", "round", "series"],
    "country": ["country"],
    "city": ["city", "location"],
    "description": ["description", "overview", "about", "summary"],
    "total_funding": ["total funding (usd)", "total funding", "annual revenue (usd)", "annual revenue", "arr", "revenue"],
    "region": ["region"],
    "headquarters": ["headquarters", "hq", "headquarter"],
    "category_label": ["category"],
    "core_focus": [
        "core sor / complex impl. focus",
        "core sor complex impl focus",
        "core system of record / complex implementation focus",
    ],
    "revenue_funding_label": ["revenue / funding", "revenue funding"],
    "classification": ["classification"],
    "analyst_icp_score": ["icp fit score (0-10)", "icp fit score 0 10", "icp fit score"],
    "analyst_intent_score": ["intent score (0-10)", "intent score 0 10", "intent score"],
    "fit_type": ["fit type"],
    "confidence": ["confidence"],
    "icp_why": ["icp why"],
    "intent_why": ["intent why"],
    "ps_impl_hiring": ["ps/impl hiring", "ps impl hiring"],
    "leadership_org_moves": ["leadership / org moves", "leadership org moves"],
    "pr_funding_expansion": ["pr / funding / expansion", "pr funding expansion"],
    "events_thought_leadership": ["events / thought leadership", "events thought leadership"],
    "reviews_case_studies": ["reviews / case studies", "reviews case studies"],
    "internal_ai_overlap": ["internal ai/agentic overlap", "internal ai agentic overlap"],
    "strategic_constraints": ["m and a / ipo / strategic constraints", "m a ipo strategic constraints", "m and a ipo strategic constraints"],
    "ps_cs_contraction": ["ps / cs contraction", "ps cs contraction"],
    "build_vs_buy_impl_auto": ["build vs buy for impl. auto", "build vs buy for impl auto"],
    "ai_acquisition_impl": ["ai acquisition for impl.", "ai acquisition for impl"],
    "final_qual": ["final qual"],
    "sdr": ["sdr", "sdr name", "sdr rep"],
    "ae": ["ae", "ae name", "account executive", "owner", "account owner", "assigned to", "rep", "sales rep"],
    "contact_name": ["contact", "prospect name", "full name", "name"],
    "contact_first_name": ["first", "first name"],
    "contact_last_name": ["last", "last name"],
    "contact_title": ["title", "job title", "job", "role"],
    "contact_email": ["email", "work email"],
    "contact_phone": [
        "direct mobile personal",
        "mobile",
        "mobile phone",
        "phone",
        "phone number",
        "direct phone",
        "cell",
        "cell phone",
        "personal phone",
        "hq direct line",
        "direct line",
    ],
    "contact_timezone": ["timezone", "time zone", "timezones", "time zones"],
    "linkedin_url": ["linkedin", "linkedin url", "linkedin profile"],
    "next_steps": ["next steps", "recommended next step"],
    "ownership_stage": ["ownership stage"],
    "pe_investors": ["pe investors"],
    "vc_growth_investors": ["vc growth investors", "vc / growth investors", "vc investors", "growth investors"],
    "strategic_investors": ["strategic other investors", "strategic / other investors", "strategic investors", "other investors", "investors"],
    "angel_1_name": ["angel 1 name"],
    "angel_1_strength": ["angel 1 strength 1 5", "angel 1 strength", "connection strength 1 5"],
    "angel_1_path": ["angel 1 connection path", "connection path"],
    "angel_1_why": ["angel 1 why it works", "why it works"],
    "angel_2_name": ["angel 2 name"],
    "angel_2_strength": ["angel 2 strength 1 5", "angel 2 strength", "connection strength 1 5 2"],
    "angel_2_path": ["angel 2 connection path", "connection path 2"],
    "angel_2_why": ["angel 2 why it works", "why it works 2"],
    "angel_3_name": ["angel 3 name"],
    "angel_3_strength": ["angel 3 strength 1 5", "angel 3 strength", "connection strength 1 5 3"],
    "angel_3_path": ["angel 3 connection path", "connection path 3"],
    "angel_3_why": ["angel 3 why it works", "why it works 3"],
    "recommended_outreach_strategy": ["recommended outreach strategy"],
    "conversation_starter": ["conversation starter"],
    "what_they_do": ["what they do"],
    "who_they_are": ["who they are"],
    "tier": ["tier", "account tier"],
    "email_confidence": ["email confidence"],
    "direct_mobile": ["direct mobile personal", "direct mobile", "direct  mobile personal", "mobile phone"],
    "hq_direct_line": ["hq direct line"],
    "hq_switchboard": ["hq switchboard toll free", "hq switchboard", "hq switchboard  toll free"],
    "tenure_role": ["tenure role"],
    "tenure_company": ["tenure company"],
    "contact_location": ["location"],
    "contact_icp_score": ["icp score"],
    "contact_intent_score": ["intent score"],
    "contact_source": ["source"],
    "apollo_id": ["apollo id"],
    "career_arc": ["career arc"],
    "linkedin_activity": ["linkedin activity posts", "linkedin activity"],
    "intent_signals": ["intent signals"],
    "primary_messaging_angle": ["primary messaging angle"],
    "personalization_hook": ["personalization hook"],
    "research_confidence": ["research confidence"],
    "outreach_priority": ["outreach priority"],
    "email1_subject": ["email 1 subject"],
    "email1_body": ["email 1 body"],
    "email1_when": ["email 1 when to use", "email 1  when to use"],
    "email2_subject": ["email 2 subject"],
    "email2_body": ["email 2 body"],
    "email2_when": ["email 2 when to use", "email 2  when to use"],
    "email3_subject": ["email 3 subject"],
    "email3_body": ["email 3 body"],
    "email3_when": ["email 3 when to use", "email 3  when to use"],
}

_ALIASES: dict[str, list[str]] = {
    field: [_normalize_header(alias) for alias in aliases]
    for field, aliases in _ALIASES_RAW.items()
}


def _normalize_row(headers: list[str], values: list[str]) -> dict[str, str]:
    row: dict[str, str] = {}
    seen: dict[str, int] = {}
    for idx, header in enumerate(headers):
        if not header:
            continue
        seen[header] = seen.get(header, 0) + 1
        key = header if seen[header] == 1 else f"{header} {seen[header]}"
        row[key] = (values[idx] if idx < len(values) else "").strip()
    return row


def _header_score(values: list[str]) -> int:
    normalized = [_normalize_header(value) for value in values if (value or "").strip()]
    if not normalized:
        return 0

    matched_fields: set[str] = set()
    for field, aliases in _ALIASES.items():
        if any(alias in normalized for alias in aliases):
            matched_fields.add(field)

    score = len(matched_fields)
    has_company = "name" in matched_fields or "domain" in matched_fields
    has_contact = (
        "contact_name" in matched_fields
        or "contact_email" in matched_fields
        or "contact_title" in matched_fields
    )
    if has_company:
        score += 2
    if has_contact:
        score += 2
    return score


def _detect_header_row(sheet_rows: list[list[str]]) -> tuple[int, list[str]]:
    best_index = 0
    best_headers: list[str] = []
    best_score = -1

    for idx, values in enumerate(sheet_rows[:12]):
        score = _header_score(values)
        if score > best_score:
            best_index = idx
            best_headers = [_normalize_header(value) for value in values]
            best_score = score

    return best_index, best_headers


def parse_csv(content: bytes) -> list[dict[str, str]]:
    """Parse CSV bytes into normalized dicts. Skip rows without name or domain."""
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        cleaned = {
            _normalize_header(k): (v or "").strip()
            for k, v in row.items()
            if k and k.strip()
        }
        has_name = any(cleaned.get(a) for a in _ALIASES["name"])
        has_domain = any(cleaned.get(a) for a in _ALIASES["domain"])
        if has_name or has_domain:
            rows.append(cleaned)
    return rows


def _read_xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for si in root.findall("main:si", _XLSX_NS):
        texts = [node.text or "" for node in si.findall(".//main:t", _XLSX_NS)]
        strings.append("".join(texts))
    return strings


def _read_xlsx_hyperlinks(archive: zipfile.ZipFile, sheet_path: str) -> dict[str, str]:
    """Return a mapping of cell reference (e.g. 'C3') -> URL for hyperlinks in a sheet."""
    rels_path = sheet_path.replace("worksheets/sheet", "worksheets/_rels/sheet").replace(".xml", ".xml.rels")
    if rels_path not in archive.namelist():
        return {}

    _rels_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    rels_root = ET.fromstring(archive.read(rels_path))
    rel_id_to_url: dict[str, str] = {}
    for rel in rels_root.findall(f"{{{_rels_ns}}}Relationship"):
        if rel.attrib.get("Type", "").endswith("/hyperlink"):
            rel_id_to_url[rel.attrib["Id"]] = rel.attrib.get("Target", "")

    if not rel_id_to_url:
        return {}

    sheet_root = ET.fromstring(archive.read(sheet_path))
    _r_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    cell_to_url: dict[str, str] = {}
    for hl in sheet_root.findall(".//main:hyperlinks/main:hyperlink", _XLSX_NS):
        cell_ref = hl.attrib.get("ref", "")
        rel_id = hl.attrib.get(f"{{{_r_ns}}}id", "")
        url = rel_id_to_url.get(rel_id, "")
        if cell_ref and url:
            cell_to_url[cell_ref.split(":")[0]] = url
    return cell_to_url


def _sheet_entries(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel_map = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels.findall("{http://schemas.openxmlformats.org/package/2006/relationships}Relationship")
    }
    entries: list[tuple[str, str]] = []
    for sheet in workbook.findall("main:sheets/main:sheet", _XLSX_NS):
        rel_id = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = rel_map.get(rel_id or "")
        if target:
            entries.append((sheet.attrib.get("name", ""), f"xl/{target.lstrip('/')}"))
    return entries


def _xlsx_column_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha()).upper()
    index = 0
    for ch in letters:
        index = (index * 26) + (ord(ch) - 64)
    return max(index - 1, 0)


def _read_xlsx_sheet_rows(archive: zipfile.ZipFile, sheet_path: str, shared_strings: list[str]) -> list[list[str]]:
    sheet = ET.fromstring(archive.read(sheet_path))
    sheet_rows: list[list[str]] = []

    for row in sheet.findall("main:sheetData/main:row", _XLSX_NS):
        values: list[str] = []
        for cell in row.findall("main:c", _XLSX_NS):
            cell_ref = cell.attrib.get("r", "")
            target_idx = _xlsx_column_index(cell_ref) if cell_ref else len(values)
            while len(values) < target_idx:
                values.append("")

            cell_type = cell.attrib.get("t")
            value_node = cell.find("main:v", _XLSX_NS)
            if cell_type == "s" and value_node is not None and value_node.text is not None:
                shared_idx = int(value_node.text)
                values.append(shared_strings[shared_idx] if shared_idx < len(shared_strings) else "")
            elif cell_type == "inlineStr":
                text_node = cell.find("main:is/main:t", _XLSX_NS)
                values.append(text_node.text if text_node is not None else "")
            else:
                values.append(value_node.text if value_node is not None and value_node.text is not None else "")
        sheet_rows.append(values)

    return sheet_rows


def parse_xlsx(content: bytes) -> list[dict[str, str]]:
    """Parse all sheets of an XLSX workbook into normalized dict rows."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        sheet_entries = _sheet_entries(archive)
        if not sheet_entries:
            return []

        shared_strings = _read_xlsx_shared_strings(archive)
        rows: list[dict[str, str]] = []
        for _sheet_name, sheet_path in sheet_entries:
            sheet_rows = _read_xlsx_sheet_rows(archive, sheet_path, shared_strings)
            if not sheet_rows:
                continue

            header_index, headers = _detect_header_row(sheet_rows)
            if not headers:
                continue

            for values in sheet_rows[header_index + 1 :]:
                if not any((value or "").strip() for value in values):
                    continue

                cleaned = _normalize_row(headers, values)
                has_name = any(cleaned.get(alias) for alias in _ALIASES["name"])
                has_domain = any(cleaned.get(alias) for alias in _ALIASES["domain"])
                if has_name or has_domain:
                    rows.append(cleaned)

        return rows


def parse_prospect_xlsx(content: bytes) -> list[dict[str, str]]:
    """
    Parse the fixed Beacon prospect workbook structure.
    - sheet name: Prospecting
    - row 1: grouped section labels
    - row 2: actual headers
    - row 3+: prospect rows
    """
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        sheet_entries = _sheet_entries(archive)
        if not sheet_entries:
            return []

        prospecting_sheet = next(
            ((name, path) for name, path in sheet_entries if name.strip().lower() == "prospecting"),
            None,
        )
        if not prospecting_sheet:
            return []

        _, sheet_path = prospecting_sheet
        shared_strings = _read_xlsx_shared_strings(archive)
        sheet_rows = _read_xlsx_sheet_rows(archive, sheet_path, shared_strings)
        if len(sheet_rows) < 3:
            return []

        headers = [_normalize_header(value) for value in sheet_rows[1]]
        cell_to_url = _read_xlsx_hyperlinks(archive, sheet_path)

        name_col_indices: set[int] = set()
        for col_idx, header in enumerate(headers):
            if header in _ALIASES["contact_name"] or header in _ALIASES["contact_first_name"]:
                name_col_indices.add(col_idx)

        def _col_letter(idx: int) -> str:
            result = ""
            idx += 1
            while idx:
                idx, rem = divmod(idx - 1, 26)
                result = chr(65 + rem) + result
            return result

        rows: list[dict[str, str]] = []
        for data_idx, values in enumerate(sheet_rows[2:]):
            if not any((value or "").strip() for value in values):
                continue

            cleaned = _normalize_row(headers, values)
            excel_row = data_idx + 3

            linkedin_url = ""
            for col_idx in range(max(len(headers), len(values))):
                cell_ref = f"{_col_letter(col_idx)}{excel_row}"
                url = cell_to_url.get(cell_ref, "")
                if not url:
                    continue
                if "linkedin.com" in url:
                    linkedin_url = url
                    break

            if linkedin_url and not cleaned.get("linkedin"):
                cleaned["linkedin"] = linkedin_url

            has_company = any(cleaned.get(alias) for alias in _ALIASES["name"])
            has_contact = any(
                cleaned.get(alias)
                for alias in (_ALIASES["contact_name"] + _ALIASES["contact_email"] + _ALIASES["contact_title"])
            )
            if has_company or has_contact:
                rows.append(cleaned)

        return rows


def parse_tabular_file(filename: str, content: bytes) -> list[dict[str, str]]:
    lower_name = (filename or "").lower()
    if lower_name.endswith(".xlsx"):
        return parse_xlsx(content)
    return parse_csv(content)


def parse_prospect_upload_file(filename: str, content: bytes) -> list[dict[str, str]]:
    lower_name = (filename or "").lower()
    if lower_name.endswith(".xlsx"):
        # Prefer the strict Beacon-template parser (sheet name "Prospecting",
        # grouped section labels in row 1, headers in row 2). If the file
        # isn't that shape — common for Apollo / Sales Nav exports where the
        # sheet has a single header row and any name — fall back to the
        # permissive parser used for Account Sourcing uploads. Either way the
        # caller gets back a list of normalized-header dicts, so downstream
        # row_to_contact_fields / row_to_company_fields handle both.
        rows = parse_prospect_xlsx(content)
        if rows:
            return rows
        return parse_xlsx(content)
    return parse_csv(content)

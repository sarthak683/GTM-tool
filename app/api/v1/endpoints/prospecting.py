"""
Prospecting — bulk CSV import of target companies.

Accepts flexible CSV formats. The parser does fuzzy column matching so it
works with Tracxn exports, Apollo exports, or a simple 1-column "company name"
list. Minimum requirement: at least a company name OR a domain per row.

Supported column names (case-insensitive, any order):
  name / company name / company / organization
  domain / domain name / website / url
  industry / sector / sector (practice area & feed)
  employee_count / total employee count / employees / headcount
  funding_stage / company stage / stage / round
  country / city
  description / overview / about
  total funding (usd) / total funding / annual revenue (usd)

Flow:
  1. Parse + normalise CSV rows (flexible column aliases)
  2. Create Company records (skip exact domain duplicates)
  3. Queue enrichment task for each new company
  4. Store batch status in Redis (TTL 24h)
  5. Return batch_id for polling
"""
import csv
import io
import json
import re
import uuid
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import redis.asyncio as aioredis
from fastapi import APIRouter, File, HTTPException, UploadFile

from app.config import settings
from app.core.dependencies import CurrentUser, DBSession
from app.models.company import Company
from app.repositories.company import CompanyRepository
from app.services.icp_scorer import score_company
from app.tasks.enrichment import enrich_company_task

router = APIRouter(prefix="/prospecting", tags=["prospecting"])

_BATCH_TTL = 86_400

# ── Column alias map ─────────────────────────────────────────────────────────
# Maps our internal field → list of acceptable CSV column names (lowercase)
_ALIASES: dict[str, list[str]] = {
    "name":           ["name", "company name", "company", "organization"],
    "domain":         ["domain", "domain name", "website", "url", "web"],
    "industry":       ["industry", "sector", "sector (pratice area & feed)",
                       "sector (practice area & feed)", "vertical", "category"],
    "employee_count": ["employee_count", "total employee count", "employees",
                       "headcount", "employee count", "no. of employees"],
    "funding_stage":  ["funding_stage", "company stage", "stage",
                       "funding stage", "round", "series"],
    "country":        ["country"],
    "city":           ["city", "location"],
    "description":    ["description", "overview", "about", "summary"],
    "total_funding":  ["total funding (usd)", "total funding",
                       "annual revenue (usd)", "annual revenue", "arr", "revenue"],
}

def _find(row: dict, field: str) -> str:
    """Return the first non-empty value matching any alias for field."""
    for alias in _ALIASES.get(field, [field]):
        val = row.get(alias, "").strip()
        if val:
            return val
    return ""


def _slugify(name: str) -> str:
    """Convert a company name to a plain domain-safe slug."""
    s = name.lower()
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", "", s.strip())
    return s or "unknown"


def _clean_domain(raw: str) -> str:
    """Extract bare domain from a URL or domain string."""
    raw = raw.strip().lower()
    if not raw:
        return ""
    if raw.startswith("http"):
        parsed = urlparse(raw)
        raw = parsed.netloc.removeprefix("www.")
    raw = raw.removeprefix("www.")
    return raw.split("/")[0]  # drop any path


def _parse_number(val: str) -> Optional[float]:
    """Parse numbers like '70,000,000' or '4,000,000'."""
    if not val:
        return None
    cleaned = re.sub(r"[,$\s]", "", val)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_employee_count(val: str) -> Optional[int]:
    """Parse '51+ (Jul 17, 2025)' or '1000' into an integer."""
    if not val:
        return None
    # strip annotations like "(Jul 17, 2025)"
    base = val.split("(")[0]
    # keep only digits
    digits = re.sub(r"[^\d]", "", base)
    try:
        return int(digits) if digits else None
    except ValueError:
        return None


def _parse_csv(content: bytes) -> list[dict]:
    """
    Parse CSV bytes into a list of normalised dicts (all keys lowercased,
    values stripped). Rows without at least a name or domain are skipped.
    """
    text = content.decode("utf-8-sig")  # strip BOM
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        cleaned = {
            k.strip().lower(): (v or "").strip()
            for k, v in row.items()
            if k and k.strip()
        }
        # Need at least one of: name or domain
        has_name   = any(cleaned.get(a) for a in _ALIASES["name"])
        has_domain = any(cleaned.get(a) for a in _ALIASES["domain"])
        if has_name or has_domain:
            rows.append(cleaned)
    return rows


def _get_redis():
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


def _row_to_fields(row: dict) -> dict:
    """
    Map a normalised CSV row to Company field dict.
    Domain is derived from the company name if not provided.
    """
    name = _find(row, "name") or "Unknown Company"

    # Domain
    domain_raw = _find(row, "domain")
    domain = _clean_domain(domain_raw)
    if not domain:
        # Fall back to a slug so the NOT NULL constraint is satisfied.
        # The user can correct it after enrichment.
        domain = f"{_slugify(name)}.unknown"

    fields: dict = {"name": name, "domain": domain}

    # Industry — take last segment after ">" to avoid "Real Estate > Construction Tech"
    industry_raw = _find(row, "industry")
    if industry_raw:
        # e.g. "Enterprise Applications > GRC Software" → "GRC Software"
        last_segment = industry_raw.split(">")[-1].split(",")[0].strip()
        fields["industry"] = last_segment[:120]

    # Employee count
    emp = _parse_employee_count(_find(row, "employee_count"))
    if emp is not None:
        fields["employee_count"] = emp

    # Funding stage
    stage = _find(row, "funding_stage")
    if stage:
        fields["funding_stage"] = stage

    # Total funding as a rough ARR proxy
    funding = _parse_number(_find(row, "total_funding"))
    if funding:
        fields["arr_estimate"] = funding

    # Store extra context in enrichment_sources for later use
    extra: dict = {}
    for f in ("country", "city", "description"):
        val = _find(row, f)
        if val:
            extra[f] = val[:500]  # cap long descriptions
    if extra:
        fields["enrichment_sources"] = {"import": extra}

    return fields


# ── Routes ───────────────────────────────────────────────────────────────────

@router.post("/bulk")
async def bulk_prospect(
    current_user: CurrentUser,
    file: UploadFile = File(...),
    session: DBSession = None,
):
    """
    Upload a CSV of target companies. Works with Tracxn exports, Apollo
    exports, or a simple list with just company names.

    Minimum columns needed: company name  OR  domain (at least one per row).
    """
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a .csv")

    rows = _parse_csv(await file.read())
    if not rows:
        raise HTTPException(
            status_code=400,
            detail=(
                "No valid rows found. The CSV needs at least a company name or domain column. "
                "Accepted names: 'Company Name', 'Domain Name', 'name', 'domain', 'website'."
            ),
        )

    repo = CompanyRepository(session)
    batch_id = str(uuid.uuid4())
    created, skipped, failed = [], [], []

    for row in rows:
        fields = _row_to_fields(row)
        domain = fields["domain"]
        name = fields["name"]

        # Accounts are only created via Account Sourcing (manual add or workbook
        # upload). This legacy endpoint now runs as a dry-run that reports which
        # rows already exist and which would need to be added in Account Sourcing.
        try:
            existing = None
            if not domain.endswith(".unknown"):
                existing = await repo.get_by_domain(domain)
            if not existing:
                existing = await repo.get_by_name(name)

            if existing:
                skipped.append(f"{name} ({domain}) — already exists")
            else:
                skipped.append(f"{name} ({domain}) — not found; add via Account Sourcing")

        except Exception as e:
            failed.append({"name": name, "domain": domain, "error": str(e)})

    batch = {
        "batch_id": batch_id,
        "created_at": datetime.utcnow().isoformat(),
        "total": len(rows),
        "created": len(created),
        "skipped": len(skipped),
        "failed": len(failed),
        "companies": created,
        "skipped_names": skipped,
        "failed_rows": failed,
    }

    redis_client = _get_redis()
    await redis_client.setex(f"batch:{batch_id}", _BATCH_TTL, json.dumps(batch))
    await redis_client.aclose()

    return batch


@router.get("/status/{batch_id}")
async def batch_status(batch_id: str, current_user: CurrentUser):
    """Poll enrichment progress for a bulk import batch."""
    redis_client = _get_redis()
    raw = await redis_client.get(f"batch:{batch_id}")
    await redis_client.aclose()

    if not raw:
        raise HTTPException(status_code=404, detail="Batch not found or expired")

    batch = json.loads(raw)

    from celery.result import AsyncResult
    from app.celery_app import celery_app

    completed = 0
    for company in batch.get("companies", []):
        task_id = company.get("task_id")
        if not task_id:
            continue
        result = AsyncResult(task_id, app=celery_app)
        company["status"] = result.state.lower()
        if result.state == "SUCCESS":
            completed += 1

    batch["completed_enrichments"] = completed
    return batch

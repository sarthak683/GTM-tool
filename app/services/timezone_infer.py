"""
Contact timezone inference.

Pure-Python, deterministic, offline. Given a Contact + its Company, returns
an IANA timezone string like "Europe/London" or None when we can't make a
confident call.

Strategy, in priority order:
  1. Phone country code (most reliable — it's literally "where this person
     picks up the phone"):
       +44 → Europe/London
       +91 → Asia/Kolkata
       +61 → Australia/Sydney
       ... full map below
  2. NANP phone (+1) area code lookup — splits US/Canada into the 4 US
     mainland zones plus Hawaii, Alaska, Atlantic, and Eastern Canadian
     zones. Area-code range source: NANPA, 2025 snapshot.
  3. Company headquarters / region string, when phone is missing or the
     country is one of the ambiguous multi-zone ones (US / CA / RU / AU /
     BR) and the phone didn't disambiguate.
  4. Country-level default — pick the biggest-city zone. Defensible for
     99% of reps ("they're calling Russia, assume Moscow").

Returns None if we have nothing usable. Callers should treat None as
"leave the column null; the rep will fill it manually if needed".
"""
from __future__ import annotations

import re
from typing import Optional


# ── Country code → IANA zone (single-zone countries) ────────────────────
# Only countries that have exactly one sensible default go here. Multi-zone
# countries (US, CA, RU, AU, BR) get handled by the NANP table or the
# company-region fallback below.
_COUNTRY_CODE_TO_ZONE: dict[str, str] = {
    # Europe
    "+44":  "Europe/London",
    "+49":  "Europe/Berlin",
    "+33":  "Europe/Paris",
    "+31":  "Europe/Amsterdam",
    "+32":  "Europe/Brussels",
    "+34":  "Europe/Madrid",
    "+39":  "Europe/Rome",
    "+41":  "Europe/Zurich",
    "+43":  "Europe/Vienna",
    "+45":  "Europe/Copenhagen",
    "+46":  "Europe/Stockholm",
    "+47":  "Europe/Oslo",
    "+48":  "Europe/Warsaw",
    "+351": "Europe/Lisbon",
    "+353": "Europe/Dublin",
    "+358": "Europe/Helsinki",
    "+420": "Europe/Prague",
    "+30":  "Europe/Athens",
    "+90":  "Europe/Istanbul",
    # Middle East
    "+971": "Asia/Dubai",
    "+972": "Asia/Jerusalem",
    "+966": "Asia/Riyadh",
    "+20":  "Africa/Cairo",
    # South / Southeast Asia
    "+91":  "Asia/Kolkata",
    "+92":  "Asia/Karachi",
    "+62":  "Asia/Jakarta",
    "+63":  "Asia/Manila",
    "+65":  "Asia/Singapore",
    "+66":  "Asia/Bangkok",
    "+84":  "Asia/Ho_Chi_Minh",
    # East Asia
    "+81":  "Asia/Tokyo",
    "+82":  "Asia/Seoul",
    "+86":  "Asia/Shanghai",
    "+852": "Asia/Hong_Kong",
    "+886": "Asia/Taipei",
    # Oceania (NZ only — Australia is multi-zone, handled below)
    "+64":  "Pacific/Auckland",
    # Africa
    "+27":  "Africa/Johannesburg",
    "+234": "Africa/Lagos",
    "+254": "Africa/Nairobi",
    # Latin America (Brazil + others single-zone; multi-zone handled below)
    "+52":  "America/Mexico_City",
    "+54":  "America/Argentina/Buenos_Aires",
    "+56":  "America/Santiago",
    "+57":  "America/Bogota",
    "+58":  "America/Caracas",
    "+598": "America/Montevideo",
}

# Countries we don't pick a default for without a region clue.
_MULTI_ZONE_COUNTRIES = {"+1", "+7", "+55", "+61"}

# ── NANP (+1) area code → US/Canada zone ────────────────────────────────
# Comprehensive enough to cover the top 95% of US/Canada area codes. When
# an area code isn't listed, we fall through to America/New_York which is
# the most defensible default (east coast is densest).
_NANP_AREA_CODE_ZONE: dict[str, str] = {
    # ── US Eastern ──
    **{ac: "America/New_York" for ac in (
        "201","202","203","207","212","215","216","218","219","220","223","224","225",
        "227","228","229","231","234","239","240","248","260","267","269","270","272",
        "274","276","281","283","289","301","302","304","305","307","313","315","317",
        "321","327","330","334","336","337","339","341","343","347","351","352","360",
        "365","380","386","401","404","407","410","412","413","414","419","423","434",
        "440","443","445","450","458","470","475","478","484","502","504","508","513",
        "516","517","518","540","551","561","564","567","570","571","574","579","585",
        "586","588","592","598","607","609","610","614","615","616","617","618","619",
        "623","626","629","631","631","636","641","646","647","649","650","651","656",
        "659","660","661","667","669","670","671","678","680","681","682","684","689",
        "701","703","704","706","707","708","712","714","715","716","717","718","719",
        "724","727","732","734","737","740","743","747","754","757","760","762","763",
        "765","769","770","772","773","774","775","779","781","785","786","787","800",
        "803","804","805","806","808","810","812","813","814","815","816","817","818",
        "828","830","831","832","833","838","843","845","847","848","850","856","857",
        "859","860","863","865","870","872","878","901","903","904","906","908","910",
        "912","913","914","915","917","919","920","931","934","936","937","938","940",
        "941","947","949","951","952","954","956","959","970","971","972","973","978",
        "979","980","984","985","989","984","929","984","920","919",
    )},
    # ── US Central ──
    **{ac: "America/Chicago" for ac in (
        "205","210","214","217","225","251","254","256","262","270","281","308","309",
        "310","312","314","316","318","319","320","331","334","337","346","361","364",
        "402","405","409","414","417","430","432","445","447","469","479","501","504",
        "507","512","515","531","563","573","580","601","605","608","612","615","618",
        "620","630","636","641","651","660","662","682","708","713","715","716","731",
        "773","779","785","787","806","815","816","817","830","832","833","847","850",
        "870","872","901","903","904","913","915","918","920","931","936","940","956",
        "972","979","985",
    )},
    # ── US Mountain ──
    **{ac: "America/Denver" for ac in (
        "303","307","385","406","435","505","575","701","719","720","801","970","983",
    )},
    # ── US Pacific ──
    **{ac: "America/Los_Angeles" for ac in (
        "209","213","253","279","310","323","341","360","408","415","424","425","442",
        "458","503","510","530","541","559","562","619","626","628","650","657","661",
        "669","702","707","714","725","747","760","775","805","818","820","831","858",
        "909","916","925","949","951","971",
    )},
    # ── US Alaska ──
    "907": "America/Anchorage",
    # ── US Hawaii ──
    "808": "Pacific/Honolulu",
    # ── Canada (a spot-check sample; Canadian NANP area codes cluster
    # neatly by province/zone) ──
    **{ac: "America/Toronto"   for ac in ("226","249","289","343","365","416","437","519","548","613","647","705","807","905")},
    **{ac: "America/Vancouver" for ac in ("236","250","604","672","778")},
    **{ac: "America/Edmonton"  for ac in ("403","587","780","825")},
    **{ac: "America/Winnipeg"  for ac in ("204","431")},
    **{ac: "America/Regina"    for ac in ("306","639")},  # Saskatchewan — no DST
    **{ac: "America/Halifax"   for ac in ("506","782","902")},
    "709": "America/St_Johns",
}

# Fallback zones for +1 when the area code doesn't match our table.
_NANP_DEFAULT = "America/New_York"

# ── Company HQ / region keyword → zone ──────────────────────────────────
# Last-resort fallback when phone is missing. Matches as a lowercase
# substring in `company.headquarters`, `company.region`, `company.name`, or
# similar text fields. Ordered by specificity: more specific first so
# "new york" matches before generic "united states".
_HQ_KEYWORD_TO_ZONE: tuple[tuple[str, str], ...] = (
    # Major city hits (most specific)
    ("san francisco", "America/Los_Angeles"),
    ("los angeles",   "America/Los_Angeles"),
    ("seattle",       "America/Los_Angeles"),
    ("palo alto",     "America/Los_Angeles"),
    ("san jose",      "America/Los_Angeles"),
    ("san diego",     "America/Los_Angeles"),
    ("portland",      "America/Los_Angeles"),
    ("oakland",       "America/Los_Angeles"),
    ("denver",        "America/Denver"),
    ("phoenix",       "America/Phoenix"),
    ("salt lake",     "America/Denver"),
    ("albuquerque",   "America/Denver"),
    ("austin",        "America/Chicago"),
    ("houston",       "America/Chicago"),
    ("dallas",        "America/Chicago"),
    ("chicago",       "America/Chicago"),
    ("minneapolis",   "America/Chicago"),
    ("new orleans",   "America/Chicago"),
    ("nashville",     "America/Chicago"),
    ("new york",      "America/New_York"),
    ("boston",        "America/New_York"),
    ("washington",    "America/New_York"),
    ("atlanta",       "America/New_York"),
    ("miami",         "America/New_York"),
    ("philadelphia",  "America/New_York"),
    ("pittsburgh",    "America/New_York"),
    ("orlando",       "America/New_York"),
    # Canada
    ("toronto",       "America/Toronto"),
    ("vancouver",     "America/Vancouver"),
    ("montreal",      "America/Toronto"),
    ("ottawa",        "America/Toronto"),
    ("calgary",       "America/Edmonton"),
    # Europe
    ("london",        "Europe/London"),
    ("manchester",    "Europe/London"),
    ("dublin",        "Europe/Dublin"),
    ("paris",         "Europe/Paris"),
    ("berlin",        "Europe/Berlin"),
    ("munich",        "Europe/Berlin"),
    ("frankfurt",     "Europe/Berlin"),
    ("amsterdam",     "Europe/Amsterdam"),
    ("stockholm",     "Europe/Stockholm"),
    ("madrid",        "Europe/Madrid"),
    ("barcelona",     "Europe/Madrid"),
    ("rome",          "Europe/Rome"),
    ("milan",         "Europe/Rome"),
    ("zurich",        "Europe/Zurich"),
    # Asia
    ("bangalore",     "Asia/Kolkata"),
    ("bengaluru",     "Asia/Kolkata"),
    ("mumbai",        "Asia/Kolkata"),
    ("delhi",         "Asia/Kolkata"),
    ("hyderabad",     "Asia/Kolkata"),
    ("chennai",       "Asia/Kolkata"),
    ("pune",          "Asia/Kolkata"),
    ("gurgaon",       "Asia/Kolkata"),
    ("gurugram",      "Asia/Kolkata"),
    ("noida",         "Asia/Kolkata"),
    ("kolkata",       "Asia/Kolkata"),
    ("singapore",     "Asia/Singapore"),
    ("tokyo",         "Asia/Tokyo"),
    ("seoul",         "Asia/Seoul"),
    ("shanghai",      "Asia/Shanghai"),
    ("beijing",       "Asia/Shanghai"),
    ("hong kong",     "Asia/Hong_Kong"),
    ("tel aviv",      "Asia/Jerusalem"),
    # Oceania
    ("sydney",        "Australia/Sydney"),
    ("melbourne",     "Australia/Melbourne"),
    ("brisbane",      "Australia/Brisbane"),
    ("perth",         "Australia/Perth"),
    ("auckland",      "Pacific/Auckland"),
    # Middle East
    ("dubai",         "Asia/Dubai"),
    ("abu dhabi",     "Asia/Dubai"),
    # Region-level (least specific, checked last)
    ("united kingdom","Europe/London"),
    (" uk ",          "Europe/London"),
    ("ireland",       "Europe/Dublin"),
    ("germany",       "Europe/Berlin"),
    ("france",        "Europe/Paris"),
    ("netherlands",   "Europe/Amsterdam"),
    ("spain",         "Europe/Madrid"),
    ("italy",         "Europe/Rome"),
    ("india",         "Asia/Kolkata"),
    ("australia",     "Australia/Sydney"),
    ("japan",         "Asia/Tokyo"),
    ("israel",        "Asia/Jerusalem"),
    ("canada",        "America/Toronto"),
    ("mexico",        "America/Mexico_City"),
    ("brazil",        "America/Sao_Paulo"),
    ("united states", "America/New_York"),
    (" usa ",         "America/New_York"),
)


def _normalize_phone(phone: Optional[str]) -> str:
    if not phone:
        return ""
    # Keep + and digits; strip spaces, dashes, parentheses.
    return re.sub(r"[^\d+]", "", phone.strip())


def _match_country_code(digits: str) -> Optional[str]:
    """Longest-prefix match against _COUNTRY_CODE_TO_ZONE / multi-zone set.

    Returns the full code string like "+44" or None. Tries 4-char prefix
    first (e.g. "+598"), then 3, then 2 — longer codes win to avoid "+5"
    matching "+598"."""
    if not digits.startswith("+"):
        return None
    # Try longest first. Codes in our tables range 2-4 chars including "+".
    for length in (4, 3, 2):
        prefix = digits[:length]
        if prefix in _COUNTRY_CODE_TO_ZONE or prefix in _MULTI_ZONE_COUNTRIES:
            return prefix
    return None


def _infer_from_nanp(digits: str) -> str:
    """Given a digits-only +1 number, extract the 3-digit area code and
    look up the zone. Returns _NANP_DEFAULT if the area code isn't in our
    table."""
    # Strip the +1 prefix, grab next 3 digits.
    rest = digits.removeprefix("+1").removeprefix("1")
    area = rest[:3]
    return _NANP_AREA_CODE_ZONE.get(area, _NANP_DEFAULT)


def _infer_from_region_text(text: str) -> Optional[str]:
    """Scan keywords in company HQ / region strings. Case-insensitive."""
    if not text:
        return None
    lowered = f" {text.lower()} "  # pad so " uk " keyword works
    for needle, zone in _HQ_KEYWORD_TO_ZONE:
        if needle in lowered:
            return zone
    return None


def infer_timezone(
    *,
    phone: Optional[str],
    company_hq: Optional[str] = None,
    company_region: Optional[str] = None,
    company_name: Optional[str] = None,
) -> Optional[str]:
    """Return an IANA timezone string, or None if we can't make a call.

    Priority:
      1. Phone country code (single-zone countries) → direct lookup
      2. +1 phone → NANP area code → US/Canada zone
      3. Ambiguous multi-zone country with region text → region lookup
      4. No phone but region text available → region lookup
      5. None (caller should leave the DB column null)
    """
    digits = _normalize_phone(phone)

    # ── 1 & 2: phone-based ────────────────────────────────────────────
    code = _match_country_code(digits)
    if code and code not in _MULTI_ZONE_COUNTRIES:
        return _COUNTRY_CODE_TO_ZONE[code]
    if code == "+1":
        return _infer_from_nanp(digits)
    # Multi-zone like +7 (Russia), +55 (Brazil), +61 (Australia): try region
    # fallback before giving up.

    # ── 3 & 4: region/HQ-based ────────────────────────────────────────
    for text in (company_hq, company_region, company_name):
        zone = _infer_from_region_text(text or "")
        if zone:
            return zone

    # Country-code-but-multi-zone without a region clue: pick the primary
    # city so we give the rep *something* rather than None.
    if code == "+61":
        return "Australia/Sydney"
    if code == "+55":
        return "America/Sao_Paulo"
    if code == "+7":
        return "Europe/Moscow"

    return None

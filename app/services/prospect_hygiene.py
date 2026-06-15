from __future__ import annotations

import re
from typing import Optional


ROLE_LOCAL_PART_PATTERNS = (
    "noreply",
    "no-reply",
    "no_reply",
    "donotreply",
    "do-not-reply",
    "do_not_reply",
    "mailer-daemon",
    "postmaster",
    "notifications",
    "notification",
    "calendar",
    "invite",
    "invites",
    "support",
    "help",
    "success",
    "team",
    "updates",
    "alerts",
    "billing",
    "admin",
    "info",
    "notetaker",
    "personalizedinsights",
)

GENERIC_NAME_TOKENS = {
    "contact",
    "team",
    "support",
    "notifications",
    "notification",
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "admin",
    "info",
    "notetaker",
    "sign",
    "insights",
}

# Bulk-email provider, tracking, and unsubscribe-wrapper domains.
# Anything whose email ends in one of these is never a real prospect —
# it's an infrastructure endpoint (link-tracking wrapper, unsubscribe
# redirect, transactional bounce handler, etc.). Scrapers regularly
# mis-classify these as person records.
# Match is done against the *registerable* part of the domain (e.g.
# "unsubscribe2.customer.io" hits the "customer.io" entry).
BLOCKED_EMAIL_DOMAINS = (
    # Customer.io + its unsubscribe redirector
    "customer.io",
    # Mailchimp family
    "list-manage.com",
    "mailchimp.com",
    "mcsv.net",
    "rs6.net",
    # SendGrid / Twilio
    "sendgrid.net",
    "sendgrid.com",
    "e.sendgrid.net",
    # Mailgun
    "mailgun.org",
    "mailgun.net",
    # Amazon SES (open-tracking domains)
    "amazonses.com",
    # Postmark
    "postmarkapp.com",
    "pmarker.email",
    # Mandrill
    "mandrillapp.com",
    # Marketing automation
    "marketo-mail.com",
    "mkto-mail.com",
    "mktomail.com",
    "eloqua.com",
    "en25.com",
    "exacttarget.com",
    "s10.exacttarget.com",
    "email.exacttarget.com",
    "pardot.com",
    "hsforms.com",
    "hsforms.net",
    "hubspotemail.net",
    "hs-sites.com",
    # Constant Contact / iContact
    "constantcontact.com",
    "ccsend.com",
    "icpbounce.com",
    "icontact.com",
    # Salesloft / Outreach tracking
    "salesloft.com",
    "mktdns.com",
    # Generic tracking wrappers some vendors use
    "link.mail.beehiiv.com",
    "click.revue.email",
    # Substack — authors are not the same as the people who write newsletters
    # behind them; scrapers grab the author bio as a person record. Individual
    # prospects won't have @substack.com emails.
    "substack.com",
    # Meeting assistants / e-signature / insight-notification senders that
    # appear in Gmail threads but are not human buyer prospects.
    "fyxer.com",
    "conga-sign.com",
    "clients.gartner.com",
)

# Hostname LABELS (not free-text substrings) that indicate infrastructure.
# A label is a dot-separated segment. We flag the domain when any label
# equals one of these — so "unsubscribe2.customer.io" and
# "tracking.example.com" are caught, but legitimate brand names that happen
# to contain these characters (e.g. "e2open.com" — which literally contains
# the text "open.") are NOT caught because "open" is not its own label.
# This is the anchored version of what used to be a bare substring match,
# which produced false positives on "e2open.com", "opentext.com", etc.
BLOCKED_DOMAIN_LABELS = (
    "unsubscribe",
    "unsubscribes",
    "bounce",
    "bounces",
    "tracking",
    "track",
    "click",
    "clicks",
    "open-tracking",
    "mktdns",
)

# Special-case: labels that start with one of these PREFIXES are flagged.
# Catches "unsubscribe2", "unsubscribe3", "tracking1" style numbered
# infrastructure subdomains.
BLOCKED_DOMAIN_LABEL_PREFIXES = (
    "unsubscribe",
    "tracking",
)

# Maximum number of alphanumeric characters allowed in an email local-part
# before we treat it as machine-generated gibberish (hashes, uuids, base64
# tokens used by tracking wrappers). Real people's local-parts almost never
# exceed ~30 characters — the junk record we saw had 62.
MAX_LOCAL_PART_ALNUM = 32

# Same idea for last names: human names don't run 40+ letters with no spaces.
MAX_LAST_NAME_ALNUM = 28

# Strings that only ever appear in our own enrichment UI warnings, never in
# a real person's email. If we see them in `email`, we wrote a display string
# into the wrong column somewhere — definitely not a valid prospect email.
INVALID_EMAIL_CONTENT_MARKERS = (
    "not available",
    "inferred:",
    "⚠",
)


def _normalize_name_token(value: str | None) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (value or "").strip().lower())
    return " ".join(cleaned.split())


def normalize_email(email: str | None) -> str:
    value = (email or "").strip().lower()
    if not value or "@" not in value:
        return ""
    return value


def _email_domain(email: str) -> str:
    _, _, domain = email.partition("@")
    return domain.strip().lower()


def is_blocked_email_domain(email: str | None) -> bool:
    """True when the email's domain matches a bulk-email / tracking / unsubscribe
    infrastructure pattern. Scraped contacts with such domains are never
    real prospects."""
    normalized = normalize_email(email)
    if not normalized:
        return False
    domain = _email_domain(normalized)
    if not domain:
        return False

    # Exact-match or subdomain-match against the blocklist. A suffix match
    # of ".customer.io" catches "unsubscribe2.customer.io" as well as
    # "mail.customer.io".
    for blocked in BLOCKED_EMAIL_DOMAINS:
        if domain == blocked or domain.endswith("." + blocked):
            return True

    # Label-based check: any dot-separated segment matches a blocked label
    # exactly, or starts with one of the prefix patterns.
    labels = domain.split(".")
    for label in labels:
        if label in BLOCKED_DOMAIN_LABELS:
            return True
        for prefix in BLOCKED_DOMAIN_LABEL_PREFIXES:
            if label.startswith(prefix) and len(label) > len(prefix):
                # "unsubscribe2" starts with "unsubscribe" and has extra
                # chars — infrastructure. Plain "unsubscribe" itself is
                # already in BLOCKED_DOMAIN_LABELS, so no duplication.
                return True

    return False


def is_gibberish_local_part(email: str | None) -> bool:
    """True when the part before @ is clearly machine-generated (long
    uninterrupted string of alphanumerics with no period/underscore/hyphen
    structure), e.g. tracking tokens, UUIDs, base64 blobs."""
    normalized = normalize_email(email)
    if not normalized:
        return False
    local, _, _ = normalized.partition("@")
    if not local:
        return False
    alnum = re.sub(r"[^a-z0-9]", "", local)
    # A real local-part can be long (e.g., double-barrelled name + dept),
    # but only if it has structure. We test *alphanumeric-only length* after
    # stripping separators. 32+ unbroken alnum chars is the tracking-token
    # pattern.
    if len(alnum) >= MAX_LOCAL_PART_ALNUM:
        # Also require there's little separator structure — a very long
        # name like "jonathan.van-der-berghe.engineering" has 32+ alnum
        # chars but with structure, so we let it through.
        sep_count = sum(local.count(ch) for ch in ".-_+")
        if sep_count < 2:
            return True
    return False


def has_invalid_email_markers(email: str | None) -> bool:
    """True when the email field contains our own enrichment warning strings
    (e.g. '⚠️ not available — inferred: ...'). This indicates a bug in an
    upstream enrichment path that wrote a display string into the email
    column — never a valid prospect."""
    if not email:
        return False
    lowered = email.lower()
    return any(marker in lowered for marker in INVALID_EMAIL_CONTENT_MARKERS)


def is_gibberish_last_name(last_name: str | None) -> bool:
    """True when the last name is an uninterrupted alphanumeric blob of
    unreasonable length (e.g. a hash / token / base64 string someone
    mis-parsed into the name field)."""
    if not last_name:
        return False
    alnum = re.sub(r"[^A-Za-z0-9]", "", last_name)
    if len(alnum) >= MAX_LAST_NAME_ALNUM:
        # No spaces AND no hyphens in the original means it's one word —
        # human last names aren't that long in one token.
        if " " not in last_name.strip() and "-" not in last_name:
            return True
    return False


def is_system_email_address(email: str | None) -> bool:
    normalized = normalize_email(email)
    if not normalized:
        return False

    local, _, domain = normalized.partition("@")
    if not local or not domain:
        return False

    if domain == "beacon.li":
        return True

    # Infrastructure domain and tracking-wrapper checks.
    if is_blocked_email_domain(normalized):
        return True

    # Machine-generated local-parts (tracking tokens, etc.)
    if is_gibberish_local_part(normalized):
        return True

    # Warning-string-in-email-column bug guard.
    if has_invalid_email_markers(normalized):
        return True

    compact_local = re.sub(r"[^a-z0-9]+", "", local)
    if compact_local.startswith("zippy"):
        return True

    local_tokens = {token for token in re.split(r"[^a-z0-9]+", local) if token}
    if any(pattern in local for pattern in ROLE_LOCAL_PART_PATTERNS):
        return True
    if local_tokens & {token.replace("-", "").replace("_", "") for token in GENERIC_NAME_TOKENS}:
        return True

    if "+" in local and any(pattern in compact_local for pattern in ("noreply", "notification", "calendar", "invite")):
        return True

    return False


def is_placeholder_contact_name(
    first_name: str | None,
    last_name: str | None,
) -> bool:
    first = _normalize_name_token(first_name)
    last = _normalize_name_token(last_name)
    if not first and not last:
        return True

    if last in GENERIC_NAME_TOKENS:
        return True

    # Gibberish-name detector: a 28+ char uninterrupted alphanumeric token
    # in last_name is never a real person. Catches the "Mrtvitbsm5Rucqkqm…"
    # kind of garbage we've seen land via scrapers.
    if is_gibberish_last_name(last_name):
        return True

    full_name = " ".join(part for part in [first, last] if part)
    if any(token in full_name for token in GENERIC_NAME_TOKENS):
        if len(full_name.split()) <= 2:
            return True

    return False


def is_valid_prospect_candidate(
    *,
    first_name: str | None,
    last_name: str | None,
    email: str | None,
    title: str | None = None,
    linkedin_url: str | None = None,
) -> bool:
    normalized_email = normalize_email(email)
    normalized_title = (title or "").strip()
    normalized_linkedin = (linkedin_url or "").strip()

    # Email-column-contains-warning-string bug: hard-reject regardless of
    # other fields, because it proves upstream data is corrupt.
    if email and has_invalid_email_markers(email):
        return False

    if normalized_email and is_system_email_address(normalized_email):
        return False

    if is_placeholder_contact_name(first_name, last_name):
        # If a user explicitly added strong person-like data, let it through.
        # BUT: gibberish last_name is a hard-no regardless of title/linkedin,
        # because the data is clearly corrupt, not legitimately sparse.
        if is_gibberish_last_name(last_name):
            return False
        if not normalized_title and not normalized_linkedin:
            return False

    if not any([(first_name or "").strip(), (last_name or "").strip(), normalized_email, normalized_title, normalized_linkedin]):
        return False

    return True


def invalid_prospect_reason(
    *,
    first_name: str | None,
    last_name: str | None,
    email: str | None,
    title: str | None = None,
    linkedin_url: str | None = None,
) -> Optional[str]:
    if email and has_invalid_email_markers(email):
        return "Email field contains an enrichment warning string, not a real address."
    normalized_email = normalize_email(email)
    if normalized_email and is_blocked_email_domain(normalized_email):
        return "This looks like a bulk-email or tracking wrapper domain, not a real mailbox."
    if normalized_email and is_gibberish_local_part(normalized_email):
        return "The email address looks machine-generated (tracking token), not a real person."
    if normalized_email and is_system_email_address(normalized_email):
        return "This looks like a system or role-based mailbox, not a real prospect."
    if is_gibberish_last_name(last_name):
        return "The last name looks like a machine-generated token, not a real person."
    if is_placeholder_contact_name(first_name, last_name) and not (title or "").strip() and not (linkedin_url or "").strip():
        return "This contact looks like a placeholder rather than a real person."
    if not is_valid_prospect_candidate(
        first_name=first_name,
        last_name=last_name,
        email=email,
        title=title,
        linkedin_url=linkedin_url,
    ):
        return "This row does not look like a valid prospect."
    return None

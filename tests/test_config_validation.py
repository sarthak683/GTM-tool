"""Unit tests for ``app.config.Settings``.

Pure-config tests: no DB, no app, no network. Each ``Settings`` instance is
built with ``_env_file=None`` so a developer's local ``.env`` cannot leak in and
make these non-deterministic — only explicit kwargs and the class defaults
apply.
"""
from __future__ import annotations

from app.config import Settings


def _settings(**overrides) -> Settings:
    """Build a Settings ignoring any on-disk .env (deterministic in CI/local)."""
    return Settings(_env_file=None, **overrides)


# ── validate_runtime_secrets ─────────────────────────────────────────────────

def test_runtime_secrets_ok_in_development():
    """Development is exempt: dev placeholders are fine, so no problems."""
    s = _settings(ENVIRONMENT="development")
    assert s.validate_runtime_secrets() == []


def test_runtime_secrets_block_production_on_dev_defaults():
    """Prod with the insecure dev-default secrets must report blocking problems
    that name SECRET_KEY and JWT_SECRET."""
    s = _settings(
        ENVIRONMENT="production",
        SECRET_KEY="dev_secret_key",            # the insecure dev default
        JWT_SECRET="jwt_dev_secret_change_me",  # the insecure dev default
    )
    problems = s.validate_runtime_secrets()

    assert problems, "production on dev-default secrets must NOT be empty"
    joined = " ".join(problems)
    assert "SECRET_KEY" in joined, f"expected SECRET_KEY mention, got: {problems!r}"
    assert "JWT_SECRET" in joined, f"expected JWT_SECRET mention, got: {problems!r}"


def test_runtime_secrets_pass_production_with_strong_secrets():
    """A prod env with strong, non-default secrets clears the SECRET/JWT gates.

    (The leaked-VAPID check is its own concern; the default VAPID key is empty,
    not the leaked value, so it does not trip here.)"""
    s = _settings(
        ENVIRONMENT="production",
        SECRET_KEY="a-sufficiently-strong-random-secret-value-1234567890",
        JWT_SECRET="another-strong-random-jwt-secret-at-least-32-chars-long",
        # Override explicitly: pydantic-settings still reads OS env vars even with
        # _env_file=None, and the container inherits the (leaked) VAPID key from
        # .env — which would otherwise trip the leaked-key check.
        VAPID_PRIVATE_KEY="",
    )
    problems = s.validate_runtime_secrets()
    assert problems == [], f"expected no problems, got: {problems!r}"


def test_runtime_secrets_staging_is_treated_as_production():
    """`is_production` covers staging too, so staging is also gated."""
    s = _settings(
        ENVIRONMENT="staging",
        SECRET_KEY="dev_secret_key",
        JWT_SECRET="jwt_dev_secret_change_me",
    )
    assert s.validate_runtime_secrets(), "staging must be validated like prod"


# ── allowed_email_domains parsing ────────────────────────────────────────────

def test_allowed_email_domains_parses_csv_lowercased_trimmed():
    s = _settings(ALLOWED_EMAIL_DOMAINS="Beacon.li, Example.COM ,  foo.dev")
    assert s.allowed_email_domains == ["beacon.li", "example.com", "foo.dev"]


def test_allowed_email_domains_empty_is_empty_list():
    s = _settings(ALLOWED_EMAIL_DOMAINS="")
    assert s.allowed_email_domains == []


def test_allowed_email_domains_default_is_empty_list():
    # Default for ALLOWED_EMAIL_DOMAINS is "" => allow-any => empty list.
    assert _settings().allowed_email_domains == []


# ── admin_bootstrap_emails parsing ───────────────────────────────────────────

def test_admin_bootstrap_emails_parses_csv_to_lowercased_set():
    s = _settings(ADMIN_BOOTSTRAP_EMAILS="Admin@Beacon.li,  ceo@beacon.li ")
    assert s.admin_bootstrap_emails == {"admin@beacon.li", "ceo@beacon.li"}


def test_admin_bootstrap_emails_empty_is_empty_set():
    s = _settings(ADMIN_BOOTSTRAP_EMAILS="")
    assert s.admin_bootstrap_emails == set()


def test_admin_bootstrap_emails_default_is_empty_set():
    assert _settings().admin_bootstrap_emails == set()

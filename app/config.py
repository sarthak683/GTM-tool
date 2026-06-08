from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env relative to this file so it works regardless of CWD
_ENV_FILE = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://beacon:beacon_dev@localhost:5432/beacon_crm"
    SYNC_DATABASE_URL: str = "postgresql://beacon:beacon_dev@localhost:5432/beacon_crm"

    # Legacy background queue setting (not required by the active deployment path)
    REDIS_URL: str = "redis://localhost:6379/0"

    # App
    SECRET_KEY: str = "dev_secret_key"  # dev placeholder — MUST be overridden in prod/staging
    ENVIRONMENT: str = "development"

    # ── Security / auth policy ────────────────────────────────────────────────
    # Comma-separated Google Workspace domains allowed to sign in. Empty = allow
    # any Google account (current behaviour). Set e.g. "beacon.li" to lock down.
    ALLOWED_EMAIL_DOMAINS: str = ""
    # Comma-separated emails granted admin on first sign-in. Empty keeps the
    # legacy "first user to sign in becomes admin" bootstrap.
    ADMIN_BOOTSTRAP_EMAILS: str = ""
    # When true, inbound webhooks without a verifiable signature/secret are
    # rejected (fail-closed). Defaults off so local/dev keeps working; enable in
    # prod via env.
    REQUIRE_WEBHOOK_SECRETS: bool = False
    # Per-source webhook shared secrets. INSTANTLY_/AIRCALL_ equivalents already
    # exist further down; these cover the previously-unverified sources.
    FIREFLIES_WEBHOOK_SECRET: str = ""
    TLDV_WEBHOOK_SECRET: str = ""
    RB2B_WEBHOOK_SECRET: str = ""

    # ── Observability ─────────────────────────────────────────────────────────
    SENTRY_DSN: str = ""             # empty = error tracking disabled
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0
    SENTRY_ENVIRONMENT: str = ""     # falls back to ENVIRONMENT when empty
    ENABLE_METRICS: bool = True      # expose Prometheus /metrics
    LOG_JSON: bool = False           # structured JSON logs (enable in prod)

    # Google OAuth
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/google/callback"
    FRONTEND_URL: str = "http://localhost:5173"
    GMAIL_CLIENT_ID: str = ""
    GMAIL_CLIENT_SECRET: str = ""
    GMAIL_OAUTH_REDIRECT_URI: str = "http://localhost:8000/api/v1/settings/email-sync/google/callback"

    # JWT
    JWT_SECRET: str = "jwt_dev_secret_change_me"
    JWT_EXPIRE_MINUTES: int = 1440  # 24 hours
    CORS_ORIGINS: str = ",".join([
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:8080",
    ])

    # External API keys (empty string = mock mode)
    APOLLO_API_KEY: str = ""
    HUNTER_API_KEY: str = ""
    SERPER_API_KEY: str = ""
    BUILTWITH_API_KEY: str = ""
    INSTANTLY_API_KEY: str = ""
    INSTANTLY_WEBHOOK_URL: str = ""  # e.g. https://yourdomain.com/api/v1/webhooks/instantly
    INSTANTLY_WEBHOOK_SECRET: str = ""  # Shared secret sent as X-Beacon-Webhook-Secret header
    FIREFLIES_API_KEY: str = ""
    NEWS_API_KEY: str = ""  # No longer required — news client uses Google News RSS

    # Anthropic Claude
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_API_KEY: str = ""  # alias — some .env files use this name
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"

    # CRM AI routing by complexity
    CLAUDE_MODEL_SIMPLE: str = "claude-haiku-4-5-20251001"
    CLAUDE_MODEL_STANDARD: str = "claude-sonnet-4-6"
    CLAUDE_MODEL_COMPLEX: str = "claude-opus-4-6"

    # Master switch for ALL system-generated tasks. Default is OFF: the product
    # is "manual tasks only" — the task list contains only human-created
    # (task_type="manual") tasks. Every automated generator is short-circuited
    # at the single chokepoint (refresh_system_tasks_for_entity) — AI emitter,
    # deterministic critical rules, stage playbook, contact/company hygiene, and
    # personal-email-sync. The generator code stays in-tree and dormant, so
    # auto-tasks can be re-enabled by setting this back to true (env override).
    ENABLE_SYSTEM_TASKS: bool = False

    # AI task emitter — the 5 LLM-gated codes (T-STAGE, T-AMOUNT, T-CLOSE,
    # T-MEDPICC, T-CONTACT). T-CRITICAL always runs (deterministic rules).
    # Finer sub-gate: only relevant when ENABLE_SYSTEM_TASKS is true.
    ENABLE_AI_TASK_EMITTER: bool = True

    # Demo generation tuning
    DEMO_MODEL: str = "claude-sonnet-4-6"  # Sonnet 4.6 — best availability + quality for code gen (Sonnet 4 retired 2026-06-15)
    DEMO_MAX_TOKENS: int = 30000             # Extended thinking unlocks 64K; 30K is plenty for 15-25K token demos
    DEMO_THINKING_BUDGET: int = 10000        # Tokens for planning HTML structure before writing
    DEMO_TIMEOUT_SECONDS: int = 300          # Per-attempt timeout (streaming)

    @property
    def claude_api_key(self) -> str:
        """Return whichever Claude key is set (ANTHROPIC_API_KEY or CLAUDE_API_KEY)."""
        # Some environments use the old variable name and others use the newer one,
        # so callers can depend on a single property instead of branching.
        return self.ANTHROPIC_API_KEY or self.CLAUDE_API_KEY

    @property
    def gmail_client_id(self) -> str:
        return self.GMAIL_CLIENT_ID or self.GOOGLE_CLIENT_ID

    @property
    def gmail_client_secret(self) -> str:
        return self.GMAIL_CLIENT_SECRET or self.GOOGLE_CLIENT_SECRET

    @property
    def cors_origins(self) -> List[str]:
        # `.env` stores this as a comma-separated string, but FastAPI middleware
        # expects an actual list of origins.
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    # === Zippy / Knowledge base / Vector store ===
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str = ""
    QDRANT_COLLECTION: str = "beacon_knowledge"

    OPENAI_API_KEY: str = ""
    OPENAI_EMBED_MODEL: str = "text-embedding-3-small"
    OPENAI_EMBED_DIMS: int = 1536

    AZURE_OPENAI_API_KEY: str = ""
    AZURE_OPENAI_ENDPOINT: str = ""
    AZURE_OPENAI_API_VERSION: str = "2024-12-01-preview"
    AZURE_OPENAI_DEPLOYMENT: str = ""
    AZURE_OPENAI_EMBED_DEPLOYMENT: str = ""
    AZURE_OPENAI_EMBED_MODEL: str = "text-embedding-3-small"
    AZURE_OPENAI_EMBED_DIMS: int = 1536

    NDA_TEMPLATE_DRIVE_ID_INDIA: str = ""
    NDA_TEMPLATE_DRIVE_ID_US: str = ""
    NDA_TEMPLATE_DRIVE_ID_SINGAPORE: str = ""

    ZIPPY_MODEL: str = "claude-sonnet-4-6"
    ZIPPY_MAX_TOKENS: int = 4000
    ZIPPY_TOP_K: int = 8
    ZIPPY_CHUNK_SIZE: int = 1200
    ZIPPY_CHUNK_OVERLAP: int = 200

    # Resend (email sending)
    RESEND_API_KEY: str = ""
    RESEND_FROM_EMAIL: str = "onboarding@resend.dev"

    # Sales reports: production sends to the normal recipient list. Non-production
    # environments are restricted to this allowlist so staging can test real sends
    # without accidentally emailing the team.
    SALES_REPORT_NONPROD_RECIPIENTS: str = "sarthak@beacon.li"
    SALES_REPORT_ENABLE_NONPROD_SCHEDULED_SENDS: bool = False

    # Gmail shared inbox (email-to-activity sync)
    GMAIL_SHARED_INBOX: str = ""  # e.g. sales@beacon.li
    GMAIL_CREDENTIALS_JSON: str = ""  # Path to OAuth credentials.json
    GMAIL_TOKEN_JSON: str = ""  # Path to stored token.json (auto-refreshed)
    EMAIL_SYNC_INTERVAL_SECONDS: int = 180  # 3 minutes
    EMAIL_SUMMARY_MIN_CHARS: int = 100  # Skip AI summary for short emails

    # Web Push (VAPID) — used to ring the rep's mobile PWA when they click
    # "Call" on the desktop prospect list. Generate keys with:
    #   docker compose exec -T backend python -c \
    #     "from py_vapid import Vapid; v=Vapid(); v.generate_keys(); \
    #      print('PUB:', v.public_key.to_string('uncompressed').hex()); \
    #      print('PRIV:', v.private_key.to_string().hex())"
    # Or any VAPID generator. The PUBLIC key is exposed to the browser; the
    # PRIVATE key never leaves the server.
    # NOTE: the previously committed keypair has been removed from source and
    # MUST be rotated — treat the old value as compromised. Supply both via env.
    VAPID_PUBLIC_KEY: str = ""   # base64url-encoded uncompressed P-256 public key
    VAPID_PRIVATE_KEY: str = ""  # base64url-encoded P-256 private key (never commit)
    VAPID_SUBJECT: str = "mailto:admin@beacon.li"  # mailto: or https: contact for push services

    # Aircall
    AIRCALL_API_ID: str = ""
    AIRCALL_API_TOKEN: str = ""
    AIRCALL_WEBHOOK_URL: str = ""
    AIRCALL_WEBHOOK_SECRET: str = ""  # Shared secret sent as X-Beacon-Webhook-Secret header
    AIRCALL_DEFAULT_NUMBER: str = ""  # E.164 digits of the default outbound number

    # tl;dv meeting intelligence
    TLDV_API_BASE: str = "https://pasta.tldv.io/v1alpha1"
    TLDV_API_KEY: str = ""
    TLDV_SYNC_LOOKBACK_DAYS: int = 365

    # ClickUp migration
    CLICKUP_API_BASE: str = "https://api.clickup.com/api/v2"
    CLICKUP_API_TOKEN: str = ""
    CLICKUP_TEAM_ID: str = ""
    CLICKUP_SPACE_ID: str = ""
    CLICKUP_DEALS_LIST_ID: str = ""

    # Qdrant vector DB (Zippy knowledge base)
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str = ""
    QDRANT_COLLECTION: str = "beacon_knowledge"

    # OpenAI (for embeddings used by Zippy RAG)
    OPENAI_API_KEY: str = ""
    OPENAI_EMBED_MODEL: str = "text-embedding-3-small"
    OPENAI_EMBED_DIMS: int = 1536

    # Azure OpenAI (used for the LLM and/or embeddings — controlled separately)
    AZURE_OPENAI_API_KEY: str = ""
    AZURE_OPENAI_ENDPOINT: str = ""  # e.g. https://myresource.openai.azure.com/
    AZURE_OPENAI_API_VERSION: str = "2024-12-01-preview"
    AZURE_OPENAI_DEPLOYMENT: str = ""  # existing LLM deployment (gpt-4o-mini etc.)
    # Leave empty when your Azure resource has no embedding deployment —
    # auto-detect will then fall back to the direct OpenAI API.
    AZURE_OPENAI_EMBED_DEPLOYMENT: str = ""
    AZURE_OPENAI_EMBED_MODEL: str = "text-embedding-3-small"
    AZURE_OPENAI_EMBED_DIMS: int = 1536

    # Explicit override: "openai" | "azure" | "" (auto-detect).
    # Use this when you want Azure for the LLM but OpenAI for embeddings
    # (or vice versa). Auto-detect only picks Azure for embeddings when its
    # embedding deployment is actually configured.
    EMBEDDINGS_PROVIDER: str = ""

    @property
    def embeddings_provider(self) -> str:
        """Return 'azure' or 'openai' — respects EMBEDDINGS_PROVIDER override."""
        explicit = self.EMBEDDINGS_PROVIDER.strip().lower()
        if explicit in {"openai", "azure"}:
            return explicit
        # Auto-detect: only route to Azure if the *embedding* deployment is
        # set. Having Azure configured for the LLM alone isn't enough —
        # embedding deployments are a separate Azure resource.
        if (
            self.AZURE_OPENAI_API_KEY
            and self.AZURE_OPENAI_ENDPOINT
            and self.AZURE_OPENAI_EMBED_DEPLOYMENT
        ):
            return "azure"
        return "openai"

    @property
    def embeddings_ready(self) -> bool:
        """True if we have enough config to actually call an embeddings API."""
        if self.embeddings_provider == "azure":
            return bool(
                self.AZURE_OPENAI_API_KEY
                and self.AZURE_OPENAI_ENDPOINT
                and self.AZURE_OPENAI_EMBED_DEPLOYMENT
            )
        return bool(self.OPENAI_API_KEY)

    @property
    def embeddings_dims(self) -> int:
        if self.embeddings_provider == "azure":
            return self.AZURE_OPENAI_EMBED_DIMS
        return self.OPENAI_EMBED_DIMS

    # Zippy document templates (Google Drive file IDs)
    NDA_TEMPLATE_DRIVE_ID_INDIA: str = ""
    NDA_TEMPLATE_DRIVE_ID_US: str = ""
    NDA_TEMPLATE_DRIVE_ID_SINGAPORE: str = ""
    # Zippy agent tuning
    ZIPPY_MODEL: str = "claude-sonnet-4-6"
    ZIPPY_MAX_TOKENS: int = 4000
    ZIPPY_TOP_K: int = 8
    ZIPPY_CHUNK_SIZE: int = 1200
    ZIPPY_CHUNK_OVERLAP: int = 200

    # ── Derived security helpers ──────────────────────────────────────────────
    @property
    def is_production(self) -> bool:
        """True for prod-like environments where secrets/hardening are enforced."""
        return self.ENVIRONMENT.strip().lower() in {"production", "prod", "staging"}

    @property
    def allowed_email_domains(self) -> List[str]:
        """Lowercased domain allowlist; empty list means 'allow any Google account'."""
        return [d.strip().lower() for d in self.ALLOWED_EMAIL_DOMAINS.split(",") if d.strip()]

    @property
    def admin_bootstrap_emails(self) -> set:
        """Lowercased emails auto-granted admin; empty means legacy first-user bootstrap."""
        return {e.strip().lower() for e in self.ADMIN_BOOTSTRAP_EMAILS.split(",") if e.strip()}

    def validate_runtime_secrets(self) -> List[str]:
        """
        Return a list of blocking misconfigurations for prod/staging startup.

        Empty in development. The app calls this on boot (see app/main.py) and
        refuses to start in a prod-like environment that is still running on the
        insecure dev placeholders or the leaked, now-rotated VAPID key.
        """
        if not self.is_production:
            return []
        problems: List[str] = []
        if self.SECRET_KEY == "dev_secret_key":
            problems.append("SECRET_KEY is the insecure dev default — set a strong random value.")
        if self.JWT_SECRET == "jwt_dev_secret_change_me":
            problems.append("JWT_SECRET is the insecure dev default — set a strong random value.")
        if len(self.JWT_SECRET) < 32:
            problems.append("JWT_SECRET is too short (<32 chars) for production.")
        if self.VAPID_PRIVATE_KEY == "60WTbuXtl_tv9Vi_k_f5vqhVmo03qu0bAXMTw3x_A8k":
            problems.append("VAPID_PRIVATE_KEY is the leaked committed key — rotate and set via env.")
        return problems


settings = Settings()

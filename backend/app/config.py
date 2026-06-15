from pydantic_settings import BaseSettings
from pydantic import model_validator
from functools import lru_cache
import re


def _fix_db_url(url: str) -> str:
    """
    Railway injects a bare `postgresql://...` URL.
    SQLAlchemy async requires `postgresql+asyncpg://...`.
    This function patches it transparently.
    """
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        url = re.sub(r"^postgres(?:ql)?://", "postgresql+asyncpg://", url)
    return url


class Settings(BaseSettings):
    # Self-hosted inference (vLLM on GPU VM — primary provider when set)
    vllm_base_url: str = ""           # e.g. http://34.74.52.36:8000
    vllm_model: str = "cyankiwi/gemma-4-26B-A4B-it-AWQ-4bit"
    vllm_timeout: int = 120           # seconds — GPU cold-start can take ~50s

    # LLM providers (fallback chain: vLLM → Claude → Groq → Gemini)
    anthropic_api_key: str = ""
    groq_api_key: str = ""
    google_api_key: str = ""

    # Database
    database_url: str = "sqlite+aiosqlite:///./demo.db"

    # Demo site
    base_url: str = "https://lamaqrait.github.io/rait-qa-demo"
    demo_host_url: str = ""  # when set, use GCP demo server instead of GitHub API
    playwright_timeout: int = 20000

    # Pipeline thresholds
    auto_fix_threshold: float = 0.80
    max_run_cost_usd: float = 0.50

    # Circuit breaker thresholds (all configurable — see production_evolution_plan.md §M1)
    error_rate_ceiling: float = 0.20          # >20% fails in last 10 runs → suspend auto-fix
    fpr_ceiling: float = 0.15                 # >15% human overrides in 30d → raise threshold
    confidence_drift_pp_ceiling: float = 15.0 # 7d mean shifts >15pp from 30d baseline → alert
    quarantine_threshold: int = 3             # consecutive failures before quarantine
    fpr_recovery_threshold: float = 0.10      # error rate must drop below this to auto-recover

    # Calibration mode — when True, ALL runs route to HITL (cold-start ground-truth building)
    calibration_mode: bool = False
    reflector_min_sample: int = 50            # Reflector won't propose changes until N confirmed runs

    # Suite selector tuning
    suite_selector_det_confidence: float = 0.70        # min confidence for deterministic path
    suite_selector_max_fraction: float = 0.50           # if deterministic selects > this fraction → embedding
    suite_selector_use_llm: bool = True                 # legacy flag, now controls embedding fallback
    suite_selector_embedding_threshold: float = 0.35   # cosine similarity threshold for embedding path

    # DOM inspector
    dom_inspector_max_elements: int = 200     # elements scanned per selector (was hardcoded 50)

    # GitHub (GITHUB_PAT is an alias accepted from repo-root .env)
    github_token: str = ""
    github_pat: str = ""
    github_repo_owner: str = "LamaqRAIT"
    github_repo_name: str = "rait-qa-demo"
    github_webhook_secret: str = ""

    @model_validator(mode="after")
    def _alias_github_pat(self) -> "Settings":
        if not self.github_token and self.github_pat:
            self.github_token = self.github_pat
        return self

    # Langfuse — accept both naming conventions (LANGFUSE_HOST and LANGFUSE_BASE_URL)
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_base_url: str = ""          # alias, takes precedence if set

    # JWT auth
    jwt_secret: str = "changeme-dev-only-do-not-use-in-prod"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7

    # Integrations
    slack_webhook_url: str = ""
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_project_key: str = "RAIT"

    # CORS / hosting
    backend_url: str = ""
    agent_ui_url: str = ""
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]

    def get_langfuse_host(self) -> str:
        """Return the effective Langfuse host (prefer LANGFUSE_BASE_URL alias)."""
        return self.langfuse_base_url or self.langfuse_host

    def get_db_url(self) -> str:
        """Return the database URL with asyncpg dialect for Postgres."""
        return _fix_db_url(self.database_url)

    def is_postgres(self) -> bool:
        return "postgresql" in self.database_url or "postgres" in self.database_url

    def get_cors_origins(self) -> list[str]:
        origins = list(self.cors_origins)
        for extra in [self.agent_ui_url, self.backend_url]:
            if extra and extra not in origins:
                origins.append(extra)
        # Always allow Railway deploy domains
        origins.append("https://*.up.railway.app")
        return origins

    class Config:
        env_file = ("../.env", ".env")  # try repo-root first, then backend/
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()

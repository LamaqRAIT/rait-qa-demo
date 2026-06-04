from pydantic_settings import BaseSettings
from pydantic import model_validator
from functools import lru_cache
import re


def _fix_db_url(url: str) -> str:
    """
    Railway/GCP injects a bare `postgresql://...` URL.
    SQLAlchemy async requires `postgresql+asyncpg://...`.
    """
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        url = re.sub(r"^postgres(?:ql)?://", "postgresql+asyncpg://", url)
    return url


class Settings(BaseSettings):
    # ── Self-hosted vLLM inference (GCP Cloud Run GPU) ────────────────────────
    vllm_base_url: str = ""                         # e.g. https://rait-qa-vllm-xxx.run.app
    vllm_model: str = "google/gemma-4-26b-a4b"     # model served by vLLM
    vllm_timeout_s: float = 60.0                    # HTTP timeout for vLLM calls

    # ── Database ───────────────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./demo.db"

    # ── GCS storage ───────────────────────────────────────────────────────────
    gcs_model_weights_bucket: str = "rait-qa-model-weights"
    gcs_dom_snapshots_bucket: str = "rait-qa-dom-snapshots"

    # ── OTel Collector (sidecar) ───────────────────────────────────────────────
    # Set to http://localhost:4317 in Cloud Run (sidecar on same container)
    # Leave empty to disable telemetry (local dev default)
    otel_exporter_otlp_endpoint: str = ""

    # ── Demo site ──────────────────────────────────────────────────────────────
    base_url: str = "https://lamaqrait.github.io/rait-qa-demo"
    playwright_timeout: int = 20000

    # ── Pipeline ───────────────────────────────────────────────────────────────
    # auto_fix_threshold kept for circuit_breaker.py and metrics API (deprecated — gate controls routing)
    auto_fix_threshold: float = 0.80
    max_run_cost_usd: float = 0.50

    # ── GitHub ─────────────────────────────────────────────────────────────────
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

    @model_validator(mode="after")
    def _propagate_otel_endpoint(self) -> "Settings":
        """Mirror otel_exporter_otlp_endpoint → OTEL_EXPORTER_OTLP_ENDPOINT env var."""
        import os
        if self.otel_exporter_otlp_endpoint and not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
            os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = self.otel_exporter_otlp_endpoint
        return self

    # ── JWT auth ───────────────────────────────────────────────────────────────
    jwt_secret: str = "changeme-dev-only-do-not-use-in-prod"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7

    # ── Integrations ───────────────────────────────────────────────────────────
    slack_webhook_url: str = ""
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_project_key: str = "RAIT"

    # ── CORS / hosting ─────────────────────────────────────────────────────────
    backend_url: str = ""
    agent_ui_url: str = ""
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]

    def get_db_url(self) -> str:
        return _fix_db_url(self.database_url)

    def is_postgres(self) -> bool:
        return "postgresql" in self.database_url or "postgres" in self.database_url

    def get_cors_origins(self) -> list[str]:
        origins = list(self.cors_origins)
        for extra in [self.agent_ui_url, self.backend_url]:
            if extra and extra not in origins:
                origins.append(extra)
        origins.append("https://*.up.railway.app")
        origins.append("https://*.run.app")
        return origins

    class Config:
        env_file = ("../.env", ".env")
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()

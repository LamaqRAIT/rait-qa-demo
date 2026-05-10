from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    google_api_key: str = ""
    groq_api_key: str = ""
    database_url: str = "sqlite+aiosqlite:///./demo.db"
    base_url: str = "https://lamaqrait.github.io/rait-qa-demo"
    playwright_timeout: int = 20000
    auto_fix_threshold: float = 0.80
    max_run_cost_usd: float = 0.50
    github_token: str = ""
    github_repo_owner: str = "LamaqRAIT"
    github_repo_name: str = "rait-qa-demo"
    github_webhook_secret: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    backend_url: str = ""
    agent_ui_url: str = ""
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]

    def get_cors_origins(self) -> list[str]:
        origins = list(self.cors_origins)
        for extra in [self.agent_ui_url, self.backend_url]:
            if extra and extra not in origins:
                origins.append(extra)
        return origins

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()

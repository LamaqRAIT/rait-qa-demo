from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    google_api_key: str = ""
    groq_api_key: str = ""
    database_url: str = "sqlite+aiosqlite:///./demo.db"
    base_url: str = "https://lamaqrait.github.io/rait-qa-demo"
    playwright_timeout: int = 15000
    auto_fix_threshold: float = 0.80
    github_webhook_secret: str = ""
    # Add your Railway agent-ui URL here after deploying the agent-ui service.
    # e.g. CORS_ORIGINS=["https://rait-qa-agent-ui.up.railway.app"]
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]
    agent_ui_url: str = ""  # Set to Railway agent-ui URL after deploy

    def get_cors_origins(self) -> list[str]:
        origins = list(self.cors_origins)
        if self.agent_ui_url and self.agent_ui_url not in origins:
            origins.append(self.agent_ui_url)
        return origins

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()

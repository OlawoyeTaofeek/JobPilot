"""All settings come from environment variables / .env (pydantic-settings)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    app_env: str = "dev"
    secret_key: str = "change-me"
    encryption_key: str = ""

    openai_api_key: str = ""
    agent_model: str = "gpt-5-mini"
    embedding_model: str = "text-embedding-3-small"
    openai_tracing: bool = True

    # tracing: Langfuse (optional; skipped if empty)   <- NEW
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = "https://cloud.langfuse.com"

    database_url: str = "postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent"

    jsearch_api_key: str = ""
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    hunter_api_key: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""

    mcp_server_url: str = ""
    mcp_api_key: str = ""

    max_turns: int = 10
    daily_user_budget_usd: float = 1.0
    chat_rate_limit_per_min: int = 20

    # Seed user (scripts/seed.py)
    seed_email: str | None = None
    seed_username: str | None = None
    seed_password: str | None = None
    seed_full_name: str | None = None
    jsearch_max_requests_per_day: int = 6


@lru_cache
def get_settings() -> Settings:
    return Settings()

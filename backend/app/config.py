from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "GeoVerdict.AI API"
    api_prefix: str = "/api/v1"
    environment: Literal["local", "dev", "prod"] = "local"
    database_url: str = "sqlite+aiosqlite:///./data/geoverdict.db"
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    ops_token: str = "geoverdict-ops-secret"
    default_city: str = "Москва"
    llm_provider_priority: list[str] = Field(
        default_factory=lambda: ["mock", "openai", "anthropic", "ollama", "vllm"]
    )
    geo_request_timeout_seconds: float = 14.0
    analysis_target_seconds: int = 45
    optimizer_min_improvement_pct: float = 8.0
    nominatim_url: str = "https://nominatim.openstreetmap.org/reverse"
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    user_agent: str = "GeoVerdictAI/0.1 (+https://localhost)"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    ollama_base_url: str | None = "http://localhost:11434"
    vllm_base_url: str | None = "http://localhost:8001/v1"
    langfuse_enabled: bool = False
    langfuse_host: str = "http://127.0.0.1:3002"
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "geoverdict"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

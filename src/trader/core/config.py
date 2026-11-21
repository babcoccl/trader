"""Application settings, loaded from environment variables prefixed TRADER_."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Values may be overridden by TRADER_* env vars."""

    model_config = SettingsConfigDict(
        env_prefix="TRADER_", env_file=".env", extra="ignore"
    )

    database_url: str = "postgresql+psycopg://trader:trader@localhost:5432/trader"
    mode: Literal["paper", "live"] = "paper"
    live_enabled: bool = False
    log_level: str = "INFO"
    dashboard_password: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()

from __future__ import annotations
import os
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_ENV: str = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001,http://localhost:3002,http://127.0.0.1:3002"

    DATABASE_URL: str = "sqlite+aiosqlite:///./helm.db"
    DATABASE_URL_SYNC: str = "sqlite:///./helm.db"
    REDIS_URL: str = "redis://localhost:6379/0"

    MODEL_PROVIDER: str = "deepseek"
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL: str = "deepseek-chat"
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"

    SANDBOX_PROVIDER: str = "local_docker"
    DOCKER_SANDBOX_IMAGE: str = "helm-sandbox:latest"
    SANDBOX_TIMEOUT_SECONDS: int = 300

    # Supabase Auth Configuration
    SUPABASE_URL: str = "https://irvqagtxazoawcoutknu.supabase.co"
    SUPABASE_ANON_KEY: str = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImlydnFhZ3R4YXpvYXdjb3V0a251Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODc2NjY0NTMsImV4cCI6MjEwMzI0MjQ1M30.cOTiLNg9HKgg8DR-P-BFoEXdNvKYqbI_c4VxFOnb_ic"
    WORKSPACE_DIR: str = "./workspaces"

    # Backend Security & Protection
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_DEFAULT_RPM: int = 180
    RATE_LIMIT_TASKS_RPM: int = 20
    HELM_API_KEY: str = ""
    MAX_REQUEST_BODY_BYTES: int = 10 * 1024 * 1024  # 10 MB

    @property
    def cors_origin_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


settings = Settings()

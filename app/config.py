from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    booking_token_secret: str | None
    demo_business_id: str
    frontend_api_base_url: str
    session_ttl_minutes: int
    llm_provider: str
    openai_api_key: str | None
    openai_model: str
    tracing_enabled: bool
    langfuse_public_key: str | None
    langfuse_secret_key: str | None
    langfuse_host: str | None


def _flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    return Settings(
        database_url=os.getenv(
            "DATABASE_URL",
            "postgresql://scheduling:scheduling@localhost:5433/scheduling",
        ),
        booking_token_secret=os.getenv("BOOKING_TOKEN_SECRET"),
        demo_business_id=os.getenv("DEMO_BUSINESS_ID", "demo-studio"),
        frontend_api_base_url=os.getenv("FRONTEND_API_BASE_URL", "http://localhost:8000"),
        session_ttl_minutes=int(os.getenv("SESSION_TTL_MINUTES", "60")),
        llm_provider=os.getenv("LLM_PROVIDER", "deterministic").lower(),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        tracing_enabled=_flag("TRACING_ENABLED", bool(public_key and secret_key)),
        langfuse_public_key=public_key,
        langfuse_secret_key=secret_key,
        langfuse_host=os.getenv("LANGFUSE_HOST"),
    )

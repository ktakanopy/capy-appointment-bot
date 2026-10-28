from __future__ import annotations

import math
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
    chat_redis_url: str
    tracing_enabled: bool
    langfuse_public_key: str | None
    langfuse_secret_key: str | None
    langfuse_host: str | None
    whatsapp_app_secret: str | None = None
    whatsapp_verify_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    owner_api_token: str | None = None
    understanding_provider: str = "gemma"
    decision_provider: str = "laya"
    gemma_base_url: str = "http://localhost:8081"
    laya_base_url: str = "http://localhost:8082"
    sms_webhook_secret: str | None = None
    telegram_bot_token: str | None = None
    telegram_poll_seconds: int = 20
    telegram_audio_max_bytes: int = 20_000_000
    telegram_audio_max_decoded_bytes: int = 512 * 1024 * 1024
    telegram_audio_decode_timeout_seconds: float = 120
    telegram_audio_chunk_timeout_seconds: float = 60
    telegram_audio_processing_timeout_seconds: float = 900

    def __post_init__(self):
        if not 1 <= self.telegram_poll_seconds <= 50:
            raise ValueError("TELEGRAM_POLL_SECONDS must be between 1 and 50")
        if self.telegram_audio_max_bytes <= 0 or self.telegram_audio_max_decoded_bytes < 960_000:
            raise ValueError("Telegram audio limits must allow one 30-second mono audio chunk")
        timeouts = (
            self.telegram_audio_decode_timeout_seconds,
            self.telegram_audio_chunk_timeout_seconds,
            self.telegram_audio_processing_timeout_seconds,
        )
        if any(not math.isfinite(value) or value <= 0 for value in timeouts):
            raise ValueError("Telegram audio timeouts must be finite and positive")


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
        chat_redis_url=os.getenv("CHAT_REDIS_URL", "redis://localhost:6380/0"),
        tracing_enabled=_flag("TRACING_ENABLED", bool(public_key and secret_key)),
        langfuse_public_key=public_key,
        langfuse_secret_key=secret_key,
        langfuse_host=os.getenv("LANGFUSE_HOST"),
        whatsapp_app_secret=os.getenv("WHATSAPP_APP_SECRET"),
        whatsapp_verify_token=os.getenv("WHATSAPP_VERIFY_TOKEN"),
        whatsapp_phone_number_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID"),
        owner_api_token=os.getenv("OWNER_API_TOKEN"),
        understanding_provider=os.getenv("UNDERSTANDING_PROVIDER", "gemma").lower(),
        decision_provider=os.getenv("DECISION_PROVIDER", "laya").lower(),
        gemma_base_url=os.getenv("GEMMA_BASE_URL", "http://localhost:8081"),
        laya_base_url=os.getenv("LAYA_BASE_URL", "http://localhost:8082"),
        sms_webhook_secret=os.getenv("SMS_WEBHOOK_SECRET"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_poll_seconds=int(os.getenv("TELEGRAM_POLL_SECONDS", "20")),
        telegram_audio_max_bytes=int(os.getenv("TELEGRAM_AUDIO_MAX_BYTES", "20000000")),
        telegram_audio_max_decoded_bytes=int(
            os.getenv("TELEGRAM_AUDIO_MAX_DECODED_BYTES", str(512 * 1024 * 1024))
        ),
        telegram_audio_decode_timeout_seconds=float(
            os.getenv("TELEGRAM_AUDIO_DECODE_TIMEOUT_SECONDS", "120")
        ),
        telegram_audio_chunk_timeout_seconds=float(
            os.getenv("TELEGRAM_AUDIO_CHUNK_TIMEOUT_SECONDS", "60")
        ),
        telegram_audio_processing_timeout_seconds=float(
            os.getenv("TELEGRAM_AUDIO_PROCESSING_TIMEOUT_SECONDS", "900")
        ),
    )

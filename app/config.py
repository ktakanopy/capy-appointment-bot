from __future__ import annotations

import math

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://scheduling:scheduling@localhost:5433/scheduling"
    demo_business_id: str = "demo-studio"
    session_ttl_minutes: int = Field(default=60, ge=1)
    chat_redis_url: str = "redis://localhost:6380/0"
    rabbitmq_url: SecretStr = SecretStr("amqp://guest:guest@localhost:5672/?heartbeat=30")
    telegram_recovery_interval_seconds: float = Field(default=1, gt=0, le=60, allow_inf_nan=False)
    telegram_notification_timeout_seconds: float = Field(default=1, gt=0, le=5, allow_inf_nan=False)
    tracing_enabled: bool | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str | None = None
    understanding_provider: str = "gemma"
    decision_provider: str = "laya"
    gemma_base_url: str = "http://localhost:8081"
    laya_base_url: str = "http://localhost:8081"
    telegram_bot_id: int | None = Field(default=None, gt=0, lt=2**63)
    telegram_webhook_route: str = Field(default="webhook", pattern=r"^[A-Za-z0-9_-]{1,128}$")
    agenda_api_token: SecretStr | None = None
    telegram_audio_max_bytes: int = Field(default=20_000_000, gt=0)
    telegram_audio_max_decoded_bytes: int = Field(default=512 * 1024 * 1024, ge=960_000)
    telegram_audio_decode_timeout_seconds: float = 120
    telegram_audio_chunk_timeout_seconds: float = 60
    telegram_audio_processing_timeout_seconds: float = 900

    @field_validator("telegram_bot_id", mode="before")
    @classmethod
    def blank_bot_id(cls, value):
        return None if value == "" else value

    @field_validator("understanding_provider", "decision_provider")
    @classmethod
    def lowercase_provider(cls, value: str) -> str:
        return value.lower()

    @field_validator("tracing_enabled", mode="before")
    @classmethod
    def parse_tracing(cls, value):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return value

    @field_validator(
        "telegram_audio_decode_timeout_seconds",
        "telegram_audio_chunk_timeout_seconds",
        "telegram_audio_processing_timeout_seconds",
    )
    @classmethod
    def positive_finite_timeout(cls, value: float) -> float:
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Telegram audio timeouts must be finite and positive")
        return value

    @model_validator(mode="after")
    def default_tracing(self):
        if self.tracing_enabled is None:
            self.tracing_enabled = bool(self.langfuse_public_key and self.langfuse_secret_key)
        return self


def load_settings() -> Settings:
    return Settings()

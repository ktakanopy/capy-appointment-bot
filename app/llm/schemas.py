from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SchedulingInterpretation(BaseModel):
    """Plain-language meaning and booking facts, without a routing decision."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=64_000)
    service_name: str | None = Field(default=None, max_length=120)
    professional_name: str | None = Field(default=None, max_length=120)
    local_date: str | None = Field(default=None, max_length=20)
    local_time: str | None = Field(default=None, max_length=10)
    slot_index: int | None = Field(default=None, ge=1, le=100)

    @field_validator("message")
    @classmethod
    def require_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("An interpreted message is required")
        return value.strip()

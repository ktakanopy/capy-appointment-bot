from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SchedulingExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["search", "select_slot", "create", "confirm", "cancel", "reschedule", "help", "unknown"] = "unknown"
    service_name: str | None = Field(default=None, max_length=120)
    professional_name: str | None = Field(default=None, max_length=120)
    local_date: str | None = Field(default=None, max_length=20)
    local_time: str | None = Field(default=None, max_length=10)
    slot_index: int | None = Field(default=None, ge=1, le=100)
    customer_name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=24)


class SchedulingLLMExtraction(BaseModel):
    """Strict, nullable output contract sent to the OpenAI structured-output API."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal[
        "search", "select_slot", "create", "confirm", "cancel", "reschedule", "help", "unknown"
    ] = Field(description="The scheduling intent in the latest user message.")
    service_name: str | None = Field(
        description="The service name explicitly requested, or null if absent."
    )
    professional_name: str | None = Field(
        description="The professional name explicitly requested, or null if absent."
    )
    local_date: str | None = Field(
        description="An explicit business-local date in YYYY-MM-DD format, or null."
    )
    local_time: str | None = Field(
        description="An explicit 24-hour local time in HH:MM format, or null."
    )
    slot_index: int | None = Field(
        description="The one-based number of a previously offered slot, or null."
    )
    customer_name: str | None = Field(
        description="The customer's full name when explicitly provided, or null."
    )
    phone: str | None = Field(
        description="The customer's phone number when explicitly provided, or null."
    )

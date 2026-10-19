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

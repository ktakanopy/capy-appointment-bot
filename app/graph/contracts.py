from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import AvailabilitySlot, Booking

Operation = Literal["create", "reschedule", "confirm", "cancel", "none"]
MissingField = Literal[
    "service", "date", "professional", "customer", "slot", "booking", "none"
]
NextAction = Literal[
    "BOOK",
    "RESCHEDULE",
    "CANCEL",
    "CHECK_AVAILABILITY",
    "FAQ",
    "CONFIRM",
    "ESCALATE",
    "LOOKUP_BOOKING",
    "CLARIFY",
]


class WorkflowDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Operation = "none"
    service_id: str | None = None
    service_name: str | None = None
    professional_id: str | None = None
    local_date: str | None = None
    earliest_local_time: str | None = None
    offered_slot_ids: list[str] = Field(default_factory=list)
    pending_slot_id: str | None = None
    missing: MissingField = "none"
    active_booking_id: str | None = None
    candidate_booking_ids: list[str] = Field(default_factory=list)
    escalated: bool = False
    invalid_date: bool = False


class DecisionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    next_action: NextAction
    confidence: float = Field(ge=0.0, le=1.0)
    needs_human: bool = False
    reason_code: str | None = None


class BookingRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    service_id: str
    professional_id: str | None = None
    label: str


class TurnContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    business_id: str
    message: str
    today: str
    selected_slot_id: str | None = None
    customer_id: str | None = None
    booking_id: str | None = None
    idempotency_key: str | None = None
    input_modality: Literal["text", "audio"] = "text"


class TurnResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    intent: str
    slots: list[AvailabilitySlot] = Field(default_factory=list)
    booking: Booking | None = None

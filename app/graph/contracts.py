from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import AvailabilitySlot, Booking


Operation = Literal["create", "reschedule", "confirm", "cancel", "none"]
MissingField = Literal["service", "date", "professional", "customer", "slot", "booking", "none"]
AuthKind = Literal["anonymous", "customer", "booking"]
NextAction = Literal[
    "BOOK",
    "RESCHEDULE",
    "CANCEL",
    "CHECK_AVAILABILITY",
    "FAQ",
    "CONFIRM",
    "ESCALATE",
    "LOOKUP_BOOKING",
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


class NormalizedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    customer_id: str | None = None
    input_modality: Literal["text", "audio"] = "text"
    transcript: str
    intent: str = "unknown"
    service: str | None = None
    professional: str | None = None
    location: str | None = None
    date_constraint: str | None = None
    time_constraint: str | None = None
    booking_reference: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class DecisionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    next_action: NextAction
    confidence: float = Field(ge=0.0, le=1.0)
    needs_human: bool = False
    reason_code: str | None = None


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    error_code: str | None = None
    domain_payload: dict = Field(default_factory=dict)
    retryable: bool = False


class BookingRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    service_id: str
    label: str


class TurnContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    conversation_id: str
    business_id: str
    message: str
    today: str
    selected_slot_id: str | None = None
    auth_kind: AuthKind = "anonymous"
    customer_id: str | None = None
    booking_id: str | None = None
    customer_ready: bool = False
    idempotency_key: str | None = None
    input_modality: Literal["text", "audio"] = "text"
    normalized: NormalizedRequest | None = None


class TurnResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    text: str
    intent: str
    slots: list[AvailabilitySlot] = Field(default_factory=list)
    booking: Booking | None = None
    manage_token: str | None = None

from __future__ import annotations

from datetime import date, datetime, time, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BookingStatus(StrEnum):
    SCHEDULED = "scheduled"
    CONFIRMED = "confirmed"
    CANCELED = "canceled"


class ReminderStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    DELIVERED = "delivered"
    FAILED = "failed"
    CANCELED = "canceled"


class Business(Contract):
    id: str
    name: str
    category: str
    timezone: str
    minimum_notice_minutes: int = 5
    booking_horizon_days: int = 60
    reminder_lead_minutes: int = 5
    created_at: datetime | None = None


class Professional(Contract):
    id: str
    business_id: str
    name: str
    active: bool = True


class Service(Contract):
    id: str
    business_id: str
    name: str
    description: str
    duration_minutes: int
    buffer_before_minutes: int = 0
    buffer_after_minutes: int = 0
    price_minor: int
    currency: str = "BRL"
    active: bool = True


class WeeklyAvailabilityRule(Contract):
    id: str
    business_id: str
    professional_id: str
    weekday: int = Field(ge=0, le=6)
    opens_at: time
    closes_at: time


class BlockedPeriod(Contract):
    id: str
    business_id: str
    professional_id: str
    starts_at: datetime
    ends_at: datetime


class Customer(Contract):
    id: str
    business_id: str
    full_name: str
    phone: str
    email: str | None = None


class Booking(Contract):
    id: str
    business_id: str
    service_id: str
    professional_id: str
    customer: Customer
    starts_at: datetime
    ends_at: datetime
    status: BookingStatus
    created_at: datetime
    updated_at: datetime


class Reminder(Contract):
    id: str
    booking_id: str
    send_at: datetime
    status: ReminderStatus
    attempts: int = 0


class AuditEvent(Contract):
    id: str
    business_id: str
    booking_id: str | None = None
    action: str
    occurred_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class AvailabilitySlot(Contract):
    slot_id: str
    business_id: str
    service_id: str
    professional_id: str
    professional_name: str
    starts_at: datetime
    ends_at: datetime
    local_label: str
    duration_minutes: int
    price_minor: int
    currency: str

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_aware_instant(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a UTC offset")
        return value.astimezone(timezone.utc)


class CatalogResponse(Contract):
    business: Business
    services: list[Service]
    professionals: list[Professional]


class AvailabilitySearchRequest(Contract):
    business_id: str = "demo-studio"
    service_id: str
    professional_id: str | None = None
    start_date: date
    end_date: date
    earliest_local_time: time | None = None
    latest_local_time: time | None = None

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if (self.end_date - self.start_date).days > 60:
            raise ValueError("availability range cannot exceed 60 days")
        return self


class AvailabilityResponse(Contract):
    slots: list[AvailabilitySlot]


class CustomerInput(Contract):
    full_name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=7, max_length=24)
    email: str | None = None

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        normalized = "".join(ch for ch in value if ch.isdigit() or ch == "+")
        if normalized.count("+") > 1 or ("+" in normalized and not normalized.startswith("+")):
            raise ValueError("phone must be an international phone number")
        digits = normalized.lstrip("+")
        if not digits.isdigit() or not 7 <= len(digits) <= 20:
            raise ValueError("phone must contain 7 to 20 digits")
        return normalized

    @field_validator("full_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not any(ch.isalpha() for ch in value):
            raise ValueError("full_name must contain letters")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is not None and ("@" not in value or value.startswith("@") or value.endswith("@")):
            raise ValueError("email must be a valid address")
        return value.lower() if value else None


class BookingCreateRequest(Contract):
    business_id: str = "demo-studio"
    service_id: str
    professional_id: str
    slot_id: str
    starts_at: datetime
    customer: CustomerInput

    @field_validator("starts_at")
    @classmethod
    def require_aware_instant(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("starts_at must include a UTC offset")
        return value.astimezone(timezone.utc)


class BookingRescheduleRequest(Contract):
    business_id: str = "demo-studio"
    service_id: str
    professional_id: str
    slot_id: str
    starts_at: datetime

    @field_validator("starts_at")
    @classmethod
    def require_aware_instant(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("starts_at must include a UTC offset")
        return value.astimezone(timezone.utc)


class BookingSummary(Contract):
    booking: Booking
    manage_token: str | None = None
    outcome: str = "created"


class OwnerBookingsResponse(Contract):
    business: Business
    bookings: list[Booking]


class DemoReminder(Contract):
    id: str
    reminder_id: str
    booking_id: str
    customer_name: str
    service_name: str
    starts_at: datetime
    message: str
    delivered_at: datetime


class DemoRemindersResponse(Contract):
    reminders: list[DemoReminder]


class SessionResponse(Contract):
    session_id: str
    thread_id: str
    response: str | None = None


class ChatRequest(Contract):
    session_id: str = Field(min_length=1, max_length=128)
    business_id: str = "demo-studio"
    message: str = Field(min_length=1, max_length=4000)
    manage_token: str | None = Field(default=None, max_length=512)
    selected_slot_id: str | None = Field(default=None, max_length=80)
    customer: CustomerInput | None = None


class ChatResponse(Contract):
    session_id: str
    response: str
    intent: str
    slots: list[AvailabilitySlot] = Field(default_factory=list)
    booking: Booking | None = None
    manage_token: str | None = None
    issue: str | None = None


class HealthResponse(Contract):
    status: str
    database: str = "ok"


class DomainError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

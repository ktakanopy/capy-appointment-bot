from typing import Protocol

from app.graph.contracts import BookingRef
from app.models import (
    AvailabilitySearchRequest,
    AvailabilitySlot,
    Booking,
    BookingSummary,
    CatalogResponse,
)


class SchedulingActions(Protocol):
    """Operations the workflow can request, independent of their implementation."""

    last_booking: Booking | None

    def catalog(self, business_id: str) -> CatalogResponse: ...

    def search(self, request: AvailabilitySearchRequest) -> list[AvailabilitySlot]: ...

    def create_booking(
        self, slot: AvailabilitySlot, idempotency_key: str
    ) -> BookingSummary: ...

    def confirm(self, booking_id: str, idempotency_key: str) -> tuple[Booking, str]: ...

    def cancel(self, booking_id: str, idempotency_key: str) -> tuple[Booking, str]: ...

    def reschedule(
        self, booking_id: str, slot: AvailabilitySlot, idempotency_key: str
    ) -> tuple[Booking, str]: ...

    def list_booking_refs(self) -> list[BookingRef]: ...

    def record_escalation(self, conversation_id: str) -> None: ...

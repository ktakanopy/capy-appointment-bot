from __future__ import annotations

from app.graph.contracts import BookingRef
from app.models import (
    AvailabilitySearchRequest,
    AvailabilitySlot,
    Booking,
    BookingCreateRequest,
    BookingRescheduleRequest,
    BookingSummary,
    CatalogResponse,
    DomainError,
)
from app.observability import log_event, trace_event
from app.runtime import RuntimeContext


class CustomerSchedulingActions:
    """Execute scheduling operations for a trusted customer in one conversation.

    Implements the SchedulingActions protocol through its methods. The workflow
    selects the operation; this object calls services with the trusted identity.
    """

    def __init__(
        self,
        runtime: RuntimeContext,
        business_id: str,
        customer_id: str | None,
        conversation_id: str | None = None,
    ):
        self.runtime = runtime
        self.business_id = business_id
        self.customer_id = customer_id
        self.conversation_id = conversation_id
        self.last_booking: Booking | None = None

    def catalog(self, business_id: str) -> CatalogResponse:
        if business_id != self.business_id:
            raise DomainError("business_not_found", "Business not found.", 404)
        catalog = self.runtime.repository.catalog(business_id)
        if catalog is None:
            raise DomainError("business_not_found", "Business not found.", 404)
        return catalog

    def search(self, request: AvailabilitySearchRequest) -> list[AvailabilitySlot]:
        if request.business_id != self.business_id:
            raise DomainError("business_not_found", "Business not found.", 404)
        return self.runtime.availability.search(request)

    def create_booking(
        self, slot: AvailabilitySlot, idempotency_key: str
    ) -> BookingSummary:
        if slot.business_id != self.business_id or not self.customer_id:
            raise DomainError(
                "customer_required", "A customer account is required.", 403
            )
        request = BookingCreateRequest(
            business_id=self.business_id,
            customer_id=self.customer_id,
            service_id=slot.service_id,
            professional_id=slot.professional_id,
            slot_id=slot.slot_id,
            starts_at=slot.starts_at,
        )
        result = self.runtime.bookings.create(request, idempotency_key)
        self.last_booking = result.booking
        self._record("create", result.outcome)
        return result

    def confirm(self, booking_id: str, idempotency_key: str) -> tuple[Booking, str]:
        booking, outcome = self.runtime.bookings.confirm(
            business_id=self.business_id,
            customer_id=self.customer_id,
            booking_id=booking_id,
            idempotency_key=idempotency_key,
        )
        self.last_booking = booking
        self._record("confirm", outcome)
        return booking, outcome

    def cancel(self, booking_id: str, idempotency_key: str) -> tuple[Booking, str]:
        booking, outcome = self.runtime.bookings.cancel(
            business_id=self.business_id,
            customer_id=self.customer_id,
            booking_id=booking_id,
            idempotency_key=idempotency_key,
        )
        self.last_booking = booking
        self._record("cancel", outcome)
        return booking, outcome

    def reschedule(
        self, booking_id: str, slot: AvailabilitySlot, idempotency_key: str
    ) -> tuple[Booking, str]:
        request = BookingRescheduleRequest(
            business_id=slot.business_id,
            service_id=slot.service_id,
            professional_id=slot.professional_id,
            slot_id=slot.slot_id,
            starts_at=slot.starts_at,
        )
        booking, outcome = self.runtime.bookings.reschedule(
            business_id=self.business_id,
            customer_id=self.customer_id,
            booking_id=booking_id,
            request=request,
            idempotency_key=idempotency_key,
        )
        self.last_booking = booking
        self._record("reschedule", outcome)
        return booking, outcome

    def list_booking_refs(self) -> list[BookingRef]:
        if not self.customer_id:
            return []
        bookings = self.runtime.repository.list_customer_bookings(
            business_id=self.business_id, customer_id=self.customer_id
        )
        return [
            BookingRef(
                id=booking.id,
                service_id=booking.service_id,
                professional_id=booking.professional_id,
                label=booking.starts_at.isoformat(),
            )
            for booking in bookings
        ]

    def record_escalation(self, conversation_id: str) -> None:
        log_event(self.runtime.logger, "chat.escalate", session_id=conversation_id)
        trace_event(self.runtime.tracer, conversation_id, "chat.escalate", {})

    def _record(self, operation: str, outcome: str) -> None:
        fields = {
            "operation": operation,
            "outcome": outcome,
            "business_id": self.business_id,
            "booking_id": self.last_booking.id,
        }
        log_event(self.runtime.logger, "booking.operation", **fields)
        trace_event(
            self.runtime.tracer,
            self.conversation_id or self.last_booking.id,
            "booking.operation",
            fields,
        )

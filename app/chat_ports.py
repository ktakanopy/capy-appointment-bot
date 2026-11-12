from __future__ import annotations

from app.graph.contracts import BookingRef
from app.models import BookingCreateRequest, BookingRescheduleRequest, DomainError
from app.observability import log_event, trace_event


class ChatPorts:
    """Request-scoped domain operations using a trusted transport identity."""

    def __init__(self, runtime, business_id: str, customer_id: str | None, conversation_id: str | None = None):
        self.runtime = runtime
        self.business_id = business_id
        self.customer_id = customer_id
        self.conversation_id = conversation_id
        self.last_booking = None

    def catalog(self, business_id):
        if business_id != self.business_id:
            raise DomainError("business_not_found", "Business not found.", 404)
        catalog = self.runtime.repository.catalog(business_id)
        if catalog is None:
            raise DomainError("business_not_found", "Business not found.", 404)
        return catalog

    def search(self, request):
        if request.business_id != self.business_id:
            raise DomainError("business_not_found", "Business not found.", 404)
        return self.runtime.availability.search(request)

    def create_booking(self, slot, idempotency_key):
        if slot.business_id != self.business_id or not self.customer_id:
            raise DomainError("customer_required", "A customer account is required.", 403)
        result = self.runtime.bookings.create(BookingCreateRequest(
            business_id=self.business_id, customer_id=self.customer_id,
            service_id=slot.service_id, professional_id=slot.professional_id,
            slot_id=slot.slot_id, starts_at=slot.starts_at,
        ), idempotency_key)
        self.last_booking = result.booking
        self._record("create", result.outcome)
        return result

    def confirm(self, booking_id, idempotency_key):
        result = self.runtime.bookings.confirm(self.business_id, self.customer_id, booking_id, idempotency_key)
        self.last_booking = result[0]
        self._record("confirm", result[1])
        return result

    def cancel(self, booking_id, idempotency_key):
        result = self.runtime.bookings.cancel(self.business_id, self.customer_id, booking_id, idempotency_key)
        self.last_booking = result[0]
        self._record("cancel", result[1])
        return result

    def reschedule(self, booking_id, slot, idempotency_key):
        result = self.runtime.bookings.reschedule(
            self.business_id, self.customer_id, booking_id,
            BookingRescheduleRequest(business_id=slot.business_id, service_id=slot.service_id,
                                    professional_id=slot.professional_id, slot_id=slot.slot_id, starts_at=slot.starts_at),
            idempotency_key,
        )
        self.last_booking = result[0]
        self._record("reschedule", result[1])
        return result

    def list_booking_refs(self):
        if not self.customer_id:
            return []
        return [BookingRef(id=b.id, service_id=b.service_id, professional_id=b.professional_id,
                           label=b.starts_at.isoformat())
                for b in self.runtime.repository.list_customer_bookings(self.business_id, self.customer_id)]

    def record_escalation(self, conversation_id):
        log_event(self.runtime.logger, "chat.escalate", session_id=conversation_id)
        trace_event(self.runtime.tracer, conversation_id, "chat.escalate", {})

    def _record(self, operation, outcome):
        fields = {"operation": operation, "outcome": outcome, "business_id": self.business_id,
                  "booking_id": self.last_booking.id}
        log_event(self.runtime.logger, "booking.operation", **fields)
        trace_event(self.runtime.tracer, self.conversation_id or self.last_booking.id, "booking.operation", fields)

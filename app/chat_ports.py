from __future__ import annotations

from app.graph.contracts import BookingRef, WorkflowDraft
from app.graph.memory import MemoryConversationStore
from app.models import (
    AvailabilitySearchRequest,
    AvailabilitySlot,
    Booking,
    BookingCreateRequest,
    BookingRescheduleRequest,
    CatalogResponse,
    ChatRequest,
    CustomerInput,
    DomainError,
)
from app.observability import log_event, trace_event
from app.runtime import RuntimeContext


class ChatPorts:
    """Tools the conversation workflow uses for one chat message.

    The workflow chooses what to do. This class carries out that request by
    calling the catalog, availability, and booking services. A new instance
    is created for each message.

    The management token and customer contact details stay on this object.
    The workflow state and model input use the redacted message and the IDs
    needed for the turn. A token or verified customer ID can authorize a
    booking change, but the workflow draft decides whether to create or move
    a booking.

    The `last_*` fields hold results for the HTTP response. A new management
    token is returned to the web page after booking creation; it is not saved
    in workflow state or sent to the model, logs, or traces.
    """

    def __init__(
        self,
        runtime: RuntimeContext,
        request: ChatRequest,
        token: str | None,
        customer: CustomerInput | None,
        customer_id: str | None,
        verified_phone: str | None = None,
    ):
        self.runtime = runtime
        self.request = request
        self.token = token
        self.customer = customer
        self.customer_id = customer_id
        self.verified_phone = verified_phone
        self.business_id = request.business_id
        self.last_slots: list[AvailabilitySlot] = []
        self.last_booking: Booking | None = None
        self.last_manage_token: str | None = None
        self._ready = customer is not None
        store = getattr(runtime, "chat_store", None)
        self.store = (
            store
            if store is not None and hasattr(store, "load_draft")
            else MemoryConversationStore(runtime)
        )

    def catalog(self, business_id: str) -> CatalogResponse:
        result = self.runtime.repository.catalog(business_id)
        if result is None:
            raise DomainError("business_not_found", "Business not found.", 404)
        return result

    def load_draft(self, conversation_id: str) -> WorkflowDraft:
        return self.store.load_draft(conversation_id)

    def save_draft(self, conversation_id: str, draft: WorkflowDraft) -> None:
        self.store.save_draft(conversation_id, draft)

    def load_slots(self, conversation_id: str) -> list[AvailabilitySlot]:
        return self.store.load_slots(conversation_id)

    def save_slots(self, conversation_id: str, slots: list[AvailabilitySlot]) -> None:
        self.last_slots = list(slots)
        self.store.save_slots(conversation_id, slots)

    def search(self, request: AvailabilitySearchRequest) -> list[AvailabilitySlot]:
        slots = self.runtime.availability.search(request)
        self.last_slots = list(slots)
        return slots

    def capture_customer(self, name: str | None, phone: str | None) -> bool:
        if self.customer is not None:
            return True
        trusted_phone = self.verified_phone or phone
        if name and trusted_phone:
            try:
                self.customer = CustomerInput(full_name=name, phone=trusted_phone)
            except ValueError:
                return False
            self._ready = True
            return True
        return self._ready

    def customer_ready(self) -> bool:
        return self._ready

    def create_booking(self, slot: AvailabilitySlot, idempotency_key: str):
        if self.customer is None:
            raise DomainError(
                "customer_required", "Customer details are required.", 422
            )
        result = self.runtime.bookings.create(
            BookingCreateRequest(
                business_id=slot.business_id,
                service_id=slot.service_id,
                professional_id=slot.professional_id,
                slot_id=slot.slot_id,
                starts_at=slot.starts_at,
                customer=self.customer,
            ),
            idempotency_key,
        )
        self.last_booking = result.booking
        self.last_manage_token = result.manage_token
        log_event(
            self.runtime.logger,
            "chat.booking",
            session_id=self.request.session_id,
            intent="create",
            booking_id=result.booking.id,
        )
        trace_event(
            self.runtime.tracer,
            self.request.session_id,
            "chat.booking",
            {"intent": "create", "booking_id": result.booking.id},
        )
        return result

    def confirm(self, booking_id: str, idempotency_key: str):
        if self.token:
            booking, outcome = self.runtime.bookings.confirm(
                self.token, idempotency_key
            )
        else:
            booking, outcome = self.runtime.bookings.confirm_for_customer(
                self.business_id, self.customer_id, booking_id, idempotency_key
            )
        self.last_booking = booking
        self._trace_manage("confirm", outcome)
        return booking, outcome

    def cancel(self, booking_id: str, idempotency_key: str):
        if self.token:
            booking, outcome = self.runtime.bookings.cancel(self.token, idempotency_key)
        else:
            booking, outcome = self.runtime.bookings.cancel_for_customer(
                self.business_id, self.customer_id, booking_id, idempotency_key
            )
        self.last_booking = booking
        self._trace_manage("cancel", outcome)
        return booking, outcome

    def reschedule(self, booking_id: str, slot: AvailabilitySlot, idempotency_key: str):
        payload = BookingRescheduleRequest(
            business_id=slot.business_id,
            service_id=slot.service_id,
            professional_id=slot.professional_id,
            slot_id=slot.slot_id,
            starts_at=slot.starts_at,
        )
        if self.token:
            booking, outcome = self.runtime.bookings.reschedule(
                self.token, payload, idempotency_key
            )
        else:
            booking, outcome = self.runtime.bookings.reschedule_for_customer(
                self.business_id, self.customer_id, booking_id, payload, idempotency_key
            )
        self.last_booking = booking
        self._trace_manage("reschedule", outcome)
        return booking, outcome

    def list_booking_refs(self) -> list[BookingRef]:
        bookings = []
        if self.customer_id and hasattr(
            self.runtime.repository, "list_customer_bookings"
        ):
            bookings = self.runtime.repository.list_customer_bookings(
                self.business_id, self.customer_id
            )
        elif getattr(self, "authorized_booking", None) is not None:
            bookings = [self.authorized_booking]
        return [
            BookingRef(
                id=item.id, service_id=item.service_id, label=item.starts_at.isoformat()
            )
            for item in bookings
        ]

    def messages(self, conversation_id: str) -> list[dict[str, str]]:
        if hasattr(self.store, "get_messages"):
            return self.store.get_messages(conversation_id)
        return self.store.messages(conversation_id)

    def record_escalation(self, conversation_id: str) -> None:
        log_event(self.runtime.logger, "chat.escalate", session_id=conversation_id)

    def remember_extraction(self, extraction) -> None:
        _ = extraction

    def _trace_manage(self, intent: str, outcome: str) -> None:
        log_event(
            self.runtime.logger,
            "chat.manage",
            session_id=self.request.session_id,
            intent=intent,
            outcome=outcome,
        )
        trace_event(
            self.runtime.tracer,
            self.request.session_id,
            "chat.manage",
            {"intent": intent, "outcome": outcome},
        )

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from app.graph.contracts import BookingRef, TurnContext, WorkflowDraft
from app.graph.memory import MemoryConversationStore
from app.models import (
    AvailabilitySearchRequest,
    AvailabilitySlot,
    Booking,
    BookingCreateRequest,
    BookingRescheduleRequest,
    CatalogResponse,
    ChatRequest,
    ChatResponse,
    CustomerInput,
    DomainError,
)
from app.observability import log_event, trace_event
from app.runtime import RuntimeContext

_TOKEN_PATTERN = re.compile(r"\bbk1\.[0-9a-fA-F-]{36}\.\d+\.[0-9a-f]{64}\b")


def redact_token(message: str) -> str:
    """Hide a management token that the customer pasted into the message.

    The real token, when present, arrives in a separate request field. The
    interpreter and the stored history must only see this redacted text.
    """
    return _TOKEN_PATTERN.sub("[redacted-token]", message)


class ChatPorts:
    """Request-scoped tools the workflow graph can call for one chat turn.

    The graph decides which step to run. This object performs the step without
    letting the graph see secrets or invent booking policy. A new instance is
    built for every message and discarded when the turn ends.

    The web management token and the customer name and phone stay on this
    object. The graph receives only a booking id, a customer id, or neither.
    Those identifiers authorize the caller. They do not choose create versus
    reschedule. That choice lives in the scheduling draft stored by the
    LangGraph checkpoint.

    `token` is the private web capability, when the browser sent one in the
    separate `manage_token` field. `customer_id` is set for a verified channel
    such as WhatsApp. A turn has one of those, not both, or neither when the
    caller is anonymous. `customer` is the contact data used to create a
    booking. It may come from the request or from fields extracted later in
    the turn. It is never written into graph state.

    The methods the graph calls are the domain operations: load the catalog,
    search availability, create, confirm, cancel, reschedule, and list the
    bookings this caller owns. Availability and overlap checks still happen
    inside the scheduling and booking services. This class only forwards the
    call and keeps the result for the HTTP response.

    `last_slots`, `last_booking`, and `last_manage_token` are the outcome of
    this turn for the trusted web caller. The management token is returned to
    the page after a booking is created. It is not stored in the checkpoint,
    the model prompt, logs, or traces.
    """
    def __init__(
        self,
        runtime: RuntimeContext,
        request: ChatRequest,
        token: str | None,
        customer: CustomerInput | None,
        customer_id: str | None,
    ):
        self.runtime = runtime
        self.request = request
        self.token = token
        self.customer = customer
        self.customer_id = customer_id
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
        if name and phone:
            try:
                self.customer = CustomerInput(full_name=name, phone=phone)
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


def local_today(catalog: CatalogResponse):
    """Return today's date in the business timezone.

    Relative phrases such as "tomorrow" are resolved against this date, not
    against the server's local clock.
    """
    return datetime.now(ZoneInfo(catalog.business.timezone)).date()


def _require_catalog(runtime: RuntimeContext, request: ChatRequest) -> CatalogResponse:
    """Load the catalog for a session that already exists.

    An unknown session or business stops the turn before the graph runs.
    The catalog supplies service names and the business timezone.
    """
    runtime.require_session(request.session_id)
    catalog = runtime.repository.catalog(request.business_id)
    if catalog is None:
        raise DomainError("business_not_found", "Business not found.", 404)
    return catalog


def _authorize_web_booking(runtime: RuntimeContext, token: str | None):
    """Turn a web management token into a booking id.

    The token proves that this browser may manage one booking. It is checked
    here and is not passed into the graph. No token means an anonymous turn.
    """
    if not token:
        return None, None
    booking = runtime.bookings.read(token)
    return booking.id, booking


def _build_chat_ports(
    runtime: RuntimeContext,
    request: ChatRequest,
    safe_message: str,
    token: str | None,
    authorized_booking,
) -> ChatPorts:
    """Build the request-scoped tools the graph is allowed to call.

    Customer details and the raw token stay on this object. The graph receives
    only the ports, so it can book or manage without seeing those secrets.
    """
    ports = ChatPorts(
        runtime,
        request.model_copy(update={"message": safe_message}),
        token,
        request.customer,
        None,
    )
    ports.authorized_booking = authorized_booking
    return ports


def _record_safe_message(runtime: RuntimeContext, safe_message: str) -> None:
    """Remember the redacted message when a test workflow records inputs.

    Production workflows do not have this list. Tests use it to prove that the
    raw management token never reached interpretation.
    """
    messages = getattr(getattr(runtime, "workflow", None), "messages", None)
    if isinstance(messages, list):
        messages.append(safe_message)


def _run_workflow_turn(
    runtime: RuntimeContext,
    request: ChatRequest,
    catalog: CatalogResponse,
    safe_message: str,
    ports: ChatPorts,
    booking_id: str | None,
    idempotency_key: str | None,
):
    """Ask the graph to handle one turn and return its channel-neutral result.

    The graph chooses the step. This function only supplies the already
    authorized booking id, the selected slot, and whether customer details exist.
    """
    return runtime.workflow.handle_turn(
        TurnContext(
            conversation_id=request.session_id,
            business_id=request.business_id,
            message=safe_message,
            today=local_today(catalog).isoformat(),
            selected_slot_id=request.selected_slot_id,
            auth_kind="booking" if booking_id else "anonymous",
            booking_id=booking_id,
            customer_ready=request.customer is not None,
            idempotency_key=idempotency_key,
        ),
        ports,
    )


def _to_chat_response(session_id: str, result) -> ChatResponse:
    """Map the graph result back to the HTTP chat contract.

    The management token is included only for the trusted web caller. It is
    not part of the saved draft or the model input.
    """
    return ChatResponse(
        session_id=session_id,
        response=result.text,
        intent=result.intent,
        slots=result.slots,
        booking=result.booking,
        manage_token=result.manage_token,
    )


def handle_chat(
    runtime: RuntimeContext, request: ChatRequest, idempotency_key: str | None
) -> ChatResponse:
    """Run one web chat turn without choosing the conversation step.

    The route validates HTTP input and calls this adapter. The adapter prepares
    a safe turn, the workflow graph decides what happens, and this function
    returns the existing chat response.
    """
    catalog = _require_catalog(runtime, request)
    safe_message = redact_token(request.message)
    booking_id, authorized_booking = _authorize_web_booking(
        runtime, request.manage_token
    )
    ports = _build_chat_ports(
        runtime, request, safe_message, request.manage_token, authorized_booking
    )
    _record_safe_message(runtime, safe_message)
    result = _run_workflow_turn(
        runtime, request, catalog, safe_message, ports, booking_id, idempotency_key
    )
    return _to_chat_response(request.session_id, result)


def conversation_id_for_sender(business_id: str, channel: str, sender: str) -> str:
    digest = hashlib.sha256(f"{business_id}:{channel}:{sender}".encode()).hexdigest()
    return digest[:32]

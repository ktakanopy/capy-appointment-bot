from __future__ import annotations

import hashlib
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from app.chat_ports import ChatPorts
from app.graph.contracts import TurnContext
from app.models import (
    CatalogResponse,
    ChatRequest,
    ChatResponse,
    DomainError,
)
from app.runtime import RuntimeContext

_TOKEN_PATTERN = re.compile(r"\bbk1\.[0-9a-fA-F-]{36}\.\d+\.[0-9a-f]{64}\b")


def redact_token(message: str) -> str:
    """Hide a management token that the customer pasted into the message.

    The real token, when present, arrives in a separate request field. The
    interpreter and the stored history must only see this redacted text.
    """
    return _TOKEN_PATTERN.sub("[redacted-token]", message)

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
            auth_kind="booking"
            if booking_id
            else "anonymous",  # TODO: check why we neeed this
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

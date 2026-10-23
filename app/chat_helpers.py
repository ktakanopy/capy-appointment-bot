from __future__ import annotations

import hashlib
from datetime import date, time
from typing import TYPE_CHECKING

from app.llm.schemas import SchedulingExtraction
from app.models import (
    AvailabilitySearchRequest,
    AvailabilitySlot,
    Booking,
    CatalogResponse,
    ChatRequest,
    ChatResponse,
    CustomerInput,
)
from app.observability import log_event, trace_event

if TYPE_CHECKING:
    from app.runtime import RuntimeContext


def _request_key(header_key: str | None, seed: str) -> str:
    """Use the supplied idempotency key or derive a stable key for chat actions."""
    return header_key or "chat-" + hashlib.sha256(seed.encode()).hexdigest()[:48]


def _choose_service(catalog: CatalogResponse, phrase: str | None):
    """Resolve an exact service name from this business's catalog."""
    if not phrase:
        return catalog.services[0] if len(catalog.services) == 1 else None
    folded = " ".join(phrase.split()).casefold()
    return next(
        (
            service
            for service in catalog.services
            if " ".join(service.name.split()).casefold() == folded
        ),
        None,
    )


def _choose_professional(catalog: CatalogResponse, phrase: str | None):
    """Resolve an exact professional name, returning ``None`` if it is unknown."""
    if not phrase:
        return None
    folded = " ".join(phrase.split()).casefold()
    return next(
        (
            person
            for person in catalog.professionals
            if " ".join(person.name.split()).casefold() == folded
        ),
        None,
    )


def _canonicalize_catalog_entities(
    catalog: CatalogResponse,
    extraction: SchedulingExtraction,
) -> SchedulingExtraction:
    """Replace recognized service and professional phrases with catalog names.

    Unknown phrases stay intact so intent handlers can ask the customer to clarify
    instead of silently choosing another service or professional.
    """
    values = extraction.model_dump()
    if extraction.service_name:
        service = _choose_service(catalog, extraction.service_name)
        if service is not None:
            values["service_name"] = service.name
    if extraction.professional_name:
        professional = _choose_professional(catalog, extraction.professional_name)
        if professional is not None:
            values["professional_name"] = professional.name
    return SchedulingExtraction.model_validate(values)


def _customer_from_request_or_extraction(
    request: ChatRequest,
    extraction: SchedulingExtraction,
) -> CustomerInput | None:
    """Prefer validated request details, then use valid name and phone extracted from chat."""
    if request.customer:
        return request.customer
    if extraction.customer_name and extraction.phone:
        try:
            return CustomerInput(full_name=extraction.customer_name, phone=extraction.phone)
        except ValueError:
            return None
    return None


def _slot_choice(
    runtime: RuntimeContext,
    session_id: str,
    selected_slot_id: str | None,
    index: int | None,
) -> AvailabilitySlot | None:
    """Resolve a slot ID or one-based reply number against the session's latest results."""
    slots = runtime.session_slots.get(session_id, [])
    if selected_slot_id:
        return next((slot for slot in slots if slot.slot_id == selected_slot_id), None)
    if index is not None and 1 <= index <= len(slots):
        return slots[index - 1]
    return None


def _slot_message(slots: list[AvailabilitySlot]) -> str:
    """Format search results as numbered choices, or explain when no times are available."""
    if not slots:
        return "I couldn't find an available time for that date. Try another date or professional."
    lines = [f"{index}. {slot.local_label} with {slot.professional_name}" for index, slot in enumerate(slots[:10], 1)]
    return "Here are the available times. Choose a time below or reply with its number:\n" + "\n".join(lines)


def _interpret_chat_message(
    runtime: RuntimeContext,
    session_id: str,
    catalog: CatalogResponse,
    local_today: date,
    safe_message: str,
) -> SchedulingExtraction:
    """Interpret a redacted chat message using business and session context.

    Use before intent dispatch so the workflow receives catalog names, the
    business-local date, and whether the session has previous search results.
    """
    previous_slots = runtime.session_slots.get(session_id, [])
    return runtime.workflow.interpret(
        session_id,
        safe_message,
        service_names=[service.name for service in catalog.services],
        professional_names=[professional.name for professional in catalog.professionals],
        today=local_today.isoformat(),
        has_slots=bool(previous_slots),
    )


def _chat_search_slots(
    runtime: RuntimeContext,
    request: ChatRequest,
    extraction: SchedulingExtraction,
    *,
    service_id: str,
    professional_id: str | None,
    requested_date: date,
) -> list[AvailabilitySlot]:
    """Search one service for a date, optionally filtering by professional and earliest time.

    Used for both new-booking searches and searches to reschedule an existing booking.
    """
    return runtime.availability.search(
        AvailabilitySearchRequest(
            business_id=request.business_id,
            service_id=service_id,
            professional_id=professional_id,
            start_date=requested_date,
            end_date=requested_date,
            earliest_local_time=time.fromisoformat(extraction.local_time) if extraction.local_time else None,
        )
    )


def _chat_management_response(
    runtime: RuntimeContext,
    request: ChatRequest,
    intent: str,
    updated_booking: Booking,
    action_outcome: str,
) -> ChatResponse:
    """Build and record the reply for a completed booking confirmation or cancellation.

    Use the service outcome to distinguish a new change from one already applied,
    and record the action without including the management token in logs or traces.
    """
    response_by_outcome = {
        "confirmed": "Your booking is confirmed.",
        "already_confirmed": "Your booking was already confirmed.",
        "canceled": "Your booking is canceled.",
    }
    response = response_by_outcome.get(action_outcome, "Your booking was already canceled.")
    log_event(runtime.logger, "chat.manage", session_id=request.session_id, intent=intent, outcome=action_outcome)
    trace_event(runtime.tracer, request.session_id, "chat.manage", {"intent": intent, "outcome": action_outcome})
    return ChatResponse(
        session_id=request.session_id,
        response=response,
        intent=intent,
        booking=updated_booking,
    )


def _handle_chat_management_fallback(request: ChatRequest) -> ChatResponse:
    """Explain how to manage a booking when an action cannot be applied yet.

    Used when the request lacks a management token or the requested change is unclear.
    """
    if not request.manage_token:
        return ChatResponse(
            session_id=request.session_id,
            response="To manage a booking, provide its private management token in the booking controls.",
            intent="manage_booking",
        )
    return ChatResponse(
        session_id=request.session_id,
        response="I couldn't identify the requested change. Please choose a booking action.",
        intent="clarify_management",
    )


def _handle_chat_help(request: ChatRequest, catalog: CatalogResponse) -> ChatResponse:
    """Answer a help intent with the supported tasks and this business's services."""
    service_names = ", ".join(item.name for item in catalog.services)
    return ChatResponse(
        session_id=request.session_id,
        response=f"I can help you find a time, book a service, or manage an existing booking. Services here: {service_names}.",
        intent="help",
    )


def _handle_chat_clarification(request: ChatRequest) -> ChatResponse:
    """Prompt for a service and date when no more specific intent can be handled."""
    return ChatResponse(
        session_id=request.session_id,
        response="Tell me which service you'd like and the date, or choose an action in the booking controls.",
        intent="clarify",
    )

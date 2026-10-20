from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.chat_helpers import (
    _chat_management_response,
    _chat_search_slots,
    _choose_professional,
    _choose_service,
    _customer_from_request_or_extraction,
    _handle_chat_clarification,
    _handle_chat_help,
    _handle_chat_management_fallback,
    _interpret_chat_message,
    _request_key,
    _slot_choice,
    _slot_message,
)
from app.database import database_is_ready
from app.llm.schemas import SchedulingExtraction
from app.models import (
    AvailabilityResponse,
    AvailabilitySearchRequest,
    AvailabilitySlot,
    BookingCreateRequest,
    BookingRescheduleRequest,
    BookingSummary,
    CatalogResponse,
    ChatRequest,
    ChatResponse,
    CustomerInput,
    DemoReminder,
    DemoRemindersResponse,
    DomainError,
    HealthResponse,
    OwnerBookingsResponse,
    SessionResponse,
)
from app.observability import log_event, trace_event
from app.runtime import (
    RuntimeContext,
    close_runtime,
    create_runtime,
)
from app.runtime import (
    reset_runtime as reset_runtime_context,
)

_TOKEN_PATTERN = re.compile(r"\bbk1\.[0-9a-fA-F-]{36}\.\d+\.[0-9a-f]{64}\b")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.runtime = create_runtime()
    yield
    close_runtime(getattr(app.state, "runtime", None))


app = FastAPI(
    title="General Service Scheduling API", version="1.0.0", lifespan=lifespan
)


def get_runtime(request: Request) -> RuntimeContext:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        runtime = create_runtime()
        request.app.state.runtime = runtime
    return runtime


def reset_runtime(target_app=None, settings=None) -> RuntimeContext:
    if target_app is None:
        target_app = app
    return reset_runtime_context(target_app, settings=settings)


def _catalog(runtime: RuntimeContext, business_id: str) -> CatalogResponse:
    result = runtime.repository.catalog(business_id)
    if result is None:
        raise DomainError("business_not_found", "Business not found.", 404)
    return result


def _local_today(catalog: CatalogResponse) -> date:
    """Get today's date in the business timezone for interpreting relative dates."""
    return datetime.now(ZoneInfo(catalog.business.timezone)).date()


def _handle_chat_search(
    runtime: RuntimeContext,
    request: ChatRequest,
    catalog: CatalogResponse,
    extraction: SchedulingExtraction,
) -> ChatResponse:
    """Handle a new-booking search intent by resolving catalog names and finding times.

    If the service, date, or requested professional is missing or unrecognized,
    ask the customer to clarify. Otherwise save the matching slots in the session
    and return them as choices for a later selection message.
    """
    service = _choose_service(catalog, extraction.service_name)
    if service is None:
        names = ", ".join(item.name for item in catalog.services)
        return ChatResponse(
            session_id=request.session_id,
            response=f"Which service would you like? Available services: {names}.",
            intent="clarify_service",
        )

    requested_date = (
        date.fromisoformat(extraction.local_date) if extraction.local_date else None
    )
    if not requested_date:
        return ChatResponse(
            session_id=request.session_id,
            response=f"What date would you like to book {service.name}? You can say today, tomorrow, or give a date.",
            intent="clarify_date",
        )

    professional = _choose_professional(catalog, extraction.professional_name)
    if extraction.professional_name and professional is None:
        names = ", ".join(item.name for item in catalog.professionals)
        return ChatResponse(
            session_id=request.session_id,
            response=f"I couldn't find that professional. Choose from: {names}.",
            intent="clarify_professional",
        )

    slots = _chat_search_slots(
        runtime,
        request,
        extraction,
        service_id=service.id,
        professional_id=professional.id if professional else None,
        requested_date=requested_date,
    )
    runtime.session_slots[request.session_id] = slots
    runtime.pending_slots.pop(request.session_id, None)
    log_event(
        runtime.logger,
        "chat.search",
        session_id=request.session_id,
        intent="search",
        slot_count=len(slots),
        service_id=service.id,
    )
    trace_event(
        runtime.tracer,
        request.session_id,
        "chat.search",
        {"intent": "search", "slot_count": len(slots), "service_id": service.id},
    )
    return ChatResponse(
        session_id=request.session_id,
        response=_slot_message(slots),
        intent="search",
        slots=slots,
    )


def _handle_chat_slot_selection(
    runtime: RuntimeContext,
    request: ChatRequest,
    slot: AvailabilitySlot,
    customer: CustomerInput | None,
    idempotency_key: str | None,
) -> ChatResponse:
    """Process a selected slot as a reschedule or a new booking.

    Use this when the customer selects a search result, or when they provide
    customer details after a slot was saved as pending. With a management token,
    move the existing booking to this slot and clear the pending slot. Without a
    token, save the slot and ask for details when they are missing; once details
    are available, create the booking, clear the pending slot, and return its
    management token. Saving a pending slot remembers the choice but does not
    reserve the time.
    """
    if request.manage_token:
        # The management token is only used for this booking service call.
        reschedule_request = BookingRescheduleRequest(
            business_id=slot.business_id,
            service_id=slot.service_id,
            professional_id=slot.professional_id,
            slot_id=slot.slot_id,
            starts_at=slot.starts_at,
        )
        key = _request_key(
            idempotency_key, f"{request.session_id}:{slot.slot_id}:reschedule"
        )
        updated, _outcome = runtime.bookings.reschedule(
            request.manage_token, reschedule_request, key
        )
        runtime.pending_slots.pop(request.session_id, None)
        return ChatResponse(
            session_id=request.session_id,
            response="Your booking has been moved.",
            intent="reschedule",
            booking=updated,
        )

    if not customer:
        runtime.pending_slots[request.session_id] = slot
        return ChatResponse(
            session_id=request.session_id,
            response=f"I can hold this choice while we finish the demo flow. Please share your full name and phone number for {slot.local_label}.",
            intent="collect_customer",
        )

    booking_request = BookingCreateRequest(
        business_id=slot.business_id,
        service_id=slot.service_id,
        professional_id=slot.professional_id,
        slot_id=slot.slot_id,
        starts_at=slot.starts_at,
        customer=customer,
    )
    key = _request_key(
        idempotency_key, f"{request.session_id}:{slot.slot_id}:{customer.phone}"
    )
    result = runtime.bookings.create(booking_request, key)
    runtime.pending_slots.pop(request.session_id, None)
    log_event(
        runtime.logger,
        "chat.booking",
        session_id=request.session_id,
        intent="create",
        booking_id=result.booking.id,
    )
    trace_event(
        runtime.tracer,
        request.session_id,
        "chat.booking",
        {"intent": "create", "booking_id": result.booking.id},
    )
    return ChatResponse(
        session_id=request.session_id,
        response=f"You're booked for {slot.local_label} with {slot.professional_name}. Your private management token is shown in the booking details.",
        intent="create",
        booking=result.booking,
        manage_token=result.manage_token,
    )


def _handle_chat_confirm(
    runtime: RuntimeContext,
    request: ChatRequest,
    manage_token: str,
    idempotency_key: str | None,
    safe_message: str,
) -> ChatResponse:
    """Confirm a booking using the customer's management token and an idempotent key."""
    booking = runtime.bookings.read(manage_token)
    key = _request_key(
        idempotency_key, f"{request.session_id}:{booking.id}:confirm:{safe_message}"
    )
    updated_booking, action_outcome = runtime.bookings.confirm(manage_token, key)
    return _chat_management_response(
        runtime, request, "confirm", updated_booking, action_outcome
    )


def _handle_chat_cancel(
    runtime: RuntimeContext,
    request: ChatRequest,
    manage_token: str,
    idempotency_key: str | None,
    safe_message: str,
) -> ChatResponse:
    """Cancel a booking using the customer's management token and an idempotent key."""
    booking = runtime.bookings.read(manage_token)
    key = _request_key(
        idempotency_key, f"{request.session_id}:{booking.id}:cancel:{safe_message}"
    )
    updated_booking, action_outcome = runtime.bookings.cancel(manage_token, key)
    return _chat_management_response(
        runtime, request, "cancel", updated_booking, action_outcome
    )


def _handle_chat_reschedule(
    runtime: RuntimeContext,
    request: ChatRequest,
    extraction: SchedulingExtraction,
    manage_token: str,
) -> ChatResponse:
    """Start rescheduling an existing booking when no replacement slot is selected.

    Ask for a target date if the customer has not provided one; otherwise search
    that date for the existing service and save the results for slot selection.
    """
    current_booking = runtime.bookings.read(manage_token)
    requested_date = (
        date.fromisoformat(extraction.local_date) if extraction.local_date else None
    )
    if not requested_date:
        return ChatResponse(
            session_id=request.session_id,
            response="What date would you like to move the booking to?",
            intent="reschedule",
        )
    slots = _chat_search_slots(
        runtime,
        request,
        extraction,
        service_id=current_booking.service_id,
        professional_id=None,
        requested_date=requested_date,
    )
    runtime.session_slots[request.session_id] = slots
    return ChatResponse(
        session_id=request.session_id,
        response=_slot_message(slots),
        intent="reschedule",
        slots=slots,
    )


def _handle_chat_intent(
    runtime: RuntimeContext,
    request: ChatRequest,
    catalog: CatalogResponse,
    extraction: SchedulingExtraction,
    chosen_slot: AvailabilitySlot | None,
    customer: CustomerInput | None,
    idempotency_key: str | None,
    safe_message: str,
) -> ChatResponse:
    """Dispatch each chat intent to its specific booking, search, or reply handler.

    Management actions take precedence, followed by new availability searches
    and selected-slot processing. If no action applies, handle management prompts,
    help requests, or general clarification here.
    """
    manage_token = request.manage_token
    if manage_token and extraction.intent == "confirm":
        return _handle_chat_confirm(
            runtime,
            request,
            manage_token,
            idempotency_key,
            safe_message,
        )
    if manage_token and extraction.intent == "cancel":
        return _handle_chat_cancel(
            runtime,
            request,
            manage_token,
            idempotency_key,
            safe_message,
        )
    if (
        manage_token
        and extraction.intent == "reschedule"
        and not request.selected_slot_id
    ):
        return _handle_chat_reschedule(runtime, request, extraction, manage_token)

    if extraction.intent == "search":
        return _handle_chat_search(runtime, request, catalog, extraction)

    if chosen_slot or (runtime.pending_slots.get(request.session_id) and customer):
        slot = chosen_slot or runtime.pending_slots[request.session_id]
        return _handle_chat_slot_selection(
            runtime,
            request,
            slot,
            customer,
            idempotency_key,
        )

    if extraction.intent in {"confirm", "cancel", "reschedule"}:
        return _handle_chat_management_fallback(request)
    if extraction.intent == "help":
        return _handle_chat_help(request, catalog)
    return _handle_chat_clarification(request)


@app.get("/health", response_model=HealthResponse)
def health(runtime: RuntimeContext = Depends(get_runtime)):
    database_ok = database_is_ready(runtime.settings.database_url)
    return HealthResponse(
        status="ok" if database_ok else "degraded",
        database="ok" if database_ok else "unavailable",
    )


@app.post("/sessions/new", response_model=SessionResponse)
def create_session(runtime: RuntimeContext = Depends(get_runtime)):
    session_id, thread_id = runtime.create_session()
    return SessionResponse(
        session_id=session_id,
        thread_id=thread_id,
        response="Welcome. Tell me which service you'd like to book.",
    )


@app.get("/businesses/{business_id}/catalog", response_model=CatalogResponse)
def get_catalog(business_id: str, runtime: RuntimeContext = Depends(get_runtime)):
    return _catalog(runtime, business_id)


@app.post("/availability/search", response_model=AvailabilityResponse)
def search_availability(
    request: AvailabilitySearchRequest, runtime: RuntimeContext = Depends(get_runtime)
):
    return AvailabilityResponse(slots=runtime.availability.search(request))


@app.post("/bookings", response_model=BookingSummary)
def create_booking(
    request: BookingCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    runtime: RuntimeContext = Depends(get_runtime),
):
    if not idempotency_key:
        raise DomainError(
            "idempotency_key_required", "Idempotency-Key header is required.", 422
        )
    return runtime.bookings.create(request, idempotency_key)


@app.get("/bookings/me")
def get_my_booking(
    x_booking_token: str | None = Header(default=None, alias="X-Booking-Token"),
    runtime: RuntimeContext = Depends(get_runtime),
):
    if not x_booking_token:
        raise DomainError("booking_not_found", "Booking not found.", 404)
    return runtime.bookings.read(x_booking_token)


@app.post("/bookings/me/confirm")
def confirm_booking(
    x_booking_token: str | None = Header(default=None, alias="X-Booking-Token"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    runtime: RuntimeContext = Depends(get_runtime),
):
    return _manage_status(runtime, x_booking_token, idempotency_key, "confirm")


@app.post("/bookings/me/cancel")
def cancel_booking(
    x_booking_token: str | None = Header(default=None, alias="X-Booking-Token"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    runtime: RuntimeContext = Depends(get_runtime),
):
    return _manage_status(runtime, x_booking_token, idempotency_key, "cancel")


def _manage_status(
    runtime: RuntimeContext, token: str | None, key: str | None, operation: str
):
    if not token:
        raise DomainError("booking_not_found", "Booking not found.", 404)
    if not key:
        raise DomainError(
            "idempotency_key_required", "Idempotency-Key header is required.", 422
        )
    booking, outcome = getattr(runtime.bookings, operation)(token, key)
    return {"booking": booking, "outcome": outcome}


@app.post("/bookings/me/reschedule")
def reschedule_booking(
    request: BookingRescheduleRequest,
    x_booking_token: str | None = Header(default=None, alias="X-Booking-Token"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    runtime: RuntimeContext = Depends(get_runtime),
):
    if not x_booking_token:
        raise DomainError("booking_not_found", "Booking not found.", 404)
    if not idempotency_key:
        raise DomainError(
            "idempotency_key_required", "Idempotency-Key header is required.", 422
        )
    booking, outcome = runtime.bookings.reschedule(
        x_booking_token, request, idempotency_key
    )
    return {"booking": booking, "outcome": outcome}


@app.get("/owner/bookings", response_model=OwnerBookingsResponse)
def owner_bookings(
    business_id: str | None = Query(default=None),
    start_date: date | None = None,
    end_date: date | None = None,
    runtime: RuntimeContext = Depends(get_runtime),
):
    business_id = business_id or runtime.settings.demo_business_id
    catalog = _catalog(runtime, business_id)
    start = start_date or _local_today(catalog)
    end = end_date or (start + timedelta(days=7))
    if end < start or (end - start).days > 60:
        raise DomainError(
            "invalid_date_range", "Date range must be within 60 days.", 422
        )
    zone = ZoneInfo(catalog.business.timezone)
    start_at = datetime.combine(start, time.min, zone)
    end_at = datetime.combine(end + timedelta(days=1), time.min, zone)
    return OwnerBookingsResponse(
        business=catalog.business,
        bookings=runtime.repository.list_owner_bookings(business_id, start_at, end_at),
    )


@app.get("/demo/reminders", response_model=DemoRemindersResponse)
def demo_reminders(
    business_id: str | None = Query(default=None),
    runtime: RuntimeContext = Depends(get_runtime),
):
    business_id = business_id or runtime.settings.demo_business_id
    with runtime.repository.connection() as connection:
        rows = connection.execute(
            """SELECT i.id,i.reminder_id,i.booking_id,c.full_name AS customer_name,
                      s.name AS service_name,b.starts_at,i.message,i.delivered_at
               FROM reminder_inbox i JOIN bookings b ON b.id=i.booking_id
               JOIN customers c ON c.id=b.customer_id JOIN services s ON s.id=b.service_id
               WHERE i.business_id=%s ORDER BY i.delivered_at DESC LIMIT 100""",
            (business_id,),
        ).fetchall()
    return DemoRemindersResponse(reminders=[DemoReminder(**row) for row in rows])


@app.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    runtime: RuntimeContext = Depends(get_runtime),
):
    runtime.require_session(request.session_id)
    catalog = _catalog(runtime, request.business_id)
    local_today = _local_today(catalog)
    safe_message = _TOKEN_PATTERN.sub("[redacted-token]", request.message)
    extraction = _interpret_chat_message(
        runtime,
        request.session_id,
        catalog,
        local_today,
        safe_message,
    )
    chosen_slot = _slot_choice(
        runtime,
        request.session_id,
        request.selected_slot_id,
        extraction.slot_index,
    )
    customer = _customer_from_request_or_extraction(request, extraction)

    return _handle_chat_intent(
        runtime,
        request,
        catalog,
        extraction,
        chosen_slot,
        customer,
        idempotency_key,
        safe_message,
    )


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError):
    _ = request
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    _ = request
    _ = exc
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "code": "invalid_request",
                "message": "The request did not match the API contract.",
            }
        },
    )

from __future__ import annotations

import hashlib
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.database import database_is_ready
from app.models import (
    AvailabilityResponse,
    AvailabilitySearchRequest,
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
from app.runtime import RuntimeContext, close_runtime, create_runtime, reset_runtime as reset_runtime_context


_TOKEN_PATTERN = re.compile(r"\bbk1\.[0-9a-fA-F-]{36}\.\d+\.[0-9a-f]{64}\b")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.runtime = create_runtime()
    yield
    close_runtime(getattr(app.state, "runtime", None))


app = FastAPI(title="General Service Scheduling API", version="1.0.0", lifespan=lifespan)


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


def _request_key(header_key: str | None, seed: str) -> str:
    return header_key or "chat-" + hashlib.sha256(seed.encode()).hexdigest()[:48]


def _choose_service(catalog: CatalogResponse, phrase: str | None):
    if not phrase:
        return catalog.services[0] if len(catalog.services) == 1 else None
    folded = phrase.casefold()
    return next((service for service in catalog.services if folded in service.name.casefold() or service.name.casefold() in folded), None)


def _choose_professional(catalog: CatalogResponse, phrase: str | None):
    if not phrase:
        return None
    folded = phrase.casefold()
    return next((person for person in catalog.professionals if folded in person.name.casefold()), None)


def _local_today(catalog: CatalogResponse) -> date:
    return datetime.now(ZoneInfo(catalog.business.timezone)).date()


def _slots_response(slots, intent: str, message: str, session_id: str) -> ChatResponse:
    return ChatResponse(session_id=session_id, response=message, intent=intent, slots=slots)


def _customer_from_request_or_extraction(request: ChatRequest, extraction) -> CustomerInput | None:
    if request.customer:
        return request.customer
    if extraction.customer_name and extraction.phone:
        try:
            return CustomerInput(full_name=extraction.customer_name, phone=extraction.phone)
        except ValueError:
            return None
    return None


def _slot_choice(runtime: RuntimeContext, session_id: str, selected_slot_id: str | None, index: int | None):
    slots = runtime.session_slots.get(session_id, [])
    if selected_slot_id:
        return next((slot for slot in slots if slot.slot_id == selected_slot_id), None)
    if index is not None and 1 <= index <= len(slots):
        return slots[index - 1]
    return None


def _slot_message(slots) -> str:
    if not slots:
        return "I couldn't find an available time for that date. Try another date or professional."
    lines = [f"{index}. {slot.local_label} with {slot.professional_name}" for index, slot in enumerate(slots[:10], 1)]
    return "Here are the available times. Choose a time below or reply with its number:\n" + "\n".join(lines)


@app.get("/health", response_model=HealthResponse)
def health(runtime: RuntimeContext = Depends(get_runtime)):
    database_ok = database_is_ready(runtime.settings.database_url)
    return HealthResponse(status="ok" if database_ok else "degraded", database="ok" if database_ok else "unavailable")


@app.post("/sessions/new", response_model=SessionResponse)
def create_session(runtime: RuntimeContext = Depends(get_runtime)):
    session_id, thread_id = runtime.create_session()
    return SessionResponse(session_id=session_id, thread_id=thread_id, response="Welcome. Tell me which service you'd like to book.")


@app.get("/businesses/{business_id}/catalog", response_model=CatalogResponse)
def get_catalog(business_id: str, runtime: RuntimeContext = Depends(get_runtime)):
    return _catalog(runtime, business_id)


@app.post("/availability/search", response_model=AvailabilityResponse)
def search_availability(request: AvailabilitySearchRequest, runtime: RuntimeContext = Depends(get_runtime)):
    return AvailabilityResponse(slots=runtime.availability.search(request))


@app.post("/bookings", response_model=BookingSummary)
def create_booking(
    request: BookingCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    runtime: RuntimeContext = Depends(get_runtime),
):
    if not idempotency_key:
        raise DomainError("idempotency_key_required", "Idempotency-Key header is required.", 422)
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


def _manage_status(runtime: RuntimeContext, token: str | None, key: str | None, operation: str):
    if not token:
        raise DomainError("booking_not_found", "Booking not found.", 404)
    if not key:
        raise DomainError("idempotency_key_required", "Idempotency-Key header is required.", 422)
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
        raise DomainError("idempotency_key_required", "Idempotency-Key header is required.", 422)
    booking, outcome = runtime.bookings.reschedule(x_booking_token, request, idempotency_key)
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
        raise DomainError("invalid_date_range", "Date range must be within 60 days.", 422)
    zone = ZoneInfo(catalog.business.timezone)
    start_at = datetime.combine(start, time.min, zone)
    end_at = datetime.combine(end + timedelta(days=1), time.min, zone)
    return OwnerBookingsResponse(
        business=catalog.business,
        bookings=runtime.repository.list_owner_bookings(business_id, start_at, end_at),
    )


@app.get("/demo/reminders", response_model=DemoRemindersResponse)
def demo_reminders(business_id: str | None = Query(default=None), runtime: RuntimeContext = Depends(get_runtime)):
    business_id = business_id or runtime.settings.demo_business_id
    with runtime.repository.connection() as connection:
        rows = connection.execute(
            """SELECT i.id,i.reminder_id,i.booking_id,c.full_name,s.name AS service_name,b.starts_at,i.message,i.delivered_at
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
    previous_slots = runtime.session_slots.get(request.session_id, [])
    extraction = runtime.workflow.interpret(
        request.session_id,
        safe_message,
        service_names=[service.name for service in catalog.services],
        professional_names=[professional.name for professional in catalog.professionals],
        today=local_today.isoformat(),
        has_slots=bool(previous_slots),
    )
    # The token remains in this route's local scope. It is never passed to LangGraph, the interpreter, logs, or traces.
    manage_token = request.manage_token
    selected_id = request.selected_slot_id
    chosen_slot = _slot_choice(runtime, request.session_id, selected_id, extraction.slot_index)
    customer = _customer_from_request_or_extraction(request, extraction)

    if manage_token and extraction.intent in {"confirm", "cancel"}:
        booking = runtime.bookings.read(manage_token)
        key = _request_key(idempotency_key, f"{request.session_id}:{booking.id}:{extraction.intent}:{safe_message}")
        updated, outcome = getattr(runtime.bookings, extraction.intent)(manage_token, key)
        response = "Your booking is confirmed." if outcome == "confirmed" else "Your booking was already confirmed." if outcome == "already_confirmed" else "Your booking is canceled." if outcome == "canceled" else "Your booking was already canceled."
        log_event(runtime.logger, "chat.manage", session_id=request.session_id, intent=extraction.intent, outcome=outcome)
        trace_event(runtime.tracer, request.session_id, "chat.manage", {"intent": extraction.intent, "outcome": outcome})
        return ChatResponse(session_id=request.session_id, response=response, intent=extraction.intent, booking=updated)

    if manage_token and extraction.intent == "reschedule" and not selected_id:
        current = runtime.bookings.read(manage_token)
        requested_date = date.fromisoformat(extraction.local_date) if extraction.local_date else None
        if not requested_date:
            return ChatResponse(session_id=request.session_id, response="What date would you like to move the booking to?", intent="reschedule")
        slots = runtime.availability.search(AvailabilitySearchRequest(
            business_id=request.business_id,
            service_id=current.service_id,
            professional_id=None,
            start_date=requested_date,
            end_date=requested_date,
            earliest_local_time=time.fromisoformat(extraction.local_time) if extraction.local_time else None,
        ))
        runtime.session_slots[request.session_id] = slots
        return _slots_response(slots, "reschedule", _slot_message(slots), request.session_id)

    if extraction.intent == "search":
        service = _choose_service(catalog, extraction.service_name)
        if service is None:
            names = ", ".join(item.name for item in catalog.services)
            return ChatResponse(session_id=request.session_id, response=f"Which service would you like? Available services: {names}.", intent="clarify_service")
        requested_date = date.fromisoformat(extraction.local_date) if extraction.local_date else None
        if not requested_date:
            return ChatResponse(session_id=request.session_id, response=f"What date would you like to book {service.name}? You can say today, tomorrow, or give a date.", intent="clarify_date")
        professional = _choose_professional(catalog, extraction.professional_name)
        if extraction.professional_name and professional is None:
            names = ", ".join(item.name for item in catalog.professionals)
            return ChatResponse(session_id=request.session_id, response=f"I couldn't find that professional. Choose from: {names}.", intent="clarify_professional")
        slots = runtime.availability.search(AvailabilitySearchRequest(
            business_id=request.business_id,
            service_id=service.id,
            professional_id=professional.id if professional else None,
            start_date=requested_date,
            end_date=requested_date,
            earliest_local_time=time.fromisoformat(extraction.local_time) if extraction.local_time else None,
        ))
        runtime.session_slots[request.session_id] = slots
        runtime.pending_slots.pop(request.session_id, None)
        log_event(runtime.logger, "chat.search", session_id=request.session_id, intent="search", slot_count=len(slots), service_id=service.id)
        trace_event(runtime.tracer, request.session_id, "chat.search", {"intent": "search", "slot_count": len(slots), "service_id": service.id})
        return _slots_response(slots, "search", _slot_message(slots), request.session_id)

    if chosen_slot or (runtime.pending_slots.get(request.session_id) and customer):
        slot = chosen_slot or runtime.pending_slots[request.session_id]
        if manage_token:
            # Management tokens travel only in this request and the application service call.
            reschedule_request = BookingRescheduleRequest(
                business_id=slot.business_id,
                service_id=slot.service_id,
                professional_id=slot.professional_id,
                slot_id=slot.slot_id,
                starts_at=slot.starts_at,
            )
            key = _request_key(idempotency_key, f"{request.session_id}:{slot.slot_id}:reschedule")
            updated, _outcome = runtime.bookings.reschedule(manage_token, reschedule_request, key)
            runtime.pending_slots.pop(request.session_id, None)
            return ChatResponse(session_id=request.session_id, response="Your booking has been moved.", intent="reschedule", booking=updated)
        if not customer:
            runtime.pending_slots[request.session_id] = slot
            return ChatResponse(session_id=request.session_id, response=f"I can hold this choice while we finish the demo flow. Please share your full name and phone number for {slot.local_label}.", intent="collect_customer")
        booking_request = BookingCreateRequest(
            business_id=slot.business_id,
            service_id=slot.service_id,
            professional_id=slot.professional_id,
            slot_id=slot.slot_id,
            starts_at=slot.starts_at,
            customer=customer,
        )
        key = _request_key(idempotency_key, f"{request.session_id}:{slot.slot_id}:{customer.phone}")
        result = runtime.bookings.create(booking_request, key)
        runtime.pending_slots.pop(request.session_id, None)
        log_event(runtime.logger, "chat.booking", session_id=request.session_id, intent="create", booking_id=result.booking.id)
        trace_event(runtime.tracer, request.session_id, "chat.booking", {"intent": "create", "booking_id": result.booking.id})
        return ChatResponse(
            session_id=request.session_id,
            response=f"You're booked for {slot.local_label} with {slot.professional_name}. Your private management token is shown in the booking details.",
            intent="create",
            booking=result.booking,
            manage_token=result.manage_token,
        )

    if extraction.intent in {"confirm", "cancel", "reschedule"}:
        if not manage_token:
            return ChatResponse(session_id=request.session_id, response="To manage a booking, provide its private management token in the booking controls.", intent="manage_booking")
        return ChatResponse(session_id=request.session_id, response="I couldn't identify the requested change. Please choose a booking action.", intent="clarify_management")

    if extraction.intent == "help":
        service_names = ", ".join(item.name for item in catalog.services)
        return ChatResponse(session_id=request.session_id, response=f"I can help you find a time, book a service, or manage an existing booking. Services here: {service_names}.", intent="help")
    return ChatResponse(
        session_id=request.session_id,
        response="Tell me which service you'd like and the date, or choose an action in the booking controls.",
        intent="clarify",
    )


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError):
    _ = request
    return JSONResponse(status_code=exc.status_code, content={"detail": {"code": exc.code, "message": exc.message}})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    _ = request
    _ = exc
    return JSONResponse(status_code=422, content={"detail": {"code": "invalid_request", "message": "The request did not match the API contract."}})

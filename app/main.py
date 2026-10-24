from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse

from app.channels.whatsapp import process_inbound, verify_subscription
from app.chat_adapter import handle_chat
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
    DemoReminder,
    DemoRemindersResponse,
    DomainError,
    HealthResponse,
    OwnerBookingsResponse,
    SessionResponse,
)
from app.runtime import (
    RuntimeContext,
    close_runtime,
    create_runtime,
)
from app.runtime import (
    reset_runtime as reset_runtime_context,
)


class _SuppressHealthAccess(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if (
            isinstance(record.args, tuple)
            and len(record.args) >= 3
            and str(record.args[2]).split("?", 1)[0] == "/health"
        ):
            return False
        return '"GET /health ' not in record.getMessage()


def _silence_health_access_logs() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, _SuppressHealthAccess) for item in logger.filters):
        logger.addFilter(_SuppressHealthAccess())


_silence_health_access_logs()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _silence_health_access_logs()
    if os.getenv("SKIP_RUNTIME_STARTUP") != "1":
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


@app.get("/health", response_model=HealthResponse)
def health(runtime: RuntimeContext = Depends(get_runtime)):
    database_ok = database_is_ready(runtime.settings.database_url)
    try:
        chat_history_ok = runtime.chat_store.ping()
    except Exception:
        chat_history_ok = False
    return HealthResponse(
        status="ok" if database_ok and chat_history_ok else "degraded",
        database="ok" if database_ok else "unavailable",
        chat_history="ok" if chat_history_ok else "unavailable",
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


def require_owner(
    x_owner_token: str | None = Header(default=None, alias="X-Owner-Token"),
    runtime: RuntimeContext = Depends(get_runtime),
) -> None:
    expected = getattr(runtime.settings, "owner_api_token", None)
    if not expected:
        return
    if not x_owner_token or not secrets.compare_digest(x_owner_token, expected):
        raise DomainError("unauthorized", "Owner authorization is required.", 401)


@app.get("/owner/bookings", response_model=OwnerBookingsResponse)
def owner_bookings(
    business_id: str | None = Query(default=None),
    start_date: date | None = None,
    end_date: date | None = None,
    runtime: RuntimeContext = Depends(get_runtime),
    _owner: None = Depends(require_owner),
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
    _owner: None = Depends(require_owner),
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


@app.get("/webhooks/whatsapp")
def whatsapp_verify(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    runtime: RuntimeContext = Depends(get_runtime),
):
    challenge = verify_subscription(
        hub_mode,
        hub_verify_token,
        hub_challenge,
        getattr(runtime.settings, "whatsapp_verify_token", None),
    )
    if challenge is None:
        raise DomainError("invalid_subscription", "Webhook verification failed.", 403)
    return PlainTextResponse(challenge)


@app.post("/webhooks/whatsapp")
async def whatsapp_inbound(
    request: Request, runtime: RuntimeContext = Depends(get_runtime)
):
    status, payload = process_inbound(
        runtime, await request.body(), request.headers.get("x-hub-signature-256")
    )
    return JSONResponse(
        status_code=status,
        content=payload if isinstance(payload, dict) else {"received": payload},
    )


@app.patch("/owner/settings")
def update_owner_settings(
    reminder_lead_minutes: int = Query(ge=0, le=10080),
    business_id: str | None = Query(default=None),
    runtime: RuntimeContext = Depends(get_runtime),
    _owner: None = Depends(require_owner),
):
    business_id = business_id or runtime.settings.demo_business_id
    if not hasattr(runtime.repository, "update_reminder_lead"):
        raise DomainError("not_supported", "Reminder settings cannot be updated.", 501)
    updated = runtime.repository.update_reminder_lead(
        business_id, reminder_lead_minutes
    )
    if updated is None:
        raise DomainError("business_not_found", "Business not found.", 404)
    return updated


@app.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    runtime: RuntimeContext = Depends(get_runtime),
):
    return handle_chat(runtime, request, idempotency_key)


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

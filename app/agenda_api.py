"""Expose the studio's existing scheduling services to an authorized caller."""

from datetime import date
from hmac import compare_digest
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError

from app.booking import BookingService
from app.config import Settings
from app.models import (
    AvailabilitySearchRequest, AvailabilitySlot, BookingCreateRequest,
    BookingRescheduleRequest, BookingSummary, CatalogResponse, DomainError,
    OwnerBookingsResponse,
)
from app.owner import owner_agenda
from app.repositories import PostgresRepository
from app.scheduling import AvailabilityService

router = APIRouter(prefix="/agenda", tags=["agenda"])
bearer = HTTPBearer(auto_error=False)


def authorize(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Settings:
    settings = request.app.state.settings
    token = settings.agenda_api_token
    if token is None or len(token.get_secret_value()) < 32:
        raise HTTPException(404, "Agenda API is disabled")
    if credentials is None or not compare_digest(
        credentials.credentials.encode(), token.get_secret_value().encode()
    ):
        raise HTTPException(401, "Invalid agenda credentials", headers={"WWW-Authenticate": "Bearer"})
    return settings


Agenda = Annotated[Settings, Depends(authorize)]
RetryKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)]


def catalog_for(repository: PostgresRepository, business_id: str) -> CatalogResponse:
    catalog = repository.catalog(business_id)
    if catalog is None:
        raise DomainError("business_not_found", "Business not found.", 404)
    return catalog


def owner_of(repository: PostgresRepository, business_id: str, booking_id: str) -> str:
    booking = repository.booking(booking_id, business_id=business_id)
    if booking is None:
        raise DomainError("booking_not_found", "Booking not found.", 404)
    return booking.customer.id


@router.get("/catalog")
def catalog(settings: Agenda) -> CatalogResponse:
    return catalog_for(PostgresRepository(settings.database_url), settings.demo_business_id)


@router.get("/slots")
def slots(settings: Agenda, service_id: str, start_date: date, end_date: date) -> list[AvailabilitySlot]:
    try:
        query = AvailabilitySearchRequest(
            business_id=settings.demo_business_id, service_id=service_id,
            start_date=start_date, end_date=end_date,
        )
    except ValidationError as exc:
        raise HTTPException(422, "Date range must be within 60 days") from exc
    return AvailabilityService(PostgresRepository(settings.database_url)).search(query)


@router.get("/bookings")
def bookings(
    settings: Agenda, start_date: date | None = None, end_date: date | None = None,
) -> OwnerBookingsResponse:
    repository = PostgresRepository(settings.database_url)
    return owner_agenda(
        repository, catalog_for(repository, settings.demo_business_id), start_date, end_date
    )


@router.post("/bookings")
def create(settings: Agenda, request: BookingCreateRequest, key: RetryKey) -> BookingSummary:
    request = request.model_copy(update={"business_id": settings.demo_business_id})
    return BookingService(PostgresRepository(settings.database_url)).create(request, key)


@router.post("/bookings/{booking_id}/cancel")
def cancel(settings: Agenda, booking_id: str, key: RetryKey) -> BookingSummary:
    repository = PostgresRepository(settings.database_url)
    customer_id = owner_of(repository, settings.demo_business_id, booking_id)
    booking, outcome = BookingService(repository).cancel(
        settings.demo_business_id, customer_id, booking_id, key
    )
    return BookingSummary(booking=booking, outcome=outcome)


@router.post("/bookings/{booking_id}/reschedule")
def reschedule(
    settings: Agenda, booking_id: str, request: BookingRescheduleRequest, key: RetryKey,
) -> BookingSummary:
    repository = PostgresRepository(settings.database_url)
    customer_id = owner_of(repository, settings.demo_business_id, booking_id)
    request = request.model_copy(update={"business_id": settings.demo_business_id})
    booking, outcome = BookingService(repository).reschedule(
        settings.demo_business_id, customer_id, booking_id, request, key
    )
    return BookingSummary(booking=booking, outcome=outcome)

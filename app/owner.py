from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.models import (
    CatalogResponse,
    DomainError,
    OwnerBookingsResponse,
)
from app.observability import debug_event
from app.repositories import PostgresRepository


def _today(catalog) -> date:
    return datetime.now(ZoneInfo(catalog.business.timezone)).date()


def owner_agenda(
    repository: PostgresRepository,
    catalog: CatalogResponse,
    start_date: date | None = None,
    end_date: date | None = None,
) -> OwnerBookingsResponse:
    start = start_date or _today(catalog)
    end = end_date or (start + timedelta(days=7))
    if end < start or (end - start).days > 60:
        raise DomainError(
            "invalid_date_range", "Date range must be within 60 days.", 422
        )
    zone = ZoneInfo(catalog.business.timezone)
    start_at = datetime.combine(start, time.min, zone)
    end_at = datetime.combine(end + timedelta(days=1), time.min, zone)
    bookings = repository.list_owner_bookings(catalog.business.id, start_at, end_at)
    debug_event("owner.agenda.loaded", bookings=len(bookings))
    return OwnerBookingsResponse(business=catalog.business, bookings=bookings)

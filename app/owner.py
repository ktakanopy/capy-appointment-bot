from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.models import (
    DemoReminder,
    DemoRemindersResponse,
    DomainError,
    OwnerBookingsResponse,
)
from app.repositories import PostgresRepository


def _catalog(repository: PostgresRepository, business_id: str):
    catalog = repository.catalog(business_id)
    if catalog is None:
        raise DomainError("business_not_found", "Business not found.", 404)
    return catalog


def _today(catalog) -> date:
    return datetime.now(ZoneInfo(catalog.business.timezone)).date()


def owner_agenda(
    repository: PostgresRepository,
    business_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> OwnerBookingsResponse:
    catalog = _catalog(repository, business_id)
    start = start_date or _today(catalog)
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
        bookings=repository.list_owner_bookings(business_id, start_at, end_at),
    )


def demo_reminders(
    repository: PostgresRepository, business_id: str
) -> DemoRemindersResponse:
    with repository.connection() as connection:
        rows = connection.execute(
            """SELECT i.id,i.reminder_id,i.booking_id,c.full_name AS customer_name,
                      s.name AS service_name,b.starts_at,i.message,i.delivered_at
               FROM reminder_inbox i JOIN bookings b ON b.id=i.booking_id
               JOIN customers c ON c.id=b.customer_id JOIN services s ON s.id=b.service_id
               WHERE i.business_id=%s ORDER BY i.delivered_at DESC LIMIT 100""",
            (business_id,),
        ).fetchall()
    return DemoRemindersResponse(reminders=[DemoReminder(**row) for row in rows])

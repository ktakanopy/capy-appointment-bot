from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.database import database_session
from app.db_models import BookingRow, CustomerRow, ReminderInboxRow, ServiceRow
from app.models import (
    DemoReminder,
    DemoRemindersResponse,
    DomainError,
    OwnerBookingsResponse,
)
from app.observability import debug_event
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
    bookings = repository.list_owner_bookings(business_id, start_at, end_at)
    debug_event("owner.agenda.loaded", bookings=len(bookings))
    return OwnerBookingsResponse(business=catalog.business, bookings=bookings)


def demo_reminders(
    repository: PostgresRepository, business_id: str
) -> DemoRemindersResponse:
    statement = (
        select(
            ReminderInboxRow.id, ReminderInboxRow.reminder_id, ReminderInboxRow.booking_id,
            CustomerRow.full_name.label("customer_name"), ServiceRow.name.label("service_name"),
            BookingRow.starts_at, ReminderInboxRow.message, ReminderInboxRow.delivered_at,
        )
        .join(BookingRow, BookingRow.id == ReminderInboxRow.booking_id)
        .join(CustomerRow, CustomerRow.id == BookingRow.customer_id)
        .join(ServiceRow, ServiceRow.id == BookingRow.service_id)
        .where(ReminderInboxRow.business_id == business_id)
        .order_by(ReminderInboxRow.delivered_at.desc())
        .limit(100)
    )
    with database_session(repository.database_url) as session:
        rows = session.execute(statement).mappings().all()
    debug_event("owner.reminder_inbox.loaded", reminders=len(rows))
    return DemoRemindersResponse(reminders=[DemoReminder(**row) for row in rows])

from __future__ import annotations

from datetime import datetime
from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select

from app.database import database_session
from app.db_models import (
    BlockedPeriod, BookingRow, BusinessRow, CustomerRow, ProfessionalRow,
    ServiceRow, WeeklyAvailabilityRule,
)
from app.models import (
    Booking,
    BookingStatus,
    Business,
    CatalogResponse,
    Customer,
    Professional,
    Service,
)
from app.observability import debug_event


def booking_from_row(row: Mapping[str, Any]) -> Booking:
    customer = Customer(
        id=row["customer_id"],
        business_id=row["business_id"],
        full_name=row["customer_name"],
        phone=row["customer_phone"],
        email=row["customer_email"],
    )
    return Booking(
        id=row["id"],
        business_id=row["business_id"],
        service_id=row["service_id"],
        professional_id=row["professional_id"],
        customer=customer,
        starts_at=row["starts_at"],
        ends_at=row["ends_at"],
        status=BookingStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def booking_query():
    """Keep the customer projection shared by booking reads and write results."""
    return select(
        *BookingRow.__table__.c,
        CustomerRow.full_name.label("customer_name"),
        CustomerRow.phone.label("customer_phone"),
        CustomerRow.email.label("customer_email"),
    ).join(CustomerRow, CustomerRow.id == BookingRow.customer_id)


class PostgresRepository:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def business(self, business_id: str) -> Business | None:
        with database_session(self.database_url) as session:
            business = session.get(BusinessRow, business_id)
            return Business.model_validate(business, from_attributes=True) if business else None

    def catalog(self, business_id: str) -> CatalogResponse | None:
        with database_session(self.database_url) as session:
            business = session.get(BusinessRow, business_id)
            if business is None:
                return None
            services = session.scalars(
                select(ServiceRow)
                .where(ServiceRow.business_id == business_id, ServiceRow.active)
                .order_by(ServiceRow.name)
            ).all()
            professionals = session.scalars(
                select(ProfessionalRow)
                .where(ProfessionalRow.business_id == business_id, ProfessionalRow.active)
                .order_by(ProfessionalRow.name)
            ).all()
            catalog = CatalogResponse(
                business=Business.model_validate(business, from_attributes=True),
                services=[Service.model_validate(row, from_attributes=True) for row in services],
                professionals=[
                    Professional.model_validate(row, from_attributes=True) for row in professionals
                ],
            )
        debug_event("postgres.catalog.loaded", services=len(catalog.services),
                    professionals=len(catalog.professionals))
        return catalog

    def service(self, business_id: str, service_id: str) -> Service | None:
        with database_session(self.database_url) as session:
            service = session.scalar(self._active_service(business_id, service_id))
            return Service.model_validate(service, from_attributes=True) if service else None

    @staticmethod
    def _active_service(business_id: str, service_id: str):
        return select(ServiceRow).where(
            ServiceRow.business_id == business_id, ServiceRow.id == service_id, ServiceRow.active,
        )

    def calendar(
        self,
        business_id: str,
        professional_id: str | None,
        from_utc: datetime,
        until_utc: datetime,
        exclude_booking_id: str | None = None,
    ) -> dict[str, Any] | None:
        with database_session(self.database_url) as session:
            statement = (
                select(*ProfessionalRow.__table__.c)
                .where(
                    ProfessionalRow.business_id == business_id,
                    ProfessionalRow.active,
                )
            )
            if professional_id:
                statement = statement.where(ProfessionalRow.id == professional_id)
            professional = session.execute(statement).mappings().first()
            if professional is None:
                return None
            professional_id = professional["id"]
            rules = select(
                WeeklyAvailabilityRule.weekday, WeeklyAvailabilityRule.opens_at,
                WeeklyAvailabilityRule.closes_at,
            ).where(WeeklyAvailabilityRule.professional_id == professional_id).order_by(
                WeeklyAvailabilityRule.opens_at
            )
            blocks = select(BlockedPeriod.starts_at, BlockedPeriod.ends_at).where(
                BlockedPeriod.professional_id == professional_id,
                BlockedPeriod.starts_at < until_utc, BlockedPeriod.ends_at > from_utc,
            )
            bookings = select(BookingRow.occupied_from, BookingRow.occupied_until).where(
                BookingRow.professional_id == professional_id,
                BookingRow.status.in_(("scheduled", "confirmed")),
                BookingRow.occupied_from < until_utc, BookingRow.occupied_until > from_utc,
            )
            if exclude_booking_id:
                bookings = bookings.where(BookingRow.id != exclude_booking_id)
            calendar = {
                "professional": dict(professional),
                "rules": [dict(row) for row in session.execute(rules).mappings()],
                "blocks": [dict(row) for row in session.execute(blocks).mappings()],
                "bookings": [dict(row) for row in session.execute(bookings).mappings()],
            }
        debug_event("postgres.schedules.loaded", professionals=1)
        return calendar

    def list_customer_bookings(self, business_id: str, customer_id: str) -> list[Booking]:
        statement = booking_query().where(
            BookingRow.business_id == business_id, BookingRow.customer_id == customer_id,
            BookingRow.status.in_(("scheduled", "confirmed")), BookingRow.starts_at > func.now(),
        ).order_by(BookingRow.starts_at)
        with database_session(self.database_url) as session:
            return [booking_from_row(row) for row in session.execute(statement).mappings()]

    def booking(self, booking_id: str, *, business_id: str | None = None) -> Booking | None:
        statement = booking_query().where(BookingRow.id == booking_id)
        if business_id is not None:
            statement = statement.where(BookingRow.business_id == business_id)
        with database_session(self.database_url) as session:
            row = session.execute(statement).mappings().first()
            return booking_from_row(row) if row else None

    def list_owner_bookings(self, business_id: str, start: datetime, end: datetime) -> list[Booking]:
        statement = booking_query().where(
            BookingRow.business_id == business_id, BookingRow.starts_at >= start, BookingRow.starts_at < end,
        ).order_by(BookingRow.starts_at)
        with database_session(self.database_url) as session:
            return [booking_from_row(row) for row in session.execute(statement).mappings()]

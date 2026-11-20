from __future__ import annotations

from datetime import datetime
from collections.abc import Mapping
from typing import Any

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session

from app.database import database_session
from app.db_models import (
    BlockedPeriod, BookingRow, BusinessRow, CustomerRow, ProfessionalRow,
    ProfessionalService, ReminderRow, ServiceRow, WeeklyAvailabilityRule,
)
from app.models import (
    Booking,
    BookingStatus,
    Business,
    CatalogResponse,
    Customer,
    OwnerBooking,
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

    def service_named(self, business_id: str, phrase: str) -> Service | None:
        name = func.lower(ServiceRow.name)
        match_rank = case(
            (name == func.lower(phrase), 0),
            (name.like(func.lower(f"%{phrase}%")), 1),
            else_=2,
        )
        statement = (
            select(ServiceRow)
            .where(ServiceRow.business_id == business_id, ServiceRow.active)
            .order_by(match_rank, ServiceRow.name)
            .limit(1)
        )
        with database_session(self.database_url) as session:
            service = session.scalar(statement)
            return Service.model_validate(service, from_attributes=True) if service else None

    def scheduling_data(
        self,
        business_id: str,
        service_id: str,
        professional_id: str | None,
        from_utc: datetime,
        until_utc: datetime,
        exclude_booking_id: str | None = None,
    ) -> tuple[Business | None, Service | None, list[dict[str, Any]]]:
        with database_session(self.database_url) as session:
            business_row = session.get(BusinessRow, business_id)
            service_row = session.scalar(self._active_service(business_id, service_id))
            business = Business.model_validate(business_row, from_attributes=True) if business_row else None
            service = Service.model_validate(service_row, from_attributes=True) if service_row else None
            if business is None or service is None:
                return business, service, []
            statement = (
                select(*ProfessionalRow.__table__.c)
                .join(ProfessionalService, ProfessionalService.professional_id == ProfessionalRow.id)
                .where(
                    ProfessionalRow.business_id == business_id,
                    ProfessionalService.service_id == service_id,
                    ProfessionalRow.active,
                )
                .order_by(ProfessionalRow.name)
            )
            if professional_id:
                statement = statement.where(ProfessionalRow.id == professional_id)
            professionals = session.execute(statement).mappings().all()
            schedule = self._load_schedules(session, professionals, from_utc, until_utc, exclude_booking_id)
        debug_event("postgres.schedules.loaded", professionals=len(schedule))
        return business, service, schedule

    @staticmethod
    def _load_schedules(session: Session, professionals, from_utc, until_utc, exclude_booking_id):
        """Batch the calendar reads so each extra professional adds rows instead of queries."""
        by_professional = {
            row["id"]: {"professional": dict(row), "rules": [], "blocks": [], "bookings": []}
            for row in professionals
        }
        if not by_professional:
            return []
        rules = select(
            WeeklyAvailabilityRule.professional_id, WeeklyAvailabilityRule.weekday,
            WeeklyAvailabilityRule.opens_at, WeeklyAvailabilityRule.closes_at,
        ).where(WeeklyAvailabilityRule.professional_id.in_(by_professional)).order_by(
            WeeklyAvailabilityRule.opens_at
        )
        blocks = select(BlockedPeriod.professional_id, BlockedPeriod.starts_at, BlockedPeriod.ends_at).where(
            BlockedPeriod.professional_id.in_(by_professional),
            BlockedPeriod.starts_at < until_utc, BlockedPeriod.ends_at > from_utc,
        )
        bookings = select(BookingRow.professional_id, BookingRow.occupied_from, BookingRow.occupied_until).where(
            BookingRow.professional_id.in_(by_professional),
            BookingRow.status.in_(("scheduled", "confirmed")),
            BookingRow.occupied_from < until_utc, BookingRow.occupied_until > from_utc,
        )
        if exclude_booking_id:
            bookings = bookings.where(BookingRow.id != exclude_booking_id)
        for name, statement in (("rules", rules), ("blocks", blocks), ("bookings", bookings)):
            for row in session.execute(statement).mappings():
                entry = dict(row)
                by_professional[entry.pop("professional_id")][name].append(entry)
        return list(by_professional.values())

    def customer_by_phone(self, business_id: str, phone: str) -> Customer | None:
        with database_session(self.database_url) as session:
            customer = session.scalars(select(CustomerRow).where(
                CustomerRow.business_id == business_id, CustomerRow.phone == phone,
            )).first()
            return Customer.model_validate(customer, from_attributes=True) if customer else None

    def list_customer_bookings(self, business_id: str, customer_id: str) -> list[Booking]:
        statement = booking_query().where(
            BookingRow.business_id == business_id, BookingRow.customer_id == customer_id,
            BookingRow.status.in_(("scheduled", "confirmed")), BookingRow.starts_at > func.now(),
        ).order_by(BookingRow.starts_at)
        with database_session(self.database_url) as session:
            return [booking_from_row(row) for row in session.execute(statement).mappings()]

    def update_reminder_lead(self, business_id: str, minutes: int) -> Business | None:
        statement = update(BusinessRow).where(BusinessRow.id == business_id).values(
            reminder_lead_minutes=minutes
        ).returning(BusinessRow)
        with database_session(self.database_url) as session:
            business = session.scalar(statement)
            return Business.model_validate(business, from_attributes=True) if business else None

    def booking(self, booking_id: str) -> Booking | None:
        with database_session(self.database_url) as session:
            row = session.execute(booking_query().where(BookingRow.id == booking_id)).mappings().first()
            return booking_from_row(row) if row else None

    def list_owner_bookings(self, business_id: str, start: datetime, end: datetime) -> list[OwnerBooking]:
        latest_reminder = (
            select(ReminderRow.status).where(ReminderRow.booking_id == BookingRow.id)
            .order_by(ReminderRow.created_at.desc()).limit(1).scalar_subquery()
        )
        statement = booking_query().add_columns(latest_reminder.label("reminder_status")).where(
            BookingRow.business_id == business_id, BookingRow.starts_at >= start, BookingRow.starts_at < end,
        ).order_by(BookingRow.starts_at)
        with database_session(self.database_url) as session:
            return [
                OwnerBooking(**booking_from_row(row).model_dump(), reminder_status=row["reminder_status"])
                for row in session.execute(statement).mappings()
            ]

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.database import connect
from app.models import (
    Booking,
    BookingStatus,
    Business,
    CatalogResponse,
    Customer,
    Professional,
    Service,
)


def _business(row: dict[str, Any]) -> Business:
    return Business.model_validate(row)


def _service(row: dict[str, Any]) -> Service:
    return Service.model_validate(row)


def _professional(row: dict[str, Any]) -> Professional:
    return Professional.model_validate(row)


def booking_from_row(row: dict[str, Any]) -> Booking:
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


BOOKING_SELECT = """
    SELECT b.*, c.full_name AS customer_name, c.phone AS customer_phone, c.email AS customer_email
    FROM bookings b JOIN customers c ON c.id=b.customer_id
"""


class PostgresRepository:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def connection(self):
        return connect(self.database_url)

    def business(self, business_id: str) -> Business | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM businesses WHERE id=%s", (business_id,)).fetchone()
        return _business(row) if row else None

    def catalog(self, business_id: str) -> CatalogResponse | None:
        with self.connection() as connection:
            business = connection.execute("SELECT * FROM businesses WHERE id=%s", (business_id,)).fetchone()
            if not business:
                return None
            services = connection.execute(
                "SELECT * FROM services WHERE business_id=%s AND active ORDER BY name", (business_id,)
            ).fetchall()
            professionals = connection.execute(
                "SELECT * FROM professionals WHERE business_id=%s AND active ORDER BY name", (business_id,)
            ).fetchall()
        return CatalogResponse(
            business=_business(business),
            services=[_service(row) for row in services],
            professionals=[_professional(row) for row in professionals],
        )

    def service(self, business_id: str, service_id: str) -> Service | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM services WHERE business_id=%s AND id=%s AND active",
                (business_id, service_id),
            ).fetchone()
        return _service(row) if row else None

    def service_named(self, business_id: str, phrase: str) -> Service | None:
        with self.connection() as connection:
            row = connection.execute(
                """SELECT s.* FROM services s WHERE s.business_id=%s AND s.active
                   ORDER BY CASE WHEN lower(s.name)=lower(%s) THEN 0
                     WHEN lower(s.name) LIKE lower(%s) THEN 1 ELSE 2 END, s.name LIMIT 1""",
                (business_id, phrase, f"%{phrase}%"),
            ).fetchone()
        return _service(row) if row else None

    def scheduling_data(
        self,
        business_id: str,
        service_id: str,
        professional_id: str | None,
        from_utc: datetime,
        until_utc: datetime,
        exclude_booking_id: str | None = None,
    ) -> tuple[Business | None, Service | None, list[dict[str, Any]]]:
        with self.connection() as connection:
            business_row = connection.execute("SELECT * FROM businesses WHERE id=%s", (business_id,)).fetchone()
            service_row = connection.execute(
                "SELECT * FROM services WHERE id=%s AND business_id=%s AND active",
                (service_id, business_id),
            ).fetchone()
            if not business_row or not service_row:
                return (_business(business_row) if business_row else None,
                        _service(service_row) if service_row else None, [])
            params: list[Any] = [business_id, service_id]
            filter_professional = ""
            if professional_id:
                filter_professional = " AND p.id=%s"
                params.append(professional_id)
            professionals = connection.execute(
                """SELECT p.* FROM professionals p JOIN professional_services ps ON ps.professional_id=p.id
                   WHERE p.business_id=%s AND ps.service_id=%s AND p.active""" + filter_professional + " ORDER BY p.name",
                params,
            ).fetchall()
            schedule = []
            for professional in professionals:
                rules = connection.execute(
                    "SELECT weekday,opens_at,closes_at FROM weekly_availability_rules WHERE professional_id=%s ORDER BY opens_at",
                    (professional["id"],),
                ).fetchall()
                blocks = connection.execute(
                    """SELECT starts_at,ends_at FROM blocked_periods WHERE professional_id=%s
                       AND starts_at < %s AND ends_at > %s""",
                    (professional["id"], until_utc, from_utc),
                ).fetchall()
                exclusion = " AND id <> %s" if exclude_booking_id else ""
                booking_params: list[Any] = [professional["id"], until_utc, from_utc]
                if exclude_booking_id:
                    booking_params.append(exclude_booking_id)
                bookings = connection.execute(
                    """SELECT occupied_from,occupied_until FROM bookings WHERE professional_id=%s
                       AND status IN ('scheduled','confirmed') AND occupied_from < %s AND occupied_until > %s""" + exclusion,
                    booking_params,
                ).fetchall()
                schedule.append({"professional": professional, "rules": rules, "blocks": blocks, "bookings": bookings})
        return _business(business_row), _service(service_row), schedule

    def booking(self, booking_id: str) -> Booking | None:
        with self.connection() as connection:
            row = connection.execute(BOOKING_SELECT + " WHERE b.id=%s", (booking_id,)).fetchone()
        return booking_from_row(row) if row else None

    def list_owner_bookings(self, business_id: str, start: datetime, end: datetime) -> list[Booking]:
        with self.connection() as connection:
            rows = connection.execute(
                BOOKING_SELECT + " WHERE b.business_id=%s AND b.starts_at >= %s AND b.starts_at < %s ORDER BY b.starts_at",
                (business_id, start, end),
            ).fetchall()
        return [booking_from_row(row) for row in rows]

    @staticmethod
    def row_booking(connection: psycopg.Connection, booking_id: str, *, for_update: bool = False):
        suffix = " FOR UPDATE OF b" if for_update else ""
        return connection.execute(BOOKING_SELECT + " WHERE b.id=%s" + suffix, (booking_id,)).fetchone()

    @staticmethod
    def row_booking_with_token(connection: psycopg.Connection, booking_id: str, token_digest: str):
        return connection.execute(
            BOOKING_SELECT + " WHERE b.id=%s AND b.token_digest=%s",
            (booking_id, token_digest),
        ).fetchone()

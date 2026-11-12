from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import psycopg

from app.database import connect
from app.models import (
    Booking,
    BookingCreateRequest,
    BookingRescheduleRequest,
    BookingStatus,
    BookingSummary,
    DomainError,
)
from app.repositories import PostgresRepository, booking_from_row
from app.scheduling import validate_slot_in_transaction


UTC = timezone.utc


def _request_hash(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class BookingService:
    def __init__(self, repository: PostgresRepository):
        self.repository = repository
        self.database_url = repository.database_url

    @classmethod
    def _owned_row(cls, connection, business_id: str, customer_id: str, booking_id: str, *, for_update=False):
        row = cls._row(connection, booking_id, for_update=for_update)
        if not customer_id or row is None or row["business_id"] != business_id or row["customer_id"] != customer_id:
            raise DomainError("booking_not_found", "Booking not found.", 404)
        return row

    def _check_idempotency(self, connection, business_id: str, operation: str, key: str, body_hash: str):
        if not key or len(key) > 200:
            raise DomainError("invalid_idempotency_key", "Idempotency-Key must contain 1 to 200 characters.", 422)
        # Serialize booking writes for one business. This demo accepts the
        # throughput tradeoff in exchange for one clear retry boundary.
        business = connection.execute(
            "SELECT id FROM businesses WHERE id=%s FOR UPDATE", (business_id,)
        ).fetchone()
        if business is None:
            raise DomainError("business_not_found", "Business not found.", 404)
        record = connection.execute(
            """SELECT request_hash,resource_id,result_code FROM idempotency_records
               WHERE business_id=%s AND operation=%s AND idempotency_key=%s""",
            (business_id, operation, key),
        ).fetchone()
        if record and record["request_hash"] != body_hash:
            raise DomainError("idempotency_key_reused", "This idempotency key was already used for a different request.", 409)
        return record

    def _insert_idempotency(self, connection, business_id: str, operation: str, key: str, body_hash: str, booking_id: str, result_code: str):
        connection.execute(
            """INSERT INTO idempotency_records (business_id,operation,idempotency_key,request_hash,resource_id,result_code)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (business_id, operation, key, body_hash, booking_id, result_code),
        )

    @staticmethod
    def _row(connection, booking_id: str, for_update: bool = False):
        suffix = " FOR UPDATE OF b" if for_update else ""
        return connection.execute(
            """SELECT b.*,c.full_name AS customer_name,c.phone AS customer_phone,c.email AS customer_email
               FROM bookings b JOIN customers c ON c.id=b.customer_id WHERE b.id=%s""" + suffix,
            (booking_id,),
        ).fetchone()

    def create(self, request: BookingCreateRequest, idempotency_key: str) -> BookingSummary:
        payload_hash = _request_hash(request.model_dump(mode="json"))
        try:
            with connect(self.database_url) as connection:
                record = self._check_idempotency(connection, request.business_id, "create", idempotency_key, payload_hash)
                if record:
                    row = self._row(connection, record["resource_id"])
                    if row is None:
                        raise DomainError("booking_not_found", "Booking not found.", 404)
                    self._owned_row(connection, request.business_id, request.customer_id, row["id"])
                    return BookingSummary(booking=booking_from_row(row), outcome="replayed")

                slot = validate_slot_in_transaction(
                    connection,
                    business_id=request.business_id,
                    service_id=request.service_id,
                    professional_id=request.professional_id,
                    slot_id=request.slot_id,
                    starts_at=request.starts_at,
                )
                customer = connection.execute(
                    "SELECT id FROM customers WHERE id=%s AND business_id=%s",
                    (request.customer_id, request.business_id),
                ).fetchone()
                if customer is None:
                    raise DomainError("customer_not_found", "Customer not found.", 404)
                booking_id = str(uuid.uuid4())
                connection.execute(
                    """INSERT INTO bookings (id,business_id,service_id,professional_id,customer_id,starts_at,ends_at,
                       occupied_from,occupied_until,status)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'scheduled')""",
                    (
                        booking_id, request.business_id, slot.service_id, slot.professional_id, request.customer_id,
                        slot.starts_at, slot.ends_at,
                        slot.starts_at - timedelta(minutes=self._service_buffer(connection, slot.service_id, "before")),
                        slot.ends_at + timedelta(minutes=self._service_buffer(connection, slot.service_id, "after")),
                    ),
                )
                business = connection.execute("SELECT reminder_lead_minutes FROM businesses WHERE id=%s", (request.business_id,)).fetchone()
                connection.execute(
                    "INSERT INTO reminders (id,business_id,booking_id,send_at,status) VALUES (%s,%s,%s,%s,'pending')",
                    (str(uuid.uuid4()), request.business_id, booking_id,
                     slot.starts_at - timedelta(minutes=business["reminder_lead_minutes"])),
                )
                self._insert_idempotency(connection, request.business_id, "create", idempotency_key, payload_hash, booking_id, "created")
                row = self._row(connection, booking_id)
                return BookingSummary(booking=booking_from_row(row), outcome="created")
        except psycopg.errors.ExclusionViolation as exc:
            raise DomainError("slot_unavailable", "That time is no longer available. Choose another slot.", 409) from exc

    @staticmethod
    def _service_buffer(connection, service_id: str, side: str) -> int:
        column = "buffer_before_minutes" if side == "before" else "buffer_after_minutes"
        return connection.execute(f"SELECT {column} AS minutes FROM services WHERE id=%s", (service_id,)).fetchone()["minutes"]

    def read(self, business_id: str, customer_id: str, booking_id: str) -> Booking:
        with connect(self.database_url) as connection:
            row = self._owned_row(connection, business_id, customer_id, booking_id)
        return booking_from_row(row)

    def confirm(self, business_id: str, customer_id: str, booking_id: str, idempotency_key: str):
        return self._mutate_status(business_id, customer_id, booking_id, "confirm", idempotency_key)

    def cancel(self, business_id: str, customer_id: str, booking_id: str, idempotency_key: str):
        return self._mutate_status(business_id, customer_id, booking_id, "cancel", idempotency_key)

    def _mutate_status(self, business_id: str, customer_id: str, booking_id: str, operation: str, idempotency_key: str):
        payload_hash = _request_hash({"customer_id": customer_id, "booking_id": booking_id, "operation": operation})
        try:
            with connect(self.database_url) as connection:
                record = self._check_idempotency(connection, business_id, operation, idempotency_key, payload_hash)
                row = self._owned_row(connection, business_id, customer_id, booking_id, for_update=True)
                if record:
                    saved = self._row(connection, record["resource_id"])
                    return booking_from_row(saved), record["result_code"]
                if row["starts_at"] <= datetime.now(UTC):
                    raise DomainError("booking_started", "This booking can no longer be changed.", 409)
                current = BookingStatus(row["status"])
                if operation == "confirm":
                    if current == BookingStatus.CANCELED:
                        raise DomainError("booking_not_confirmable", "A canceled booking cannot be confirmed.", 409)
                    result = "already_confirmed" if current == BookingStatus.CONFIRMED else "confirmed"
                    if current == BookingStatus.SCHEDULED:
                        connection.execute("UPDATE bookings SET status='confirmed',updated_at=now() WHERE id=%s", (booking_id,))
                else:
                    result = "already_canceled" if current == BookingStatus.CANCELED else "canceled"
                    if current != BookingStatus.CANCELED:
                        connection.execute("UPDATE bookings SET status='canceled',updated_at=now() WHERE id=%s", (booking_id,))
                        connection.execute("UPDATE reminders SET status='canceled' WHERE booking_id=%s AND status='pending'", (booking_id,))
                self._insert_idempotency(connection, row["business_id"], operation, idempotency_key, payload_hash, booking_id, result)
                updated = self._row(connection, booking_id)
                return booking_from_row(updated), result
        except psycopg.errors.ExclusionViolation as exc:
            raise DomainError("booking_conflict", "The booking could not be updated because the time is no longer available.", 409) from exc

    def reschedule(self, business_id: str, customer_id: str, booking_id: str, request: BookingRescheduleRequest, idempotency_key: str) -> tuple[Booking, str]:
        if request.business_id != business_id:
            raise DomainError("booking_not_found", "Booking not found.", 404)
        payload_hash = _request_hash({"customer_id": customer_id, "booking_id": booking_id, **request.model_dump(mode="json")})
        try:
            with connect(self.database_url) as connection:
                record = self._check_idempotency(connection, business_id, "reschedule", idempotency_key, payload_hash)
                row = self._owned_row(connection, business_id, customer_id, booking_id, for_update=True)
                if record:
                    saved = self._row(connection, record["resource_id"])
                    return booking_from_row(saved), record["result_code"]
                if BookingStatus(row["status"]) == BookingStatus.CANCELED:
                    raise DomainError("booking_not_reschedulable", "A canceled booking cannot be rescheduled.", 409)
                if row["starts_at"] <= datetime.now(UTC):
                    raise DomainError("booking_started", "This booking can no longer be changed.", 409)
                slot = validate_slot_in_transaction(
                    connection,
                    business_id=request.business_id,
                    service_id=request.service_id,
                    professional_id=request.professional_id,
                    slot_id=request.slot_id,
                    starts_at=request.starts_at,
                    exclude_booking_id=booking_id,
                )
                before = self._service_buffer(connection, slot.service_id, "before")
                after = self._service_buffer(connection, slot.service_id, "after")
                connection.execute(
                    """UPDATE bookings SET service_id=%s,professional_id=%s,starts_at=%s,ends_at=%s,
                       occupied_from=%s,occupied_until=%s,updated_at=now() WHERE id=%s""",
                    (slot.service_id, slot.professional_id, slot.starts_at, slot.ends_at,
                     slot.starts_at - timedelta(minutes=before), slot.ends_at + timedelta(minutes=after), booking_id),
                )
                lead = connection.execute("SELECT reminder_lead_minutes FROM businesses WHERE id=%s", (request.business_id,)).fetchone()["reminder_lead_minutes"]
                pending = connection.execute("SELECT id FROM reminders WHERE booking_id=%s AND status='pending' FOR UPDATE", (booking_id,)).fetchone()
                new_send_at = slot.starts_at - timedelta(minutes=lead)
                if pending:
                    connection.execute("UPDATE reminders SET send_at=%s,last_error=NULL WHERE id=%s", (new_send_at, pending["id"]))
                else:
                    connection.execute(
                        "INSERT INTO reminders (id,business_id,booking_id,send_at,status) VALUES (%s,%s,%s,%s,'pending')",
                        (str(uuid.uuid4()), request.business_id, booking_id, new_send_at),
                    )
                self._insert_idempotency(connection, request.business_id, "reschedule", idempotency_key, payload_hash, booking_id, "rescheduled")
                updated = self._row(connection, booking_id)
                return booking_from_row(updated), "rescheduled"
        except psycopg.errors.ExclusionViolation as exc:
            raise DomainError("slot_unavailable", "That time is no longer available. Choose another slot.", 409) from exc

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Literal

import psycopg

from app.database import connect
from app.models import (
    AvailabilitySlot,
    Booking,
    BookingCreateRequest,
    BookingRescheduleRequest,
    BookingStatus,
    BookingSummary,
    DomainError,
)
from app.observability import timed_event
from app.repositories import BOOKING_SELECT, PostgresRepository, booking_from_row
from app.scheduling import validate_slot_in_transaction

UTC = timezone.utc


def _hash_booking_request(value: object) -> str:
    """Hash the request to compare it with a previous attempt.

    Sort the JSON keys so the same values produce the same hash even if the
    fields arrive in a different order.
    """
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class BookingService:
    """Keep booking authorization, writes and their side effects consistent."""

    def __init__(
        self,
        repository: PostgresRepository,
        *,
        turn_guard: Callable | None = None,
        telegram_destination: tuple[int, int] | None = None,
    ):
        """Use the repository's database URL to keep reads and writes together."""
        self.repository = repository
        self.database_url = repository.database_url
        self.turn_guard = turn_guard
        self.telegram_destination = telegram_destination

    @staticmethod
    def _load_customer_booking(
        connection,
        business_id: str,
        customer_id: str | None,
        booking_id: str,
        *,
        for_update: bool = False,
    ):
        """Load a booking only for the business and customer that own it.

        A customer could know someone else's booking ID. Match the business
        and customer IDs in SQL to enforce ownership. When changing a booking,
        lock its row so another transaction cannot change it after we check it.
        """
        suffix = " FOR UPDATE OF b" if for_update else ""
        row = connection.execute(
            BOOKING_SELECT
            + " WHERE b.id=%s AND b.business_id=%s AND b.customer_id=%s"
            + suffix,
            (booking_id, business_id, customer_id or None),
        ).fetchone()
        if row is None:
            raise DomainError("booking_not_found", "Booking not found.", 404)
        return row

    def _lock_business_and_check_retry(
        self,
        connection,
        business_id: str,
        operation: str,
        idempotency_key: str,
        request_hash: str,
    ):
        """Decide whether to run this write or reuse an earlier result.

        A booking can commit before the caller gets its reply. If the caller
        sends the same request with the same key again, return the saved record
        so it can get the outcome without writing again. That is idempotency.

        Look up the key for this business and operation, then compare the
        request hash to catch a key reused for different details. Lock the
        business first so two concurrent attempts cannot both see an unused key.
        """
        if self.turn_guard:
            self.turn_guard(connection)
        if not idempotency_key or len(idempotency_key) > 200:
            raise DomainError(
                "invalid_idempotency_key",
                "Idempotency-Key must contain 1 to 200 characters.",
                422,
            )
        with timed_event("booking.business_lock", operation=operation):
            business = connection.execute(
                "SELECT id FROM businesses WHERE id=%s FOR NO KEY UPDATE", (business_id,)
            ).fetchone()
        if business is None:
            raise DomainError("business_not_found", "Business not found.", 404)
        record = connection.execute(
            """SELECT request_hash,resource_id,result_code FROM idempotency_records
               WHERE business_id=%s AND operation=%s AND idempotency_key=%s""",
            (business_id, operation, idempotency_key),
        ).fetchone()
        if record and record["request_hash"] != request_hash:
            raise DomainError(
                "idempotency_key_reused",
                "This idempotency key was already used for a different request.",
                409,
            )
        return record

    def _save_retry_result(
        self,
        connection,
        business_id: str,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        booking_id: str,
        result_code: str,
    ) -> None:
        """Save the booking ID and outcome so a retry can reuse the committed result.

        Use the caller's transaction so the result exists only if the write commits.
        """
        connection.execute(
            """INSERT INTO idempotency_records (business_id,operation,idempotency_key,request_hash,resource_id,result_code)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (
                business_id,
                operation,
                idempotency_key,
                request_hash,
                booking_id,
                result_code,
            ),
        )

    def create(
        self, request: BookingCreateRequest, idempotency_key: str
    ) -> BookingSummary:
        """Create an appointment when a customer chooses an offered time.

        Another customer may take the time between the offer and the selection,
        so check availability again and verify who will own the appointment.
        Commit the booking, reminder and retry result together so a failure
        rolls back all three. A retry with the same key returns the existing booking.
        """
        request_hash = _hash_booking_request(request.model_dump(mode="json"))
        try:
            with timed_event("booking.write", operation="create"), connect(self.database_url) as connection:
                record = self._lock_business_and_check_retry(
                    connection,
                    request.business_id,
                    "create",
                    idempotency_key,
                    request_hash,
                )
                if record:
                    row = self._load_customer_booking(
                        connection,
                        request.business_id,
                        request.customer_id,
                        record["resource_id"],
                    )
                    return BookingSummary(
                        booking=booking_from_row(row), outcome="replayed"
                    )

                slot = self._revalidate_booking_slot(connection, request)
                self._require_customer_in_business(
                    connection, request.business_id, request.customer_id
                )
                booking_id = self._insert_scheduled_booking(
                    connection, slot, request.customer_id
                )
                if self.telegram_destination:
                    connection.execute(
                        """UPDATE bookings SET telegram_bot_id=%s, telegram_chat_id=%s
                           WHERE id=%s""",
                        (*self.telegram_destination, booking_id),
                    )
                send_at = self._calculate_reminder_time(connection, slot)
                self._queue_booking_reminder(
                    connection, request.business_id, booking_id, send_at
                )
                self._save_retry_result(
                    connection,
                    request.business_id,
                    "create",
                    idempotency_key,
                    request_hash,
                    booking_id,
                    "created",
                )
                row = self._load_customer_booking(
                    connection, request.business_id, request.customer_id, booking_id
                )
                return BookingSummary(booking=booking_from_row(row), outcome="created")
        except psycopg.errors.ExclusionViolation as exc:
            raise DomainError(
                "slot_unavailable",
                "That time is no longer available. Choose another slot.",
                409,
            ) from exc

    @staticmethod
    def _revalidate_booking_slot(
        connection,
        request: BookingCreateRequest | BookingRescheduleRequest,
        *,
        exclude_booking_id: str | None = None,
    ) -> AvailabilitySlot:
        """Recheck the slot in the write transaction because offers can go stale.

        Exclude the booking being moved so its old interval cannot block its own move.
        """
        return validate_slot_in_transaction(
            connection,
            business_id=request.business_id,
            service_id=request.service_id,
            professional_id=request.professional_id,
            slot_id=request.slot_id,
            starts_at=request.starts_at,
            exclude_booking_id=exclude_booking_id,
        )

    @staticmethod
    def _require_customer_in_business(
        connection, business_id: str, customer_id: str
    ) -> None:
        """Check that the customer account belongs to the business taking the booking.

        The customer ID comes from the account linked to the Telegram sender.
        That account will own the appointment and be able to look it up or
        change it later. Query by both IDs so an account from another business
        cannot be attached to this booking.
        """
        customer = connection.execute(
            "SELECT id FROM customers WHERE id=%s AND business_id=%s",
            (customer_id, business_id),
        ).fetchone()
        if customer is None:
            raise DomainError("customer_not_found", "Customer not found.", 404)

    def _insert_scheduled_booking(
        self, connection, slot: AvailabilitySlot, customer_id: str
    ) -> str:
        """Insert a scheduled booking with the full interval used to detect overlaps."""
        booking_id = str(uuid.uuid4())
        occupied_from, occupied_until = self._calculate_occupied_interval(
            connection, slot
        )
        connection.execute(
            """INSERT INTO bookings (id,business_id,service_id,professional_id,customer_id,starts_at,ends_at,
               occupied_from,occupied_until,status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'scheduled')""",
            (
                booking_id,
                slot.business_id,
                slot.service_id,
                slot.professional_id,
                customer_id,
                slot.starts_at,
                slot.ends_at,
                occupied_from,
                occupied_until,
            ),
        )
        return booking_id

    @staticmethod
    def _calculate_occupied_interval(
        connection, slot: AvailabilitySlot
    ) -> tuple[datetime, datetime]:
        """Expand the slot with service buffers so setup and cleanup also block time."""
        buffers = connection.execute(
            "SELECT buffer_before_minutes,buffer_after_minutes FROM services WHERE id=%s",
            (slot.service_id,),
        ).fetchone()
        return (
            slot.starts_at - timedelta(minutes=buffers["buffer_before_minutes"]),
            slot.ends_at + timedelta(minutes=buffers["buffer_after_minutes"]),
        )

    @staticmethod
    def _calculate_reminder_time(connection, slot: AvailabilitySlot) -> datetime:
        """Use the same reminder policy for new and moved bookings.

        Subtract the business's lead time from the appointment's start time.
        """
        business = connection.execute(
            "SELECT reminder_lead_minutes FROM businesses WHERE id=%s",
            (slot.business_id,),
        ).fetchone()
        return slot.starts_at - timedelta(minutes=business["reminder_lead_minutes"])

    @staticmethod
    def _queue_booking_reminder(
        connection, business_id: str, booking_id: str, send_at: datetime
    ) -> None:
        """Schedule a reminder by saving a pending job with its due time.

        CAPY can reply as soon as the booking commits. A separate worker picks
        up the job when it is due and writes the reminder to the demo inbox.
        Save the job with the booking so a failed booking leaves no reminder.
        """
        connection.execute(
            """INSERT INTO reminders
                   (id,business_id,booking_id,send_at,status,telegram_bot_id,telegram_chat_id)
               SELECT %s,b.business_id,b.id,%s,'pending',t.bot_id,
                      CASE WHEN t.bot_id IS NOT NULL THEN b.telegram_chat_id END
               FROM bookings b LEFT JOIN telegram_bots t ON t.bot_id=b.telegram_bot_id
                   AND t.business_id=b.business_id AND t.enabled AND t.reminder_delivery
               WHERE b.id=%s AND b.business_id=%s""",
            (str(uuid.uuid4()), send_at, booking_id, business_id),
        )

    def read(
        self, business_id: str, customer_id: str | None, booking_id: str
    ) -> Booking:
        """Return booking details only when the business and customer both match."""
        with connect(self.database_url) as connection:
            row = self._load_customer_booking(
                connection, business_id, customer_id, booking_id
            )
        return booking_from_row(row)

    def confirm(
        self,
        business_id: str,
        customer_id: str | None,
        booking_id: str,
        idempotency_key: str,
    ) -> tuple[Booking, str]:
        """Record that the customer confirmed through the retry-safe status workflow."""
        return self._change_booking_status(
            business_id, customer_id, booking_id, "confirm", idempotency_key
        )

    def cancel(
        self,
        business_id: str,
        customer_id: str | None,
        booking_id: str,
        idempotency_key: str,
    ) -> tuple[Booking, str]:
        """Release the slot and cancel pending reminders in the same transaction."""
        return self._change_booking_status(
            business_id, customer_id, booking_id, "cancel", idempotency_key
        )

    def _change_booking_status(
        self,
        business_id: str,
        customer_id: str | None,
        booking_id: str,
        operation: Literal["confirm", "cancel"],
        idempotency_key: str,
    ) -> tuple[Booking, str]:
        """Give confirmation and cancellation the same ownership and retry checks.

        Save the outcome with the status change so a retried request can return
        the original result. Any reminder cancellation commits with the change.
        """
        request_hash = _hash_booking_request(
            {
                "customer_id": customer_id,
                "booking_id": booking_id,
                "operation": operation,
            }
        )
        try:
            with timed_event("booking.write", operation=operation), connect(self.database_url) as connection:
                record = self._lock_business_and_check_retry(
                    connection, business_id, operation, idempotency_key, request_hash
                )
                row = self._load_customer_booking(
                    connection, business_id, customer_id, booking_id, for_update=True
                )
                if record:
                    return booking_from_row(row), record["result_code"]
                self._require_upcoming_booking(row["starts_at"])
                result = self._write_booking_status(
                    connection, booking_id, BookingStatus(row["status"]), operation
                )
                self._save_retry_result(
                    connection,
                    business_id,
                    operation,
                    idempotency_key,
                    request_hash,
                    booking_id,
                    result,
                )
                updated = self._load_customer_booking(
                    connection, business_id, customer_id, booking_id
                )
                return booking_from_row(updated), result
        except psycopg.errors.ExclusionViolation as exc:
            raise DomainError(
                "booking_conflict",
                "The booking could not be updated because the time is no longer available.",
                409,
            ) from exc

    @staticmethod
    def _require_upcoming_booking(starts_at: datetime) -> None:
        """Enforce the change cutoff by comparing the start time with the UTC clock."""
        if starts_at <= datetime.now(UTC):
            raise DomainError(
                "booking_started", "This booking can no longer be changed.", 409
            )

    @staticmethod
    def _write_booking_status(
        connection,
        booking_id: str,
        current: BookingStatus,
        operation: Literal["confirm", "cancel"],
    ) -> str:
        """Apply confirmation or cancellation rules to the locked booking.

        Preserve an existing status when the customer repeats a decision. When
        the booking is canceled, mark its pending reminders as canceled so the
        worker will skip them.
        """
        if operation == "confirm" and current == BookingStatus.CANCELED:
            raise DomainError(
                "booking_not_confirmable", "A canceled booking cannot be confirmed.", 409
            )
        target = (
            BookingStatus.CONFIRMED if operation == "confirm" else BookingStatus.CANCELED
        )
        if current == target:
            return f"already_{target.value}"

        connection.execute(
            "UPDATE bookings SET status=%s,updated_at=now() WHERE id=%s",
            (target.value, booking_id),
        )
        if target == BookingStatus.CANCELED:
            connection.execute(
                """UPDATE reminders SET status='canceled', generation=generation+1
                   WHERE booking_id=%s AND status='pending'""",
                (booking_id,),
            )
            connection.execute(
                """UPDATE telegram_outbox SET status='canceled', message=NULL, finished_at=now()
                   WHERE status='ready' AND reminder_id IN
                       (SELECT id FROM reminders WHERE booking_id=%s)""",
                (booking_id,),
            )
        return target.value

    def reschedule(
        self,
        business_id: str,
        customer_id: str | None,
        booking_id: str,
        request: BookingRescheduleRequest,
        idempotency_key: str,
    ) -> tuple[Booking, str]:
        """Move the customer's upcoming appointment to a slot that is still free.

        Keep the same booking ID and commit its new time with the reminder and
        retry result. If the move fails, the original appointment and reminder
        stay in place. A matching retry returns the booking with the saved outcome.
        """
        if request.business_id != business_id:
            raise DomainError("booking_not_found", "Booking not found.", 404)
        request_hash = _hash_booking_request(
            {
                "customer_id": customer_id,
                "booking_id": booking_id,
                **request.model_dump(mode="json"),
            }
        )
        try:
            with timed_event("booking.write", operation="reschedule"), connect(self.database_url) as connection:
                record = self._lock_business_and_check_retry(
                    connection, business_id, "reschedule", idempotency_key, request_hash
                )
                row = self._load_customer_booking(
                    connection, business_id, customer_id, booking_id, for_update=True
                )
                if record:
                    return booking_from_row(row), record["result_code"]
                if BookingStatus(row["status"]) == BookingStatus.CANCELED:
                    raise DomainError(
                        "booking_not_reschedulable",
                        "A canceled booking cannot be rescheduled.",
                        409,
                    )
                self._require_upcoming_booking(row["starts_at"])
                slot = self._revalidate_booking_slot(
                    connection, request, exclude_booking_id=booking_id
                )
                self._update_booking_slot(connection, booking_id, slot)
                self._move_booking_reminder(connection, booking_id, slot)
                self._save_retry_result(
                    connection,
                    business_id,
                    "reschedule",
                    idempotency_key,
                    request_hash,
                    booking_id,
                    "rescheduled",
                )
                updated = self._load_customer_booking(
                    connection, business_id, customer_id, booking_id
                )
                return booking_from_row(updated), "rescheduled"
        except psycopg.errors.ExclusionViolation as exc:
            raise DomainError(
                "slot_unavailable",
                "That time is no longer available. Choose another slot.",
                409,
            ) from exc

    def _update_booking_slot(
        self, connection, booking_id: str, slot: AvailabilitySlot
    ) -> None:
        """Move the slot and its buffers so conflict checks use the new interval."""
        occupied_from, occupied_until = self._calculate_occupied_interval(
            connection, slot
        )
        connection.execute(
            """UPDATE bookings SET service_id=%s,professional_id=%s,starts_at=%s,ends_at=%s,
               occupied_from=%s,occupied_until=%s,updated_at=now() WHERE id=%s""",
            (
                slot.service_id,
                slot.professional_id,
                slot.starts_at,
                slot.ends_at,
                occupied_from,
                occupied_until,
                booking_id,
            ),
        )

    def _move_booking_reminder(
        self, connection, booking_id: str, slot: AvailabilitySlot
    ) -> None:
        """Keep reminders aligned with the appointment's new time.

        Lock and retime the pending job so the worker cannot deliver it while
        it is being updated. If there is no pending job, queue one for the new
        time. This also covers moves after the first reminder was delivered.
        """
        send_at = self._calculate_reminder_time(connection, slot)
        pending = connection.execute(
            "SELECT id FROM reminders WHERE booking_id=%s AND status='pending' FOR UPDATE",
            (booking_id,),
        ).fetchone()
        if pending:
            connection.execute(
                """UPDATE reminders SET send_at=%s,last_error=NULL,generation=generation+1
                   WHERE id=%s""",
                (send_at, pending["id"]),
            )
            connection.execute(
                """UPDATE telegram_outbox SET status='canceled', message=NULL, finished_at=now()
                   WHERE reminder_id=%s AND status='ready'""",
                (pending["id"],),
            )
        else:
            self._queue_booking_reminder(
                connection, slot.business_id, booking_id, send_at
            )

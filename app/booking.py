from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Literal

from psycopg.errors import ExclusionViolation
from sqlalchemy import case, func, insert, literal, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import database_session
from app.db_models import (
    BookingRow, BusinessRow, CustomerRow, IdempotencyRecord,
    ReminderRow, ServiceRow, TelegramBotRow, TelegramOutboxRow,
)
from app.models import (
    AvailabilitySlot,
    Booking,
    BookingCreateRequest,
    BookingRescheduleRequest,
    BookingStatus,
    BookingSummary,
    DomainError,
)
from app.observability import debug_event, timed_event
from app.repositories import PostgresRepository, booking_from_row, booking_query
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
        self.repository = repository
        self.database_url = repository.database_url
        self.turn_guard = turn_guard
        self.telegram_destination = telegram_destination

    @staticmethod
    def _load_customer_booking(
        session: Session,
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
        if not customer_id:
            raise DomainError("booking_not_found", "Booking not found.", 404)
        statement = booking_query().where(
            BookingRow.id == booking_id,
            BookingRow.business_id == business_id,
            BookingRow.customer_id == customer_id,
        )
        if for_update:
            statement = statement.with_for_update(of=BookingRow)
        row = session.execute(statement).mappings().first()
        if row is None:
            raise DomainError("booking_not_found", "Booking not found.", 404)
        return row

    def _lock_business_and_check_retry(
        self,
        session: Session,
        business_id: str,
        operation: str,
        idempotency_key: str,
        request_hash: str,
    ):
        """Serialize retry checks so concurrent writes cannot both see an unused key.

        The request hash binds the key to the original booking details, even if
        the caller lost the response after commit.
        """
        if self.turn_guard:
            self.turn_guard(session)
        if not idempotency_key or len(idempotency_key) > 200:
            raise DomainError(
                "invalid_idempotency_key",
                "Idempotency-Key must contain 1 to 200 characters.",
                422,
            )
        with timed_event("booking.business_lock", operation=operation):
            business = session.scalar(
                select(BusinessRow.id).where(BusinessRow.id == business_id)
                .with_for_update(key_share=True)
            )
        if business is None:
            raise DomainError("business_not_found", "Business not found.", 404)
        record = session.get(IdempotencyRecord, (business_id, operation, idempotency_key))
        if record and record.request_hash != request_hash:
            debug_event("booking.retry.rejected", operation=operation)
            raise DomainError(
                "idempotency_key_reused",
                "This idempotency key was already used for a different request.",
                409,
            )
        debug_event("booking.retry.checked", operation=operation, replay=record is not None)
        return record

    def _save_retry_result(
        self,
        session: Session,
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
        session.add(IdempotencyRecord(
            business_id=business_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            resource_id=booking_id,
            result_code=result_code,
        ))

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
            with timed_event("booking.write", operation="create"), database_session(self.database_url) as session:
                record = self._lock_business_and_check_retry(
                    session,
                    request.business_id,
                    "create",
                    idempotency_key,
                    request_hash,
                )
                if record:
                    row = self._load_customer_booking(
                        session,
                        request.business_id,
                        request.customer_id,
                        record.resource_id,
                    )
                    return BookingSummary(
                        booking=booking_from_row(row), outcome="replayed"
                    )

                slot = self._revalidate_booking_slot(session, request)
                self._require_customer_in_business(
                    session, request.business_id, request.customer_id
                )
                booking_id = self._insert_scheduled_booking(
                    session, slot, request.customer_id
                )
                if self.telegram_destination:
                    bot_id, chat_id = self.telegram_destination
                    session.execute(
                        update(BookingRow).where(BookingRow.id == booking_id)
                        .values(telegram_bot_id=bot_id, telegram_chat_id=chat_id)
                    )
                send_at = self._calculate_reminder_time(session, slot)
                self._queue_booking_reminder(
                    session, request.business_id, booking_id, send_at
                )
                self._save_retry_result(
                    session,
                    request.business_id,
                    "create",
                    idempotency_key,
                    request_hash,
                    booking_id,
                    "created",
                )
                row = self._load_customer_booking(
                    session, request.business_id, request.customer_id, booking_id
                )
                result = BookingSummary(booking=booking_from_row(row), outcome="created")
            debug_event("booking.saved", operation="create", outcome="created")
            return result
        except IntegrityError as exc:
            if not isinstance(exc.orig, ExclusionViolation):
                raise
            raise DomainError(
                "slot_unavailable",
                "That time is no longer available. Choose another slot.",
                409,
            ) from exc

    @staticmethod
    def _revalidate_booking_slot(
        session: Session,
        request: BookingCreateRequest | BookingRescheduleRequest,
        *,
        exclude_booking_id: str | None = None,
    ) -> AvailabilitySlot:
        """Recheck the slot in the write transaction because offers can go stale.

        Exclude the booking being moved so its old interval cannot block its own move.
        """
        return validate_slot_in_transaction(
            session,
            business_id=request.business_id,
            service_id=request.service_id,
            professional_id=request.professional_id,
            slot_id=request.slot_id,
            starts_at=request.starts_at,
            exclude_booking_id=exclude_booking_id,
        )

    @staticmethod
    def _require_customer_in_business(
        session: Session, business_id: str, customer_id: str
    ) -> None:
        """Prevent a customer from one business from owning another business's booking."""
        customer = session.scalar(select(CustomerRow.id).where(
            CustomerRow.id == customer_id, CustomerRow.business_id == business_id,
        ))
        if customer is None:
            raise DomainError("customer_not_found", "Customer not found.", 404)

    def _insert_scheduled_booking(
        self, session: Session, slot: AvailabilitySlot, customer_id: str
    ) -> str:
        booking_id = str(uuid.uuid4())
        occupied_from, occupied_until = self._calculate_occupied_interval(
            session, slot
        )
        session.add(BookingRow(
            id=booking_id,
            business_id=slot.business_id,
            service_id=slot.service_id,
            professional_id=slot.professional_id,
            customer_id=customer_id,
            starts_at=slot.starts_at,
            ends_at=slot.ends_at,
            occupied_from=occupied_from,
            occupied_until=occupied_until,
            status=BookingStatus.SCHEDULED.value,
        ))
        session.flush()
        return booking_id

    @staticmethod
    def _calculate_occupied_interval(
        session, slot: AvailabilitySlot
    ) -> tuple[datetime, datetime]:
        """Expand the slot with service buffers so setup and cleanup also block time."""
        before, after = session.execute(
            select(ServiceRow.buffer_before_minutes, ServiceRow.buffer_after_minutes)
            .where(ServiceRow.id == slot.service_id)
        ).one()
        return slot.starts_at - timedelta(minutes=before), slot.ends_at + timedelta(minutes=after)

    @staticmethod
    def _calculate_reminder_time(session: Session, slot: AvailabilitySlot) -> datetime:
        """Use the same reminder policy for new and moved bookings."""
        minutes = session.scalar(
            select(BusinessRow.reminder_lead_minutes).where(BusinessRow.id == slot.business_id)
        )
        return slot.starts_at - timedelta(minutes=minutes)

    @staticmethod
    def _queue_booking_reminder(
        session: Session, business_id: str, booking_id: str, send_at: datetime
    ) -> None:
        """Share the booking transaction so a rollback also removes the reminder."""
        destination = select(
            literal(str(uuid.uuid4())), BookingRow.business_id, BookingRow.id, literal(send_at),
            literal("pending"), TelegramBotRow.bot_id,
            case((TelegramBotRow.bot_id.is_not(None), BookingRow.telegram_chat_id)),
        ).select_from(BookingRow).outerjoin(
            TelegramBotRow,
            (TelegramBotRow.bot_id == BookingRow.telegram_bot_id)
            & (TelegramBotRow.business_id == BookingRow.business_id)
            & TelegramBotRow.enabled
            & TelegramBotRow.reminder_delivery,
        ).where(BookingRow.id == booking_id, BookingRow.business_id == business_id)
        session.execute(insert(ReminderRow).from_select(
            ["id", "business_id", "booking_id", "send_at", "status", "telegram_bot_id", "telegram_chat_id"],
            destination,
        ))
        debug_event("reminder.write.staged", operation="schedule")

    def read(
        self, business_id: str, customer_id: str | None, booking_id: str
    ) -> Booking:
        """Return booking details only when the business and customer both match."""
        with database_session(self.database_url) as session:
            row = self._load_customer_booking(
                session, business_id, customer_id, booking_id
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
            with timed_event("booking.write", operation=operation), database_session(self.database_url) as session:
                record = self._lock_business_and_check_retry(
                    session, business_id, operation, idempotency_key, request_hash
                )
                row = self._load_customer_booking(
                    session, business_id, customer_id, booking_id, for_update=True
                )
                if record:
                    return booking_from_row(row), record.result_code
                self._require_upcoming_booking(row["starts_at"])
                outcome = self._write_booking_status(
                    session, booking_id, BookingStatus(row["status"]), operation
                )
                self._save_retry_result(
                    session,
                    business_id,
                    operation,
                    idempotency_key,
                    request_hash,
                    booking_id,
                    outcome,
                )
                updated = self._load_customer_booking(
                    session, business_id, customer_id, booking_id
                )
                result = booking_from_row(updated), outcome
            debug_event("booking.saved", operation=operation, outcome=outcome)
            return result
        except IntegrityError as exc:
            if not isinstance(exc.orig, ExclusionViolation):
                raise
            raise DomainError(
                "booking_conflict",
                "The booking could not be updated because the time is no longer available.",
                409,
            ) from exc

    @staticmethod
    def _require_upcoming_booking(starts_at: datetime) -> None:
        if starts_at <= datetime.now(UTC):
            raise DomainError(
                "booking_started", "This booking can no longer be changed.", 409
            )

    @staticmethod
    def _write_booking_status(
        session: Session,
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

        session.execute(
            update(BookingRow).where(BookingRow.id == booking_id)
            .values(status=target.value, updated_at=func.now())
        )
        if target == BookingStatus.CANCELED:
            session.execute(update(ReminderRow).where(
                ReminderRow.booking_id == booking_id, ReminderRow.status == "pending",
            ).values(status="canceled", generation=ReminderRow.generation + 1))
            reminder_ids = select(ReminderRow.id).where(ReminderRow.booking_id == booking_id)
            session.execute(update(TelegramOutboxRow).where(
                TelegramOutboxRow.status == "ready", TelegramOutboxRow.reminder_id.in_(reminder_ids),
            ).values(status="canceled", message=None, finished_at=func.now()))
            debug_event("reminder.write.staged", operation="cancel")
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
            with timed_event("booking.write", operation="reschedule"), database_session(self.database_url) as session:
                record = self._lock_business_and_check_retry(
                    session, business_id, "reschedule", idempotency_key, request_hash
                )
                row = self._load_customer_booking(
                    session, business_id, customer_id, booking_id, for_update=True
                )
                if record:
                    return booking_from_row(row), record.result_code
                if BookingStatus(row["status"]) == BookingStatus.CANCELED:
                    raise DomainError(
                        "booking_not_reschedulable",
                        "A canceled booking cannot be rescheduled.",
                        409,
                    )
                self._require_upcoming_booking(row["starts_at"])
                slot = self._revalidate_booking_slot(
                    session, request, exclude_booking_id=booking_id
                )
                self._update_booking_slot(session, booking_id, slot)
                self._move_booking_reminder(session, booking_id, slot)
                self._save_retry_result(
                    session,
                    business_id,
                    "reschedule",
                    idempotency_key,
                    request_hash,
                    booking_id,
                    "rescheduled",
                )
                updated = self._load_customer_booking(
                    session, business_id, customer_id, booking_id
                )
                result = booking_from_row(updated), "rescheduled"
            debug_event("booking.saved", operation="reschedule", outcome="rescheduled")
            return result
        except IntegrityError as exc:
            if not isinstance(exc.orig, ExclusionViolation):
                raise
            raise DomainError(
                "slot_unavailable",
                "That time is no longer available. Choose another slot.",
                409,
            ) from exc

    def _update_booking_slot(
        self, session: Session, booking_id: str, slot: AvailabilitySlot
    ) -> None:
        """Move the slot and its buffers so conflict checks use the new interval."""
        occupied_from, occupied_until = self._calculate_occupied_interval(
            session, slot
        )
        session.execute(update(BookingRow).where(BookingRow.id == booking_id).values(
            service_id=slot.service_id,
            professional_id=slot.professional_id,
            starts_at=slot.starts_at,
            ends_at=slot.ends_at,
            occupied_from=occupied_from,
            occupied_until=occupied_until,
            updated_at=func.now(),
        ))

    def _move_booking_reminder(
        self, session: Session, booking_id: str, slot: AvailabilitySlot
    ) -> None:
        """Keep reminders aligned with the appointment's new time.

        Lock and retime the pending job so the worker cannot deliver it while
        it is being updated. If there is no pending job, queue one for the new
        time. This also covers moves after the first reminder was delivered.
        """
        send_at = self._calculate_reminder_time(session, slot)
        pending_id = session.scalar(select(ReminderRow.id).where(
            ReminderRow.booking_id == booking_id, ReminderRow.status == "pending",
        ).with_for_update())
        if pending_id:
            session.execute(update(ReminderRow).where(ReminderRow.id == pending_id).values(
                send_at=send_at, last_error=None, generation=ReminderRow.generation + 1,
            ))
            session.execute(update(TelegramOutboxRow).where(
                TelegramOutboxRow.reminder_id == pending_id, TelegramOutboxRow.status == "ready",
            ).values(status="canceled", message=None, finished_at=func.now()))
            debug_event("reminder.write.staged", operation="move")
        else:
            self._queue_booking_reminder(
                session, slot.business_id, booking_id, send_at
            )

from __future__ import annotations

import argparse
import time
import uuid
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.channels.telegram_publications import schedule_publication
from app.config import load_settings
from app.database import database_session
from app.db_models import (
    BookingRow, BusinessRow, ReminderInboxRow, ReminderRow, ServiceRow,
    TelegramBotRow, TelegramIdentity, TelegramOutboxRow,
)
from app.observability import debug_event


def reminder_message(booking: dict) -> str:
    starts_at = booking["starts_at"]
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=timezone.utc)
    local_label = starts_at.astimezone(
        ZoneInfo(booking["business_timezone"]),
    ).strftime("%Y-%m-%d %H:%M %Z")
    return (
        f"Reminder: {booking['service_name']} is scheduled for "
        f"{local_label}. Reply here if you need to reschedule."
    )


def _due_telegram_reminders(limit: int):
    return (
        select(
            ReminderRow, BookingRow.starts_at, BookingRow.status.label("booking_status"),
            ServiceRow.name.label("service_name"), BusinessRow.timezone.label("business_timezone"),
            TelegramIdentity.customer_id.is_not(None).label("authorized"),
        )
        .join(BookingRow, and_(
            BookingRow.id == ReminderRow.booking_id, BookingRow.business_id == ReminderRow.business_id,
        ))
        .join(BusinessRow, BusinessRow.id == ReminderRow.business_id)
        .join(ServiceRow, ServiceRow.id == BookingRow.service_id)
        .outerjoin(TelegramBotRow, and_(
            TelegramBotRow.bot_id == ReminderRow.telegram_bot_id,
            TelegramBotRow.business_id == ReminderRow.business_id,
            TelegramBotRow.enabled, TelegramBotRow.reminder_delivery,
        ))
        .outerjoin(TelegramIdentity, and_(
            TelegramIdentity.business_id == ReminderRow.business_id,
            TelegramIdentity.bot_id == TelegramBotRow.bot_id,
            TelegramIdentity.telegram_user_id == ReminderRow.telegram_chat_id,
            TelegramIdentity.customer_id == BookingRow.customer_id,
        ))
        .where(
            ReminderRow.status == "pending", ReminderRow.send_at <= func.clock_timestamp(),
            ReminderRow.telegram_bot_id.is_not(None),
            ReminderRow.queued_generation.is_distinct_from(ReminderRow.generation),
        )
        .order_by(ReminderRow.send_at, ReminderRow.id)
        .limit(limit)
        .with_for_update(of=ReminderRow, skip_locked=True)
    )


def enqueue_telegram_reminders(database_url: str, limit: int = 25) -> int:
    """Queue each due generation and record the result in the same transaction."""
    with database_session(database_url) as session:
        rows = session.execute(_due_telegram_reminders(limit)).mappings().all()
        for row in rows:
            reminder = row[ReminderRow]
            if not row["authorized"] or row["booking_status"] == "canceled":
                debug_event("reminder.telegram.skipped", reason="invalid_delivery_route")
                reminder.status = "failed"
                reminder.last_error = "invalid_delivery_route"
                continue
            _queue_telegram_reminder(session, reminder, reminder_message(row))
    if rows:
        debug_event("reminder.telegram.batch_handled", count=len(rows))
    return len(rows)


def _queue_telegram_reminder(session: Session, reminder: ReminderRow, message: str) -> None:
    """Use the reminder generation to deduplicate wake-ups after rescheduling."""
    outgoing_id = session.scalar(insert(TelegramOutboxRow).values(
        business_id=reminder.business_id, bot_id=reminder.telegram_bot_id,
        chat_id=reminder.telegram_chat_id, reminder_id=reminder.id,
        reminder_generation=reminder.generation, message=message,
    ).on_conflict_do_nothing(
        index_elements=["reminder_id", "reminder_generation"],
    ).returning(TelegramOutboxRow.id))
    if outgoing_id is not None:
        schedule_publication(session, outgoing_id=outgoing_id)
    reminder.queued_generation = reminder.generation
    debug_event("reminder.telegram.outbox_staged")


class ReminderWorker:
    """Postgres poller for reminder delivery; intentionally has no graph dependency."""

    def __init__(self, database_url: str):
        self.database_url = database_url

    def deliver_due(self, limit: int = 25) -> int:
        statement = select(ReminderRow).where(
            ReminderRow.status == "pending", ReminderRow.send_at <= func.now(),
            ReminderRow.telegram_bot_id.is_(None),
        ).order_by(ReminderRow.send_at).limit(limit).with_for_update(skip_locked=True)
        with database_session(self.database_url) as session:
            reminders = session.scalars(statement).all()
            for reminder in reminders:
                try:
                    with session.begin_nested():
                        self._deliver_to_demo_inbox(session, reminder)
                except Exception as exc:
                    self._record_failed_delivery(reminder, exc)
                    session.flush()
        if reminders:
            debug_event("reminder.demo.batch_handled", count=len(reminders))
        return len(reminders)

    @staticmethod
    def _deliver_to_demo_inbox(session: Session, reminder: ReminderRow) -> None:
        booking = session.execute(
            select(BookingRow.status, BookingRow.starts_at, BusinessRow.timezone.label("business_timezone"),
                   ServiceRow.name.label("service_name"))
            .join(ServiceRow, ServiceRow.id == BookingRow.service_id)
            .join(BusinessRow, BusinessRow.id == BookingRow.business_id)
            .where(BookingRow.id == reminder.booking_id)
        ).mappings().first()
        if booking is None or booking["status"] == "canceled":
            debug_event("reminder.demo.skipped", reason="booking_unavailable")
            reminder.status = "canceled"
            return
        session.execute(insert(ReminderInboxRow).values(
            id=str(uuid.uuid4()), reminder_id=reminder.id, booking_id=reminder.booking_id,
            business_id=reminder.business_id, message=reminder_message(booking),
        ).on_conflict_do_nothing(index_elements=["reminder_id"]))
        reminder.status = "delivered"
        reminder.delivered_at = func.now()
        reminder.last_error = None

    @staticmethod
    def _record_failed_delivery(reminder: ReminderRow, error: Exception) -> None:
        # The demo worker's retry budget counts failed deliveries only.
        reminder.attempts += 1
        should_retry = reminder.attempts < 3
        reason = type(error.orig if isinstance(error, DBAPIError) else error).__name__
        debug_event("reminder.demo.delivery_failed", attempt=reminder.attempts,
                    will_retry=should_retry, error_type=reason)
        reminder.status = "pending" if should_retry else "failed"
        reminder.last_error = reason
        if should_retry:
            reminder.send_at = func.now() + timedelta(seconds=30)

    def run_forever(self, poll_seconds: float = 2.0) -> None:
        debug_event("reminder.demo.worker_started", poll_seconds=poll_seconds)
        while True:
            self.deliver_due()
            time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local demo reminder worker")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    settings = load_settings()
    ReminderWorker(settings.database_url).run_forever(args.poll_seconds)


if __name__ == "__main__":
    main()

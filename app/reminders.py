from __future__ import annotations

import argparse
import time
import uuid
from datetime import timezone
from zoneinfo import ZoneInfo

from app.config import load_settings
from app.database import connect


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


def enqueue_telegram_reminders(database_url: str, limit: int = 25) -> int:
    """Queue each due generation and record the result in the same transaction."""
    with connect(database_url) as connection:
        rows = connection.execute(
            """SELECT r.id,r.business_id,r.generation,r.telegram_bot_id,r.telegram_chat_id,
                      b.starts_at,b.status AS booking_status,s.name AS service_name,
                      biz.timezone AS business_timezone,i.customer_id IS NOT NULL AS authorized
               FROM reminders r
               JOIN bookings b ON b.id=r.booking_id AND b.business_id=r.business_id
               JOIN businesses biz ON biz.id=r.business_id
               JOIN services s ON s.id=b.service_id
               LEFT JOIN telegram_bots t ON t.bot_id=r.telegram_bot_id
                 AND t.business_id=r.business_id AND t.enabled AND t.reminder_delivery
               LEFT JOIN telegram_identities i ON i.business_id=r.business_id AND i.bot_id=t.bot_id
                 AND i.telegram_user_id=r.telegram_chat_id AND i.customer_id=b.customer_id
               WHERE r.status='pending' AND r.send_at <= clock_timestamp()
                 AND r.telegram_bot_id IS NOT NULL
                 AND r.queued_generation IS DISTINCT FROM r.generation
               ORDER BY r.send_at,r.id LIMIT %s FOR UPDATE OF r SKIP LOCKED""",
            (limit,),
        ).fetchall()
        for row in rows:
            if not row["authorized"] or row["booking_status"] == "canceled":
                connection.execute(
                    """UPDATE reminders SET status='failed',last_error='invalid_delivery_route'
                       WHERE id=%s""",
                    (row["id"],),
                )
                continue
            connection.execute(
                """INSERT INTO telegram_outbox
                       (business_id,bot_id,chat_id,reminder_id,reminder_generation,message)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (reminder_id,reminder_generation) DO NOTHING""",
                (row["business_id"], row["telegram_bot_id"], row["telegram_chat_id"],
                 row["id"], row["generation"], reminder_message(row)),
            )
            connection.execute(
                "UPDATE reminders SET queued_generation=%s WHERE id=%s",
                (row["generation"], row["id"]),
            )
        return len(rows)


class ReminderWorker:
    """Postgres poller for reminder delivery; intentionally has no graph dependency."""

    def __init__(self, database_url: str):
        self.database_url = database_url

    def deliver_due(self, limit: int = 25) -> int:
        with connect(self.database_url) as connection:
            rows = connection.execute(
                """SELECT id,business_id,booking_id,attempts FROM reminders
                   WHERE status='pending' AND send_at <= now() AND telegram_bot_id IS NULL
                   ORDER BY send_at LIMIT %s FOR UPDATE SKIP LOCKED""",
                (limit,),
            ).fetchall()
            for reminder in rows:
                try:
                    with connection.transaction():
                        booking = connection.execute(
                            """SELECT b.status,b.starts_at,biz.timezone AS business_timezone,
                                      s.name AS service_name
                               FROM bookings b JOIN services s ON s.id=b.service_id
                               JOIN businesses biz ON biz.id=b.business_id WHERE b.id=%s""",
                            (reminder["booking_id"],),
                        ).fetchone()
                        if booking is None or booking["status"] == "canceled":
                            connection.execute(
                                "UPDATE reminders SET status='canceled' WHERE id=%s",
                                (reminder["id"],),
                            )
                            continue
                        message = reminder_message(booking)
                        connection.execute(
                            """INSERT INTO reminder_inbox (id,reminder_id,booking_id,business_id,message)
                               VALUES (%s,%s,%s,%s,%s) ON CONFLICT (reminder_id) DO NOTHING""",
                            (
                                str(uuid.uuid4()),
                                reminder["id"],
                                reminder["booking_id"],
                                reminder["business_id"],
                                message,
                            ),
                        )
                        connection.execute(
                            "UPDATE reminders SET status='delivered',delivered_at=now(),last_error=NULL WHERE id=%s",
                            (reminder["id"],),
                        )
                except Exception as exc:
                    # attempts counts failed deliveries, not successful claims.
                    attempts = reminder["attempts"] + 1
                    should_retry = attempts < 3
                    connection.execute(
                        """UPDATE reminders
                           SET status=%s, attempts=%s, last_error=%s,
                               send_at=CASE
                                   WHEN %s THEN now() + interval '30 seconds'
                                   ELSE send_at
                               END
                           WHERE id=%s""",
                        (
                            "pending" if should_retry else "failed",
                            attempts,
                            type(exc).__name__,
                            should_retry,
                            reminder["id"],
                        ),
                    )
        return len(rows)

    def run_forever(self, poll_seconds: float = 2.0) -> None:
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

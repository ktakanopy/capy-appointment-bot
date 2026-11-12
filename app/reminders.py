from __future__ import annotations

import argparse
import time
import uuid
from datetime import timezone
from zoneinfo import ZoneInfo

from app.config import load_settings
from app.database import connect


class DemoInboxSender:
    def send(self, connection, reminder, booking, message: str) -> None:
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


class ReminderWorker:
    """Postgres poller for reminder delivery; intentionally has no graph dependency."""

    def __init__(self, database_url: str, sender=None):
        self.database_url = database_url
        self.sender = sender or DemoInboxSender()

    def deliver_due(self, limit: int = 25) -> int:
        with connect(self.database_url) as connection:
            rows = connection.execute(
                """SELECT id,business_id,booking_id,attempts FROM reminders
                   WHERE status='pending' AND send_at <= now()
                   ORDER BY send_at LIMIT %s FOR UPDATE SKIP LOCKED""",
                (limit,),
            ).fetchall()
            for reminder in rows:
                try:
                    with connection.transaction():
                        booking = connection.execute(
                            """SELECT b.status,b.starts_at,biz.timezone AS business_timezone,
                                      c.full_name,s.name AS service_name
                               FROM bookings b JOIN customers c ON c.id=b.customer_id
                               JOIN services s ON s.id=b.service_id
                               JOIN businesses biz ON biz.id=b.business_id WHERE b.id=%s""",
                            (reminder["booking_id"],),
                        ).fetchone()
                        if booking is None or booking["status"] == "canceled":
                            connection.execute(
                                "UPDATE reminders SET status='canceled' WHERE id=%s",
                                (reminder["id"],),
                            )
                            continue
                        starts_at = booking["starts_at"]
                        if starts_at.tzinfo is None:
                            starts_at = starts_at.replace(tzinfo=timezone.utc)
                        local_label = starts_at.astimezone(
                            ZoneInfo(booking["business_timezone"])
                        ).strftime("%Y-%m-%d %H:%M %Z")
                        message = (
                            f"Reminder: {booking['service_name']} is scheduled for "
                            f"{local_label}. Reply here if you need to reschedule."
                        )
                        self.sender.send(connection, reminder, booking, message)
                        connection.execute(
                            "UPDATE reminders SET status='delivered',delivered_at=now(),last_error=NULL WHERE id=%s",
                            (reminder["id"],),
                        )
                except Exception as exc:
                    # attempts counts failed deliveries, not successful claims.
                    attempts = reminder["attempts"] + 1
                    if attempts < 3:
                        connection.execute(
                            """UPDATE reminders SET status='pending',send_at=now()+interval '30 seconds',
                               attempts=%s,last_error=%s WHERE id=%s""",
                            (attempts, type(exc).__name__, reminder["id"]),
                        )
                    else:
                        connection.execute(
                            "UPDATE reminders SET status='failed',attempts=%s,last_error=%s WHERE id=%s",
                            (attempts, type(exc).__name__, reminder["id"]),
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

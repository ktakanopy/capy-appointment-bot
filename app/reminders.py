from __future__ import annotations

import argparse
import time
import uuid

from app.config import load_settings
from app.database import connect


class ReminderWorker:
    """Postgres poller for the local demo inbox; intentionally has no graph dependency."""

    def __init__(self, database_url: str):
        self.database_url = database_url

    def deliver_due(self, limit: int = 25) -> int:
        with connect(self.database_url) as connection:
            rows = connection.execute(
                """WITH due AS (
                       SELECT id FROM reminders WHERE status='pending' AND send_at <= now()
                       ORDER BY send_at LIMIT %s FOR UPDATE SKIP LOCKED
                   )
                   UPDATE reminders r SET status='processing',attempts=r.attempts+1,claimed_at=now()
                   FROM due WHERE r.id=due.id RETURNING r.id,r.business_id,r.booking_id,r.attempts""",
                (limit,),
            ).fetchall()
            for reminder in rows:
                try:
                    with connection.transaction():
                        booking = connection.execute(
                            """SELECT b.status,b.starts_at,c.full_name,s.name AS service_name
                               FROM bookings b JOIN customers c ON c.id=b.customer_id
                               JOIN services s ON s.id=b.service_id WHERE b.id=%s""",
                            (reminder["booking_id"],),
                        ).fetchone()
                        if booking is None or booking["status"] == "canceled":
                            connection.execute("UPDATE reminders SET status='canceled',claimed_at=NULL WHERE id=%s", (reminder["id"],))
                            continue
                        message = (
                            f"Reminder: {booking['service_name']} is scheduled for "
                            f"{booking['starts_at'].isoformat()}. Reply here if you need to reschedule."
                        )
                        connection.execute(
                            """INSERT INTO reminder_inbox (id,reminder_id,booking_id,business_id,message)
                               VALUES (%s,%s,%s,%s,%s) ON CONFLICT (reminder_id) DO NOTHING""",
                            (str(uuid.uuid4()), reminder["id"], reminder["booking_id"], reminder["business_id"], message),
                        )
                        connection.execute(
                            "UPDATE reminders SET status='delivered',delivered_at=now(),claimed_at=NULL,last_error=NULL WHERE id=%s",
                            (reminder["id"],),
                        )
                except Exception as exc:
                    if reminder["attempts"] < 3:
                        connection.execute(
                            """UPDATE reminders SET status='pending',send_at=now()+interval '30 seconds',
                               claimed_at=NULL,last_error=%s WHERE id=%s""",
                            (type(exc).__name__, reminder["id"]),
                        )
                    else:
                        connection.execute(
                            "UPDATE reminders SET status='failed',claimed_at=NULL,last_error=%s WHERE id=%s",
                            (type(exc).__name__, reminder["id"]),
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

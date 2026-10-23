from __future__ import annotations

from datetime import datetime, timezone

from app.database import connect
from app.reminders import ReminderWorker
from tests.integration.helpers import create_booking, offered_slots


def test_due_reminder_is_delivered_once_to_demo_inbox(scheduling_db):
    slot = offered_slots(scheduling_db)[0]
    created = create_booking(scheduling_db, slot, key="reminder-deliver", phone="+5511999940001")
    with connect(scheduling_db["url"]) as connection:
        connection.execute(
            "UPDATE reminders SET send_at=%s WHERE booking_id=%s",
            (datetime.now(timezone.utc), created.booking.id),
        )

    worker = ReminderWorker(scheduling_db["url"])
    assert worker.deliver_due() == 1
    assert worker.deliver_due() == 0
    with connect(scheduling_db["url"]) as connection:
        reminder = connection.execute(
            "SELECT status,attempts FROM reminders WHERE booking_id=%s", (created.booking.id,)
        ).fetchone()
        inbox = connection.execute(
            "SELECT count(*) AS n FROM reminder_inbox WHERE booking_id=%s", (created.booking.id,)
        ).fetchone()
    assert reminder["status"] == "delivered"
    assert reminder["attempts"] == 1
    assert inbox["n"] == 1


def test_worker_failure_is_retryable_then_reaches_bounded_terminal_state(scheduling_db):
    slot = offered_slots(scheduling_db)[0]
    created = create_booking(scheduling_db, slot, key="reminder-failure", phone="+5511999940002")
    with connect(scheduling_db["url"]) as connection:
        connection.execute(
            "UPDATE reminders SET send_at=%s WHERE booking_id=%s",
            (datetime.now(timezone.utc), created.booking.id),
        )
        connection.execute("""CREATE FUNCTION fail_demo_inbox_insert() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'simulated inbox outage'; END $$""")
        connection.execute("""CREATE TRIGGER fail_demo_inbox_insert_trigger
            BEFORE INSERT ON reminder_inbox FOR EACH ROW EXECUTE FUNCTION fail_demo_inbox_insert()""")

    try:
        worker = ReminderWorker(scheduling_db["url"])
        for expected_attempt in range(1, 4):
            assert worker.deliver_due() == 1
            with connect(scheduling_db["url"]) as connection:
                state = connection.execute(
                    "SELECT status,attempts,last_error FROM reminders WHERE booking_id=%s",
                    (created.booking.id,),
                ).fetchone()
                connection.execute(
                    "UPDATE reminders SET send_at=%s WHERE booking_id=%s",
                    (datetime.now(timezone.utc), created.booking.id),
                )
            assert state["attempts"] == expected_attempt
            if expected_attempt < 3:
                assert state["status"] == "pending"
                assert state["last_error"] == "RaiseException"
            else:
                assert state["status"] == "failed"
                assert state["last_error"] == "RaiseException"
    finally:
        with connect(scheduling_db["url"]) as connection:
            connection.execute("DROP TRIGGER IF EXISTS fail_demo_inbox_insert_trigger ON reminder_inbox")
            connection.execute("DROP FUNCTION IF EXISTS fail_demo_inbox_insert()")
            connection.execute(
                "UPDATE reminders SET status='pending',send_at=%s,last_error=NULL WHERE booking_id=%s",
                (datetime.now(timezone.utc), created.booking.id),
            )

    assert worker.deliver_due() == 1
    with connect(scheduling_db["url"]) as connection:
        state = connection.execute(
            "SELECT status,attempts FROM reminders WHERE booking_id=%s", (created.booking.id,)
        ).fetchone()
        inbox = connection.execute(
            "SELECT count(*) AS n FROM reminder_inbox WHERE booking_id=%s", (created.booking.id,)
        ).fetchone()
    assert state["status"] == "delivered"
    assert state["attempts"] == 4
    assert inbox["n"] == 1

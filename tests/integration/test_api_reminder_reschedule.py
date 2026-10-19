from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.database import connect
from app.main import app
from app.reminders import ReminderWorker
from tests.integration.helpers import next_open_day


def test_api_booking_reminder_reply_reschedules_and_updates_owner_agenda(scheduling_db, caplog):
    target_date = next_open_day().isoformat()
    with caplog.at_level(logging.INFO, logger="service_scheduling"):
        with TestClient(app) as client:
            catalog_response = client.get("/businesses/demo-studio/catalog")
            assert catalog_response.status_code == 200
            assert catalog_response.json()["services"][0]["id"] == "service-demo"

            search_response = client.post("/availability/search", json={
                "business_id": "demo-studio",
                "service_id": "service-demo",
                "start_date": target_date,
                "end_date": target_date,
            })
            assert search_response.status_code == 200
            slot = search_response.json()["slots"][0]

            create_response = client.post("/bookings", headers={"Idempotency-Key": "api-create"}, json={
                "business_id": slot["business_id"],
                "service_id": slot["service_id"],
                "professional_id": slot["professional_id"],
                "slot_id": slot["slot_id"],
                "starts_at": slot["starts_at"],
                "customer": {"full_name": "API Customer", "phone": "+5511999950001"},
            })
            assert create_response.status_code == 200
            created = create_response.json()
            token = created["manage_token"]
            booking_id = created["booking"]["id"]
            assert client.get("/bookings/me", headers={"X-Booking-Token": token}).json()["id"] == booking_id

            with connect(scheduling_db["url"]) as connection:
                connection.execute(
                    "UPDATE reminders SET send_at=%s WHERE booking_id=%s",
                    (datetime.now(timezone.utc), booking_id),
                )
            assert ReminderWorker(scheduling_db["url"]).deliver_due() == 1
            inbox = client.get("/demo/reminders", params={"business_id": "demo-studio"}).json()["reminders"]
            assert len(inbox) == 1
            assert inbox[0]["booking_id"] == booking_id

            session = client.post("/sessions/new").json()["session_id"]
            search_reply = client.post("/chat", json={
                "session_id": session,
                "message": "Please reschedule this booking to tomorrow",
                "manage_token": token,
            })
            assert search_reply.status_code == 200
            proposed = search_reply.json()["slots"]
            assert proposed
            selected = proposed[0]

            move_reply = client.post("/chat", json={
                "session_id": session,
                "message": "That time works",
                "manage_token": token,
                "selected_slot_id": selected["slot_id"],
            })
            assert move_reply.status_code == 200
            assert move_reply.json()["intent"] == "reschedule"
            selected_start = datetime.fromisoformat(selected["starts_at"])
            moved_start = datetime.fromisoformat(move_reply.json()["booking"]["starts_at"])
            assert moved_start.astimezone(timezone.utc) == selected_start.astimezone(timezone.utc)

            agenda = client.get("/owner/bookings", params={
                "business_id": "demo-studio",
                "start_date": target_date,
                "end_date": target_date,
            }).json()["bookings"]
            agenda_start = datetime.fromisoformat(next(item for item in agenda if item["id"] == booking_id)["starts_at"])
            assert agenda_start.astimezone(timezone.utc) == selected_start.astimezone(timezone.utc)

            graph_state = app.state.runtime.graph.get_state({"configurable": {"thread_id": session}}).values
            assert token not in repr(graph_state)
            assert token not in caplog.text

    with connect(scheduling_db["url"]) as connection:
        reminder_rows = connection.execute(
            "SELECT status,send_at FROM reminders WHERE booking_id=%s ORDER BY created_at", (booking_id,)
        ).fetchall()
    assert sorted(row["status"] for row in reminder_rows) == ["delivered", "pending"]
    assert token not in repr(reminder_rows)

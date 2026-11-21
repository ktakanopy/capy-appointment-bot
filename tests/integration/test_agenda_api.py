"""Verify that the private HTTP API applies the existing booking rules."""

from fastapi.testclient import TestClient

from app.channels.telegram_repository import TelegramRepository
from app.main import app
from tests.integration.helpers import next_open_day


def test_agenda_authentication_and_booking_lifecycle(scheduling_db, monkeypatch):
    token = "private-agenda-test-token-at-least-32-chars"
    monkeypatch.setenv("AGENDA_API_TOKEN", token)
    customer = TelegramRepository(scheduling_db["url"]).ensure_customer_for_sender(
        "demo-studio", 42, 101, "Example Customer"
    )
    with TestClient(app) as caller:
        assert caller.get("/agenda/catalog").status_code == 401

    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as caller:
        catalog = caller.get("/agenda/catalog").json()
        day = next_open_day().isoformat()
        service = catalog["services"][0]
        params = {"start_date": day, "end_date": day, "service_id": service["id"]}
        slots = caller.get("/agenda/slots", params=params).json()
        assert len(slots) >= 2
        slot_fields = ("slot_id", "service_id", "professional_id", "starts_at")
        body = {field: slots[0][field] for field in slot_fields}
        assert caller.post("/agenda/bookings", json=body).status_code == 422
        body["customer_id"] = customer["customer_id"]
        first = caller.post("/agenda/bookings", json=body, headers={"Idempotency-Key": "create"})
        assert first.status_code == 200, first.text
        booking_id = first.json()["booking"]["id"]
        replay = caller.post("/agenda/bookings", json=body, headers={"Idempotency-Key": "create"})
        assert replay.json()["booking"]["id"] == booking_id
        assert replay.json()["outcome"] == "replayed"

        other = caller.post("/agenda/bookings", json=body, headers={"Idempotency-Key": "conflict"})
        assert other.status_code == 409

        moved = caller.post(
            f"/agenda/bookings/{booking_id}/reschedule",
            json={field: slots[1][field] for field in slot_fields},
            headers={"Idempotency-Key": "move"},
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["booking"]["starts_at"] == slots[1]["starts_at"]
        canceled = caller.post(
            f"/agenda/bookings/{booking_id}/cancel", headers={"Idempotency-Key": "cancel"}
        )
        assert canceled.json()["booking"]["status"] == "canceled"

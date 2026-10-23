from __future__ import annotations

import uuid
from typing import Any

import httpx


class BackendClient:
    def __init__(self, base_url: str, timeout: float = 20.0, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(base_url=self.base_url, timeout=timeout)

    def create_session(self) -> dict[str, Any]:
        response = self._client.post("/sessions/new")
        response.raise_for_status()
        return response.json()

    def catalog(self, business_id: str) -> dict[str, Any]:
        response = self._client.get(f"/businesses/{business_id}/catalog")
        response.raise_for_status()
        return response.json()

    def send_message(
        self,
        session_id: str,
        message: str,
        *,
        business_id: str = "demo-studio",
        manage_token: str | None = None,
        selected_slot_id: str | None = None,
        customer: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"session_id": session_id, "business_id": business_id, "message": message}
        if manage_token:
            payload["manage_token"] = manage_token
        if selected_slot_id:
            payload["selected_slot_id"] = selected_slot_id
        if customer:
            payload["customer"] = customer
        headers = {"Idempotency-Key": idempotency_key or str(uuid.uuid4())}
        response = self._client.post("/chat", json=payload, headers=headers)
        response.raise_for_status()
        return response.json()

    def search_availability(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post("/availability/search", json=payload)
        response.raise_for_status()
        return response.json()

    def create_booking(self, payload: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any]:
        response = self._client.post(
            "/bookings",
            json=payload,
            headers={"Idempotency-Key": idempotency_key or str(uuid.uuid4())},
        )
        response.raise_for_status()
        return response.json()

    def manage(self, action: str, token: str, idempotency_key: str | None = None) -> dict[str, Any]:
        response = self._client.post(
            f"/bookings/me/{action}",
            headers={"X-Booking-Token": token, "Idempotency-Key": idempotency_key or str(uuid.uuid4())},
        )
        response.raise_for_status()
        return response.json()

    def reschedule(self, payload: dict[str, Any], token: str, idempotency_key: str | None = None) -> dict[str, Any]:
        response = self._client.post(
            "/bookings/me/reschedule",
            json=payload,
            headers={"X-Booking-Token": token, "Idempotency-Key": idempotency_key or str(uuid.uuid4())},
        )
        response.raise_for_status()
        return response.json()

    def my_booking(self, token: str) -> dict[str, Any]:
        response = self._client.get("/bookings/me", headers={"X-Booking-Token": token})
        response.raise_for_status()
        return response.json()

    def owner_bookings(self, business_id: str, start_date: str, end_date: str) -> dict[str, Any]:
        response = self._client.get(
            "/owner/bookings",
            params={"business_id": business_id, "start_date": start_date, "end_date": end_date},
        )
        response.raise_for_status()
        return response.json()

    def reminders(self, business_id: str = "demo-studio") -> dict[str, Any]:
        response = self._client.get("/demo/reminders", params={"business_id": business_id})
        response.raise_for_status()
        return response.json()

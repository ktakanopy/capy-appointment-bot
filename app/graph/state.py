from __future__ import annotations

from typing import Any, TypedDict


class SchedulingGraphState(TypedDict, total=False):
    conversation_id: str
    business_id: str
    message: str
    today: str
    selected_slot_id: str | None
    auth_kind: str
    customer_id: str | None
    booking_id: str | None
    customer_ready: bool
    idempotency_key: str | None
    input_modality: str
    skip_interpret: bool
    normalized_request: dict[str, Any]
    service_names: list[str]
    professional_names: list[str]
    catalog: dict[str, Any]
    draft: dict[str, Any]
    extraction: dict[str, Any]
    decision: dict[str, Any]
    route: str
    result: dict[str, Any]

from __future__ import annotations

from app.graph.context import bind_ports, reset_ports
from app.graph.contracts import TurnContext, TurnResult
from app.graph.ports import WorkflowPorts
from app.models import AvailabilitySlot


class LangGraphWorkflow:
    def __init__(self, graph):
        self.graph = graph

    def handle_turn(self, turn: TurnContext, ports: WorkflowPorts) -> TurnResult:
        token = bind_ports(ports)
        try:
            state = {
                "conversation_id": turn.conversation_id,
                "business_id": turn.business_id,
                "message": turn.message,
                "today": turn.today,
                "selected_slot_id": turn.selected_slot_id,
                "auth_kind": turn.auth_kind,
                "customer_id": turn.customer_id,
                "booking_id": turn.booking_id,
                "customer_ready": turn.customer_ready,
                "idempotency_key": turn.idempotency_key,
                "input_modality": turn.input_modality,
            }
            if turn.normalized is not None:
                state["skip_interpret"] = True
                state["normalized_request"] = turn.normalized.model_dump(mode="json")
            result = self.graph.invoke(state)
        finally:
            reset_ports(token)
        payload = result.get("result") or {}
        slots = list(getattr(ports, "last_slots", []) or [])
        return TurnResult(
            text=payload.get("text") or "Tell me which service you'd like and the date.",
            intent=payload.get("intent") or "clarify",
            slots=[slot if isinstance(slot, AvailabilitySlot) else AvailabilitySlot.model_validate(slot) for slot in slots],
            booking=getattr(ports, "last_booking", None),
            manage_token=getattr(ports, "last_manage_token", None),
        )

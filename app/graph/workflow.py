from __future__ import annotations

from app.graph.context import bind_ports, reset_ports
from app.graph.contracts import TurnContext, TurnResult, WorkflowDraft
from app.graph.ports import WorkflowPorts
from app.models import AvailabilitySlot


class LangGraphWorkflow:
    """Run one conversation turn on the compiled graph.

    When the graph has a checkpointer, the session id is the thread id. The
    checkpoint keeps the scheduling draft and redacted messages. This method
    does not put a management token or phone number into that state.
    """

    def __init__(self, graph):
        self.graph = graph
        self._last_state: dict = {}

    def handle_turn(self, turn: TurnContext, ports: WorkflowPorts) -> TurnResult:
        token = bind_ports(ports)
        config = {"configurable": {"thread_id": turn.conversation_id}}
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
                "skip_interpret": turn.normalized is not None,
            }
            if turn.message:
                state["messages"] = [{"role": "user", "content": turn.message}]
            if turn.normalized is not None:
                state["normalized_request"] = turn.normalized.model_dump(mode="json")
            if turn.draft is not None and not self._has_checkpoint(config):
                state["draft"] = turn.draft.model_dump(mode="json")
            if self.graph.checkpointer is not None:
                result = self.graph.invoke(state, config)
            else:
                result = self.graph.invoke(state)
            self._last_state = result
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

    def draft(self, conversation_id: str) -> WorkflowDraft:
        """Return the scheduling draft saved for this conversation thread."""
        values = self._checkpoint_values(conversation_id) or self._last_state
        raw = values.get("draft") if values else None
        return WorkflowDraft.model_validate(raw) if raw else WorkflowDraft()

    def messages(self, conversation_id: str) -> list[dict[str, str]]:
        """Return the redacted transcript saved in the thread checkpoint."""
        values = self._checkpoint_values(conversation_id) or self._last_state
        return list((values or {}).get("messages") or [])

    def _has_checkpoint(self, config: dict) -> bool:
        return bool(self._checkpoint_values(config["configurable"]["thread_id"]))

    def _checkpoint_values(self, conversation_id: str) -> dict | None:
        if self.graph.checkpointer is None:
            return None
        snapshot = self.graph.get_state({"configurable": {"thread_id": conversation_id}})
        return snapshot.values or None

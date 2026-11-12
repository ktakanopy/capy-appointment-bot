from app.graph.context import bind_ports, reset_ports
from app.graph.contracts import TurnContext, TurnResult, WorkflowDraft
from app.privacy import sanitize_message


class LangGraphWorkflow:
    """One graph invocation per turn; the checkpoint is the conversation store."""

    def __init__(self, graph):
        self.graph = graph

    def handle_turn(self, turn: TurnContext, ports) -> TurnResult:
        message = sanitize_message(turn.message)
        state = {
            "conversation_id": turn.conversation_id, "business_id": turn.business_id,
            "message": message, "today": turn.today, "selected_slot_id": turn.selected_slot_id,
            "customer_id": turn.customer_id, "booking_id": turn.booking_id,
            "idempotency_key": turn.idempotency_key, "input_modality": turn.input_modality,
            "messages": [{"role": "user", "content": message}],
        }
        config = {"configurable": {"thread_id": turn.conversation_id}}
        binding = bind_ports(ports)
        try:
            result = self.graph.invoke(state, config) if self.graph.checkpointer else self.graph.invoke(state)
        finally:
            reset_ports(binding)
        payload = dict(result["result"])
        payload["booking"] = ports.last_booking
        return TurnResult.model_validate(payload)

    def draft(self, conversation_id: str) -> WorkflowDraft:
        return WorkflowDraft.model_validate(self.state(conversation_id).get("draft") or {})

    def messages(self, conversation_id: str) -> list[dict[str, str]]:
        return list(self.state(conversation_id).get("messages") or [])

    def state(self, conversation_id: str) -> dict:
        if self.graph.checkpointer is None:
            return {}
        return self.graph.get_state({"configurable": {"thread_id": conversation_id}}).values or {}

from app.graph.actions import SchedulingActions
from app.graph.context import TurnInput, TurnRuntime
from app.graph.contracts import TurnContext, TurnResult, WorkflowDraft
from app.observability import debug_event


class LangGraphWorkflow:
    """One graph invocation per turn; the checkpoint is the conversation store."""

    def __init__(self, graph):
        self.graph = graph

    def handle_turn(
        self,
        turn: TurnContext,
        actions: SchedulingActions,
        *,
        checkpoint_thread: str | None = None,
        recording: bytes | None = None,
        display_name: str | None = None,
    ) -> TurnResult:
        """Pass raw input through runtime context so checkpoints receive only sanitized text."""
        if (turn.message is None) == (recording is None):
            raise ValueError("A turn requires exactly one message or recording")
        modality = "audio" if recording is not None else turn.input_modality
        debug_event(
            "workflow.turn.started", modality=modality, identified=bool(turn.customer_id)
        )
        state = turn.model_dump(mode="json", exclude={"message"})
        state["input_modality"] = modality
        config = None
        if self.graph.checkpointer is not None:
            config = {"configurable": {"thread_id": checkpoint_thread or turn.conversation_id}}
        context = TurnRuntime(actions, TurnInput(turn.message, recording, display_name))
        graph_state = self.graph.invoke(state, config=config, context=context)
        turn_result = dict(graph_state["result"])
        turn_result["booking"] = actions.last_booking
        result = TurnResult.model_validate(turn_result)
        debug_event("workflow.turn.completed", intent=result.intent, has_booking=result.booking is not None)
        return result

    def draft(self, conversation_id: str) -> WorkflowDraft:
        return WorkflowDraft.model_validate(
            self.state(conversation_id).get("draft") or {}
        )

    def messages(self, conversation_id: str) -> list[dict[str, str]]:
        return list(self.state(conversation_id).get("messages") or [])

    def state(self, conversation_id: str) -> dict:
        if self.graph.checkpointer is None:
            return {}
        return (
            self.graph.get_state(
                {"configurable": {"thread_id": conversation_id}}
            ).values
            or {}
        )

from __future__ import annotations

from app.graph.contracts import WorkflowDraft
from app.models import AvailabilitySlot


class MemoryConversationStore:
    def __init__(self, runtime):
        self.runtime = runtime
        if not hasattr(runtime, "drafts"):
            runtime.drafts = {}
        if not hasattr(runtime, "session_slots"):
            runtime.session_slots = {}
        if not hasattr(runtime, "pending_slots"):
            runtime.pending_slots = {}

    def load_draft(self, conversation_id: str) -> WorkflowDraft:
        draft = self.runtime.drafts.get(conversation_id, WorkflowDraft())
        return draft.model_copy(deep=True)

    def save_draft(self, conversation_id: str, draft: WorkflowDraft) -> None:
        self.runtime.drafts[conversation_id] = draft.model_copy(deep=True)
        if draft.pending_slot_id:
            slots = self.runtime.session_slots.get(conversation_id, [])
            pending = next((slot for slot in slots if slot.slot_id == draft.pending_slot_id), None)
            if pending is not None:
                self.runtime.pending_slots[conversation_id] = pending
        else:
            self.runtime.pending_slots.pop(conversation_id, None)

    def load_slots(self, conversation_id: str) -> list[AvailabilitySlot]:
        return list(self.runtime.session_slots.get(conversation_id, []))

    def save_slots(self, conversation_id: str, slots: list[AvailabilitySlot]) -> None:
        self.runtime.session_slots[conversation_id] = list(slots)

    def messages(self, conversation_id: str) -> list[dict[str, str]]:
        history = getattr(self.runtime, "messages", {})
        return list(history.get(conversation_id, []))

    def ensure(self, conversation_id: str) -> None:
        self.runtime.drafts.setdefault(conversation_id, WorkflowDraft())
        sessions = getattr(self.runtime, "sessions", None)
        if isinstance(sessions, dict):
            sessions.setdefault(conversation_id, 0)

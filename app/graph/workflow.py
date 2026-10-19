from __future__ import annotations

from app.llm.schemas import SchedulingExtraction


class LangGraphWorkflow:
    def __init__(self, graph):
        self.graph = graph

    def interpret(
        self,
        session_id: str,
        message: str,
        *,
        service_names: list[str],
        professional_names: list[str],
        today: str,
        has_slots: bool,
    ) -> SchedulingExtraction:
        result = self.graph.invoke(
            {
                "message": message,
                "service_names": service_names,
                "professional_names": professional_names,
                "today": today,
                "has_slots": has_slots,
            },
            {"configurable": {"thread_id": session_id}},
        )
        return SchedulingExtraction.model_validate(result["extraction"])

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.graph.state import SchedulingGraphState
from app.llm.provider import Interpreter


def build_graph(interpreter: Interpreter, checkpointer):
    """Build a one-turn interpretation graph; all policy and writes stay in services."""

    def interpret(state: SchedulingGraphState) -> dict:
        extraction = interpreter.interpret(
            state.get("message", ""),
            {
                "service_names": state.get("service_names", []),
                "professional_names": state.get("professional_names", []),
                "today": state.get("today"),
                "has_slots": state.get("has_slots", False),
            },
        )
        return {"extraction": extraction.model_dump(mode="json")}

    graph = StateGraph(SchedulingGraphState)
    graph.add_node("interpret", interpret)
    graph.add_edge(START, "interpret")
    graph.add_edge("interpret", END)
    return graph.compile(checkpointer=checkpointer)

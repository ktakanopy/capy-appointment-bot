from langgraph.checkpoint.memory import InMemorySaver

from app.graph.builder import build_graph
from app.graph.workflow import LangGraphWorkflow
from app.llm.provider import DeterministicInterpreter


def test_langgraph_workflow_returns_typed_scheduling_extraction():
    graph = build_graph(DeterministicInterpreter(), checkpointer=InMemorySaver())
    workflow = LangGraphWorkflow(graph)

    result = workflow.interpret(
        "session-1",
        "Book Massage tomorrow",
        service_names=["Massage"],
        professional_names=["Alex"],
        today="2026-10-19",
        has_slots=False,
    )

    assert result.intent == "search"
    assert result.service_name == "Massage"
    assert result.local_date == "2026-10-20"

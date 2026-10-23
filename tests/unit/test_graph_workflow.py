from app.graph.builder import build_graph
from app.graph.contracts import TurnContext
from app.graph.decision import DeterministicDecision
from app.graph.workflow import LangGraphWorkflow
from app.llm.provider import DeterministicInterpreter
from tests.unit.test_conversation_graph import Ports


def test_langgraph_workflow_searches_from_typed_extraction():
    graph = build_graph(DeterministicInterpreter(), DeterministicDecision())
    workflow = LangGraphWorkflow(graph)
    ports = Ports()

    result = workflow.handle_turn(
        TurnContext(
            conversation_id="session-1",
            business_id="demo-studio",
            message="Book Massage tomorrow",
            today="2026-10-19",
        ),
        ports,
    )

    assert result.intent == "search"
    assert ports.requests[0].service_id == "massage"
    assert ports.requests[0].start_date.isoformat() == "2026-10-20"

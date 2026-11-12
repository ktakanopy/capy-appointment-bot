from app.graph.builder import build_graph
from app.graph.contracts import TurnContext
from app.graph.decision import DeterministicDecision
from app.graph.workflow import LangGraphWorkflow
from app.llm.provider import DeterministicInterpreter
from app.llm.schemas import SchedulingInterpretation
from tests.unit.test_conversation_graph import FakeSchedulingActions


class ClearMessageInterpreter:
    def interpret(self, message, context):
        _ = message, context
        return SchedulingInterpretation(
            message="Book Massage on 2026-10-20.",
            service_name="Massage",
            local_date="2026-10-20",
        )


def test_langgraph_routes_the_interpreted_message():
    graph = build_graph(ClearMessageInterpreter(), DeterministicDecision())
    workflow = LangGraphWorkflow(graph)
    actions = FakeSchedulingActions()

    result = workflow.handle_turn(
        TurnContext(
            conversation_id="session-1",
            business_id="demo-studio",
            message="I want to book a massage tomorrow",
            today="2026-10-19",
        ),
        actions,
    )

    assert result.intent == "search"
    assert actions.requests[0].service_id == "massage"
    assert actions.requests[0].start_date.isoformat() == "2026-10-20"


def test_langgraph_workflow_searches_from_typed_extraction():
    graph = build_graph(DeterministicInterpreter(), DeterministicDecision())
    workflow = LangGraphWorkflow(graph)
    actions = FakeSchedulingActions()

    result = workflow.handle_turn(
        TurnContext(
            conversation_id="session-1",
            business_id="demo-studio",
            message="Book Massage tomorrow",
            today="2026-10-19",
        ),
        actions,
    )

    assert result.intent == "search"
    assert actions.requests[0].service_id == "massage"
    assert actions.requests[0].start_date.isoformat() == "2026-10-20"

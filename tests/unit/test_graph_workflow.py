from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.graph.builder import build_graph
from app.graph.contracts import TurnContext
from app.graph.decision import DeterministicDecision
from app.graph.workflow import LangGraphWorkflow
from app.llm.gemma import ProviderError
from app.llm.provider import DeterministicInterpreter
from app.llm.schemas import SchedulingInterpretation
from tests.unit.test_conversation_graph import FakeSchedulingActions, _turn


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


def test_concurrent_recordings_are_isolated_and_voice_matches_text():
    both_turns_started = Barrier(2)
    typed_message = "Book Massage tomorrow"

    def transcribe(recording, context):
        both_turns_started.wait(timeout=5)
        if recording == b"rejected":
            raise ProviderError("invalid_recording")
        assert recording == b"valid"
        return typed_message

    workflow = LangGraphWorkflow(
        build_graph(DeterministicInterpreter(), transcribe=transcribe)
    )
    typed_actions = FakeSchedulingActions()
    typed = workflow.handle_turn(_turn(typed_message), typed_actions)
    voice_actions = FakeSchedulingActions()
    rejected_actions = FakeSchedulingActions()
    voice_turn = _turn(None)
    rejected_turn = voice_turn.model_copy(update={"conversation_id": "session-2"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        voice = pool.submit(
            workflow.handle_turn, voice_turn, voice_actions, recording=b"valid"
        )
        rejected = pool.submit(
            workflow.handle_turn, rejected_turn, rejected_actions, recording=b"rejected"
        )
        assert voice.result() == typed
        with pytest.raises(ProviderError, match="invalid_recording"):
            rejected.result()

    assert voice_actions.requests == typed_actions.requests
    assert not rejected_actions.created

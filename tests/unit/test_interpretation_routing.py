import json

from langgraph.checkpoint.memory import InMemorySaver

from app.graph.builder import build_graph
from app.graph.contracts import BookingRef, DecisionOutput
from app.graph.decision import DeterministicDecision
from app.graph.laya import LayaDecision
from app.graph.workflow import LangGraphWorkflow
from app.llm.gemma import GemmaUnderstanding
from app.llm.provider import DeterministicInterpreter
from app.llm.schemas import SchedulingInterpretation
from tests.conftest import sample_slot
from tests.unit.test_conversation_graph import FakeSchedulingActions, _turn
from tests.unit.test_gemma_laya import _Response


def test_real_clients_send_only_gemmas_message_and_current_context_to_laya(monkeypatch):
    rewritten = "Book Massage on 2099-01-05."
    captured = []

    def respond(request, timeout):
        body = json.loads(request.data)
        if request.full_url.endswith("/understand"):
            assert body["message"] == "Uh, could I maybe get a massage that day?"
            return _Response(
                {
                    "message": rewritten,
                    "service_name": "Massage",
                    "local_date": "2099-01-05",
                }
            )
        assert request.full_url.endswith("/decide")
        captured.append(body)
        return _Response({"next_action": "CHECK_AVAILABILITY", "confidence": 0.9})

    monkeypatch.setattr("app.llm.gemma.urlrequest.urlopen", respond)
    workflow = LangGraphWorkflow(
        build_graph(
            GemmaUnderstanding("http://gemma"),
            LayaDecision("http://laya"),
            checkpointer=InMemorySaver(),
        )
    )
    actions = FakeSchedulingActions(slots=[sample_slot()])
    result = workflow.handle_turn(
        _turn("Uh, could I maybe get a massage that day?", customer_id="customer-1"),
        actions,
    )

    assert result.intent == "search"
    assert set(captured[0]) == {"message", "context"}
    assert captured[0]["message"] == rewritten
    assert captured[0]["context"]["service_id"] == "massage"
    assert captured[0]["context"]["local_date"] == "2099-01-05"
    assert (
        captured[0]["context"]["operation"] == "none"
    )  # Laya has not chosen an operation yet.
    assert "intent" not in workflow.state("session-1")["interpretation"]
    assert "normalized_request" not in workflow.state("session-1")
    assert not actions.created


def test_ambiguity_can_request_clarification_without_a_write():
    class AmbiguousMeaning:
        def interpret(self, message, context):
            return SchedulingInterpretation(
                message="The customer is unsure whether to cancel or move their booking."
            )

    class AskForClarity:
        def decide(self, message, context):
            assert "unsure" in message
            return DecisionOutput(next_action="CLARIFY", confidence=0.95)

    workflow = LangGraphWorkflow(build_graph(AmbiguousMeaning(), AskForClarity()))
    actions = FakeSchedulingActions()
    result = workflow.handle_turn(
        _turn("Maybe cancel, or move it?", customer_id="customer-1"), actions
    )
    assert result.intent == "clarify"
    assert actions.created == actions.canceled == actions.rescheduled == []


def test_rewritten_contacts_are_sanitized_before_laya_and_every_checkpoint():
    class RewrittenContact:
        def interpret(self, message, context):
            return SchedulingInterpretation(
                message="Book Massage; contact ada@example.com or +5511999998888."
            )

    class CaptureMeaning:
        def decide(self, message, context):
            assert "ada@example.com" not in message
            assert "+5511999998888" not in message
            return DecisionOutput(next_action="CLARIFY", confidence=0.9)

    workflow = LangGraphWorkflow(
        build_graph(RewrittenContact(), CaptureMeaning(), checkpointer=InMemorySaver())
    )
    workflow.handle_turn(_turn("Book Massage"), FakeSchedulingActions())
    for snapshot in workflow.graph.get_state_history(
        {"configurable": {"thread_id": "session-1"}}
    ):
        assert "ada@example.com" not in str(snapshot.values)
        assert "+5511999998888" not in str(snapshot.values)


def test_a_booking_reference_choice_cannot_be_routed_to_a_stale_available_slot():
    class MisrouteNumber(DeterministicDecision):
        def decide(self, message, context):
            if message == "1":
                return DecisionOutput(next_action="BOOK", confidence=0.99)
            return super().decide(message, context)

    actions = FakeSchedulingActions(
        slots=[sample_slot()],
        refs=[
            BookingRef(id="one", service_id="massage", label="one"),
            BookingRef(id="two", service_id="massage", label="two"),
        ],
    )
    workflow = LangGraphWorkflow(
        build_graph(
            DeterministicInterpreter(), MisrouteNumber(), checkpointer=InMemorySaver()
        )
    )
    workflow.handle_turn(
        _turn("Book Massage 2099-01-05", customer_id="customer-1"), actions
    )
    workflow.handle_turn(_turn("Cancel my booking", customer_id="customer-1"), actions)
    result = workflow.handle_turn(_turn("1", customer_id="customer-1"), actions)
    assert result.intent == "clarify_booking"
    assert actions.created == actions.canceled == actions.rescheduled == []


def test_a_new_search_can_replace_an_unfinished_booking_reference_question():
    actions = FakeSchedulingActions(
        slots=[sample_slot()],
        refs=[
            BookingRef(id="one", service_id="massage", label="one"),
            BookingRef(id="two", service_id="massage", label="two"),
        ],
    )
    workflow = LangGraphWorkflow(
        build_graph(
            DeterministicInterpreter(),
            DeterministicDecision(),
            checkpointer=InMemorySaver(),
        )
    )
    first = workflow.handle_turn(
        _turn("Cancel my booking", customer_id="customer-1"), actions
    )
    assert first.intent == "clarify_booking"
    second = workflow.handle_turn(
        _turn("Book Massage 2099-01-05", customer_id="customer-1"), actions
    )
    assert second.intent == "search"
    assert workflow.draft("session-1").candidate_booking_ids == []
    third = workflow.handle_turn(_turn("1", customer_id="customer-1"), actions)
    assert third.intent == "create"
    assert not actions.canceled

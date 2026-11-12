import json
from unittest.mock import patch

import pytest

from app.graph.contracts import DecisionOutput, WorkflowDraft
from app.graph.laya import LayaDecision
from app.llm.gemma import GemmaUnderstanding, ProviderError


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def read(self, amount=None):
        return self.payload if amount is None else self.payload[:amount]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_gemma_returns_a_clear_message_and_facts_without_routing():
    payload = {
        "message": "Book Massage on 2099-01-05.",
        "service_name": "Massage",
        "local_date": "2099-01-05",
    }
    with patch("app.llm.gemma.urlrequest.urlopen", return_value=_Response(payload)):
        extraction = GemmaUnderstanding("http://gemma").interpret(
            "book", {"service_names": ["Massage"]}
        )

    assert extraction.message == "Book Massage on 2099-01-05."
    assert "intent" not in extraction.model_dump()
    assert extraction.service_name == "Massage"
    assert extraction.local_date == "2099-01-05"


def test_laya_decision_parses_typed_action():
    payload = {
        "next_action": "CHECK_AVAILABILITY",
        "confidence": 0.8,
        "needs_human": False,
    }
    with patch("app.graph.laya.urlrequest.urlopen", return_value=_Response(payload)):
        decision = LayaDecision("http://laya").decide(
            "Book Massage tomorrow.",
            WorkflowDraft(),
        )

    assert decision == DecisionOutput(
        next_action="CHECK_AVAILABILITY", confidence=0.8, needs_human=False
    )


def test_laya_receives_only_the_interpreted_message_and_context():
    captured = {}

    def capture(req, timeout):
        captured["body"] = json.loads(req.data.decode())
        return _Response(
            {"next_action": "FAQ", "confidence": 0.9, "needs_human": False}
        )

    with patch("app.graph.laya.urlrequest.urlopen", side_effect=capture):
        LayaDecision("http://laya").decide(
            "What is the price of Massage?",
            WorkflowDraft(service_id="massage", service_name="Massage"),
        )

    assert captured["body"] == {
        "message": "What is the price of Massage?",
        "context": WorkflowDraft(
            service_id="massage", service_name="Massage"
        ).model_dump(mode="json"),
    }
    assert "bk1." not in json.dumps(captured["body"])


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": "   "},
        {"message": "Book Massage.", "intent": "search"},
        {"message": "Book Massage.", "next_action": "BOOK"},
    ],
)
def test_invalid_or_routing_gemma_payload_is_a_provider_error(payload):
    with patch(
        "app.llm.gemma.urlrequest.urlopen",
        return_value=_Response(payload),
    ):
        with pytest.raises(ProviderError):
            GemmaUnderstanding("http://gemma").interpret("hello", {})

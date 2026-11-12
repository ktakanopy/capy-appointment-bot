import json
from unittest.mock import patch

import pytest

from app.graph.contracts import DecisionOutput, NormalizedRequest, WorkflowDraft
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


def test_gemma_text_maps_to_extraction_without_booking():
    payload = {
        "intent": "search",
        "service_name": "Massage",
        "local_date": "2099-01-05",
    }
    with patch("app.llm.gemma.urlrequest.urlopen", return_value=_Response(payload)):
        extraction = GemmaUnderstanding("http://gemma").interpret(
            "book", {"service_names": ["Massage"]}
        )

    assert extraction.intent == "search"
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
            NormalizedRequest(conversation_id="c1", transcript="book", intent="search"),
            WorkflowDraft(),
        )

    assert decision == DecisionOutput(
        next_action="CHECK_AVAILABILITY", confidence=0.8, needs_human=False
    )


def test_laya_request_does_not_include_a_management_token():
    captured = {}

    def capture(req, timeout):
        captured["body"] = json.loads(req.data.decode())
        return _Response(
            {"next_action": "FAQ", "confidence": 0.9, "needs_human": False}
        )

    with patch("app.graph.laya.urlrequest.urlopen", side_effect=capture):
        LayaDecision("http://laya").decide(
            NormalizedRequest(
                conversation_id="c1", transcript="how much", intent="help"
            ),
            WorkflowDraft(),
        )

    assert "bk1." not in json.dumps(captured["body"])


def test_invalid_gemma_payload_is_a_provider_error():
    with patch(
        "app.llm.gemma.urlrequest.urlopen",
        return_value=_Response({"intent": "not-an-intent"}),
    ):
        with pytest.raises(ProviderError):
            GemmaUnderstanding("http://gemma").interpret("hello", {})

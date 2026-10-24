import json
from unittest.mock import patch

import pytest

from app.graph.contracts import DecisionOutput, NormalizedRequest, WorkflowDraft
from app.graph.laya import FallbackDecision, LayaDecision
from app.llm.gemma import GemmaUnderstanding, ProviderError
from app.llm.provider import DeterministicInterpreter, FallbackInterpreter


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_gemma_text_maps_to_extraction_without_booking():
    payload = {"intent": "search", "service_name": "Massage", "local_date": "2099-01-05"}
    with patch("app.llm.gemma.urlrequest.urlopen", return_value=_Response(payload)):
        extraction = GemmaUnderstanding("http://gemma").interpret("book", {"service_names": ["Massage"]})

    assert extraction.intent == "search"
    assert extraction.service_name == "Massage"
    assert extraction.local_date == "2099-01-05"


def test_gemma_audio_returns_transcript_and_same_intent():
    payload = {"transcript": "move it to Friday", "intent": "reschedule"}
    with patch("app.llm.gemma.urlrequest.urlopen", return_value=_Response(payload)):
        normalized = GemmaUnderstanding("http://gemma").understand_audio(b"audio", {"conversation_id": "c1"})

    assert normalized.input_modality == "audio"
    assert normalized.transcript == "move it to Friday"
    assert normalized.intent == "reschedule"


def test_unreachable_gemma_falls_back_without_logging_the_message():
    class Logger:
        def __init__(self):
            self.events = []

        def info(self, event, extra=None):
            self.events.append((event, extra))

    logger = Logger()
    interpreter = FallbackInterpreter(GemmaUnderstanding("http://gemma"), DeterministicInterpreter(), logger)
    with patch("app.llm.gemma.urlrequest.urlopen", side_effect=OSError("down")):
        extraction = interpreter.interpret("Book Massage tomorrow", {"today": "2026-10-19", "service_names": ["Massage"]})

    assert extraction.intent == "search"
    assert extraction.service_name == "Massage"
    assert "Massage" not in str(logger.events)


def test_laya_decision_parses_typed_action():
    payload = {"next_action": "CHECK_AVAILABILITY", "confidence": 0.8, "needs_human": False}
    with patch("app.graph.laya.urlrequest.urlopen", return_value=_Response(payload)):
        decision = LayaDecision("http://laya").decide(
            NormalizedRequest(conversation_id="c1", transcript="book", intent="search"),
            WorkflowDraft(),
        )

    assert decision == DecisionOutput(next_action="CHECK_AVAILABILITY", confidence=0.8, needs_human=False)


def test_unreachable_laya_falls_back_to_deterministic_decision():
    from app.graph.decision import DeterministicDecision

    class Logger:
        def info(self, event, extra=None):
            self.event = event if isinstance(event, str) else event.get("event")

    logger = Logger()
    decision = FallbackDecision(LayaDecision("http://laya"), DeterministicDecision(), logger)
    with patch("app.graph.laya.urlrequest.urlopen", side_effect=OSError("down")):
        result = decision.decide(
            NormalizedRequest(conversation_id="c1", transcript="cancel it", intent="cancel"),
            WorkflowDraft(),
        )

    assert result.next_action == "CANCEL"
    assert logger.event == "decision.fallback"


def test_laya_request_does_not_include_a_management_token():
    captured = {}

    def capture(req, timeout):
        captured["body"] = json.loads(req.data.decode())
        return _Response({"next_action": "FAQ", "confidence": 0.9, "needs_human": False})

    with patch("app.graph.laya.urlrequest.urlopen", side_effect=capture):
        LayaDecision("http://laya").decide(
            NormalizedRequest(conversation_id="c1", transcript="how much", intent="help"),
            WorkflowDraft(),
        )

    assert "bk1." not in json.dumps(captured["body"])


def test_openai_setting_overrides_the_gemma_default():
    from types import SimpleNamespace

    from app.runtime import understanding_choice

    settings = SimpleNamespace(
        llm_provider="openai",
        openai_api_key="test-key",
        understanding_provider="gemma",
    )

    assert understanding_choice(settings) == "openai"


def test_invalid_gemma_payload_is_a_provider_error():
    with patch("app.llm.gemma.urlrequest.urlopen", return_value=_Response({"intent": "not-an-intent"})):
        with pytest.raises(ProviderError):
            GemmaUnderstanding("http://gemma").interpret("hello", {})

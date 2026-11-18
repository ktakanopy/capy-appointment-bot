import io
import math
import wave
from array import array
from unittest.mock import Mock

import pytest

from app.channels.audio_chunks import AudioChunker
from app.graph.builder import build_graph
from app.graph.contracts import BookingRef, DecisionOutput
from app.graph.workflow import LangGraphWorkflow
from app.llm.gemma import GemmaUnderstanding, ProviderError
from tests.unit.test_conversation_graph import FakeSchedulingActions, _turn


def recording(seconds):
    samples = array(
        "h",
        (
            int(4000 * math.sin(i * 2 * math.pi * 440 / 16000))
            for i in range(seconds * 16000)
        ),
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav_file.writeframes(samples.tobytes())
    return buffer.getvalue()


def test_complete_long_recording_preserves_late_correction(monkeypatch):
    model = GemmaUnderstanding("http://fixture")
    texts = iter(
        [
            "Book a massage tomorrow",
            "Actually cancel my appointment instead",
            "Please cancel it",
        ]
    )
    calls = []

    def post(body, **kwargs):
        calls.append(body)
        return {"transcript": next(texts)}

    monkeypatch.setattr(model, "_post", post)
    transcript = model.transcribe_recording(recording(61), {}, chunker=AudioChunker())
    assert len(calls) == 3
    assert "Actually cancel my appointment instead" in transcript
    assert transcript.endswith("Please cancel it")
    assert all(call["modality"] == "audio" for call in calls)


def test_failed_middle_chunk_stops_before_interpretation_and_routing(monkeypatch):
    model = GemmaUnderstanding("http://fixture")
    calls = []

    def post(body, **kwargs):
        calls.append(body)
        if len(calls) == 2:
            raise ProviderError("unavailable")
        return {"transcript": "Book a massage"}

    monkeypatch.setattr(model, "_post", post)
    interpreter, router = Mock(), Mock()
    actions = FakeSchedulingActions()
    workflow = LangGraphWorkflow(
        build_graph(interpreter, router, transcribe=model.transcribe_recording)
    )
    with pytest.raises(ProviderError):
        workflow.handle_turn(_turn(None), actions, recording=recording(61))
    assert len(calls) == 2
    interpreter.interpret.assert_not_called()
    router.decide.assert_not_called()
    assert actions.created == actions.canceled == actions.rescheduled == []


def test_corrupt_audio_is_rejected():
    from app.channels.audio_chunks import AudioError

    with pytest.raises(AudioError):
        with AudioChunker().chunks(b"not audio") as chunks:
            list(chunks)


def test_full_audio_is_interpreted_before_one_laya_route_and_booking_action(
    monkeypatch,
):
    model = GemmaUnderstanding("http://fixture")
    chunks = iter(
        [
            "Book Massage tomorrow.",
            "Actually cancel my appointment instead.",
            "Please cancel it.",
        ]
    )
    calls = []

    def post(body, **kwargs):
        calls.append(body["modality"])
        if body["modality"] == "audio":
            return {"transcript": next(chunks)}
        assert calls == ["audio", "audio", "audio", "text"]
        assert "Actually cancel my appointment instead" in body["message"]
        return {"message": "Cancel my booking."}

    class RouteMeaning:
        def decide(self, message, context):
            assert calls == ["audio", "audio", "audio", "text"]
            assert message == "Cancel my booking."
            calls.append("route")
            return DecisionOutput(next_action="CANCEL", confidence=0.9)

    monkeypatch.setattr(model, "_post", post)
    actions = FakeSchedulingActions(
        refs=[BookingRef(id="booking-1", service_id="massage", label="later")]
    )
    workflow = LangGraphWorkflow(
        build_graph(model, RouteMeaning(), transcribe=model.transcribe_recording)
    )
    result = workflow.handle_turn(
        _turn(None, customer_id="customer-1"), actions, recording=recording(61)
    )
    assert calls == ["audio", "audio", "audio", "text", "route"]
    assert result.intent == "cancel"
    assert len(actions.canceled) == 1
    assert not actions.created

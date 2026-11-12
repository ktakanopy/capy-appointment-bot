import io
import math
import wave
from array import array

import pytest

from app.channels.audio_chunks import AudioChunker
from app.llm.gemma import GemmaUnderstanding, ProviderError


def recording(seconds):
    samples = array(
        "h",
        (
            int(4000 * math.sin(i * 2 * math.pi * 440 / 16000))
            for i in range(seconds * 16000)
        ),
    )
    out = io.BytesIO()
    with wave.open(out, "wb") as f:
        f.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        f.writeframes(samples.tobytes())
    return out.getvalue()


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
    assert all(c["modality"] == "audio" for c in calls)


def test_failed_middle_chunk_returns_no_partial_transcript(monkeypatch):
    model = GemmaUnderstanding("http://fixture")
    calls = []

    def post(body, **kwargs):
        calls.append(body)
        if len(calls) == 2:
            raise ProviderError("unavailable")
        return {"transcript": "Book a massage"}

    monkeypatch.setattr(model, "_post", post)
    with pytest.raises(ProviderError):
        model.transcribe_recording(recording(61), {}, chunker=AudioChunker())
    assert len(calls) == 2


def test_corrupt_audio_is_rejected():
    from app.channels.audio_chunks import AudioError

    with pytest.raises(AudioError):
        with AudioChunker().chunks(b"not audio") as chunks:
            list(chunks)

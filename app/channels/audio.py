from __future__ import annotations

from typing import Protocol

from app.graph.contracts import NormalizedRequest


class AudioUnderstanding(Protocol):
    def understand(self, audio: bytes, context: dict) -> NormalizedRequest: ...


class FixtureAudioUnderstanding:
    def __init__(self, normalized: NormalizedRequest):
        self.normalized = normalized

    def understand(self, audio: bytes, context: dict) -> NormalizedRequest:
        _ = audio, context
        return self.normalized.model_copy(update={"input_modality": "audio"})

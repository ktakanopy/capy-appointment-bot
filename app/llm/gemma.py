from __future__ import annotations

import base64
import json
import math
import re
from time import monotonic
from urllib import request as urlrequest
from urllib.error import URLError

from app.channels.audio_chunks import AudioChunker, AudioError
from app.graph.contracts import NormalizedRequest
from app.llm.schemas import SchedulingExtraction


_MAX_RESPONSE_BYTES = 1_000_000


class ProviderError(RuntimeError):
    pass


class GemmaUnderstanding:
    """Text, SMS, and audio understanding through a local Gemma 3n E2B server.

    The model returns scheduling fields only. It does not choose a graph node
    or decide that a time is free.
    """

    def __init__(self, base_url: str, timeout_seconds: float = 8):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def interpret(self, message: str, context: dict) -> SchedulingExtraction:
        payload = self._post({"modality": "text", "message": message, "context": _safe_context(context)})
        return _extraction(payload)

    def understand_audio(self, audio: bytes, context: dict) -> NormalizedRequest:
        """Understand one already bounded clip; use transcribe_recording for uploads."""
        payload = self._post(
            {
                "modality": "audio",
                "audio_base64": base64.b64encode(audio).decode("ascii"),
                "context": _safe_context(context),
            }
        )
        extraction = _extraction(payload)
        transcript = payload.get("transcript") if isinstance(payload.get("transcript"), str) else ""
        return NormalizedRequest(
            conversation_id=str(context.get("conversation_id") or ""),
            transcript=transcript,
            intent=extraction.intent,
            service=extraction.service_name,
            professional=extraction.professional_name,
            date_constraint=extraction.local_date,
            time_constraint=extraction.local_time,
            input_modality="audio",
            confidence=0.9 if extraction.intent != "unknown" else 0.2,
        )

    def transcribe_recording(
        self,
        audio: bytes,
        context: dict,
        *,
        chunker: AudioChunker | None = None,
        processing_timeout_seconds: float = 900,
        max_transcript_characters: int = 64_000,
    ) -> str:
        """Transcribe a whole recording using pause-aware, overlapping clips.

        No chunk-level intent is used. Only return after every chunk succeeds,
        so the caller can interpret this complete transcript once and run one
        workflow turn. Decoding/provider failures never return a partial result.

        Matching phrases of at least three words at overlapping boundaries are
        joined conservatively. Without word timestamps, exact alignment is not
        guaranteed; short or differently transcribed repetitions are retained.
        """
        if not math.isfinite(processing_timeout_seconds) or processing_timeout_seconds <= 0:
            raise ValueError("processing_timeout_seconds must be finite and positive")
        if max_transcript_characters <= 0:
            raise ValueError("max_transcript_characters must be positive")
        deadline = monotonic() + processing_timeout_seconds
        chunker = chunker or AudioChunker(
            decode_timeout_seconds=min(120, processing_timeout_seconds),
        )
        transcript = ""
        try:
            decode_budget = deadline - monotonic()
            if decode_budget <= 0:
                raise ProviderError("audio_processing_timeout")
            with chunker.chunks(
                audio,
                decode_timeout_seconds=min(chunker.decode_timeout_seconds, decode_budget),
            ) as chunks:
                for chunk in chunks:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise ProviderError("audio_processing_timeout")
                    payload = self._post(
                        {
                            "modality": "audio",
                            "audio_base64": base64.b64encode(chunk.wav_bytes).decode("ascii"),
                            "context": _safe_context(context),
                        },
                        timeout_seconds=min(self.timeout_seconds, remaining),
                    )
                    text = payload.get("transcript")
                    if not isinstance(text, str) or not text.strip():
                        raise ProviderError("empty_audio_transcript")
                    if len(text) > max_transcript_characters:
                        raise ProviderError("audio_transcript_limit")
                    transcript = _join_transcripts(transcript, text.strip(), chunk.overlap_seconds)
                    if len(transcript) > max_transcript_characters:
                        raise ProviderError("audio_transcript_limit")
                    if monotonic() >= deadline:
                        raise ProviderError("audio_processing_timeout")
        except AudioError as exc:
            raise ProviderError(str(exc)) from None
        if not transcript:
            raise ProviderError("empty_audio_transcript")
        return transcript

    def _post(self, body: dict, *, timeout_seconds: float | None = None) -> dict:
        encoded = json.dumps(body).encode()
        req = urlrequest.Request(
            f"{self.base_url}/understand",
            data=encoded,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urlrequest.urlopen(
                req, timeout=self.timeout_seconds if timeout_seconds is None else timeout_seconds,
            ) as response:
                body = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(body) > _MAX_RESPONSE_BYTES:
                    raise ProviderError("response_limit")
                parsed = json.loads(body.decode())
        except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise ProviderError(type(exc).__name__) from exc
        if not isinstance(parsed, dict):
            raise ProviderError("invalid_response")
        return parsed


def _join_transcripts(previous: str, current: str, overlap_seconds: float) -> str:
    if not previous:
        return current
    previous_words = previous.split()
    current_words = current.split()
    # Restrict matching to the audio overlap. Never globally deduplicate text:
    # repetition elsewhere in a message can be intentional.
    max_words = min(len(previous_words), len(current_words), math.ceil(overlap_seconds * 6))
    for count in range(max_words, 2, -1):
        left = [re.sub(r"[^\w]", "", word).casefold() for word in previous_words[-count:]]
        right = [re.sub(r"[^\w]", "", word).casefold() for word in current_words[:count]]
        if all(left) and left == right:
            remainder = " ".join(current_words[count:])
            return f"{previous} {remainder}".strip()
    return f"{previous} {current}"


def _safe_context(context: dict) -> dict:
    return {
        "service_names": context.get("service_names", []),
        "professional_names": context.get("professional_names", []),
        "today": context.get("today"),
        "has_slots": bool(context.get("has_slots")),
        "history": [
            {"role": item.get("role"), "content": item.get("content")}
            for item in context.get("history", [])
            if isinstance(item, dict) and item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str)
        ],
    }


def _extraction(payload: dict) -> SchedulingExtraction:
    try:
        return SchedulingExtraction.model_validate(
            {
                "intent": payload.get("intent") or "unknown",
                "service_name": payload.get("service_name"),
                "professional_name": payload.get("professional_name"),
                "local_date": payload.get("local_date"),
                "local_time": payload.get("local_time"),
                "slot_index": payload.get("slot_index"),
                "customer_name": payload.get("customer_name"),
                "phone": payload.get("phone"),
            }
        )
    except ValueError as exc:
        raise ProviderError("invalid_extraction") from exc

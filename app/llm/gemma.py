from __future__ import annotations

import json
from urllib import request as urlrequest
from urllib.error import URLError

from app.graph.contracts import NormalizedRequest
from app.llm.schemas import SchedulingExtraction


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
        import base64

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

    def _post(self, body: dict) -> dict:
        encoded = json.dumps(body).encode()
        req = urlrequest.Request(
            f"{self.base_url}/understand",
            data=encoded,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=self.timeout_seconds) as response:
                parsed = json.loads(response.read().decode())
        except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise ProviderError(type(exc).__name__) from exc
        if not isinstance(parsed, dict):
            raise ProviderError("invalid_response")
        return parsed


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

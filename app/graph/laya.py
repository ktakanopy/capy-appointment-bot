from __future__ import annotations

import json
from urllib import request as urlrequest
from urllib.error import URLError

from app.graph.contracts import DecisionOutput, WorkflowDraft
from app.llm.gemma import ProviderError


class LayaDecision:
    """Route Gemma's interpreted message using the current conversation context.

    Laya proposes the next action. The workflow still rejects an action the
    draft does not allow.
    """

    def __init__(self, base_url: str, timeout_seconds: float = 8):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def decide(self, message: str, context: WorkflowDraft) -> DecisionOutput:
        body = {
            "message": message,
            "context": context.model_dump(mode="json"),
        }
        encoded = json.dumps(body).encode()
        req = urlrequest.Request(
            f"{self.base_url}/decide",
            data=encoded,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=self.timeout_seconds) as response:
                body = response.read(1_000_001)
                if len(body) > 1_000_000:
                    raise ProviderError("response_limit")
                payload = json.loads(body.decode())
        except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise ProviderError(type(exc).__name__) from exc
        if not isinstance(payload, dict):
            raise ProviderError("invalid_response")
        try:
            return DecisionOutput.model_validate(payload)
        except ValueError as exc:
            raise ProviderError("invalid_decision") from exc

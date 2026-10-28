from __future__ import annotations

import json
from urllib import request as urlrequest
from urllib.error import URLError

from app.graph.contracts import DecisionOutput, NormalizedRequest, WorkflowDraft
from app.llm.gemma import ProviderError


class LayaDecision:
    """Route a normalized request through a local fine-tuned Laya server.

    Laya proposes the next action. The workflow still rejects an action the
    draft does not allow.
    """

    def __init__(self, base_url: str, timeout_seconds: float = 8):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def decide(self, request: NormalizedRequest, draft: WorkflowDraft) -> DecisionOutput:
        body = {
            "request": request.model_dump(mode="json"),
            "draft": draft.model_dump(mode="json"),
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
                payload = json.loads(response.read().decode())
        except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise ProviderError(type(exc).__name__) from exc
        if not isinstance(payload, dict):
            raise ProviderError("invalid_response")
        try:
            return DecisionOutput.model_validate(payload)
        except ValueError as exc:
            raise ProviderError("invalid_decision") from exc


class FallbackDecision:
    def __init__(self, primary, fallback, logger=None):
        self.primary = primary
        self.fallback = fallback
        self.logger = logger

    def decide(self, request: NormalizedRequest, draft: WorkflowDraft) -> DecisionOutput:
        from app.observability import log_event

        try:
            return self.primary.decide(request, draft)
        except Exception as error:
            if self.logger is not None:
                log_event(self.logger, "decision.fallback", failure_type=type(error).__name__)
            return self.fallback.decide(request, draft)

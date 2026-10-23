from __future__ import annotations

import re

from app.graph.contracts import DecisionOutput, NormalizedRequest, WorkflowDraft
from app.llm.schemas import SchedulingExtraction


_FAQ = re.compile(
    r"\b(price|cost|how much|quanto custa|duration|how long|dura[cç][aã]o|prepar|pol[ií]tica|cancellation policy)\b",
    re.I,
)
_LOOKUP = re.compile(
    r"\b(when is my|my appointment|my booking|que horas|meu hor[aá]rio|minha consulta|meu agendamento)\b",
    re.I,
)


class DecisionEngine:
    def decide(self, request: NormalizedRequest, draft: WorkflowDraft) -> DecisionOutput: ...


class DeterministicDecision:
    def decide(self, request: NormalizedRequest, draft: WorkflowDraft) -> DecisionOutput:
        if request.intent == "confirm":
            return DecisionOutput(next_action="CONFIRM", confidence=0.95, needs_human=False)
        if request.intent == "cancel":
            return DecisionOutput(next_action="CANCEL", confidence=0.95, needs_human=False)
        if request.intent == "reschedule" or (draft.operation == "reschedule" and request.intent in {"unknown", "search"}):
            return DecisionOutput(next_action="RESCHEDULE", confidence=0.9, needs_human=False, reason_code="reschedule")
        if _LOOKUP.search(request.transcript) and request.intent not in {"confirm", "cancel", "reschedule", "select_slot"}:
            return DecisionOutput(next_action="LOOKUP_BOOKING", confidence=0.9, needs_human=False)
        if _FAQ.search(request.transcript) and request.intent in {"unknown", "help"}:
            return DecisionOutput(next_action="FAQ", confidence=0.9, needs_human=False, reason_code="catalog")
        if draft.service_id and draft.local_date and request.intent in {"unknown", "search"} and draft.operation == "create":
            return DecisionOutput(next_action="CHECK_AVAILABILITY", confidence=0.9, needs_human=False)
        if request.intent == "select_slot":
            return DecisionOutput(next_action="BOOK", confidence=0.9, needs_human=False)
        if request.intent == "search":
            return DecisionOutput(next_action="CHECK_AVAILABILITY", confidence=0.9, needs_human=False)
        if request.intent == "help" or _FAQ.search(request.transcript):
            return DecisionOutput(next_action="FAQ", confidence=0.9, needs_human=False, reason_code="catalog")
        if _LOOKUP.search(request.transcript):
            return DecisionOutput(next_action="LOOKUP_BOOKING", confidence=0.9, needs_human=False)
        return DecisionOutput(
            next_action="ESCALATE",
            confidence=0.2,
            needs_human=True,
            reason_code="unsupported",
        )


def normalized_from_extraction(
    extraction: SchedulingExtraction,
    *,
    conversation_id: str,
    customer_id: str | None,
    transcript: str,
    modality: str,
    draft: WorkflowDraft,
) -> NormalizedRequest:
    missing: list[str] = []
    if not draft.service_id and not extraction.service_name:
        missing.append("service")
    if not draft.local_date and not extraction.local_date:
        missing.append("date")
    confidence = 0.2 if extraction.intent == "unknown" else 0.9
    return NormalizedRequest(
        conversation_id=conversation_id,
        customer_id=customer_id,
        input_modality="audio" if modality == "audio" else "text",
        transcript=transcript,
        intent=extraction.intent,
        service=extraction.service_name,
        professional=extraction.professional_name,
        date_constraint=extraction.local_date,
        time_constraint=extraction.local_time,
        booking_reference=draft.active_booking_id,
        missing_fields=missing,
        confidence=confidence,
    )

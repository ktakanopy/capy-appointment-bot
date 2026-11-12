from __future__ import annotations

import re

from app.graph.contracts import DecisionOutput, WorkflowDraft

_RESCHEDULE = re.compile(r"\b(reschedule|move|change|mudar|remarcar|trocar)\b", re.I)
_CANCEL = re.compile(r"\b(cancel|cancelar|cancela)\b", re.I)
_CONFIRM = re.compile(r"\b(confirm|confirmar|confirmo)\b", re.I)
_SEARCH = re.compile(
    r"\b(available|availability|book|booking|schedule|appointment|agendar|marcar|hor[aá]rio|dispon[ií]vel)\b",
    re.I,
)
_FAQ = re.compile(
    r"\b(price|cost|how much|quanto custa|pre[cç]o|duration|how long|dura[cç][aã]o|prepar|pol[ií]tica|cancellation policy|help|ajuda|options|services|servi[cç]os)\b",
    re.I,
)
_LOOKUP = re.compile(
    r"\b(when is my|my appointment|my booking|upcoming appointments|que horas|meu hor[aá]rio|minha consulta|meu agendamento)\b",
    re.I,
)
_OVERRIDE = re.compile(
    r"\b(ignore|disregard|forget)\b[^.!?]{0,60}\b(rules?|instructions?|prompt|policy|policies)\b"
    r"|\b(system prompt|jailbreak|developer message)\b"
    r"|\bignore\b[^.!?]{0,60}\b(regras|instru[cç][oõ]es)\b",
    re.I,
)
_CHOICE = re.compile(
    r"\b(\d{1,2}|first|second|third|1st|2nd|3rd|primeiro|primeira|segundo|segunda|terceiro|terceira)\b",
    re.I,
)
_DATE_OR_TIME = re.compile(r"\b(?:20\d{2}-\d{2}-\d{2}|\d{1,2}:\d{2})\b")


class DeterministicDecision:
    """Explicit evaluation baseline for Laya's message-and-context contract."""

    def decide(self, message: str, context: WorkflowDraft) -> DecisionOutput:
        choosing = bool(_CHOICE.search(message) and not _DATE_OR_TIME.search(message))
        if _OVERRIDE.search(message):
            action = "ESCALATE"
        elif _CANCEL.search(message) and _RESCHEDULE.search(message):
            action = "CLARIFY"
        elif _FAQ.search(message):
            action = "FAQ"
        elif _RESCHEDULE.search(message):
            action = "RESCHEDULE"
        elif _CANCEL.search(message):
            action = "CANCEL"
        elif _CONFIRM.search(message):
            action = "CONFIRM"
        elif _LOOKUP.search(message):
            action = "LOOKUP_BOOKING"
        elif choosing and context.candidate_booking_ids:
            action = {
                "cancel": "CANCEL",
                "confirm": "CONFIRM",
                "reschedule": "RESCHEDULE",
            }.get(context.operation, "CLARIFY")
        elif choosing and context.offered_slot_ids:
            action = "BOOK"
        elif _SEARCH.search(message):
            action = "CHECK_AVAILABILITY"
        elif context.operation in {"create", "reschedule"} and _DATE_OR_TIME.search(
            message
        ):
            action = (
                "RESCHEDULE"
                if context.operation == "reschedule"
                else "CHECK_AVAILABILITY"
            )
        else:
            action = "ESCALATE"
        return DecisionOutput(
            next_action=action,
            confidence=0.2 if action == "ESCALATE" else 0.9,
            needs_human=action == "ESCALATE",
        )

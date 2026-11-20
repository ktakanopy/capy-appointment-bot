from __future__ import annotations

import re
import unicodedata
from typing import Literal

ConsentResponse = Literal["approve", "decline", "unclear"]

_APPROVALS = {
    "yes",
    "yes please",
    "yes please book it",
    "yes book it",
    "yes go ahead",
    "go ahead",
    "please book it",
    "book it",
    "i confirm",
    "confirm",
    "sim",
    "sim por favor",
    "sim pode agendar",
    "sim pode marcar",
    "pode agendar",
    "pode marcar",
    "confirmo",
    "pode confirmar",
}
_DECLINES = {
    "no",
    "no thanks",
    "no thank you",
    "dont book it",
    "no dont book it",
    "do not book it",
    "no do not book it",
    "not now",
    "forget it",
    "cancel it",
    "nao",
    "nao obrigado",
    "nao obrigada",
    "nao pode agendar",
    "deixa pra la",
    "nao deixa pra la",
}


def classify_booking_consent(message: str) -> ConsentResponse:
    """Recognize only short, unambiguous approval or refusal for a pending slot.

    Booking writes need the customer's own latest words to express consent.
    A narrow phrase list keeps a paraphrased model interpretation or a reply
    such as “yes, but at five” from approving a different appointment.
    """
    normalized = unicodedata.normalize("NFKD", message.casefold())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalized.replace("’", "'").replace("‘", "'")
    normalized = normalized.replace("don't", "dont")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    if normalized in _APPROVALS:
        return "approve"
    if normalized in _DECLINES:
        return "decline"
    return "unclear"

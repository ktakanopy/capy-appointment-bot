from __future__ import annotations

from app.models import DomainError


def canonical_phone(value: str) -> str:
    digits = "".join(character for character in value if character.isdigit())
    if not 7 <= len(digits) <= 20:
        raise DomainError("invalid_phone", "Phone number is not valid.", 422)
    return f"+{digits}"

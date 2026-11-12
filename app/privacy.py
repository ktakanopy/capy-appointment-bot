"""Best-effort contact redaction before model input and checkpoint writes.

Known names can be removed; arbitrary names in free text cannot be identified
reliably. This is not a guarantee of named-entity anonymization.
"""
import re
from datetime import date

_TOKEN = re.compile(r"\bbk1\.[0-9a-fA-F-]{36}\.\d+\.[0-9a-f]{64}\b")
_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d ().-]{5,22}\d(?!\w)")


def sanitize_message(text: str, known_name: str | None = None) -> str:
    def phone(match):
        value = match.group()
        try:
            date.fromisoformat(value)
            return value
        except ValueError:
            return "[contact]"
    text = _TOKEN.sub("[private-token]", text)
    text = _EMAIL.sub("[contact]", text)
    text = _PHONE.sub(phone, text)
    if known_name:
        text = re.sub(r"(?<!\w)" + re.escape(known_name) + r"(?!\w)", "[customer]", text, flags=re.I)
    return text

from __future__ import annotations

from typing import TypedDict


class SchedulingGraphState(TypedDict, total=False):
    message: str
    service_names: list[str]
    professional_names: list[str]
    today: str
    has_slots: bool
    extraction: dict

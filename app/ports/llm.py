from __future__ import annotations

from typing import Protocol

from app.llm.schemas import SchedulingExtraction


class Interpreter(Protocol):
    def interpret(self, message: str, context: dict) -> SchedulingExtraction: ...

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Protocol

from app.graph.decision import DeterministicDecision
from app.graph.laya import LayaDecision
from app.llm.gemma import GemmaUnderstanding
from app.llm.schemas import SchedulingInterpretation


class Interpreter(Protocol):
    def interpret(self, message: str, context: dict) -> SchedulingInterpretation: ...


class DeterministicInterpreter:
    """Small, predictable local interpreter used only as an explicit evaluation baseline."""

    def interpret(self, message: str, context: dict) -> SchedulingInterpretation:
        text = " ".join(message.strip().split())
        lowered = text.lower()
        service_name = None
        for candidate in sorted(
            context.get("service_names", []), key=len, reverse=True
        ):
            if candidate.lower() in lowered:
                service_name = candidate
                break
        professional_name = None
        for candidate in context.get("professional_names", []):
            if candidate.lower() in lowered:
                professional_name = candidate
                break
        local_date = self._date(text, context.get("today"))
        local_time = self._time(text)
        slot_index = (
            self._index(lowered)
            if context.get("has_slots") and not local_date and not local_time
            else None
        )
        if local_date:
            text = re.sub(
                r"\b(tomorrow|amanh[ãa]|today|hoje)\b", local_date, text, flags=re.I
            )
        return SchedulingInterpretation(
            message=text,
            service_name=service_name,
            professional_name=professional_name,
            local_date=local_date,
            local_time=local_time,
            slot_index=slot_index,
        )

    @staticmethod
    def _date(text: str, today: date | None) -> str | None:
        match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
        if match:
            try:
                return date.fromisoformat(match.group(1)).isoformat()
            except ValueError:
                return None
        match = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", text)
        if match:
            try:
                return date(
                    int(match.group(3)), int(match.group(2)), int(match.group(1))
                ).isoformat()
            except ValueError:
                return None
        if isinstance(today, str):
            try:
                today = date.fromisoformat(today)
            except ValueError:
                today = None
        if today:
            if re.search(r"\b(tomorrow|amanh[ãa])\b", text, re.I):
                return (today + timedelta(days=1)).isoformat()
            if re.search(r"\b(today|hoje)\b", text, re.I):
                return today.isoformat()
        return None

    @staticmethod
    def _time(text: str) -> str | None:
        match = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
        if match:
            hour, minute = int(match.group(1)), int(match.group(2))
            if hour < 24 and minute < 60:
                return f"{hour:02}:{minute:02}"
        match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text, re.I)
        if match:
            hour = int(match.group(1)) % 12
            if match.group(3).lower() == "pm":
                hour += 12
            minute = int(match.group(2) or "0")
            return f"{hour:02}:{minute:02}"
        return None

    @staticmethod
    def _index(text: str) -> int | None:
        numeric = re.search(r"\b(\d{1,2})\b", text)
        if numeric:
            return int(numeric.group(1))
        for index, terms in enumerate(
            (
                ("first", "1st", "primeiro", "primeira"),
                ("second", "2nd", "segundo", "segunda"),
                ("third", "3rd", "terceiro", "terceira"),
            ),
            1,
        ):
            if any(term in text for term in terms):
                return index
        return None


def build_interpreter(
    provider: str, base_url: str = "http://localhost:8081"
) -> Interpreter:
    """Construct the requested provider; never silently substitute predictions."""
    if provider == "deterministic":
        return DeterministicInterpreter()
    if provider == "gemma":
        return GemmaUnderstanding(base_url)
    raise ValueError(f"Unsupported understanding provider: {provider}")


def build_decision(provider: str, base_url: str = "http://localhost:8082"):
    if provider == "deterministic":
        return DeterministicDecision()
    if provider == "laya":
        return LayaDecision(base_url)
    raise ValueError(f"Unsupported routing provider: {provider}")

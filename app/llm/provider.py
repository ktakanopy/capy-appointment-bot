from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Protocol

from app.llm.schemas import SchedulingExtraction


class Interpreter(Protocol):
    def interpret(self, message: str, context: dict) -> SchedulingExtraction: ...


class DeterministicInterpreter:
    """Small, predictable local interpreter used for the demo and as a safe fallback."""

    def interpret(self, message: str, context: dict) -> SchedulingExtraction:
        text = " ".join(message.strip().split())
        lowered = text.lower()
        intent = "unknown"
        if re.search(r"\b(reschedule|reschedul|move|change|mudar|remarcar|trocar)\b", lowered):
            intent = "reschedule"
        elif re.search(r"\b(cancel|cancelar|cancela)\b", lowered):
            intent = "cancel"
        elif re.search(r"\b(confirm|confirmar|confirmo)\b", lowered):
            intent = "confirm"
        elif re.search(r"\b(available|availability|book|booking|schedule|appointment|agendar|marcar|hor[aá]rio|dispon[ií]vel)\b", lowered):
            intent = "search"
        elif re.search(r"\b(help|ajuda|options|services|servi[cç]os)\b", lowered):
            intent = "help"
        elif context.get("has_slots") and re.search(r"\b(\d+|first|second|third|1st|2nd|3rd|primeiro|primeira|segundo|segunda)\b", lowered):
            intent = "select_slot"

        service_name = None
        for candidate in sorted(context.get("service_names", []), key=len, reverse=True):
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
        slot_index = self._index(lowered) if intent == "select_slot" else None
        phone_match = re.search(r"(?<!\w)(\+?\d[\d\s().-]{5,22}\d)(?!\w)", text)
        phone = re.sub(r"[\s().-]", "", phone_match.group(1)) if phone_match else None
        customer_name = None
        if phone_match:
            possible_name = text[:phone_match.start()].strip(" ,;:-")
            possible_name = re.sub(r"^(my name is|i am|i'm|name|nome)\s*:?\s+", "", possible_name, flags=re.I)
            possible_name = re.sub(r"\b(my )?(phone|number|contact|telefone|celular)\b.*$", "", possible_name, flags=re.I).strip(" ,;:-")
            if len(possible_name.split()) >= 2 and not self._looks_like_request(possible_name):
                customer_name = possible_name
        return SchedulingExtraction(
            intent=intent,
            service_name=service_name,
            professional_name=professional_name,
            local_date=local_date,
            local_time=local_time,
            slot_index=slot_index,
            customer_name=customer_name,
            phone=phone,
        )

    @staticmethod
    def _looks_like_request(value: str) -> bool:
        return bool(re.search(r"\b(book|schedule|appointment|available|cancel|confirm|reschedule|agendar|hor[aá]rio)\b", value, re.I))

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
                return date(int(match.group(3)), int(match.group(2)), int(match.group(1))).isoformat()
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
        for index, terms in enumerate((
            ("first", "1st", "primeiro", "primeira"),
            ("second", "2nd", "segundo", "segunda"),
            ("third", "3rd", "terceiro", "terceira"),
        ), 1):
            if any(term in text for term in terms):
                return index
        return None


class OpenAIInterpreter:
    def __init__(self, api_key: str, model: str):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model

    def interpret(self, message: str, context: dict) -> SchedulingExtraction:
        schema = SchedulingExtraction.model_json_schema()
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract scheduling intent and fields as JSON matching the supplied schema. "
                        "Do not authorize, claim availability, or perform actions. Use only listed services and professionals. "
                        "Return a local_date in YYYY-MM-DD when explicit, and a 24-hour local_time when explicit. "
                        "If information is absent, use null. Intent is one of search, select_slot, create, confirm, cancel, reschedule, help, unknown.\n"
                        f"Schema: {json.dumps(schema)}\n"
                        f"Available services: {context.get('service_names', [])}\n"
                        f"Available professionals: {context.get('professional_names', [])}\n"
                        f"Today's business-local date: {context.get('today')}"
                    ),
                },
                {"role": "user", "content": message},
            ],
        )
        content = response.choices[0].message.content or "{}"
        return SchedulingExtraction.model_validate_json(content)


class FallbackInterpreter:
    def __init__(self, primary: Interpreter, fallback: Interpreter):
        self.primary = primary
        self.fallback = fallback

    def interpret(self, message: str, context: dict) -> SchedulingExtraction:
        try:
            return self.primary.interpret(message, context)
        except Exception:
            return self.fallback.interpret(message, context)


def build_interpreter(provider: str, api_key: str | None, model: str) -> Interpreter:
    deterministic = DeterministicInterpreter()
    if provider == "openai" and api_key:
        return FallbackInterpreter(OpenAIInterpreter(api_key, model), deterministic)
    return deterministic

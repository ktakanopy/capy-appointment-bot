from __future__ import annotations

from datetime import date, time

from app.chat_helpers import _choose_professional, _choose_service, _slot_message
from app.graph.actions import SchedulingActions
from app.graph.contracts import DecisionOutput, TurnResult, WorkflowDraft
from app.graph.decision import normalized_from_extraction
from app.llm.schemas import SchedulingExtraction
from app.models import (
    AvailabilitySearchRequest,
    AvailabilitySlot,
    CatalogResponse,
    Service,
)


def _draft(state: dict) -> WorkflowDraft:
    return WorkflowDraft.model_validate(state.get("draft") or {})


def _extraction(state: dict) -> SchedulingExtraction:
    raw = dict(state.get("extraction") or {})
    raw.pop("customer_name", None)
    raw.pop("phone", None)
    return SchedulingExtraction.model_validate(raw)


def _catalog(state: dict) -> CatalogResponse:
    return CatalogResponse.model_validate(state["catalog"])


def _parse_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _parse_time(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = time.fromisoformat(value)
    except ValueError:
        return None
    if parsed.second or parsed.microsecond:
        return None
    return f"{parsed.hour:02}:{parsed.minute:02}"


def _result(text: str, intent: str, **extra) -> dict:
    route = extra.pop("route", "persist")
    draft = extra.pop("draft", None)
    update = {
        "result": TurnResult(text=text, intent=intent).model_dump(mode="json"),
        "route": route,
    }
    if draft is not None:
        update["draft"] = draft
    return update


def load_context(state: dict, actions: SchedulingActions) -> dict:
    draft = (
        WorkflowDraft.model_validate(state["draft"])
        if state.get("draft")
        else WorkflowDraft()
    )
    catalog = actions.catalog(state["business_id"])
    return {
        "draft": draft.model_dump(mode="json"),
        "catalog": catalog.model_dump(mode="json"),
        "service_names": [item.name for item in catalog.services],
        "professional_names": [item.name for item in catalog.professionals],
    }


def interpret(state: dict, actions: SchedulingActions, interpreter) -> dict:
    message = state.get("message", "")
    history = list(state.get("messages") or [])
    if history and history[-1].get("content") == message:
        history = history[:-1]
    extraction = interpreter.interpret(
        message,
        {
            "service_names": state.get("service_names", []),
            "professional_names": state.get("professional_names", []),
            "today": state.get("today"),
            "has_slots": bool(
                state.get("offered_slots") or _draft(state).candidate_booking_ids
            ),
            "history": history,
        },
    )
    safe = extraction.model_dump(mode="json")
    return {"extraction": safe}


def merge_draft(state: dict) -> dict:
    draft = _draft(state)
    extraction = _extraction(state)
    catalog = _catalog(state)
    invalid_date = False
    if extraction.service_name:
        service = _choose_service(catalog, extraction.service_name)
        if service is not None:
            draft.service_id = service.id
            draft.service_name = service.name
            extraction = extraction.model_copy(update={"service_name": service.name})
    elif (
        draft.service_id is None
        and len(catalog.services) == 1
        and extraction.intent in {"search", "create"}
    ):
        service = catalog.services[0]
        draft.service_id = service.id
        draft.service_name = service.name
    if extraction.professional_name:
        professional = _choose_professional(catalog, extraction.professional_name)
        if professional is not None:
            draft.professional_id = professional.id
    parsed_date = _parse_date(extraction.local_date)
    if extraction.local_date and parsed_date is None:
        invalid_date = True
        draft.local_date = None
    elif parsed_date:
        draft.local_date = parsed_date
    parsed_time = _parse_time(extraction.local_time)
    if parsed_time:
        draft.earliest_local_time = parsed_time
    if extraction.intent in {"reschedule", "confirm", "cancel"}:
        draft.operation = extraction.intent
    elif extraction.intent in {"search", "create"} and draft.operation != "reschedule":
        draft.operation = "create"
    elif draft.operation == "none" and (
        extraction.intent == "select_slot" or state.get("selected_slot_id")
    ):
        draft.operation = "create"
    draft.invalid_date = invalid_date
    draft.missing = (
        "booking"
        if draft.candidate_booking_ids and extraction.slot_index
        else _missing(draft, extraction, catalog)
    )
    return {
        "draft": draft.model_dump(mode="json"),
        "extraction": extraction.model_dump(mode="json"),
    }


def _missing(
    draft: WorkflowDraft, extraction: SchedulingExtraction, catalog: CatalogResponse
) -> str:
    if (
        extraction.service_name
        and _choose_service(catalog, extraction.service_name) is None
        and draft.service_id is None
    ):
        return "service"
    if (
        extraction.professional_name
        and _choose_professional(catalog, extraction.professional_name) is None
    ):
        return "professional"
    if draft.invalid_date:
        return "date"
    if (
        draft.operation in {"create", "reschedule", "none"}
        and not draft.service_id
        and extraction.intent in {"search", "reschedule", "create"}
    ):
        return "service"
    if (
        draft.operation in {"create", "reschedule"}
        and not draft.local_date
        and extraction.intent in {"search", "reschedule", "create", "unknown"}
    ):
        return "date"
    return "none"


def route_turn(state: dict, decision_engine, threshold: float) -> dict:
    draft = _draft(state)
    extraction = _extraction(state)
    normalized = normalized_from_extraction(
        extraction,
        conversation_id=state["conversation_id"],
        customer_id=None,
        transcript=state.get("message", ""),
        modality=state.get("input_modality", "text"),
        draft=draft,
    )
    # A numbered reply to a booking clarification selects only a currently owned booking.
    selected_booking_id = None
    if (
        draft.missing == "booking"
        and draft.candidate_booking_ids
        and extraction.slot_index
    ):
        if 1 <= extraction.slot_index <= len(draft.candidate_booking_ids):
            selected_booking_id = draft.candidate_booking_ids[extraction.slot_index - 1]
            draft.active_booking_id = selected_booking_id
            draft.candidate_booking_ids = []
            extraction = extraction.model_copy(update={"slot_index": None})
    decision = decision_engine.decide(normalized, draft)
    can_proceed = not decision.needs_human and decision.confidence >= threshold
    if selected_booking_id and can_proceed:
        route = "prepare_action"
    else:
        route = _constrain(decision, draft, extraction, state, threshold)
    return {
        "normalized_request": normalized.model_dump(mode="json"),
        "extraction": extraction.model_dump(mode="json"),
        "decision": decision.model_dump(mode="json"),
        "route": route,
        "draft": draft.model_dump(mode="json"),
    }


def _constrain(
    decision: DecisionOutput,
    draft: WorkflowDraft,
    extraction: SchedulingExtraction,
    state: dict,
    threshold: float,
) -> str:
    if state.get("selected_slot_id") and not any(
        s["slot_id"] == state["selected_slot_id"]
        for s in state.get("offered_slots", [])
    ):
        return "select"  # Invalid offered-slot references can only produce a clarification.
    if decision.needs_human or decision.confidence < threshold:
        return "escalate"
    if state.get("selected_slot_id") or extraction.slot_index:
        return "select"
    if decision.next_action == "LOOKUP_BOOKING":
        return "lookup"
    if decision.next_action == "FAQ":
        return "help"
    action = decision.next_action
    if (
        action == "RESCHEDULE"
        and extraction.intent != "reschedule"
        and draft.operation != "reschedule"
    ):
        action = "CHECK_AVAILABILITY"
    if (
        action == "CONFIRM"
        and extraction.intent != "confirm"
        and draft.operation != "confirm"
    ):
        return "clarify"
    if (
        action == "CANCEL"
        and extraction.intent != "cancel"
        and draft.operation != "cancel"
    ):
        return "clarify"
    if action == "ESCALATE":
        return "escalate"
    if action in {"CONFIRM", "CANCEL"}:
        return "prepare_action"
    if action == "RESCHEDULE":
        return "prepare_action"
    if action in {"CHECK_AVAILABILITY", "BOOK"}:
        if (
            draft.missing in {"service", "date", "professional"}
            or not draft.service_id
            or not draft.local_date
        ):
            return "clarify"
        return "search"
    return "clarify"


def clarify(state: dict, actions: SchedulingActions) -> dict:
    draft = _draft(state)
    extraction = _extraction(state)
    catalog = _catalog(state)
    if extraction.intent in {"confirm", "cancel", "reschedule"} and not state.get(
        "customer_id"
    ):
        return _result(
            "Use your Telegram account to manage your appointments.",
            "manage_booking",
            route="persist",
            draft=draft.model_dump(mode="json"),
        )
    if draft.missing == "professional" or (
        extraction.professional_name
        and _choose_professional(catalog, extraction.professional_name) is None
    ):
        names = ", ".join(item.name for item in catalog.professionals)
        draft.missing = "professional"
        return _result(
            f"I couldn't find that professional. Choose from: {names}.",
            "clarify_professional",
            route="persist",
            draft=draft.model_dump(mode="json"),
        )
    if draft.missing == "service" or (
        extraction.intent == "search" and not draft.service_id
    ):
        names = ", ".join(item.name for item in catalog.services)
        draft.missing = "service"
        return _result(
            f"Which service would you like? Available services: {names}.",
            "clarify_service",
            route="persist",
            draft=draft.model_dump(mode="json"),
        )
    if draft.invalid_date or draft.missing == "date" or not draft.local_date:
        draft.missing = "date"
        service_name = draft.service_name or "that service"
        text = (
            f"What date would you like to book {service_name}? You can say today, tomorrow, or give a date."
            if draft.operation != "reschedule"
            else "What date would you like to move the booking to?"
        )
        if draft.invalid_date:
            text = "That date was not valid. Please give a date as YYYY-MM-DD, or say today or tomorrow."
        return _result(
            text, "clarify_date", route="persist", draft=draft.model_dump(mode="json")
        )
    return _result(
        "Tell me which service you'd like and the date,.",
        "clarify",
        route="persist",
        draft=draft.model_dump(mode="json"),
    )


def search(state: dict, actions: SchedulingActions) -> dict:
    draft = _draft(state)
    catalog = _catalog(state)
    service = _service(catalog, draft.service_id)
    if service is None or not draft.local_date:
        return clarify(state, actions)
    earliest = _parse_time(draft.earliest_local_time)
    slots = actions.search(
        AvailabilitySearchRequest(
            business_id=state["business_id"],
            service_id=service.id,
            professional_id=draft.professional_id,
            start_date=date.fromisoformat(draft.local_date),
            end_date=date.fromisoformat(draft.local_date),
            earliest_local_time=time.fromisoformat(earliest) if earliest else None,
        )
    )
    draft.offered_slot_ids = [slot.slot_id for slot in slots]
    draft.pending_slot_id = None
    if draft.operation != "reschedule":
        draft.operation = "create"
    draft.missing = "slot" if slots else "date"
    intent = "reschedule" if draft.operation == "reschedule" else "search"
    update = _result(
        _slot_message(slots),
        intent,
        route="persist",
        draft=draft.model_dump(mode="json"),
    )
    update["offered_slots"] = [slot.model_dump(mode="json") for slot in slots]
    update["result"]["slots"] = update["offered_slots"]
    return update


def select(state: dict, actions: SchedulingActions) -> dict:
    draft = _draft(state)
    extraction = _extraction(state)
    slots = _offered_slots(state)
    chosen = _chosen_slot(slots, state.get("selected_slot_id"), extraction.slot_index)
    if chosen is None:
        return _result(
            "Choose one of the times I just offered.",
            "clarify",
            route="persist",
            draft=draft.model_dump(mode="json"),
        )
    draft.pending_slot_id = chosen.slot_id
    if extraction.intent == "reschedule" or draft.operation == "reschedule":
        draft.operation = "reschedule"
    else:
        draft.operation = "create"
    if draft.operation == "create" and not state.get("customer_id"):
        draft.missing = "booking"
        return _result(
            "Use your Telegram account to book an appointment.",
            "manage_booking",
            route="persist",
            draft=draft.model_dump(mode="json"),
        )
    return {
        "route": "prepare_action",
        "draft": draft.model_dump(mode="json"),
        "selected_slot_id": chosen.slot_id,
    }


def prepare_action(state: dict, actions: SchedulingActions) -> dict:
    draft = _draft(state)
    if draft.operation in {"confirm", "cancel", "reschedule"} and not state.get(
        "customer_id"
    ):
        return _result(
            "Use your Telegram account to manage your appointments.",
            "manage_booking",
            route="persist",
            draft=draft.model_dump(mode="json"),
        )
    if draft.operation == "reschedule":
        return _prepare_reschedule(state, draft, actions)
    if draft.operation == "create":
        return _prepare_create(state, draft, actions)
    if draft.operation in {"confirm", "cancel"}:
        return _prepare_booking_status_change(state, draft, actions)
    return clarify(state, actions)


def _prepare_reschedule(
    state: dict, draft: WorkflowDraft, actions: SchedulingActions
) -> dict:
    booking_id = state.get("booking_id") or draft.active_booking_id
    refs = actions.list_booking_refs()
    if not booking_id:
        if not refs:
            draft.operation = "create"
            draft.missing = "date" if not draft.local_date else "none"
            return _result(
                "I could not find an upcoming appointment to move. Tell me the service and date if you want to book a new one.",
                "clarify",
                route="persist",
                draft=draft.model_dump(mode="json"),
            )
        if len(refs) > 1:
            draft.candidate_booking_ids = [item.id for item in refs]
            draft.missing = "booking"
            lines = [f"{index}. {item.label}" for index, item in enumerate(refs, 1)]
            return _result(
                "Which appointment should I move?\n" + "\n".join(lines),
                "clarify_booking",
                route="persist",
                draft=draft.model_dump(mode="json"),
            )
        booking_id = refs[0].id
        draft.active_booking_id = booking_id
    if not draft.service_id:
        selected = next((item for item in refs if item.id == booking_id), None)
        if selected is not None:
            draft.service_id = selected.service_id
            draft.professional_id = draft.professional_id or selected.professional_id
    if not draft.pending_slot_id:
        if draft.local_date and draft.service_id:
            return search(
                {
                    **state,
                    "draft": draft.model_dump(mode="json"),
                    "booking_id": booking_id,
                },
                actions,
            )
        draft.missing = "service" if not draft.service_id else "date"
        text = (
            "Which service is the booking for?"
            if not draft.service_id
            else "What date would you like to move the booking to?"
        )
        return _result(
            text,
            "reschedule",
            route="persist",
            draft=draft.model_dump(mode="json"),
        )
    return {
        "route": "execute",
        "draft": draft.model_dump(mode="json"),
        "booking_id": booking_id,
    }


def _prepare_create(
    state: dict, draft: WorkflowDraft, actions: SchedulingActions
) -> dict:
    if not draft.pending_slot_id and not state.get("selected_slot_id"):
        return clarify(state, actions)
    if not state.get("customer_id"):
        draft.missing = "booking"
        return _result(
            "Use your Telegram account to book an appointment.",
            "manage_booking",
            route="persist",
            draft=draft.model_dump(mode="json"),
        )
    return {"route": "execute", "draft": draft.model_dump(mode="json")}


def _prepare_booking_status_change(
    state: dict, draft: WorkflowDraft, actions: SchedulingActions
) -> dict:
    booking_id = state.get("booking_id") or draft.active_booking_id
    if not booking_id and state.get("customer_id"):
        refs = actions.list_booking_refs()
        if len(refs) != 1:
            draft.candidate_booking_ids = [item.id for item in refs]
            draft.missing = "booking"
            return _result(
                "I need to know which appointment you mean.",
                "clarify_booking",
                route="persist",
                draft=draft.model_dump(mode="json"),
            )
        booking_id = refs[0].id
        draft.active_booking_id = booking_id
    if not booking_id:
        return _result(
            "Use your Telegram account to manage your appointments.",
            "manage_booking",
            route="persist",
            draft=draft.model_dump(mode="json"),
        )
    return {
        "route": "execute",
        "draft": draft.model_dump(mode="json"),
        "booking_id": booking_id,
    }


def execute(state: dict, actions: SchedulingActions) -> dict:
    draft = _draft(state)
    key = state["idempotency_key"]
    if not key:
        raise ValueError("A stable turn idempotency key is required")
    slots = _offered_slots(state)
    chosen = _chosen_slot(
        slots, draft.pending_slot_id or state.get("selected_slot_id"), None
    )
    booking_id = state.get("booking_id") or draft.active_booking_id
    if draft.operation == "create":
        if chosen is None:
            return _result(
                "Choose one of the times I just offered.",
                "clarify",
                route="persist",
                draft=draft.model_dump(mode="json"),
            )
        actions.create_booking(chosen, key)
        draft.pending_slot_id = None
        draft.missing = "none"
        draft.operation = "none"
        return _result(
            f"You're booked for {chosen.local_label} with {chosen.professional_name}.",
            "create",
            route="persist",
            draft=draft.model_dump(mode="json"),
        )
    if draft.operation == "confirm":
        _, outcome = actions.confirm(booking_id, key)
        text = (
            "Your booking is confirmed."
            if outcome == "confirmed"
            else "Your booking was already confirmed."
        )
        return _result(
            text, "confirm", route="persist", draft=draft.model_dump(mode="json")
        )
    if draft.operation == "cancel":
        _, outcome = actions.cancel(booking_id, key)
        text = (
            "Your booking is canceled."
            if outcome == "canceled"
            else "Your booking was already canceled."
        )
        return _result(
            text, "cancel", route="persist", draft=draft.model_dump(mode="json")
        )
    if draft.operation == "reschedule":
        if chosen is None or not booking_id:
            return _result(
                "Choose one of the times I just offered.",
                "clarify",
                route="persist",
                draft=draft.model_dump(mode="json"),
            )
        actions.reschedule(booking_id, chosen, key)
        draft.pending_slot_id = None
        draft.operation = "none"
        return _result(
            "Your booking has been moved.",
            "reschedule",
            route="persist",
            draft=draft.model_dump(mode="json"),
        )
    return clarify(state, actions)


def help_reply(state: dict, actions: SchedulingActions) -> dict:
    _ = actions
    catalog = _catalog(state)
    message = state.get("message", "")
    lowered = message.lower()
    if any(word in lowered for word in ("price", "cost", "how much", "quanto")):
        lines = [
            f"{item.name}: {item.currency} {item.price_minor / 100:.2f}"
            for item in catalog.services
        ]
        text = "Here are the current prices:\n" + "\n".join(lines)
    elif any(word in lowered for word in ("duration", "how long", "dura")):
        lines = [
            f"{item.name}: {item.duration_minutes} minutes" for item in catalog.services
        ]
        text = "Here are the service durations:\n" + "\n".join(lines)
    elif "prepar" in lowered:
        lines = [f"{item.name}: {item.description}" for item in catalog.services]
        text = "\n".join(lines)
    elif "polic" in lowered or "fee" in lowered:
        text = "You can cancel or reschedule until the appointment starts. This demo does not charge a cancellation fee."
    else:
        names = ", ".join(item.name for item in catalog.services)
        text = f"I can help you find a time, book a service, or manage an existing booking. Services here: {names}."
    return _result(text, "help", route="persist")


def lookup(state: dict, actions: SchedulingActions) -> dict:
    if not state.get("customer_id"):
        return _result(
            "I can only look up appointments for a verified channel identity. A phone number typed in the message is not enough.",
            "manage_booking",
            route="persist",
        )
    refs = actions.list_booking_refs()
    if not refs:
        return _result(
            "I could not find an upcoming appointment.", "lookup", route="persist"
        )
    lines = [f"{index}. {item.label}" for index, item in enumerate(refs, 1)]
    return _result(
        "Here are your upcoming appointments:\n" + "\n".join(lines),
        "lookup",
        route="persist",
    )


def escalate(state: dict, actions: SchedulingActions) -> dict:
    draft = _draft(state)
    draft.escalated = True
    actions.record_escalation(state["conversation_id"])
    return _result(
        "I can't help with that request. I can help you find a time, book, confirm, cancel, or reschedule.",
        "escalate",
        route="persist",
        draft=draft.model_dump(mode="json"),
    )


def persist(state: dict, actions: SchedulingActions) -> dict:
    _ = actions
    draft = _draft(state)
    result = dict(
        state.get("result")
        or _result("Tell me which service you'd like and the date.", "clarify")[
            "result"
        ]
    )
    update = {"result": result, "draft": draft.model_dump(mode="json")}
    if result.get("text"):
        update["messages"] = [{"role": "assistant", "content": result["text"]}]
    return update


def _service(catalog: CatalogResponse, service_id: str | None) -> Service | None:
    if not service_id:
        return None
    return next((item for item in catalog.services if item.id == service_id), None)


def _offered_slots(state: dict) -> list[AvailabilitySlot]:
    return [
        AvailabilitySlot.model_validate(item) for item in state.get("offered_slots", [])
    ]


def _chosen_slot(
    slots: list[AvailabilitySlot], slot_id: str | None, index: int | None
) -> AvailabilitySlot | None:
    if slot_id:
        return next((slot for slot in slots if slot.slot_id == slot_id), None)
    if index is not None and 1 <= index <= len(slots):
        return slots[index - 1]
    return None

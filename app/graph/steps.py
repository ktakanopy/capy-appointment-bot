from __future__ import annotations

from datetime import date, time

from app.chat_helpers import _choose_professional, _choose_service, _request_key, _slot_message
from app.graph.contracts import BookingRef, DecisionOutput, TurnResult, WorkflowDraft
from app.graph.decision import DeterministicDecision, normalized_from_extraction
from app.graph.ports import WorkflowPorts
from app.llm.schemas import SchedulingExtraction
from app.models import AvailabilitySearchRequest, AvailabilitySlot, CatalogResponse, Service


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
    update = {"result": TurnResult(text=text, intent=intent).model_dump(mode="json"), "route": route}
    if draft is not None:
        update["draft"] = draft
    return update


def load_context(state: dict, ports: WorkflowPorts) -> dict:
    draft = WorkflowDraft.model_validate(state["draft"]) if state.get("draft") else WorkflowDraft()
    catalog = ports.catalog(state["business_id"])
    return {
        "draft": draft.model_dump(mode="json"),
        "catalog": catalog.model_dump(mode="json"),
        "service_names": [item.name for item in catalog.services],
        "professional_names": [item.name for item in catalog.professionals],
    }


def interpret(state: dict, ports: WorkflowPorts, interpreter) -> dict:
    if state.get("skip_interpret"):
        from app.graph.contracts import NormalizedRequest

        normalized = NormalizedRequest.model_validate(state["normalized_request"])
        intent = normalized.intent if normalized.intent in {
            "search", "select_slot", "create", "confirm", "cancel", "reschedule", "help", "unknown"
        } else "unknown"
        extraction = SchedulingExtraction(
            intent=intent,
            service_name=normalized.service,
            professional_name=normalized.professional,
            local_date=normalized.date_constraint,
            local_time=normalized.time_constraint,
        )
        return {
            "extraction": extraction.model_dump(mode="json"),
            "customer_ready": bool(state.get("customer_ready") or ports.customer_ready()),
        }
    history = list(state.get("messages") or [])
    if history and history[-1].get("content") == state.get("message"):
        history = history[:-1]
    extraction = interpreter.interpret(
        state.get("message", ""),
        {
            "service_names": state.get("service_names", []),
            "professional_names": state.get("professional_names", []),
            "today": state.get("today"),
            "has_slots": bool(_offered_slots(state, ports)),
            "history": history,
        },
    )
    ports.remember_extraction(extraction)
    ready = ports.capture_customer(extraction.customer_name, extraction.phone)
    safe = extraction.model_dump(mode="json")
    safe["customer_name"] = None
    safe["phone"] = None
    return {
        "extraction": safe,
        "customer_ready": bool(state.get("customer_ready") or ready or ports.customer_ready()),
    }


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
    elif draft.service_id is None and len(catalog.services) == 1 and extraction.intent == "search":
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
    if extraction.intent == "reschedule":
        draft.operation = "reschedule"
    elif extraction.intent == "confirm":
        draft.operation = "confirm"
    elif extraction.intent == "cancel":
        draft.operation = "cancel"
    elif extraction.intent == "search" and draft.operation != "reschedule":
        draft.operation = "create"
    elif extraction.intent == "select_slot" and draft.operation == "none":
        draft.operation = "create"
    elif state.get("selected_slot_id") and draft.operation == "none":
        draft.operation = "create"
    draft.invalid_date = invalid_date
    draft.missing = _missing(draft, extraction, catalog)
    return {"draft": draft.model_dump(mode="json"), "extraction": extraction.model_dump(mode="json")}


def _missing(draft: WorkflowDraft, extraction: SchedulingExtraction, catalog: CatalogResponse) -> str:
    if extraction.service_name and _choose_service(catalog, extraction.service_name) is None and draft.service_id is None:
        return "service"
    if extraction.professional_name and _choose_professional(catalog, extraction.professional_name) is None:
        return "professional"
    if draft.invalid_date:
        return "date"
    if draft.operation in {"create", "reschedule", "none"} and not draft.service_id and extraction.intent in {"search", "reschedule"}:
        return "service"
    if draft.operation in {"create", "reschedule"} and not draft.local_date and extraction.intent in {"search", "reschedule", "unknown"}:
        return "date"
    return "none"


def route_turn(state: dict, decision_engine, threshold: float) -> dict:
    from app.graph.contracts import NormalizedRequest

    draft = _draft(state)
    extraction = _extraction(state)
    if state.get("normalized_request"):
        normalized = NormalizedRequest.model_validate(state["normalized_request"])
    else:
        normalized = normalized_from_extraction(
            extraction,
            conversation_id=state["conversation_id"],
            customer_id=state.get("customer_id"),
            transcript=state.get("message", ""),
            modality=state.get("input_modality", "text"),
            draft=draft,
        )
    decision = decision_engine.decide(normalized, draft)
    route = _constrain(decision, draft, extraction, state, threshold)
    return {
        "normalized_request": normalized.model_dump(mode="json"),
        "decision": decision.model_dump(mode="json"),
        "route": route,
        "draft": draft.model_dump(mode="json"),
    }


def _constrain(decision: DecisionOutput, draft: WorkflowDraft, extraction: SchedulingExtraction, state: dict, threshold: float) -> str:
    if state.get("selected_slot_id") or extraction.slot_index or (draft.pending_slot_id and state.get("customer_ready")):
        return "select"
    if decision.next_action == "LOOKUP_BOOKING":
        return "lookup"
    if decision.next_action == "FAQ":
        return "help"
    recovered = draft.service_id and draft.local_date and draft.operation in {"create", "reschedule"}
    if (decision.needs_human or decision.confidence < threshold) and not recovered:
        return "escalate"
    action = decision.next_action
    if action == "RESCHEDULE" and extraction.intent != "reschedule" and draft.operation != "reschedule":
        action = "CHECK_AVAILABILITY"
    if action == "CONFIRM" and extraction.intent != "confirm" and draft.operation != "confirm":
        return "clarify"
    if action == "CANCEL" and extraction.intent != "cancel" and draft.operation != "cancel":
        return "clarify"
    if action == "ESCALATE":
        return "escalate"
    if action in {"CONFIRM", "CANCEL"}:
        return "prepare_action"
    if action == "RESCHEDULE":
        return "prepare_action"
    if action in {"CHECK_AVAILABILITY", "BOOK"}:
        if draft.missing in {"service", "date", "professional"} or not draft.service_id or not draft.local_date:
            return "clarify"
        return "search"
    return "clarify"


def clarify(state: dict, ports: WorkflowPorts) -> dict:
    draft = _draft(state)
    extraction = _extraction(state)
    catalog = _catalog(state)
    if extraction.intent in {"confirm", "cancel", "reschedule"} and state.get("auth_kind") == "anonymous":
        return _result(
            "To manage a booking, provide its private management token in the booking controls.",
            "manage_booking",
            route="persist",
            draft=draft.model_dump(mode="json"),
        )
    if draft.missing == "professional" or (
        extraction.professional_name and _choose_professional(catalog, extraction.professional_name) is None
    ):
        names = ", ".join(item.name for item in catalog.professionals)
        draft.missing = "professional"
        return _result(
            f"I couldn't find that professional. Choose from: {names}.",
            "clarify_professional",
            route="persist",
            draft=draft.model_dump(mode="json"),
        )
    if draft.missing == "service" or (extraction.intent == "search" and not draft.service_id):
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
        return _result(text, "clarify_date", route="persist", draft=draft.model_dump(mode="json"))
    return _result(
        "Tell me which service you'd like and the date, or choose an action in the booking controls.",
        "clarify",
        route="persist",
        draft=draft.model_dump(mode="json"),
    )


def search(state: dict, ports: WorkflowPorts) -> dict:
    draft = _draft(state)
    extraction = _extraction(state)
    catalog = _catalog(state)
    service = _service(catalog, draft.service_id)
    if service is None or not draft.local_date:
        return clarify(state, ports)
    earliest = _parse_time(draft.earliest_local_time)
    slots = ports.search(
        AvailabilitySearchRequest(
            business_id=state["business_id"],
            service_id=service.id,
            professional_id=draft.professional_id,
            start_date=date.fromisoformat(draft.local_date),
            end_date=date.fromisoformat(draft.local_date),
            earliest_local_time=time.fromisoformat(earliest) if earliest else None,
        )
    )
    ports.save_slots(state["conversation_id"], slots)
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
    return update


def select(state: dict, ports: WorkflowPorts) -> dict:
    draft = _draft(state)
    extraction = _extraction(state)
    slots = _offered_slots(state, ports)
    chosen = _chosen_slot(slots, state.get("selected_slot_id"), extraction.slot_index)
    if chosen is None and draft.pending_slot_id and state.get("customer_ready"):
        chosen = next((slot for slot in slots if slot.slot_id == draft.pending_slot_id), None)
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
    if draft.operation == "create" and not (state.get("customer_ready") or ports.customer_ready()):
        draft.missing = "customer"
        return _result(
            f"I can hold this choice while we finish the demo flow. Please share your full name and phone number for {chosen.local_label}.",
            "collect_customer",
            route="persist",
            draft=draft.model_dump(mode="json"),
        )
    return {
        "route": "prepare_action",
        "draft": draft.model_dump(mode="json"),
        "selected_slot_id": chosen.slot_id,
    }


def prepare_action(state: dict, ports: WorkflowPorts) -> dict:
    draft = _draft(state)
    if draft.operation in {"confirm", "cancel", "reschedule"} and state.get("auth_kind") == "anonymous":
        return _result(
            "To manage a booking, provide its private management token in the booking controls.",
            "manage_booking",
            route="persist",
            draft=draft.model_dump(mode="json"),
        )
    if draft.operation == "reschedule":
        booking_id = state.get("booking_id") or draft.active_booking_id
        if not booking_id:
            refs = ports.list_booking_refs()
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
        if not draft.pending_slot_id:
            if draft.local_date and draft.service_id:
                return search({**state, "draft": draft.model_dump(mode="json"), "booking_id": booking_id}, ports)
            draft.missing = "date"
            return _result(
                "What date would you like to move the booking to?",
                "reschedule",
                route="persist",
                draft=draft.model_dump(mode="json"),
            )
        return {"route": "execute", "draft": draft.model_dump(mode="json"), "booking_id": booking_id}
    if draft.operation == "create":
        if not draft.pending_slot_id and not state.get("selected_slot_id"):
            return clarify(state, ports)
        if not (state.get("customer_ready") or ports.customer_ready()):
            draft.missing = "customer"
            return _result(
                "Please share your full name and phone number to finish the booking.",
                "collect_customer",
                route="persist",
                draft=draft.model_dump(mode="json"),
            )
        return {"route": "execute", "draft": draft.model_dump(mode="json")}
    if draft.operation in {"confirm", "cancel"}:
        booking_id = state.get("booking_id") or draft.active_booking_id
        if not booking_id and state.get("auth_kind") == "customer":
            refs = ports.list_booking_refs()
            if len(refs) != 1:
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
                "To manage a booking, provide its private management token in the booking controls.",
                "manage_booking",
                route="persist",
                draft=draft.model_dump(mode="json"),
            )
        return {"route": "execute", "draft": draft.model_dump(mode="json"), "booking_id": booking_id}
    return clarify(state, ports)


def execute(state: dict, ports: WorkflowPorts) -> dict:
    draft = _draft(state)
    key = _request_key(state.get("idempotency_key"), f"{state['conversation_id']}:{draft.operation}:{draft.pending_slot_id or draft.active_booking_id}")
    slots = _offered_slots(state, ports)
    chosen = _chosen_slot(slots, draft.pending_slot_id or state.get("selected_slot_id"), None)
    booking_id = state.get("booking_id") or draft.active_booking_id
    if draft.operation == "create":
        if chosen is None:
            return _result("Choose one of the times I just offered.", "clarify", route="persist", draft=draft.model_dump(mode="json"))
        summary = ports.create_booking(chosen, key)
        draft.pending_slot_id = None
        draft.missing = "none"
        draft.operation = "none"
        _ = summary
        return _result(
            f"You're booked for {chosen.local_label} with {chosen.professional_name}. Your private management token is shown in the booking details.",
            "create",
            route="persist",
            draft=draft.model_dump(mode="json"),
        )
    if draft.operation == "confirm":
        booking, outcome = ports.confirm(booking_id, key)
        text = "Your booking is confirmed." if outcome == "confirmed" else "Your booking was already confirmed."
        return _result(text, "confirm", route="persist", draft=draft.model_dump(mode="json"))
    if draft.operation == "cancel":
        booking, outcome = ports.cancel(booking_id, key)
        text = "Your booking is canceled." if outcome == "canceled" else "Your booking was already canceled."
        return _result(text, "cancel", route="persist", draft=draft.model_dump(mode="json"))
    if draft.operation == "reschedule":
        if chosen is None or not booking_id:
            return _result("Choose one of the times I just offered.", "clarify", route="persist", draft=draft.model_dump(mode="json"))
        ports.reschedule(booking_id, chosen, key)
        draft.pending_slot_id = None
        draft.operation = "none"
        return _result("Your booking has been moved.", "reschedule", route="persist", draft=draft.model_dump(mode="json"))
    return clarify(state, ports)


def help_reply(state: dict, ports: WorkflowPorts) -> dict:
    _ = ports
    catalog = _catalog(state)
    message = state.get("message", "")
    lowered = message.lower()
    if any(word in lowered for word in ("price", "cost", "how much", "quanto")):
        lines = [f"{item.name}: {item.currency} {item.price_minor / 100:.2f}" for item in catalog.services]
        text = "Here are the current prices:\n" + "\n".join(lines)
    elif any(word in lowered for word in ("duration", "how long", "dura")):
        lines = [f"{item.name}: {item.duration_minutes} minutes" for item in catalog.services]
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


def lookup(state: dict, ports: WorkflowPorts) -> dict:
    if state.get("auth_kind") == "anonymous":
        return _result(
            "I can only look up appointments for a verified channel identity. A phone number typed in the message is not enough.",
            "manage_booking",
            route="persist",
        )
    refs = ports.list_booking_refs()
    if not refs:
        return _result("I could not find an upcoming appointment.", "lookup", route="persist")
    lines = [f"{index}. {item.label}" for index, item in enumerate(refs, 1)]
    return _result("Here are your upcoming appointments:\n" + "\n".join(lines), "lookup", route="persist")


def escalate(state: dict, ports: WorkflowPorts) -> dict:
    draft = _draft(state)
    draft.escalated = True
    ports.record_escalation(state["conversation_id"])
    return _result(
        "I can't help with that request. I can help you find a time, book, confirm, cancel, or reschedule.",
        "escalate",
        route="persist",
        draft=draft.model_dump(mode="json"),
    )


def persist(state: dict, ports: WorkflowPorts) -> dict:
    _ = ports
    draft = _draft(state)
    result = dict(state.get("result") or _result("Tell me which service you'd like and the date.", "clarify")["result"])
    update = {"result": result, "draft": draft.model_dump(mode="json")}
    if result.get("text"):
        update["messages"] = [{"role": "assistant", "content": result["text"]}]
    return update


def _service(catalog: CatalogResponse, service_id: str | None) -> Service | None:
    if not service_id:
        return None
    return next((item for item in catalog.services if item.id == service_id), None)


def _offered_slots(state: dict, ports: WorkflowPorts) -> list[AvailabilitySlot]:
    raw = state.get("offered_slots")
    if raw:
        return [AvailabilitySlot.model_validate(item) for item in raw]
    return ports.load_slots(state["conversation_id"])


def _chosen_slot(slots: list[AvailabilitySlot], slot_id: str | None, index: int | None) -> AvailabilitySlot | None:
    if slot_id:
        return next((slot for slot in slots if slot.slot_id == slot_id), None)
    if index is not None and 1 <= index <= len(slots):
        return slots[index - 1]
    return None


def default_decision():
    return DeterministicDecision()

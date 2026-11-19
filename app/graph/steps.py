from __future__ import annotations

from datetime import date, time

from pydantic import ValidationError

from app.chat_helpers import _choose_professional, _choose_service, _slot_message
from app.graph.actions import SchedulingActions
from app.graph.context import TurnInput
from app.graph.contracts import DecisionOutput, NextAction, Operation, TurnResult, WorkflowDraft
from app.graph.state import HISTORY_LIMIT
from app.llm.gemma import ProviderError
from app.llm.schemas import SchedulingInterpretation
from app.models import (
    AvailabilitySearchRequest,
    AvailabilitySlot,
    CatalogResponse,
    Service,
)
from app.observability import debug_event
from app.privacy import sanitize_message


_EXISTING_BOOKING_OPERATIONS: dict[NextAction, Operation] = {
    "CONFIRM": "confirm",
    "CANCEL": "cancel",
    "RESCHEDULE": "reschedule",
}


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
    """Build the state update that carries a node's reply through the graph.

    Nodes such as clarify and lookup supply the customer-facing text and an
    outcome label such as "clarify_date". TurnResult validates these values
    and supplies defaults for slots and booking before conversion to a
    JSON-compatible dictionary. The workflow reads this result after the graph
    finishes and returns it to the caller.

    The optional route selects the next node when the graph uses a conditional
    edge. It defaults to "persist", which finalizes the reply and adds it to
    conversation history. An optional draft carries updated scheduling details
    into state for later turns. Other keyword arguments are currently ignored.

    For example, _result("What date?", "clarify_date") returns an update
    containing result text "What date?", intent "clarify_date", empty slots,
    booking None, and route "persist".
    """
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
    saved_draft = state.get("draft")
    draft = WorkflowDraft.model_validate(saved_draft or {})
    catalog = actions.catalog(state["business_id"])
    debug_event(
        "workflow.context.loaded", services=len(catalog.services),
        professionals=len(catalog.professionals), resumed=bool(saved_draft),
    )
    return {
        "draft": draft.model_dump(mode="json"),
        "catalog": catalog.model_dump(mode="json"),
        "service_names": [item.name for item in catalog.services],
        "professional_names": [item.name for item in catalog.professionals],
    }


def understand_input(state: dict, turn_input: TurnInput, interpreter, transcribe) -> dict:
    """Interpret one complete message so routing can account for late corrections.

    Keep one history slot for the current message and redact it before it
    reaches the interpreter or checkpoint.
    """
    catalog_context = {
        "service_names": state["service_names"],
        "professional_names": state["professional_names"],
        "today": state["today"],
    }
    if turn_input.recording is None:
        message = turn_input.text
        if message is None:
            raise ValueError("Missing turn input")
    else:
        if transcribe is None:
            raise ProviderError("transcription_unavailable")
        message = transcribe(turn_input.recording, catalog_context)
        if not message or not message.strip():
            raise ProviderError("empty_audio_transcript")

    message = sanitize_message(message, turn_input.display_name)
    has_choices = bool(
        state.get("offered_slots")
        or WorkflowDraft.model_validate(state.get("draft") or {}).candidate_booking_ids
    )
    interpretation = interpreter.interpret(
        message,
        {
            **catalog_context,
            "has_slots": has_choices,
            "history": (state.get("messages") or [])[-(HISTORY_LIMIT - 1):],
        },
    )
    safe = {
        key: sanitize_message(value, turn_input.display_name) if isinstance(value, str) else value
        for key, value in interpretation.model_dump(mode="json").items()
    }
    debug_event(
        "workflow.message.interpreted", has_service=bool(interpretation.service_name),
        has_date=bool(interpretation.local_date), has_time=bool(interpretation.local_time),
        has_numbered_choice=interpretation.slot_index is not None,
    )
    return {
        "message": message,
        "messages": [{"role": "user", "content": message}],
        "interpretation": safe,
    }


def merge_draft(state: dict) -> dict:
    """Combine this turn's facts with the saved draft before choosing an action.

    Resolve catalog names to trusted IDs and track missing details so later
    steps can ask for clarification or search using the accumulated context.
    """
    draft = WorkflowDraft.model_validate(state.get("draft") or {})
    interpretation = SchedulingInterpretation.model_validate(state["interpretation"])
    catalog = CatalogResponse.model_validate(state["catalog"])
    interpretation = _merge_service(draft, interpretation, catalog)
    _merge_professional(draft, interpretation, catalog)
    _merge_date_and_time(draft, interpretation)
    draft.missing = (
        "booking"
        if draft.candidate_booking_ids and interpretation.slot_index
        else _missing(draft, interpretation, catalog)
    )
    debug_event(
        "workflow.draft.merged", operation=draft.operation, missing=draft.missing,
        invalid_date=draft.invalid_date,
    )
    return {
        "draft": draft.model_dump(mode="json"),
        "interpretation": interpretation.model_dump(mode="json"),
    }


def _merge_service(
    draft: WorkflowDraft,
    interpretation: SchedulingInterpretation,
    catalog: CatalogResponse,
) -> SchedulingInterpretation:
    """Use a catalog service ID and canonical name when the requested service matches.

    Select the only available service when the customer has not named one.
    """
    if interpretation.service_name:
        service = _choose_service(catalog, interpretation.service_name)
        if service is not None:
            draft.service_id = service.id
            draft.service_name = service.name
            interpretation = interpretation.model_copy(
                update={"service_name": service.name}
            )
    elif draft.service_id is None and len(catalog.services) == 1:
        service = catalog.services[0]
        draft.service_id = service.id
        draft.service_name = service.name
    return interpretation


def _merge_professional(
    draft: WorkflowDraft,
    interpretation: SchedulingInterpretation,
    catalog: CatalogResponse,
) -> None:
    """Store a matching professional's ID so availability uses a catalog entry."""
    if interpretation.professional_name:
        professional = _choose_professional(catalog, interpretation.professional_name)
        if professional is not None:
            draft.professional_id = professional.id


def _merge_date_and_time(
    draft: WorkflowDraft, interpretation: SchedulingInterpretation
) -> None:
    """Keep valid timing preferences and clear an invalid date before clarification."""
    parsed_date = _parse_date(interpretation.local_date)
    draft.invalid_date = bool(interpretation.local_date and parsed_date is None)
    if interpretation.local_date and parsed_date is None:
        draft.local_date = None
    elif parsed_date:
        draft.local_date = parsed_date
    parsed_time = _parse_time(interpretation.local_time)
    if parsed_time:
        draft.earliest_local_time = parsed_time


def _missing(
    draft: WorkflowDraft,
    interpretation: SchedulingInterpretation,
    catalog: CatalogResponse,
) -> str:
    """Identify the next detail needed to make the draft usable for scheduling."""
    if (
        interpretation.service_name
        and _choose_service(catalog, interpretation.service_name) is None
        and draft.service_id is None
    ):
        return "service"
    if (
        interpretation.professional_name
        and _choose_professional(catalog, interpretation.professional_name) is None
    ):
        return "professional"
    if draft.invalid_date:
        return "date"
    if not draft.service_id:
        return "service"
    if not draft.local_date:
        return "date"
    return "none"


def route_turn(state: dict, decision_engine, threshold: float) -> dict:
    """Turn Laya's proposed action into a route allowed by the scheduling rules."""
    try:
        draft = WorkflowDraft.model_validate(state.get("draft") or {})
        interpretation = SchedulingInterpretation.model_validate(state["interpretation"])
    except (KeyError, ValidationError) as exc:
        debug_event("workflow.route.failed", stage="read_state", error_type=type(exc).__name__)
        raise

    debug_event(
        "workflow.route.started",
        operation=draft.operation,
        missing=draft.missing,
        confidence_threshold=threshold,
        booking_option_count=len(draft.candidate_booking_ids),
        has_numbered_choice=interpretation.slot_index is not None,
    )
    try:
        decision = decision_engine.decide(interpretation.message, draft)
    except Exception as exc:
        debug_event("workflow.route.failed", stage="request_decision", error_type=type(exc).__name__)
        raise

    selected_booking_id = None
    if not decision.needs_human and decision.confidence >= threshold:
        _update_draft_operation(draft, decision.next_action)
        selected_booking_id = _booking_id_for_choice(
            draft, decision.next_action, interpretation.slot_index
        )
        if selected_booking_id is not None:
            draft.active_booking_id = selected_booking_id
            draft.candidate_booking_ids = []
            interpretation = interpretation.model_copy(update={"slot_index": None})
    else:
        debug_event(
            "workflow.route.draft_update_skipped",
            action=decision.next_action,
            reason="needs_human" if decision.needs_human else "below_confidence_threshold",
            confidence=decision.confidence,
            confidence_threshold=threshold,
        )

    if selected_booking_id:
        route = "prepare_action"
        debug_event(
            "workflow.route.rule_applied",
            action=decision.next_action,
            route=route,
            reason="existing_booking_selected",
        )
    else:
        route = _choose_next_node(decision, draft, state, threshold)

    debug_event(
        "workflow.route.selected",
        action=decision.next_action,
        confidence=decision.confidence,
        needs_human=decision.needs_human,
        route=route,
        operation=draft.operation,
        missing=draft.missing,
    )
    return {
        "interpretation": interpretation.model_dump(mode="json"),
        "decision": decision.model_dump(mode="json"),
        "route": route,
        "draft": draft.model_dump(mode="json"),
    }


def _update_draft_operation(draft: WorkflowDraft, action: NextAction) -> None:
    """Record which booking operation the customer is working toward.

    Laya proposes the next action for one message. The draft keeps the
    broader goal across messages so a reply like "2" can finish moving an
    existing appointment. When the customer starts a new availability
    search, clear the old booking references so the next numbered reply
    can refer to the new time options.
    """
    previous_operation = draft.operation
    previous_booking_option_count = len(draft.candidate_booking_ids)
    had_active_booking = bool(draft.active_booking_id)
    operation = _EXISTING_BOOKING_OPERATIONS.get(action)
    # An explicit confirm, cancel, or reschedule action sets the goal for later replies.
    if operation is not None:
        draft.operation = operation
        reason = "explicit_existing_booking_action"
    # Searching for or choosing a new time can be part of the same reschedule.
    elif draft.operation == "reschedule":
        reason = "reschedule_in_progress"
    # Clear both booking references so the new search starts with fresh choices.
    elif action == "CHECK_AVAILABILITY":
        draft.operation = "create"
        draft.candidate_booking_ids = []
        draft.active_booking_id = None
        reason = "new_availability_search"
    # With no booking list to resolve, a numbered choice can select a new time.
    elif action == "BOOK" and not draft.candidate_booking_ids:
        draft.operation = "create"
        reason = "time_choice_for_new_booking"
    elif action == "BOOK":
        reason = "existing_booking_choice_pending"
    else:
        reason = "action_does_not_change_operation"

    debug_event(
        "workflow.draft.operation",
        action=action,
        reason=reason,
        previous_operation=previous_operation,
        operation=draft.operation,
        previous_booking_option_count=previous_booking_option_count,
        booking_option_count=len(draft.candidate_booking_ids),
        had_active_booking=had_active_booking,
        has_active_booking=bool(draft.active_booking_id),
    )


def _booking_id_for_choice(
    draft: WorkflowDraft, action: NextAction, choice: int | None
) -> str | None:
    """Resolve a numbered reply against the customer's saved booking options.

    The same number field can refer to an available time. Use it here only
    when the conversation is waiting for the customer to choose a booking.
    """
    booking_id = None
    option_count = len(draft.candidate_booking_ids)
    if action not in _EXISTING_BOOKING_OPERATIONS:
        reason = "action_does_not_target_existing_booking"
    elif draft.missing != "booking":
        reason = "draft_is_not_waiting_for_booking_choice"
    elif choice is None:
        reason = "no_numbered_choice"
    elif not 1 <= choice <= option_count:
        reason = "booking_choice_out_of_range"
    else:
        booking_id = draft.candidate_booking_ids[choice - 1]
        reason = "booking_choice_matched"

    debug_event(
        "workflow.route.booking_choice",
        action=action,
        missing=draft.missing,
        option_number=choice,
        booking_option_count=option_count,
        reason=reason,
    )
    return booking_id


def _choose_next_node(
    decision: DecisionOutput,
    draft: WorkflowDraft,
    state: dict,
    threshold: float,
) -> str:
    """Check the draft and offered slots before selecting the next graph node."""
    action = decision.next_action
    selected_slot_id = state.get("selected_slot_id")
    if selected_slot_id and not any(
        slot["slot_id"] == selected_slot_id for slot in state.get("offered_slots", [])
    ):
        route, reason = "select", "selected_slot_not_in_current_offer"
    elif decision.needs_human:
        route, reason = "escalate", "needs_human"
    elif decision.confidence < threshold:
        route, reason = "escalate", "below_confidence_threshold"
    elif action == "CLARIFY":
        route, reason = "clarify", "router_requested_clarification"
    elif action == "LOOKUP_BOOKING":
        route, reason = "lookup", "router_requested_booking_lookup"
    elif action == "FAQ":
        route, reason = "help", "router_requested_faq"
    elif action == "ESCALATE":
        route, reason = "escalate", "router_requested_escalation"
    elif action in _EXISTING_BOOKING_OPERATIONS:
        route, reason = "prepare_action", "existing_booking_action"
    elif action == "BOOK":
        if draft.candidate_booking_ids:
            route, reason = "clarify", "existing_booking_choice_pending"
        else:
            route, reason = "select", "choose_from_offered_times"
    elif action == "CHECK_AVAILABILITY":
        if (
            draft.missing in {"service", "date", "professional"}
            or not draft.service_id
            or not draft.local_date
        ):
            route, reason = "clarify", "availability_details_missing"
        else:
            route, reason = "search", "availability_details_ready"
    else:
        route, reason = "clarify", "unrecognized_action"

    debug_event(
        "workflow.route.rule_applied",
        action=action,
        route=route,
        reason=reason,
        operation=draft.operation,
        missing=draft.missing,
        has_service=bool(draft.service_id),
        has_date=bool(draft.local_date),
        confidence=decision.confidence,
        confidence_threshold=threshold,
    )
    return route


def clarify(state: dict, actions: SchedulingActions) -> dict:
    draft = WorkflowDraft.model_validate(state.get("draft") or {})
    debug_event("workflow.clarification.requested", missing=draft.missing, operation=draft.operation)
    interpretation = SchedulingInterpretation.model_validate(state["interpretation"])
    catalog = CatalogResponse.model_validate(state["catalog"])
    if state.get("decision", {}).get("next_action") == "CLARIFY":
        return _result(
            "Could you clarify your request? Tell me what you'd like to do and which appointment or time you mean.",
            "clarify",
            route="persist",
            draft=draft.model_dump(mode="json"),
        )
    if draft.candidate_booking_ids:
        return _result(
            "Choose one of the appointments I just listed, or tell me what you'd like to do instead.",
            "clarify_booking",
            route="persist",
            draft=draft.model_dump(mode="json"),
        )
    if draft.operation in {"confirm", "cancel", "reschedule"} and not state.get(
        "customer_id"
    ):
        return _result(
            "Use your Telegram account to manage your appointments.",
            "manage_booking",
            route="persist",
            draft=draft.model_dump(mode="json"),
        )
    if draft.missing == "professional" or (
        interpretation.professional_name
        and _choose_professional(catalog, interpretation.professional_name) is None
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
        draft.operation == "create" and not draft.service_id
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
        "Tell me which service you'd like and the date.",
        "clarify",
        route="persist",
        draft=draft.model_dump(mode="json"),
    )


def search(state: dict, actions: SchedulingActions) -> dict:
    draft = WorkflowDraft.model_validate(state.get("draft") or {})
    catalog = CatalogResponse.model_validate(state["catalog"])
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
    debug_event("workflow.slots.offered", count=len(slots), operation=draft.operation)
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
    draft = WorkflowDraft.model_validate(state.get("draft") or {})
    interpretation = SchedulingInterpretation.model_validate(state["interpretation"])
    slots = _offered_slots(state)
    selected_slot_id = state.get("selected_slot_id")
    chosen = _chosen_slot(slots, selected_slot_id, interpretation.slot_index)
    debug_event(
        "workflow.slot.matched", offered=len(slots), matched=chosen is not None,
        option_number=interpretation.slot_index if chosen and not selected_slot_id else None,
    )
    if chosen is None:
        return _result(
            "Choose one of the times I just offered.",
            "clarify",
            route="persist",
            draft=draft.model_dump(mode="json"),
        )
    draft.pending_slot_id = chosen.slot_id
    if draft.operation == "reschedule":
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
    draft = WorkflowDraft.model_validate(state.get("draft") or {})
    debug_event("workflow.action.preparing", operation=draft.operation)
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
    draft = WorkflowDraft.model_validate(state.get("draft") or {})
    debug_event("workflow.action.executing", operation=draft.operation)
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


def help_reply(state: dict) -> dict:
    debug_event("workflow.help.requested")
    """Answer scheduling questions from the catalog already loaded for this turn."""
    catalog = CatalogResponse.model_validate(state["catalog"])
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
    debug_event("workflow.bookings.looked_up", count=len(refs))
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
    draft = WorkflowDraft.model_validate(state.get("draft") or {})
    draft.escalated = True
    debug_event("workflow.escalated")
    actions.record_escalation(state["conversation_id"])
    return _result(
        "I can't help with that request. I can help you find a time, book, confirm, cancel, or reschedule.",
        "escalate",
        route="persist",
        draft=draft.model_dump(mode="json"),
    )


def persist(state: dict) -> dict:
    """Finalize the reply and draft so the checkpointer can carry context forward."""
    draft = WorkflowDraft.model_validate(state.get("draft") or {})
    result = dict(
        state.get("result")
        or _result("Tell me which service you'd like and the date.", "clarify")[
            "result"
        ]
    )
    update = {"result": result, "draft": draft.model_dump(mode="json")}
    if result.get("text"):
        update["messages"] = [{"role": "assistant", "content": result["text"]}]
    debug_event("workflow.reply.prepared", intent=result["intent"], missing=draft.missing)
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

from datetime import date

from app.llm.schemas import SchedulingExtraction
from tests.conftest import sample_slot


def test_invalid_request_uses_stable_error_shape(client):
    response = client.post("/chat", json={"message": "hello"})

    assert response.status_code == 422
    assert response.json() == {
        "detail": {"code": "invalid_request", "message": "The request did not match the API contract."}
    }


def test_openapi_contains_no_clinic_identity_fields(client):
    schema_text = str(client.get("/openapi.json").json())

    assert "patient_id" not in schema_text
    assert "date_of_birth" not in schema_text


def test_unknown_session_returns_domain_error(client):
    response = client.post("/chat", json={"session_id": "missing", "message": "hello"})

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "session_not_found"


def test_chat_redacts_token_like_text_before_interpretation(client, api_runtime):
    token = "bk1.123e4567-e89b-12d3-a456-426614174000.1." + "a" * 64

    response = client.post("/chat", json={"session_id": "session-1", "message": f"hello {token}"})

    assert response.status_code == 200
    assert api_runtime.workflow.messages == [f"hello [redacted-token]"]
    assert token not in response.text


def test_separate_management_token_never_reaches_workflow_logs_or_traces(client, api_runtime, caplog):
    import logging

    token = "bk1.123e4567-e89b-12d3-a456-426614174000.1." + "c" * 64
    trace_inputs = []

    class Trace:
        def event(self, *, input, **_kwargs):
            trace_inputs.append(input)

    class Tracer:
        def trace(self, **_kwargs):
            return Trace()

    api_runtime.workflow.extraction = SchedulingExtraction(intent="cancel")
    api_runtime.tracer = Tracer()
    with caplog.at_level(logging.INFO, logger="test-scheduling"):
        response = client.post("/chat", json={
            "session_id": "session-1",
            "message": "cancel my booking",
            "manage_token": token,
        })

    assert response.status_code == 200
    assert api_runtime.workflow.messages == ["cancel my booking"]
    assert api_runtime.bookings.supplied_tokens == [token, token]
    assert token not in caplog.text
    assert token not in repr(trace_inputs)


def test_search_turn_stores_only_the_slots_it_offered(client, api_runtime):
    api_runtime.workflow.extraction = SchedulingExtraction(
        intent="search", service_name="Massage", local_date=date(2099, 1, 5).isoformat()
    )
    api_runtime.availability.slots = [sample_slot()]

    response = client.post("/chat", json={"session_id": "session-1", "message": "book Massage on 2099-01-05"})

    assert response.status_code == 200
    assert response.json()["intent"] == "search"
    assert response.json()["slots"][0]["slot_id"] == "slot-1"
    assert api_runtime.session_slots["session-1"][0].slot_id == "slot-1"
    assert api_runtime.availability.requests[0].service_id == "massage"


def test_unoffered_slot_id_cannot_create_a_booking(client, api_runtime):
    api_runtime.workflow.extraction = SchedulingExtraction(intent="unknown")
    api_runtime.session_slots["session-1"] = [sample_slot()]

    response = client.post("/chat", json={
        "session_id": "session-1",
        "message": "choose this time",
        "selected_slot_id": "slot-not-offered",
        "customer": {"full_name": "Ada Lovelace", "phone": "+5511999999999"},
    })

    assert response.status_code == 200
    assert api_runtime.bookings.created == []


def test_offered_slot_and_customer_details_create_booking(client, api_runtime):
    api_runtime.workflow.extraction = SchedulingExtraction(intent="unknown")
    api_runtime.session_slots["session-1"] = [sample_slot()]

    response = client.post("/chat", json={
        "session_id": "session-1",
        "message": "choose this time",
        "selected_slot_id": "slot-1",
        "customer": {"full_name": "Ada Lovelace", "phone": "+5511999999999"},
    })

    assert response.status_code == 200
    assert response.json()["intent"] == "create"
    assert response.json()["manage_token"] == "demo-token"
    assert len(api_runtime.bookings.created) == 1
    assert api_runtime.bookings.created[0][0].slot_id == "slot-1"


def test_management_intent_without_token_does_not_call_booking_service(client, api_runtime):
    api_runtime.workflow.extraction = SchedulingExtraction(intent="cancel")

    response = client.post("/chat", json={"session_id": "session-1", "message": "cancel my booking"})

    assert response.status_code == 200
    assert response.json()["intent"] == "manage_booking"
    assert api_runtime.bookings.created == []

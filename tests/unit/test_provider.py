from app.llm.provider import DeterministicInterpreter, FallbackInterpreter


def test_deterministic_interpreter_extracts_name_and_phone():
    interpreter = DeterministicInterpreter()

    result = interpreter.interpret(
        "My name is Ada Lovelace, my phone is +55 (11) 99999-9999",
        {
            "today": "2026-10-19",
            "service_names": ["Massage"],
            "professional_names": ["Alex"],
            "has_slots": False,
        },
    )

    assert result.intent == "unknown"
    assert result.service_name is None
    assert result.local_date is None
    assert result.local_time is None
    assert result.customer_name == "Ada Lovelace"
    assert result.phone == "+5511999999999"


def test_deterministic_interpreter_extracts_service_date_and_time():
    result = DeterministicInterpreter().interpret(
        "Book Massage tomorrow at 10:30",
        {"today": "2026-10-19", "service_names": ["Massage"]},
    )

    assert result.intent == "search"
    assert result.service_name == "Massage"
    assert result.local_date == "2026-10-20"
    assert result.local_time == "10:30"


def test_deterministic_interpreter_resolves_slot_ordinal_only_with_offers():
    interpreter = DeterministicInterpreter()

    result = interpreter.interpret("I'd like the second one", {"has_slots": True})

    assert result.intent == "select_slot"
    assert result.slot_index == 2


def test_fallback_interpreter_uses_deterministic_provider_after_failure():
    class BrokenInterpreter:
        def interpret(self, _message, _context):
            raise RuntimeError("provider unavailable")

    result = FallbackInterpreter(BrokenInterpreter(), DeterministicInterpreter()).interpret(
        "cancel my booking", {}
    )

    assert result.intent == "cancel"

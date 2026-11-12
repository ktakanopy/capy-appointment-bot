from app.llm.provider import DeterministicInterpreter, build_decision, build_interpreter


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


def test_provider_selection_never_silently_substitutes():
    import pytest

    from app.graph.laya import LayaDecision
    from app.llm.gemma import GemmaUnderstanding

    assert isinstance(build_interpreter("gemma"), GemmaUnderstanding)
    assert isinstance(build_decision("laya"), LayaDecision)
    with pytest.raises(ValueError):
        build_interpreter("typo")
    with pytest.raises(ValueError):
        build_decision("typo")

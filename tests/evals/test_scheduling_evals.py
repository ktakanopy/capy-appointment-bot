from app.evals.judge import evaluate_extraction
from app.evals.models import EvaluationScenario
from app.llm.provider import DeterministicInterpreter


def test_evaluation_compares_structured_extraction_fields():
    scenario = EvaluationScenario(
        scenario_id="book-date",
        title="Find a service for tomorrow",
        utterance="Book Massage tomorrow",
        context={"today": "2026-10-19", "service_names": ["Massage"]},
        expected={"intent": "search", "service_name": "Massage", "local_date": "2026-10-20"},
        category="intent",
    )
    observed = DeterministicInterpreter().interpret(scenario.utterance, scenario.context)

    assert evaluate_extraction(scenario, observed) == []


def test_evaluation_reports_each_structured_mismatch():
    scenario = EvaluationScenario(
        scenario_id="book-date",
        title="Find a service for tomorrow",
        utterance="Book Massage tomorrow",
        context={"today": "2026-10-19", "service_names": ["Massage"]},
        expected={"intent": "search", "local_date": "2026-10-21"},
        category="intent",
    )
    observed = DeterministicInterpreter().interpret(scenario.utterance, scenario.context)

    assert evaluate_extraction(scenario, observed) == ["local_date: expected '2026-10-21', observed '2026-10-20'"]

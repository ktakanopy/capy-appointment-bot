from __future__ import annotations

from app.evals.models import EvaluationScenario
from app.llm.schemas import SchedulingExtraction


def evaluate_extraction(scenario: EvaluationScenario, extraction: SchedulingExtraction) -> list[str]:
    observed = extraction.model_dump(mode="json")
    return [
        f"{field}: expected {expected!r}, observed {observed.get(field)!r}"
        for field, expected in scenario.expected.items()
        if observed.get(field) != expected
    ]

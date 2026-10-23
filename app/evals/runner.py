from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.config import load_settings
from app.evals.judge import evaluate_extraction
from app.evals.models import EvaluationResult
from app.evals.scenarios.core_scenarios import CORE_SCENARIOS
from app.llm.provider import build_interpreter


def run_scenarios(scenarios=None) -> list[EvaluationResult]:
    settings = load_settings()
    interpreter = build_interpreter(settings.llm_provider, settings.openai_api_key, settings.openai_model)
    results: list[EvaluationResult] = []
    for scenario in scenarios or CORE_SCENARIOS:
        try:
            extraction = interpreter.interpret(scenario.utterance, scenario.context)
            mismatches = evaluate_extraction(scenario, extraction)
            results.append(EvaluationResult(
                scenario_id=scenario.scenario_id,
                title=scenario.title,
                status="pass" if not mismatches else "fail",
                expected=scenario.expected,
                observed=extraction.model_dump(mode="json"),
                mismatches=mismatches,
            ))
        except Exception as exc:
            results.append(EvaluationResult(
                scenario_id=scenario.scenario_id,
                title=scenario.title,
                status="error",
                expected=scenario.expected,
                observed={"error_type": type(exc).__name__},
                mismatches=[str(exc)],
            ))
    return results


def main() -> None:
    results = run_scenarios()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(".eval_runs")
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / f"scheduling-{run_id}.json"
    output_path.write_text(json.dumps([item.model_dump(mode="json") for item in results], indent=2) + "\n")
    for item in results:
        print(f"{item.status.upper():5} {item.scenario_id}: {item.title}")
        for mismatch in item.mismatches:
            print(f"  - {mismatch}")
    print(f"Saved {len(results)} scenario results to {output_path}")
    from app.evals.routing import ROUTE_SCENARIOS, evaluate_route

    for scenario_id, transcript, intent, expected in ROUTE_SCENARIOS:
        evaluate_route(intent, transcript, expected)
        print(f"PASS  {scenario_id}: route {expected}")


if __name__ == "__main__":
    main()

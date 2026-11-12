import json

from app.evals.models import EvaluationScenario
from app.evals.runner import load_dataset, main, run_scenarios


def test_failed_extraction_makes_cli_exit_nonzero(tmp_path):
    case = EvaluationScenario(
        scenario_id="wrong",
        title="Wrong label",
        utterance="cancel",
        expected={"intent": "confirm"},
    )
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(case.model_dump_json() + "\n")
    output = tmp_path / "results.json"
    assert (
        main(
            [
                "--dataset",
                str(dataset),
                "--understanding",
                "deterministic",
                "--routing",
                "deterministic",
                "--layer",
                "extraction",
                "--output",
                str(output),
            ]
        )
        == 1
    )
    receipt = json.loads(output.read_text())
    assert receipt["results"][0]["status"] == "fail"
    assert receipt["dataset_sha256"]


def test_live_provider_failure_is_reported_as_error_without_fallback(monkeypatch):
    from app.llm.gemma import ProviderError

    def fail(*args):
        raise ProviderError("unavailable")

    monkeypatch.setattr("app.llm.gemma.GemmaUnderstanding.interpret", fail)
    results = run_scenarios(
        [EvaluationScenario(scenario_id="outage", title="Outage", utterance="cancel")],
        understanding="gemma",
        routing="deterministic",
    )
    assert results[0].status == "error"
    assert results[0].observed["fallback_used"] is False


def test_smoke_task_dataset_passes_and_languages_are_explicit():
    cases = load_dataset("evals/data/smoke-text.jsonl")
    assert {c.language for c in cases} == {"en", "pt"}
    assert all(r.status == "pass" for r in run_scenarios(cases))


def test_duplicate_ids_and_split_leakage_are_rejected(tmp_path):
    import pytest

    case = EvaluationScenario(scenario_id="same", title="Same", group_id="pair")
    path = tmp_path / "cases.jsonl"
    path.write_text(case.model_dump_json() + "\n" + case.model_dump_json() + "\n")
    with pytest.raises(ValueError):
        load_dataset(path)
    path.write_text(
        case.model_dump_json()
        + "\n"
        + case.model_copy(
            update={"scenario_id": "different", "split": "train"}
        ).model_dump_json()
        + "\n"
    )
    with pytest.raises(ValueError):
        load_dataset(path)

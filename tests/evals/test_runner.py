import json

from app.evals.models import EvaluationScenario, EvaluationTurn
from app.evals.runner import load_dataset, main, run_scenarios


def test_failed_extraction_makes_cli_exit_nonzero(tmp_path):
    case = EvaluationScenario(
        scenario_id="wrong",
        title="Wrong label",
        utterance="cancel",
        expected={"service_name": "Massage"},
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


def test_fixed_input_routing_uses_only_labelled_meaning_without_gemma_or_audio(
    monkeypatch,
):
    def must_not_run(*args, **kwargs):
        raise AssertionError("Fixed-input routing must not invoke understanding")

    monkeypatch.setattr("app.llm.gemma.GemmaUnderstanding.interpret", must_not_run)
    monkeypatch.setattr(
        "app.llm.gemma.GemmaUnderstanding.transcribe_recording", must_not_run
    )
    case = EvaluationScenario(
        scenario_id="labelled-audio",
        title="Route the gold interpretation of an audio case",
        turns=[
            EvaluationTurn(
                audio_path="not-read.wav",
                interpreted_message="Cancel my booking.",
                expected_action="CANCEL",
            )
        ],
    )
    result = run_scenarios(
        [case], understanding="gemma", routing="deterministic", layer="routing"
    )[0]
    assert result.status == "pass"
    assert (
        result.observed["turns"][0]["interpretation"]["message"] == "Cancel my booking."
    )


def test_fixed_input_routing_requires_a_labelled_interpreted_message():
    case = EvaluationScenario(
        scenario_id="missing-meaning",
        title="Do not substitute original text for missing interpretation",
        turns=[EvaluationTurn(utterance="cancel", expected_action="CANCEL")],
    )
    result = run_scenarios([case], layer="routing")[0]
    assert result.status == "error"
    assert result.observed["fallback_used"] is False

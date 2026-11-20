import hashlib
from pathlib import Path

import pytest

from app.evals.scenarios.audio import load_audio_manifest, prepare_audio, resolve_audio
from app.evals.scenarios.render import render_markdown
from app.evals.scenarios.report import render_html
from app.evals.scenarios.runner import run_attempts
from app.evals.scenarios.spec import Scenario, ScenarioDataset, load_dataset

ROOT = Path(__file__).resolve().parents[2]


def test_yaml_golden_dataset_renders_the_executable_conversations():
    dataset = load_dataset(ROOT / "evals/scenarios/golden.yaml", repository_root=ROOT)
    rendered = render_markdown(dataset)

    assert {case.language for case in dataset.scenarios} == {"en", "pt"}
    assert "Yes, book it." in rendered
    assert "Sim, pode agendar." in rendered
    assert "assistant_example" not in rendered
    assert "reference only" in rendered


def test_golden_run_captures_gemma_laya_and_requires_a_separate_consent_turn():
    dataset = load_dataset(ROOT / "evals/scenarios/golden.yaml", repository_root=ROOT)
    scenarios = [
        case.model_copy(
            update={
                "example_conversation": [
                    step.model_copy(update={"assistant_example": f"REFERENCE-{step.id}-ONLY"})
                    for step in case.example_conversation
                ]
            }
        )
        for case in dataset.scenarios
    ]
    dataset = dataset.model_copy(update={"scenarios": scenarios})
    run = run_attempts(
        [dataset],
        repository_root=ROOT,
        understanding="deterministic",
        routing="deterministic",
        judge_provider="off",
        judge_model=None,
        judge_base_url=None,
        judge_gate=False,
        audio_manifest_path=None,
        jobs=2,
        repeats=1,
    )

    assert [item["status"] for item in run["attempts"]] == ["passed", "passed"]
    for attempt in run["attempts"]:
        actual_history = [
            {"role": role, "content": content}
            for turn in attempt["transcript"]
            for role, content in (("user", turn["user"]), ("assistant", turn["assistant"]))
        ]
        assert attempt["turns"][0]["gemma_history"] == []
        assert attempt["turns"][1]["gemma_history"] == actual_history[:2]
        assert attempt["turns"][2]["gemma_history"] == actual_history[:4]
        assert [item["intent"] for item in attempt["turns"]][-2:] == [
            "request_booking_confirmation",
            "create",
        ]
        assert attempt["turns"][-2]["writes"]["create"] == 0
        assert attempt["turns"][-1]["writes"]["create"] == 1
        assert attempt["turns"][0]["gemma"]
        assert attempt["turns"][0]["laya"]
        examples = [
            step["assistant_example"]
            for step in attempt["case_snapshot"]["example_conversation"]
        ]
        assert all(example not in str(attempt["transcript"]) for example in examples)
        assert all(
            example not in str([turn["gemma_history"] for turn in attempt["turns"]])
            for example in examples
        )


def test_yaml_loader_rejects_duplicate_keys(tmp_path):
    source = tmp_path / "duplicate.yaml"
    source.write_text("schema_version: 1\ndataset_id: golden\ndataset_id: hidden\n")

    with pytest.raises(ValueError, match="duplicate YAML key"):
        load_dataset(source, repository_root=ROOT)


def test_recorded_audio_manifest_checksums_assets_and_reports_provenance(tmp_path):
    audio = ROOT / "evals/data/audio/en-confirm.wav"
    scenario = Scenario.model_validate(
        {
            "id": "recorded-audio",
            "spec_id": "CAPY-AUDIO-001",
            "category": "audio",
            "language": "en",
            "group_id": "recorded-audio",
            "setup": {"today": "2026-10-19"},
            "example_conversation": [
                {
                    "id": "input",
                    "user": {
                        "modality": "audio",
                        "source": "recorded",
                        "path": "evals/data/audio/en-confirm.wav",
                        "reference_transcript": "Please confirm my booking.",
                    },
                    "assertions": {"writes": {}},
                }
            ],
            "judge": {
                "prompt": "Judge the observed reply.",
                "criteria": [
                    {"id": "CAPY-AUDIO-001-REPLY", "turn_id": "input", "expected": "Clear."}
                ],
            },
            "final_assertions": {"writes": {}},
        }
    )
    dataset = ScenarioDataset(
        schema_version=1, dataset_id="audio-fixture", scenarios=[scenario]
    )
    manifest_path = tmp_path / "audio.json"
    manifest = prepare_audio(
        dataset,
        repository_root=ROOT,
        manifest_path=manifest_path,
        backend="recorded",
        voices={},
    )
    checked = load_audio_manifest(manifest_path, ROOT)
    data, identity = resolve_audio(
        "recorded-audio", "input", None, checked, ROOT
    )

    assert data == audio.read_bytes()
    assert identity["backend"] == "recorded"
    assert identity["sha256"] == hashlib.sha256(data).hexdigest()
    assert manifest["assets"]["recorded-audio:input"]["reused"] is True


def test_html_report_escapes_transcript_text_and_shows_voice_details():
    report = render_html(
        [
            {
                "run_id": "test-run",
                "created_at": "2026-11-19T00:00:00Z",
                "profiles": {"understanding": "deterministic", "routing": "deterministic"},
                "coverage": {},
                "attempts": [
                    {
                        "attempt_id": "attempt-1",
                        "dataset_id": "golden",
                        "case_id": "html-escape",
                        "spec_id": "CAPY-HTML-001",
                        "category": "privacy",
                        "language": "en",
                        "status": "failed",
                        "repetition": 1,
                        "transcript": [],
                        "turns": [
                            {
                                "turn_id": "unsafe",
                                "modality": "audio",
                                "input": "<script>alert(1)</script>",
                                "reply": "No booking was made.",
                                "gemma": {},
                                "laya": {},
                                "writes": {},
                            }
                        ],
                        "voice_assets": [
                            {
                                "backend": "local-qwen3",
                                "model": "qwen3-tts-1.7b-voice-design-8bit",
                                "voice": "storyteller",
                                "sha256": "abc123",
                            }
                        ],
                        "mismatches": ["expected no script execution"],
                        "timings_ms": {"elapsed": 12},
                        "providers": {"understanding": "deterministic", "routing": "deterministic"},
                    }
                ],
            }
        ]
    )

    assert "&lt;script&gt;" in report
    assert "<script>alert(1)</script>" not in report
    assert "local-qwen3" in report
    assert "storyteller" in report

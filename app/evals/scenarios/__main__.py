from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.evals.scenarios.audio import prepare_audio
from app.evals.scenarios.render import render_markdown
from app.evals.scenarios.report import render_html
from app.evals.scenarios.runner import make_receipt, run_attempts
from app.evals.scenarios.spec import (
    Scenario,
    ScenarioDataset,
    load_dataset,
    validate_dataset_groups,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_GOLDEN = REPOSITORY_ROOT / "evals" / "scenarios" / "golden.yaml"
RUN_DIRECTORY = REPOSITORY_ROOT / ".eval_runs" / "scenarios"


def _parser() -> argparse.ArgumentParser:
    """Declare separate YAML commands so rendering, synthesis, runs, replay and reports are explicit."""
    parser = argparse.ArgumentParser(
        description="Run CAPY YAML conversation evaluations and render reports."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    render = commands.add_parser("render-spec", help="render YAML behaviors as Markdown")
    render.add_argument("--dataset", type=Path, required=True)
    render.add_argument("--output", type=Path)

    audio = commands.add_parser("prepare-audio", help="prepare and record audio fixtures")
    audio.add_argument("--dataset", type=Path, required=True)
    audio.add_argument("--backend", choices=["recorded", "local-qwen3", "openrouter"], default="recorded")
    audio.add_argument("--model")
    audio.add_argument("--voice", action="append", default=[], metavar="LANG=VOICE")
    audio.add_argument(
        "--local-script",
        type=Path,
        default=Path.home() / "projects" / "voice-project" / "generate_voice.py",
    )
    audio.add_argument("--local-python", default=sys.executable)
    audio.add_argument(
        "--manifest",
        type=Path,
        default=Path(".eval_runs/scenarios/audio-manifest.json"),
    )
    audio.add_argument("--allow-remote", action="store_true")

    run = commands.add_parser("run", help="run dataset conversations concurrently")
    run.add_argument("--dataset", type=Path, action="append", default=[])
    run.add_argument("--case", action="append", default=[])
    run.add_argument("--category", action="append", default=[])
    run.add_argument("--profile", choices=["offline", "live"], default="offline")
    run.add_argument("--understanding", choices=["deterministic", "gemma"])
    run.add_argument("--routing", choices=["deterministic", "laya"])
    run.add_argument("--judge-provider", choices=["off", "local", "openrouter"], default="off")
    run.add_argument("--judge-model")
    run.add_argument("--judge-base-url")
    run.add_argument("--judge-gate", action="store_true")
    run.add_argument("--allow-remote", action="store_true")
    run.add_argument("--audio-manifest", type=Path)
    run.add_argument("--jobs", type=int)
    run.add_argument("--repeat", type=int, default=3)

    replay = commands.add_parser("replay", help="rerun exact attempts with fresh state")
    replay.add_argument("--from", dest="receipt", type=Path, required=True)
    replay.add_argument("--attempts", default="unsuccessful")
    replay.add_argument("--understanding", choices=["deterministic", "gemma"])
    replay.add_argument("--routing", choices=["deterministic", "laya"])
    replay.add_argument("--judge-provider", choices=["off", "local", "openrouter"])
    replay.add_argument("--judge-model")
    replay.add_argument("--judge-base-url")
    replay.add_argument("--judge-gate", action="store_true", default=None)
    replay.add_argument("--allow-remote", action="store_true")
    replay.add_argument("--audio-manifest", type=Path)
    replay.add_argument("--jobs", type=int, default=2)

    report = commands.add_parser("report", help="render HTML from existing JSON receipts")
    report.add_argument("receipts", type=Path, nargs="+")
    report.add_argument("--output", type=Path, required=True)
    return parser


def _load_datasets(paths: list[Path]) -> list[ScenarioDataset]:
    """Validate each selected file and ensure the invocation has no duplicate dataset IDs."""
    datasets = [load_dataset(path, repository_root=REPOSITORY_ROOT) for path in paths]
    if len({dataset.dataset_id for dataset in datasets}) != len(datasets):
        raise ValueError("dataset IDs must be unique in a run")
    validate_dataset_groups(datasets)
    return datasets


def _filter_datasets(
    datasets: list[ScenarioDataset], case_ids: list[str], categories: list[str]
) -> list[ScenarioDataset]:
    """Select requested cases before execution to keep focused runs inexpensive."""
    selected = []
    for dataset in datasets:
        scenarios = [
            case
            for case in dataset.scenarios
            if (not case_ids or case.id in case_ids)
            and (not categories or case.category in categories)
        ]
        if scenarios:
            selected.append(
                ScenarioDataset(
                    schema_version=dataset.schema_version,
                    dataset_id=dataset.dataset_id,
                    description=dataset.description,
                    scenarios=scenarios,
                )
            )
    if not selected:
        raise ValueError("no scenarios matched the selected case and category filters")
    validate_dataset_groups(selected)
    return selected


def _resolve_path(path: Path | None) -> Path | None:
    """Resolve relative CLI paths from the repository root for reproducible commands."""
    if path is None:
        return None
    return path if path.is_absolute() else (REPOSITORY_ROOT / path).resolve()


def _voice_overrides(values: list[str]) -> dict[str, str]:
    """Parse repeated language-to-voice options into validated en/pt settings."""
    voices = {}
    for value in values:
        if "=" not in value:
            raise ValueError("voice overrides use LANG=VOICE, for example en=documentary")
        language, voice = value.split("=", 1)
        if language not in {"en", "pt"} or not voice:
            raise ValueError("voice overrides use en or pt and a nonempty voice name")
        if language in voices:
            raise ValueError(f"voice for {language} was provided more than once")
        voices[language] = voice
    return voices


def _write_report(receipt: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    """Write one timestamped JSON receipt and its matching self-contained HTML report."""
    output_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = output_dir / f"run-{receipt['run_id']}.json"
    report_path = output_dir / f"report-{receipt['run_id']}.html"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report_path.write_text(render_html([receipt]), encoding="utf-8")
    return receipt_path, report_path


def _select_replay_attempts(receipt: dict[str, Any], selection: str) -> list[dict[str, Any]]:
    """Choose exact attempts by status or ID so replay can target a specific failure."""
    attempts = receipt.get("attempts", [])
    if selection == "all":
        selected = attempts
    elif selection == "failed":
        selected = [item for item in attempts if item.get("status") == "failed"]
    elif selection == "error":
        selected = [item for item in attempts if item.get("status") == "error"]
    elif selection == "unsuccessful":
        selected = [item for item in attempts if item.get("status") in {"failed", "error"}]
    else:
        requested = {value.strip() for value in selection.split(",") if value.strip()}
        selected = [item for item in attempts if item.get("attempt_id") in requested]
        found = {item.get("attempt_id") for item in selected}
        missing = requested - found
        if missing:
            raise ValueError(f"attempt IDs were not found: {', '.join(sorted(missing))}")
    if not selected:
        raise ValueError("attempt selection matched no cases")
    return selected


def _datasets_from_receipt(receipt: dict[str, Any], selected: list[dict[str, Any]]) -> list[ScenarioDataset]:
    """Rebuild validated cases from receipt snapshots so replay survives source-file edits."""
    scenarios_by_dataset: dict[str, dict[str, Scenario]] = {}
    snapshots = {
        (dataset["dataset_id"], case["id"]): case
        for dataset in receipt.get("dataset_manifest", [])
        for case in dataset.get("scenarios", [])
    }
    for attempt in selected:
        key = (attempt["dataset_id"], attempt["case_id"])
        raw = snapshots.get(key) or attempt.get("case_snapshot")
        if not raw:
            raise ValueError(f"receipt has no scenario snapshot for {key}")
        scenario = Scenario.model_validate(raw)
        scenarios_by_dataset.setdefault(key[0], {})[key[1]] = scenario
    datasets = [
        ScenarioDataset(
            schema_version=1,
            dataset_id=dataset_id,
            description="Replayed from a scenario snapshot in a prior receipt.",
            scenarios=list(cases.values()),
        )
        for dataset_id, cases in scenarios_by_dataset.items()
    ]
    validate_dataset_groups(datasets)
    return datasets


def _handle_run(args) -> int:
    """Resolve provider profiles, run selected cases, persist the receipt, and set exit status."""
    paths = args.dataset or [DEFAULT_GOLDEN]
    loaded_datasets = _load_datasets(paths)
    datasets = _filter_datasets(loaded_datasets, args.case, args.category)
    understanding = args.understanding or ("gemma" if args.profile == "live" else "deterministic")
    routing = args.routing or ("laya" if args.profile == "live" else "deterministic")
    jobs = args.jobs if args.jobs is not None else (2 if args.profile == "live" else 4)
    if args.judge_gate and args.judge_provider == "off":
        raise ValueError("--judge-gate requires an enabled --judge-provider")
    if args.judge_provider == "openrouter" and not args.allow_remote:
        raise ValueError("OpenRouter judge requires the explicit --allow-remote flag")
    judge_model = args.judge_model or ("local-judge" if args.judge_provider == "local" else None)
    audio_manifest = _resolve_path(args.audio_manifest)
    execution = run_attempts(
        datasets,
        repository_root=REPOSITORY_ROOT,
        understanding=understanding,
        routing=routing,
        judge_provider=args.judge_provider,
        judge_model=judge_model,
        judge_base_url=args.judge_base_url,
        judge_gate=args.judge_gate,
        audio_manifest_path=audio_manifest,
        jobs=jobs,
        repeats=args.repeat,
    )
    source_paths = {
        dataset.dataset_id: path.resolve()
        for dataset, path in zip(loaded_datasets, paths)
    }
    receipt = make_receipt(
        execution,
        datasets,
        understanding=understanding,
        routing=routing,
        judge_provider=args.judge_provider,
        judge_model=judge_model,
        judge_gate=args.judge_gate,
        jobs=jobs,
        repeats=args.repeat,
        audio_manifest_path=audio_manifest,
        repository_root=REPOSITORY_ROOT,
        dataset_sources=source_paths,
    )
    receipt_path, report_path = _write_report(receipt, RUN_DIRECTORY)
    _print_summary(receipt)
    print(f"JSON: {receipt_path}")
    print(f"HTML: {report_path}")
    return _exit_status(receipt)


def _handle_replay(args) -> int:
    """Repeat selected receipt attempts from fresh state while preserving parent IDs."""
    source_path = _resolve_path(args.receipt)
    original = json.loads(source_path.read_text(encoding="utf-8"))
    selected = _select_replay_attempts(original, args.attempts)
    datasets = _datasets_from_receipt(original, selected)
    original_profiles = original.get("profiles", {})
    understanding = args.understanding or original_profiles.get("understanding", "deterministic")
    routing = args.routing or original_profiles.get("routing", "deterministic")
    judge_provider = args.judge_provider or original_profiles.get("judge_provider", "off")
    judge_model = args.judge_model or original_profiles.get("judge_model")
    if judge_provider == "local" and not judge_model:
        judge_model = "local-judge"
    judge_gate = args.judge_gate if args.judge_gate is not None else bool(original_profiles.get("judge_gate"))
    if judge_gate and judge_provider == "off":
        raise ValueError("judge replay gating requires an enabled judge provider")
    if judge_provider == "openrouter" and not args.allow_remote:
        raise ValueError("OpenRouter judge replay requires the explicit --allow-remote flag")
    audio_manifest = _resolve_path(args.audio_manifest)
    if audio_manifest is None and original_profiles.get("audio_manifest"):
        audio_manifest = _resolve_path(Path(original_profiles["audio_manifest"]))
    replay_specs = [
        (item["dataset_id"], Scenario.model_validate(item["case_snapshot"]), item.get("repetition", 1), item["attempt_id"])
        for item in selected
    ]
    execution = run_attempts(
        datasets,
        repository_root=REPOSITORY_ROOT,
        understanding=understanding,
        routing=routing,
        judge_provider=judge_provider,
        judge_model=judge_model,
        judge_base_url=args.judge_base_url or original_profiles.get("judge_base_url"),
        judge_gate=judge_gate,
        audio_manifest_path=audio_manifest,
        jobs=args.jobs,
        repeats=1,
        parent_run_id=original.get("run_id"),
        replay_specs=replay_specs,
    )
    receipt = make_receipt(
        execution,
        datasets,
        understanding=understanding,
        routing=routing,
        judge_provider=judge_provider,
        judge_model=judge_model,
        judge_gate=judge_gate,
        jobs=args.jobs,
        repeats=1,
        audio_manifest_path=audio_manifest,
        repository_root=REPOSITORY_ROOT,
        dataset_sources={},
    )
    receipt_path, report_path = _write_report(receipt, RUN_DIRECTORY)
    _print_summary(receipt)
    print(f"JSON: {receipt_path}")
    print(f"HTML: {report_path}")
    return _exit_status(receipt)


def _print_summary(receipt):
    """Print one concise outcome line per attempt before showing artifact paths."""
    for attempt in receipt["attempts"]:
        print(f"{attempt['status'].upper():6} {attempt['dataset_id']}/{attempt['case_id']} repeat={attempt['repetition']}")


def _exit_status(receipt):
    """Map attempt outcomes to CI-friendly success, assertion-failure, or error codes."""
    statuses = {item["status"] for item in receipt["attempts"]}
    return 2 if "error" in statuses else 1 if "failed" in statuses else 0


def main(argv: list[str] | None = None) -> int:
    """Dispatch the evaluation CLI and convert expected setup failures into readable errors."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "render-spec":
            dataset = load_dataset(args.dataset, repository_root=REPOSITORY_ROOT)
            rendered = render_markdown(dataset)
            if args.output:
                output = _resolve_path(args.output)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(rendered, encoding="utf-8")
                print(f"Markdown: {output}")
            else:
                print(rendered, end="")
            return 0
        if args.command == "prepare-audio":
            if args.backend == "openrouter" and not args.allow_remote:
                raise ValueError("OpenRouter speech generation requires the explicit --allow-remote flag")
            dataset = load_dataset(args.dataset, repository_root=REPOSITORY_ROOT)
            manifest_path = _resolve_path(args.manifest)
            manifest = prepare_audio(
                dataset,
                repository_root=REPOSITORY_ROOT,
                manifest_path=manifest_path,
                backend=args.backend,
                voices=_voice_overrides(args.voice),
                model=args.model,
                local_script=args.local_script,
                local_python=args.local_python,
            )
            print(f"Prepared {len(manifest['assets'])} audio assets: {manifest_path}")
            return 0
        if args.command == "run":
            return _handle_run(args)
        if args.command == "replay":
            return _handle_replay(args)
        if args.command == "report":
            receipts = [json.loads(path.read_text(encoding="utf-8")) for path in args.receipts]
            output = _resolve_path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(render_html(receipts), encoding="utf-8")
            print(f"HTML: {output}")
            return 0
    except (OSError, ValueError, KeyError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

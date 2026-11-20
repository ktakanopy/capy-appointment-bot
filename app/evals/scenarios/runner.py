from __future__ import annotations

import hashlib
import json
import statistics
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from langgraph.checkpoint.memory import InMemorySaver

from app.channels.audio_chunks import AudioChunker
from app.config import load_settings
from app.evals.fixtures import ScenarioSchedulingActions
from app.evals.scenarios.audio import load_audio_manifest, resolve_audio
from app.evals.scenarios.judge import judge_conversation
from app.evals.scenarios.spec import Scenario, ScenarioDataset
from app.graph.booking_messages import booking_details
from app.graph.builder import build_graph
from app.graph.contracts import TurnContext
from app.graph.workflow import LangGraphWorkflow
from app.llm.gemma import GemmaUnderstanding
from app.llm.provider import build_decision, build_interpreter
from app.llm.schemas import SchedulingInterpretation
from app.models import AvailabilitySlot, Booking, CatalogResponse
from app.privacy import sanitize_message

WRITE_NAMES = ("create", "confirm", "cancel", "reschedule")


class ObservedInterpreter:
    """Capture the validated Gemma object before graph merge changes its fields."""

    def __init__(self, provider):
        """Keep one provider and per-turn evidence separate for each attempt."""
        self.provider = provider
        self.output: dict[str, Any] | None = None
        self.milliseconds = 0.0
        self.history: list[dict[str, str]] = []

    def interpret(self, message: str, context: dict) -> SchedulingInterpretation:
        """Save the bounded prior history and sanitized interpretation for scoring."""
        started = perf_counter()
        self.history = [
            {
                "role": str(item.get("role", "")),
                "content": sanitize_message(str(item.get("content", ""))),
            }
            for item in context.get("history", [])
        ]
        try:
            result = self.provider.interpret(message, context)
            safe = {
                key: sanitize_message(value) if isinstance(value, str) else value
                for key, value in result.model_dump(mode="json").items()
            }
            self.output = SchedulingInterpretation.model_validate(safe).model_dump(mode="json")
            return result
        finally:
            self.milliseconds = (perf_counter() - started) * 1000


class ObservedDecision:
    """Capture Laya's typed route proposal separately from deterministic graph rules."""

    def __init__(self, provider):
        """Wrap one routing provider so its decision and latency can be reported."""
        self.provider = provider
        self.output: dict[str, Any] | None = None
        self.milliseconds = 0.0

    def decide(self, message: str, context):
        """Return the real typed proposal while recording a sanitized report copy."""
        started = perf_counter()
        try:
            result = self.provider.decide(message, context)
            safe = {
                key: sanitize_message(value) if isinstance(value, str) else value
                for key, value in result.model_dump(mode="json").items()
            }
            self.output = type(result).model_validate(safe).model_dump(mode="json")
            return result
        finally:
            self.milliseconds = (perf_counter() - started) * 1000


class ObservedTranscriber:
    """Measure transcription and support an explicit reference-transcript baseline."""

    def __init__(self, provider: GemmaUnderstanding | None):
        """Use Gemma for live transcription or the authored reference in offline runs."""
        self.provider = provider
        self.reference_transcript: str | None = None
        self.transcript: str | None = None
        self.milliseconds = 0.0
        self.chunker = AudioChunker()

    def __call__(self, recording: bytes, context: dict) -> str:
        """Transcribe one recording and measure only the transcription stage."""
        started = perf_counter()
        try:
            if self.provider is None:
                if not self.reference_transcript:
                    raise ValueError("offline audio needs a reference transcript")
                self.transcript = self.reference_transcript
            else:
                self.transcript = self.provider.transcribe_recording(
                    recording, context, chunker=self.chunker
                )
            return self.transcript
        finally:
            self.milliseconds = (perf_counter() - started) * 1000


def run_attempts(
    datasets: list[ScenarioDataset],
    *,
    repository_root: Path,
    understanding: Literal["deterministic", "gemma"],
    routing: Literal["deterministic", "laya"],
    judge_provider: Literal["off", "local", "openrouter"],
    judge_model: str | None,
    judge_base_url: str | None,
    judge_gate: bool,
    audio_manifest_path: Path | None,
    jobs: int,
    repeats: int,
    attempt_ids: dict[tuple[str, int], str] | None = None,
    parent_run_id: str | None = None,
    parent_attempt_ids: dict[tuple[str, int], str] | None = None,
    replay_specs: list[tuple[str, Scenario, int, str]] | None = None,
) -> dict[str, Any]:
    """Run independent case repetitions concurrently while preserving turn order."""
    if jobs < 1 or repeats < 1:
        raise ValueError("jobs and repeats must be positive")
    scenarios = [(dataset.dataset_id, case) for dataset in datasets for case in dataset.scenarios]
    if judge_provider == "openrouter" and any(
        case.provenance.casefold() != "synthetic" for _, case in scenarios
    ):
        raise ValueError("remote judging is restricted to synthetic scenarios")
    manifest = load_audio_manifest(audio_manifest_path, repository_root)
    settings = load_settings()
    scheduled_at = perf_counter()
    tasks = []
    specifications = (
        replay_specs
        if replay_specs is not None
        else [
            (dataset_id, scenario, repetition, "")
            for dataset_id, scenario in scenarios
            for repetition in range(1, repeats + 1)
        ]
    )
    for dataset_id, scenario, repetition, replay_parent in specifications:
        key = (scenario.id, repetition)
        attempt_id = (attempt_ids or {}).get(key) or uuid.uuid4().hex
        parent_attempt_id = replay_parent or (parent_attempt_ids or {}).get(key)
        tasks.append(
            (
                dataset_id,
                scenario,
                repetition,
                attempt_id,
                parent_attempt_id,
                scheduled_at,
            )
        )
    indexed: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="capy-eval") as executor:
        futures = {}
        for index, task in enumerate(tasks):
            future = executor.submit(
                _run_case,
                task,
                repository_root,
                manifest,
                settings,
                understanding,
                routing,
                judge_provider,
                judge_model,
                judge_base_url,
                judge_gate,
                parent_run_id,
            )
            futures[future] = (index, task)
        for future in as_completed(futures):
            index, task = futures[future]
            try:
                indexed[index] = future.result()
            except Exception as error:
                indexed[index] = _worker_setup_error(
                    task,
                    parent_run_id=parent_run_id,
                    understanding=understanding,
                    routing=routing,
                    judge_provider=judge_provider,
                    error=error,
                )
    attempts = [indexed[index] for index in range(len(tasks))]
    return {
        "attempts": attempts,
        "scheduled_attempts": len(tasks),
        "effective_jobs": min(jobs, len(tasks)),
        "dataset_ids": [dataset.dataset_id for dataset in datasets],
    }


def _worker_setup_error(
    task,
    *,
    parent_run_id: str | None,
    understanding: str,
    routing: str,
    judge_provider: str,
    error: Exception,
) -> dict[str, Any]:
    """Keep constructor failures in the receipt instead of losing a whole run."""
    dataset_id, scenario, repetition, attempt_id, parent_attempt_id, scheduled_at = task
    return {
        "attempt_id": attempt_id,
        "parent_run_id": parent_run_id,
        "parent_attempt_id": parent_attempt_id,
        "dataset_id": dataset_id,
        "case_id": scenario.id,
        "spec_id": scenario.spec_id,
        "category": scenario.category,
        "language": scenario.language,
        "split": scenario.split,
        "repetition": repetition,
        "status": "error",
        "case_snapshot": scenario.model_dump(mode="json"),
        "transcript": [],
        "turns": [],
        "writes": {name: 0 for name in WRITE_NAMES},
        "booking": None,
        "voice_assets": [],
        "judge": None,
        "error_type": type(error).__name__,
        "mismatches": [],
        "timings_ms": {
            "queue_wait": max(0.0, (perf_counter() - scheduled_at) * 1000),
            "elapsed": 0.0,
            "turns": 0,
        },
        "providers": {
            "understanding": understanding,
            "routing": routing,
            "judge": judge_provider,
        },
    }


def _run_case(
    task,
    repository_root: Path,
    audio_manifest: dict[str, Any],
    settings,
    understanding: str,
    routing: str,
    judge_provider: str,
    judge_model: str | None,
    judge_base_url: str | None,
    judge_gate: bool,
    parent_run_id: str | None,
) -> dict[str, Any]:
    """Run one isolated conversation sequentially and capture its full evidence."""
    dataset_id, scenario, repetition, attempt_id, parent_attempt_id, scheduled_at = task
    queued_at = perf_counter()
    started = queued_at
    interpreter_provider = build_interpreter(understanding, settings.gemma_base_url)
    decision_provider = build_decision(routing, settings.laya_base_url)
    interpreter = ObservedInterpreter(interpreter_provider)
    decision = ObservedDecision(decision_provider)
    gemma = GemmaUnderstanding(settings.gemma_base_url) if understanding == "gemma" else None
    transcriber = ObservedTranscriber(gemma)
    workflow = LangGraphWorkflow(
        build_graph(
            interpreter,
            decision,
            checkpointer=InMemorySaver(),
            transcribe=transcriber,
        )
    )
    context = {
        "today": scenario.setup.today,
        "service_names": [scenario.setup.service_name],
        "business_timezone": scenario.setup.business_timezone,
    }
    actions = ScenarioSchedulingActions(
        context, existing_booking=scenario.setup.existing_booking
    )
    mismatches: list[str] = []
    transcript: list[dict[str, str]] = []
    turn_evidence: list[dict[str, Any]] = []
    observed_offers: dict[str, list[AvailabilitySlot]] = {}
    selected_facts: dict[str, dict[str, Any]] = {}
    voice_assets: list[dict[str, Any]] = []
    try:
        for index, step in enumerate(scenario.example_conversation):
            before = actions.writes.copy()
            recording = None
            audio_asset = None
            if step.user.modality == "audio":
                recording, audio_asset = resolve_audio(
                    scenario.id,
                    step.id,
                    step.user.path,
                    audio_manifest,
                    repository_root,
                )
                transcriber.reference_transcript = step.user.reference_transcript
                voice_assets.append(
                    {"turn_id": step.id, **audio_asset}
                )
                user_message = step.user.reference_transcript or "[audio]"
            else:
                user_message = step.user.text or ""
            interpreter.output = None
            decision.output = None
            transcriber.transcript = None
            turn = TurnContext(
                conversation_id=attempt_id,
                business_id="demo-studio",
                message=user_message if recording is None else None,
                today=scenario.setup.today,
                customer_id="fixture-customer" if scenario.setup.verified_customer else None,
                idempotency_key=f"eval:{attempt_id}:{step.id}",
                input_modality=step.user.modality,
                language=scenario.language,
            )
            result = workflow.handle_turn(
                turn,
                actions,
                recording=recording,
            )
            state = workflow.state(attempt_id)
            actual_message = sanitize_message(str(state.get("message", user_message)))
            assistant = sanitize_message(result.text)
            transcript.append(
                {"turn_id": step.id, "user": actual_message, "assistant": assistant}
            )
            writes = {name: actions.writes.get(name, 0) - before.get(name, 0) for name in WRITE_NAMES}
            expected_gemma = step.assertions.gemma
            actual_gemma = interpreter.output or {}
            for field, expected in expected_gemma.items():
                if actual_gemma.get(field) != expected:
                    mismatches.append(
                        f"{scenario.id}/{step.id}: Gemma {field} expected {expected!r}, got {actual_gemma.get(field)!r}"
                    )
            if step.assertions.laya:
                for field, expected in step.assertions.laya.model_dump(exclude_none=True).items():
                    actual = (decision.output or {}).get(field)
                    if actual != expected:
                        mismatches.append(
                            f"{scenario.id}/{step.id}: Laya {field} expected {expected!r}, got {actual!r}"
                        )
            if step.assertions.intent and result.intent != step.assertions.intent:
                mismatches.append(
                    f"{scenario.id}/{step.id}: intent expected {step.assertions.intent}, got {result.intent}"
                )
            if (
                step.assertions.offered_slot_count is not None
                and len(result.slots) != step.assertions.offered_slot_count
            ):
                mismatches.append(
                    f"{scenario.id}/{step.id}: expected {step.assertions.offered_slot_count} offered slots, got {len(result.slots)}"
                )
            _compare_writes(
                step.assertions.writes,
                writes,
                f"{scenario.id}/{step.id}",
                mismatches,
            )
            if result.slots:
                observed_offers[step.id] = result.slots
            draft = state.get("draft") or {}
            selected_id = draft.get("confirmation_requested_for")
            if selected_id:
                selected = next(
                    (
                        AvailabilitySlot.model_validate(item)
                        for item in state.get("offered_slots", [])
                        if item.get("slot_id") == selected_id
                    ),
                    None,
                )
                if selected:
                    selected_facts[step.id] = _slot_facts(
                        selected,
                        CatalogResponse.model_validate(state["catalog"]),
                        scenario.language,
                    )
            turn_evidence.append(
                {
                    "turn_id": step.id,
                    "modality": step.user.modality,
                    "input": actual_message,
                    "reply": assistant,
                    "intent": result.intent,
                    "gemma": actual_gemma,
                    "gemma_history": list(interpreter.history),
                    "laya": decision.output,
                    "writes": writes,
                    "slots": [slot.model_dump(mode="json") for slot in result.slots],
                    "selected_offer": selected_facts.get(step.id),
                    "booking": _booking_projection(actions.last_booking),
                    "timings_ms": {
                        "gemma": interpreter.milliseconds,
                        "laya": decision.milliseconds,
                        "transcription": transcriber.milliseconds if recording else 0.0,
                    },
                    "audio": audio_asset,
                }
            )
            _compare_turn_booking(
                step.assertions.booking,
                actions.last_booking,
                observed_offers,
                scenario.id,
                step.id,
                mismatches,
            )

        _compare_writes(
            scenario.final_assertions.writes,
            actions.writes,
            f"{scenario.id}/final",
            mismatches,
        )
        _compare_turn_booking(
            scenario.final_assertions.booking,
            actions.last_booking,
            observed_offers,
            scenario.id,
            "final",
            mismatches,
        )
        existing_status = actions.bookings.get("fixture-booking")
        expected_existing_status = scenario.final_assertions.existing_booking_status
        if expected_existing_status:
            actual_existing_status = (
                existing_status.status.value if existing_status is not None else None
            )
            if actual_existing_status != expected_existing_status:
                mismatches.append(
                    f"{scenario.id}/final: existing booking expected {expected_existing_status}, got {actual_existing_status}"
                )

        judge_result = None
        if judge_provider != "off":
            if not judge_model:
                raise ValueError("--judge-model is required when a judge is enabled")
            facts_by_criterion = _judge_facts(
                scenario,
                turn_evidence,
                actions.last_booking,
                actions.catalog("demo-studio"),
            )
            judge_result = judge_conversation(
                scenario,
                transcript,
                facts_by_criterion,
                provider=judge_provider,
                model=judge_model,
                base_url=judge_base_url,
            )
            if judge_gate:
                for verdict in judge_result["verdicts"]:
                    if not verdict["passed"]:
                        mismatches.append(
                            f"{scenario.id}: judge failed {verdict['criterion_id']}"
                        )
        status = "failed" if mismatches else "passed"
        return {
            "attempt_id": attempt_id,
            "parent_run_id": parent_run_id,
            "parent_attempt_id": parent_attempt_id,
            "dataset_id": dataset_id,
            "case_id": scenario.id,
            "spec_id": scenario.spec_id,
            "category": scenario.category,
            "language": scenario.language,
            "split": scenario.split,
            "repetition": repetition,
            "status": status,
            "case_snapshot": scenario.model_dump(mode="json"),
            "transcript": transcript,
            "turns": turn_evidence,
            "writes": actions.writes,
            "booking": _booking_projection(actions.last_booking),
            "existing_booking_status": (
                existing_status.status.value if existing_status is not None else None
            ),
            "voice_assets": voice_assets,
            "judge": judge_result,
            "mismatches": mismatches,
            "timings_ms": {
                "queue_wait": (queued_at - scheduled_at) * 1000,
                "elapsed": (perf_counter() - started) * 1000,
                "turns": len(turn_evidence),
            },
            "providers": {
                "understanding": understanding,
                "routing": routing,
                "judge": judge_provider,
            },
        }
    except Exception as error:
        return {
            "attempt_id": attempt_id,
            "parent_run_id": parent_run_id,
            "parent_attempt_id": parent_attempt_id,
            "dataset_id": dataset_id,
            "case_id": scenario.id,
            "spec_id": scenario.spec_id,
            "category": scenario.category,
            "language": scenario.language,
            "split": scenario.split,
            "repetition": repetition,
            "status": "error",
            "case_snapshot": scenario.model_dump(mode="json"),
            "transcript": transcript,
            "turns": turn_evidence,
            "writes": actions.writes,
            "booking": _booking_projection(actions.last_booking),
            "voice_assets": voice_assets,
            "judge": None,
            "error_type": type(error).__name__,
            "mismatches": mismatches,
            "timings_ms": {
                "queue_wait": (queued_at - scheduled_at) * 1000,
                "elapsed": (perf_counter() - started) * 1000,
                "turns": len(turn_evidence),
            },
            "providers": {
                "understanding": understanding,
                "routing": routing,
                "judge": judge_provider,
            },
        }


def _compare_writes(expected: dict[str, int], actual: dict[str, int], label: str, mismatches: list[str]) -> None:
    """Compare every supported write count so unlisted operations default to zero."""
    for operation in WRITE_NAMES:
        wanted = expected.get(operation, 0)
        observed = actual.get(operation, 0)
        if wanted != observed:
            mismatches.append(f"{label}: {operation} writes expected {wanted}, got {observed}")


def _compare_turn_booking(expected, booking: Booking | None, offers, case_id, turn_id, mismatches) -> None:
    """Match saved booking status and slot facts to the offer named by the scenario."""
    if expected is None:
        return
    if booking is None:
        mismatches.append(f"{case_id}/{turn_id}: expected a saved booking, got none")
        return
    status = booking.status.value
    if expected.status and status != expected.status:
        mismatches.append(f"{case_id}/{turn_id}: booking status expected {expected.status}, got {status}")
    match = expected.matches_offer
    if match:
        candidates = offers.get(match.turn_id, [])
        selected = candidates[match.position - 1] if match.position <= len(candidates) else None
        if selected is None:
            mismatches.append(f"{case_id}/{turn_id}: referenced offer was not observed")
        elif (
            booking.service_id != selected.service_id
            or booking.professional_id != selected.professional_id
            or booking.starts_at != selected.starts_at
            or booking.ends_at != selected.ends_at
        ):
            mismatches.append(f"{case_id}/{turn_id}: saved booking does not match the referenced offer")


def _booking_projection(booking: Booking | None) -> dict[str, Any] | None:
    """Expose only safe booking fields needed to diagnose an evaluation result."""
    if booking is None:
        return None
    return {
        "status": booking.status.value,
        "service_id": booking.service_id,
        "professional_id": booking.professional_id,
        "starts_at": booking.starts_at.isoformat(),
        "ends_at": booking.ends_at.isoformat(),
    }


def _slot_facts(
    slot: AvailabilitySlot, catalog: CatalogResponse, language: str
) -> dict[str, str]:
    """Project an offered slot into localized appointment facts for the judge."""
    service = next(
        (item.name for item in catalog.services if item.id == slot.service_id),
        "service",
    )
    return {
        **booking_details(
            slot.starts_at,
            service,
            slot.professional_name,
            catalog.business.timezone,
            language,
        ),
        "starts_at": slot.starts_at.isoformat(),
        "service_id": slot.service_id,
        "professional_id": slot.professional_id,
    }


def _judge_facts(scenario, turn_evidence, booking, catalog):
    """Give each rubric item only the selected offer or saved booking facts it requested."""
    facts: dict[str, dict[str, Any]] = {}
    evidence_by_turn = {item["turn_id"]: item for item in turn_evidence}
    for criterion in scenario.judge.criteria:
        turn = evidence_by_turn.get(criterion.turn_id, {})
        if criterion.facts_from == "selected_offer":
            facts[criterion.id] = turn.get("selected_offer") or {}
        elif criterion.facts_from == "saved_booking":
            selected_offer = next(
                (
                    item.get("selected_offer")
                    for item in turn_evidence
                    if item.get("selected_offer")
                ),
                {},
            )
            safe_booking = _booking_projection(booking)
            saved_booking_facts = {}
            if booking is not None:
                service = next(
                    (item.name for item in catalog.services if item.id == booking.service_id),
                    "service",
                )
                professional = next(
                    (item.name for item in catalog.professionals if item.id == booking.professional_id),
                    "professional",
                )
                saved_booking_facts = {
                    **booking_details(
                        booking.starts_at,
                        service,
                        professional,
                        catalog.business.timezone,
                        scenario.language,
                    ),
                    "status": booking.status.value,
                }
            facts[criterion.id] = {
                "selected_offer": selected_offer,
                "saved_booking": {**saved_booking_facts, **(safe_booking or {})},
            }
        else:
            facts[criterion.id] = {}
    return facts


def make_receipt(
    run: dict[str, Any],
    datasets: list[ScenarioDataset],
    *,
    understanding: str,
    routing: str,
    judge_provider: str,
    judge_model: str | None,
    judge_gate: bool,
    jobs: int,
    repeats: int,
    audio_manifest_path: Path | None,
    repository_root: Path,
    dataset_sources: dict[str, Path] | None = None,
) -> dict[str, Any]:
    """Create a self-contained run receipt with the exact scenario snapshots used."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_id = f"{timestamp}-{uuid.uuid4().hex[:8]}"
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        revision = "unknown"
    working_tree = _working_tree_identity(repository_root)
    dataset_manifest = []
    for dataset in datasets:
        path = dataset_sources.get(dataset.dataset_id) if dataset_sources is not None else None
        if dataset_sources is None:
            path = repository_root / "evals" / "scenarios" / f"{dataset.dataset_id}.yaml"
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path and path.is_file() else None
        dataset_manifest.append(
            {
                "dataset_id": dataset.dataset_id,
                "sha256": digest,
                "source": (
                    str(path.resolve().relative_to(repository_root.resolve()))
                    if path and path.is_file() and path.resolve().is_relative_to(repository_root.resolve())
                    else None
                ),
                "scenarios": [case.model_dump(mode="json") for case in dataset.scenarios],
            }
        )
    return {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_revision": revision,
        "working_tree": working_tree,
        "dataset_manifest": dataset_manifest,
        "profiles": {
            "understanding": understanding,
            "routing": routing,
            "judge_provider": judge_provider,
            "judge_model": judge_model,
            "judge_gate": judge_gate,
            "audio_manifest": (
                str(audio_manifest_path.relative_to(repository_root))
                if audio_manifest_path and audio_manifest_path.is_relative_to(repository_root)
                else str(audio_manifest_path) if audio_manifest_path else None
            ),
        },
        "concurrency": {
            "scheduled_attempts": run["scheduled_attempts"],
            "effective_jobs": run["effective_jobs"],
            "requested_jobs": jobs,
            "repetitions": repeats,
        },
        "coverage": _coverage(datasets, run["attempts"]),
        "attempts": run["attempts"],
    }


def _working_tree_identity(repository_root: Path) -> dict[str, Any]:
    """Hash local changes so a receipt can distinguish runs before and after commit."""
    root = repository_root.resolve()
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.splitlines()
        tracked_changes = subprocess.run(
            ["git", "diff", "--binary", "HEAD"],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        return {"available": False}

    digest = hashlib.sha256(tracked_changes)
    included = 0
    for relative in untracked:
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(path.read_bytes())
        included += 1
    return {
        "available": True,
        "dirty": bool(status),
        "changed_path_count": len(status),
        "untracked_file_count": included,
        "sha256": digest.hexdigest(),
    }


def _coverage(datasets, attempts):
    """Separate declared behavior layers from evidence actually observed in this run."""
    declared = {}
    for dataset in datasets:
        for case in dataset.scenarios:
            declared.setdefault(case.spec_id, set()).update(case.declared_coverage)
    observed = {}
    for attempt in attempts:
        entry = observed.setdefault(
            attempt["spec_id"],
            {"deterministic": False, "model": False, "database": False},
        )
        entry["deterministic"] |= bool(attempt.get("turns"))
        entry["model"] |= (
            bool(attempt.get("turns"))
            and (
                attempt["providers"]["understanding"] == "gemma"
                or attempt["providers"]["routing"] == "laya"
            )
        )
    return {
        spec_id: {
            "declared": sorted(declared.get(spec_id, set())),
            "observed": layers,
        }
        for spec_id, layers in observed.items()
    }

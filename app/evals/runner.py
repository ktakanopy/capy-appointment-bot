"""Reproducible extraction, fixed-input routing and task evaluations."""

import argparse
import hashlib
import json
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from langgraph.checkpoint.memory import InMemorySaver

from app.channels.audio_chunks import AudioChunker
from app.config import load_settings
from app.evals.fixtures import ScenarioSchedulingActions
from app.evals.models import EvaluationResult, EvaluationScenario, EvaluationTurn
from app.graph.builder import build_graph
from app.graph.contracts import TurnContext
from app.graph.workflow import LangGraphWorkflow
from app.llm.gemma import GemmaUnderstanding
from app.llm.provider import build_decision, build_interpreter
from app.llm.schemas import SchedulingInterpretation
from app.privacy import sanitize_message


class TimedInterpreter:
    def __init__(self, provider):
        self.provider, self.milliseconds = provider, 0.0

    def interpret(self, message, context):
        start = perf_counter()
        try:
            return self.provider.interpret(message, context)
        finally:
            self.milliseconds = (perf_counter() - start) * 1000


class TimedDecision:
    def __init__(self, provider):
        self.provider, self.milliseconds = provider, 0.0

    def decide(self, message, context):
        start = perf_counter()
        try:
            return self.provider.decide(message, context)
        finally:
            self.milliseconds = (perf_counter() - start) * 1000


def load_dataset(path):
    path = Path(path)
    cases = [
        EvaluationScenario.model_validate_json(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    if not cases or len({c.scenario_id for c in cases}) != len(cases):
        raise ValueError("Dataset must be nonempty with unique case IDs")
    groups = {}
    for case in cases:
        group = case.group_id or case.scenario_id
        if group in groups and groups[group] != case.split:
            raise ValueError(f"Group {group} crosses dataset splits")
        groups[group] = case.split
        for turn in case.turns:
            if turn.audio_path:
                asset = (path.parent / turn.audio_path).resolve()
                if (
                    not asset.is_relative_to(path.parent.resolve())
                    or not asset.is_file()
                ):
                    raise ValueError(
                        f"Missing or unsafe audio asset in {case.scenario_id}"
                    )
                if not turn.reference_transcript:
                    raise ValueError("Audio cases require a reference transcript")
    return cases


def _word_errors(reference, observed):
    # Word-level edit distance; punctuation/case normalized for this small corpus.
    ref, obs = (
        re.findall(r"\w+", reference.casefold()),
        re.findall(r"\w+", observed.casefold()),
    )
    row = list(range(len(obs) + 1))
    for i, word in enumerate(ref, 1):
        new = [i]
        for j, other in enumerate(obs, 1):
            new.append(min(new[-1] + 1, row[j] + 1, row[j - 1] + (word != other)))
        row = new
    return {"word_errors": row[-1], "reference_words": len(ref)}


def run_scenarios(
    scenarios=None,
    *,
    understanding="deterministic",
    routing="deterministic",
    layer="workflow",
    dataset_dir=Path("evals/data"),
):
    settings = load_settings()
    if scenarios is None:
        scenarios = load_dataset(Path(dataset_dir) / "smoke-text.jsonl")
    return [
        _evaluate_case(case, settings, understanding, routing, layer, dataset_dir)
        for case in scenarios
    ]


def _evaluate_case(case, settings, understanding, routing, layer, dataset_dir):
    interpreter = TimedInterpreter(
        build_interpreter(understanding, settings.gemma_base_url)
    )
    decision = TimedDecision(build_decision(routing, settings.laya_base_url))
    workflow = LangGraphWorkflow(
        build_graph(interpreter, decision, checkpointer=InMemorySaver())
    )
    context = {"today": "2026-10-19", "service_names": ["Massage"], **case.context}
    actions = ScenarioSchedulingActions(context, case.existing_booking)
    observed, mismatches = {"turns": [], "fallback_used": False}, []
    counters = {
        "labelled_fields": 0,
        "correct_fields": 0,
        "labelled_actions": 0,
        "correct_actions": 0,
    }
    started = perf_counter()
    try:
        turns = case.turns or [
            EvaluationTurn(utterance=case.utterance, expected=case.expected)
        ]
        counters["labelled_fields"] = (
            sum(len(t.expected) for t in turns) if layer != "routing" else 0
        )
        counters["labelled_actions"] = (
            sum(bool(t.expected_action) for t in turns) if layer != "extraction" else 0
        )
        for i, turn in enumerate(turns):
            turn_context = {**context, **turn.context}
            if layer == "routing":
                if not turn.interpreted_message:
                    raise ValueError(
                        "Fixed-input routing requires an interpreted_message label"
                    )
                message, transcript_ms, audio_metrics = (
                    sanitize_message(turn.interpreted_message),
                    0.0,
                    {},
                )
            else:
                message, transcript_ms, audio_metrics = _read_turn_input(
                    turn, turn_context, settings, understanding, dataset_dir
                )
            actions.last_booking = None
            if layer == "workflow":
                reply = workflow.handle_turn(
                    TurnContext(
                        conversation_id=case.scenario_id,
                        business_id="demo-studio",
                        message=message,
                        today=turn_context["today"],
                        customer_id="fixture-customer" if case.authenticated else None,
                        idempotency_key=f"eval:{case.scenario_id}:{i}",
                        input_modality="audio" if turn.audio_path else "text",
                    ),
                    actions,
                )
                state = workflow.state(case.scenario_id)
                interpretation = SchedulingInterpretation.model_validate(
                    state["interpretation"]
                )
                action = state["decision"]["next_action"]
                if turn.expected_intent and reply.intent != turn.expected_intent:
                    mismatches.append(
                        f"turn {i}: intent expected {turn.expected_intent}, observed {reply.intent}"
                    )
            elif layer == "routing":
                interpretation = SchedulingInterpretation(message=message)
                action = decision.decide(message, turn.draft).next_action
            else:
                interpretation = interpreter.interpret(message, turn_context)
                action = None
            fields = interpretation.model_dump(mode="json")
            _score_turn(turn, i, fields, action, layer, counters, mismatches)
            observed["turns"].append(
                {
                    "interpretation": fields,
                    "action": action,
                    "understanding_ms": interpreter.milliseconds,
                    "routing_ms": decision.milliseconds,
                    "transcription_ms": transcript_ms,
                    **audio_metrics,
                }
            )
        if layer == "workflow":
            for op, count in case.expected_writes.items():
                if actions.writes.get(op) != count:
                    mismatches.append(
                        f"{op}: expected {count} writes, observed {actions.writes.get(op)}"
                    )
        observed["writes"] = actions.writes
        status = "fail" if mismatches else "pass"
    except Exception as error:
        # Exception messages may contain private provider payloads/URLs.
        observed["error_type"] = type(error).__name__
        status = "error"
    observed["metrics"] = counters
    observed.update(
        language=case.language,
        modality="audio" if any(t.audio_path for t in case.turns) else "text",
        elapsed_ms=(perf_counter() - started) * 1000,
        understanding="labelled" if layer == "routing" else understanding,
        routing="not_run" if layer == "extraction" else routing,
        layer=layer,
    )
    return EvaluationResult(
        scenario_id=case.scenario_id,
        title=case.title,
        status=status,
        expected=case.model_dump(mode="json"),
        observed=observed,
        mismatches=mismatches,
    )


def _read_turn_input(turn, context, settings, understanding, dataset_dir):
    if not turn.audio_path:
        return sanitize_message(turn.utterance), 0.0, {}
    if understanding != "gemma":
        raise ValueError("Audio evaluations require Gemma understanding")
    started = perf_counter()
    recording = (Path(dataset_dir) / turn.audio_path).read_bytes()
    transcript = GemmaUnderstanding(settings.gemma_base_url).transcribe_recording(
        recording, context, chunker=AudioChunker()
    )
    elapsed_ms = (perf_counter() - started) * 1000
    metrics = _word_errors(turn.reference_transcript, transcript)
    return sanitize_message(transcript), elapsed_ms, metrics


def _score_turn(turn, index, fields, action, layer, counters, mismatches):
    if layer != "routing":
        for field, expected in turn.expected.items():
            actual = fields.get(field)
            counters["correct_fields"] += int(actual == expected)
            if actual != expected:
                mismatches.append(
                    f"turn {index}: {field} expected {expected!r}, observed {actual!r}"
                )
    if layer != "extraction" and turn.expected_action:
        counters["correct_actions"] += int(action == turn.expected_action)
        if action != turn.expected_action:
            mismatches.append(
                f"turn {index}: action expected {turn.expected_action}, observed {action}"
            )
    if layer == "routing" and not turn.expected_action:
        raise ValueError("Fixed-input routing cases require expected_action")


def summarize(results):
    groups = defaultdict(list)
    for item in results:
        groups[f"{item.observed['language']}/{item.observed['modality']}"].append(item)
    summary = {}
    for group, items in groups.items():
        durations = sorted(i.observed["elapsed_ms"] for i in items)
        totals = {
            k: sum(i.observed["metrics"][k] for i in items)
            for k in items[0].observed["metrics"]
        }
        fields, actions = totals["labelled_fields"], totals["labelled_actions"]
        summary[group] = {
            **totals,
            "field_accuracy": totals["correct_fields"] / fields if fields else None,
            "routing_accuracy": totals["correct_actions"] / actions
            if actions
            else None,
            "task_success": sum(i.status == "pass" for i in items) / len(items),
            "cases": len(items),
            "passes": sum(i.status == "pass" for i in items),
            "errors": sum(i.status == "error" for i in items),
            "p50_ms": statistics.median(durations),
            "p95_ms": durations[
                min(len(durations) - 1, int((len(durations) - 1) * 0.95 + 0.5))
            ],
        }
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=Path("evals/data/smoke-text.jsonl")
    )
    parser.add_argument(
        "--understanding", choices=["gemma", "deterministic"], default="gemma"
    )
    parser.add_argument("--routing", choices=["laya", "deterministic"], default="laya")
    parser.add_argument(
        "--layer", choices=["extraction", "routing", "workflow"], default="workflow"
    )
    parser.add_argument("--gemma-revision", default="unspecified")
    parser.add_argument("--laya-revision", default="unspecified")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        cases = load_dataset(args.dataset)
        results = run_scenarios(
            cases,
            understanding=args.understanding,
            routing=args.routing,
            layer=args.layer,
            dataset_dir=args.dataset.parent,
        )
    except (ValueError, OSError) as error:
        parser.error(f"Dataset validation failed ({type(error).__name__})")
    dataset_hash = hashlib.sha256(args.dataset.read_bytes())
    for case in cases:
        for turn in case.turns:
            if turn.audio_path:
                dataset_hash.update(
                    (args.dataset.parent / turn.audio_path).read_bytes()
                )
    receipt = {
        "schema_version": 2,
        "dataset_sha256": dataset_hash.hexdigest(),
        "understanding": args.understanding,
        "routing": args.routing,
        "layer": args.layer,
        "gemma_revision": args.gemma_revision,
        "laya_revision": args.laya_revision,
        "summary": summarize(results),
        "results": [r.model_dump(mode="json") for r in results],
    }
    output = (
        args.output
        or Path(".eval_runs")
        / f"scheduling-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    for item in results:
        print(f"{item.status.upper():5} {item.scenario_id}")
    print(f"Results: {output}")
    return int(any(r.status != "pass" for r in results))


if __name__ == "__main__":
    raise SystemExit(main())

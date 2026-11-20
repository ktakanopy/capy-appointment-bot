from __future__ import annotations

import html
import json
import statistics
from collections import defaultdict
from typing import Any


def render_html(receipts: list[dict[str, Any]]) -> str:
    """Build a self-contained report with status, latency, voice, and failure breakdowns."""
    attempts = [attempt for receipt in receipts for attempt in receipt.get("attempts", [])]
    totals = _counts(attempts)
    judge = _judge_counts(attempts)
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    datasets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        modality = _modality(attempt)
        voice = _voice(attempt)
        groups[(attempt.get("dataset_id", ""), attempt.get("category", ""), attempt.get("language", ""), modality, voice)].append(attempt)
        categories[attempt.get("category", "unknown")].append(attempt)
        datasets[attempt.get("dataset_id", "unknown")].append(attempt)

    chunks = [
        "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<title>CAPY evaluation report</title>",
        "<style>",
        "body{font:15px system-ui,sans-serif;margin:2rem auto;padding:0 1rem;max-width:1200px;color:#1e302a;background:#fbfaf6}",
        "h1,h2{color:#173f36}table{border-collapse:collapse;width:100%;margin:1rem 0 2rem;background:white}th,td{padding:.55rem .7rem;border:1px solid #ccd7d2;text-align:left;vertical-align:top}th{background:#eaf1ec}code,pre{background:#edf1ef;padding:.2rem .35rem;border-radius:4px;white-space:pre-wrap;overflow-wrap:anywhere}.metric{display:inline-block;padding:.8rem 1rem;margin:.3rem;border:1px solid #c9d7cf;border-radius:8px;background:white}.fail{color:#a43623}.error{color:#8a5113}.pass{color:#176447}details{margin:.7rem 0;padding:.6rem;border:1px solid #cbd6d0;border-radius:8px;background:white}summary{cursor:pointer;font-weight:650}.bar{height:.7rem;background:#d8e7df;min-width:2px}.bar span{display:block;height:100%;background:#2c806b}</style>",
        "<body><h1>CAPY evaluation report</h1>",
        f"<p>Runs: {len(receipts)} · Attempts: {len(attempts)} · Created: {_e(', '.join(str(receipt.get('created_at', 'unknown')) for receipt in receipts))}</p>",
        f"<p>Source revisions: {_e(', '.join(str(receipt.get('source_revision', 'unknown')) for receipt in receipts))}</p>",
        f"<p>Working tree: {_e(_working_tree_summary(receipts))}</p>",
        f"<p>Concurrency: {_e(_concurrency_summary(receipts))}</p>",
        f"<p>Providers: {_e(_provider_summary(receipts))}</p>",
        "<section>",
        f"<span class=\"metric pass\">Passed: {totals['passed']}</span>",
        f"<span class=\"metric fail\">Failed: {totals['failed']}</span>",
        f"<span class=\"metric error\">Errors: {totals['error']}</span>",
        f"<span class=\"metric\">Pass rate: {totals['passed'] / len(attempts):.1%}</span>" if attempts else "<span class=\"metric\">Pass rate: n/a</span>",
        f"<span class=\"metric\">Judge criteria: {judge['passed']}/{judge['total']} passed, {judge['failed']} failed; {judge['errors']} judge errors</span>" if judge["enabled"] else "<span class=\"metric\">Judge: off</span>",
        "</section>",
        "<h2>Coverage by behavior</h2>",
        _coverage_table(receipts),
        "<h2>Results by dataset and category</h2>",
        _simple_breakdown("Dataset", datasets),
        _simple_breakdown("Category", categories),
        "<h2>Language, modality, and voice breakdown</h2>",
        _group_table(groups),
        "<h2>Failed and errored cases</h2>",
        _failure_table(attempts),
        "<h2>Judge criterion failures</h2>",
        _judge_failure_table(attempts),
        "<h2>Inconsistent results across replays</h2>",
        _flaky_table(attempts),
        "<h2>Attempt details</h2>",
    ]
    for attempt in attempts:
        chunks.append(_attempt_details(attempt))
    chunks.append("</body></html>")
    return "\n".join(chunks)


def _coverage_table(receipts):
    """Combine declared and observed evaluation layers by behavior ID."""
    coverage = {}
    for receipt in receipts:
        for behavior, value in receipt.get("coverage", {}).items():
            current = coverage.setdefault(
                behavior,
                {"declared": set(), "observed": {"deterministic": False, "model": False, "database": False}},
            )
            current["declared"].update(value.get("declared", []))
            for layer, happened in value.get("observed", {}).items():
                current["observed"][layer] = current["observed"].get(layer, False) or happened
    rows = ["<table><thead><tr><th>Behavior</th><th>Declared layers</th><th>Observed evidence</th></tr></thead><tbody>"]
    for behavior, value in sorted(coverage.items()):
        observed = ", ".join(name for name, yes in value["observed"].items() if yes) or "none"
        declared = ", ".join(sorted(value["declared"])) or "none"
        rows.append(f"<tr><td><code>{_e(behavior)}</code></td><td>{_e(declared)}</td><td>{_e(observed)}</td></tr>")
    if not coverage:
        rows.append("<tr><td colspan=3>No behavior coverage metadata is present.</td></tr>")
    rows.append("</tbody></table>")
    return "".join(rows)


def _simple_breakdown(label, groups):
    """Render attempt counts and pass rates for one dataset or category grouping."""
    rows = [f"<table><thead><tr><th>{_e(label)}</th><th>Attempts</th><th>Passed</th><th>Failed</th><th>Errors</th><th>Pass rate</th></tr></thead><tbody>"]
    for name, items in sorted(groups.items()):
        counts = _counts(items)
        rate = f"{counts['passed'] / len(items):.1%}" if items else "n/a"
        rows.append(f"<tr><td>{_e(name)}</td><td>{len(items)}</td><td>{counts['passed']}</td><td>{counts['failed']}</td><td>{counts['error']}</td><td>{rate}</td></tr>")
    rows.append("</tbody></table>")
    return "".join(rows)


def _group_table(groups):
    """Show result rates and latency percentiles for language, modality, and voice groups."""
    rows = ["<table><thead><tr><th>Dataset</th><th>Category</th><th>Language</th><th>Modality</th><th>Voice backend / model / preset</th><th>Attempts</th><th>Pass rate</th><th>Mean ms</th><th>p50 ms</th><th>p95 ms</th></tr></thead><tbody>"]
    for key, items in sorted(groups.items()):
        counts = _counts(items)
        durations = sorted(float(item.get("timings_ms", {}).get("elapsed", 0)) for item in items)
        average = statistics.mean(durations) if durations else None
        p50 = statistics.median(durations) if durations else None
        p95 = durations[min(len(durations) - 1, round((len(durations) - 1) * .95))] if durations else None
        rate = f"{counts['passed'] / len(items):.1%}" if items else "n/a"
        values = [*key, str(len(items)), rate, _number(average), _number(p50), _number(p95)]
        rows.append("<tr>" + "".join(f"<td>{_e(value)}</td>" for value in values) + "</tr>")
    rows.append("</tbody></table>")
    return "".join(rows)


def _failure_table(attempts):
    """List failed assertions and execution errors so a reader can find affected cases."""
    failures = [item for item in attempts if item.get("status") in {"failed", "error"}]
    rows = ["<table><thead><tr><th>Dataset</th><th>Case</th><th>Behavior</th><th>Status</th><th>Category</th><th>Language</th><th>Evidence</th></tr></thead><tbody>"]
    for item in failures:
        evidence = "; ".join(item.get("mismatches", [])) or item.get("error_type", "unknown error")
        rows.append(
            "<tr>" + "".join(
                f"<td>{_e(value)}</td>"
                for value in (
                    item.get("dataset_id", ""), item.get("case_id", ""),
                    item.get("spec_id", ""), item.get("status", ""),
                    item.get("category", ""), item.get("language", ""), evidence,
                )
            ) + "</tr>"
        )
    if not failures:
        rows.append("<tr><td colspan=7>No failed or errored attempts.</td></tr>")
    rows.append("</tbody></table>")
    return "".join(rows)


def _judge_failure_table(attempts):
    """Show advisory judge failures even when they do not gate the attempt status."""
    failures = [
        (attempt, verdict)
        for attempt in attempts
        for verdict in (attempt.get("judge") or {}).get("verdicts", [])
        if not verdict.get("passed")
    ]
    rows = ["<table><thead><tr><th>Dataset</th><th>Case</th><th>Criterion</th><th>Evidence</th><th>Reason</th></tr></thead><tbody>"]
    for attempt, verdict in failures:
        rows.append(
            "<tr>" + "".join(
                f"<td>{_e(value)}</td>"
                for value in (
                    attempt.get("dataset_id", ""),
                    attempt.get("case_id", ""),
                    verdict.get("criterion_id", ""),
                    verdict.get("evidence", ""),
                    verdict.get("reason", ""),
                )
            ) + "</tr>"
        )
    if not failures:
        rows.append("<tr><td colspan=5>No failed judge criteria.</td></tr>")
    rows.append("</tbody></table>")
    return "".join(rows)


def _flaky_table(attempts):
    """Find case IDs that received different outcomes across repetitions or replay."""
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for attempt in attempts:
        grouped[(attempt.get("dataset_id", ""), attempt.get("case_id", ""))].append(
            attempt.get("status", "unknown")
        )
    flaky = [
        (dataset, case, statuses)
        for (dataset, case), statuses in grouped.items()
        if len(set(statuses)) > 1
    ]
    rows = ["<table><thead><tr><th>Dataset</th><th>Case</th><th>Attempt outcomes</th></tr></thead><tbody>"]
    for dataset, case, statuses in sorted(flaky):
        rows.append(
            f"<tr><td>{_e(dataset)}</td><td>{_e(case)}</td><td>{_e(', '.join(statuses))}</td></tr>"
        )
    if not flaky:
        rows.append("<tr><td colspan=3>No case has mixed outcomes across its recorded attempts.</td></tr>")
    rows.append("</tbody></table>")
    return "".join(rows)


def _attempt_details(attempt):
    """Expand one attempt into expected/actual turns, effects, timing, and judge evidence."""
    title = f"{attempt.get('status', 'unknown').upper()} · {attempt.get('dataset_id')}/{attempt.get('case_id')} · repeat {attempt.get('repetition')}"
    lines = [f"<details><summary class=\"{_e(attempt.get('status', ''))}\">{_e(title)}</summary>"]
    lines.append(f"<p>Attempt <code>{_e(attempt.get('attempt_id', ''))}</code>; parent <code>{_e(attempt.get('parent_attempt_id') or 'none')}</code></p>")
    expected_turns = {
        item.get("id"): item
        for item in attempt.get("case_snapshot", {}).get("example_conversation", [])
    }
    lines.append("<table><thead><tr><th>Turn</th><th>Modality</th><th>Actual user input</th><th>Actual reply</th><th>Expected interpretation</th><th>Actual interpretation</th><th>Expected Laya</th><th>Actual Laya</th><th>Expected intent</th><th>Actual intent</th><th>Expected writes</th><th>Actual writes</th><th>Expected booking</th><th>Actual booking</th></tr></thead><tbody>")
    for turn in attempt.get("turns", []):
        expected = expected_turns.get(turn.get("turn_id"), {}).get("assertions", {})
        lines.append("<tr>" + "".join(
            f"<td>{_e(value)}</td>"
            for value in (
                turn.get("turn_id", ""), turn.get("modality", ""),
                turn.get("input", ""), turn.get("reply", ""),
                json.dumps(expected.get("gemma", {}), ensure_ascii=False),
                json.dumps(turn.get("gemma"), ensure_ascii=False),
                json.dumps(expected.get("laya"), ensure_ascii=False),
                json.dumps(turn.get("laya"), ensure_ascii=False),
                expected.get("intent", ""),
                turn.get("intent", ""),
                json.dumps(expected.get("writes", {}), ensure_ascii=False),
                json.dumps(turn.get("writes", {}), ensure_ascii=False),
                json.dumps(expected.get("booking"), ensure_ascii=False),
                json.dumps(turn.get("booking"), ensure_ascii=False),
            )
        ) + "</tr>")
    lines.append("</tbody></table>")
    lines.append("<h3>Gemma's prior conversation context</h3>")
    for turn in attempt.get("turns", []):
        history = turn.get("gemma_history", [])
        lines.append(
            f"<details><summary>{_e(turn.get('turn_id', 'turn'))}: "
            f"{len(history)} earlier messages</summary><pre>"
            f"{_e(json.dumps(history, ensure_ascii=False, indent=2))}</pre></details>"
        )
    lines.append("<h3>Per-turn provider timings (ms)</h3><table><thead><tr><th>Turn</th><th>Transcription</th><th>Gemma</th><th>Laya</th></tr></thead><tbody>")
    for turn in attempt.get("turns", []):
        timing = turn.get("timings_ms", {})
        lines.append(
            "<tr>" + "".join(
                f"<td>{_e(value)}</td>"
                for value in (
                    turn.get("turn_id", ""),
                    _number(timing.get("transcription", 0)),
                    _number(timing.get("gemma", 0)),
                    _number(timing.get("laya", 0)),
                )
            ) + "</tr>"
        )
    lines.append("</tbody></table>")
    expected_final = attempt.get("case_snapshot", {}).get("final_assertions", {})
    lines.append(f"<h3>Expected final assertions</h3><pre>{_e(json.dumps(expected_final, ensure_ascii=False, indent=2))}</pre>")
    lines.append(f"<h3>Provider timings</h3><pre>{_e(json.dumps(attempt.get('timings_ms', {}), ensure_ascii=False, indent=2))}</pre>")
    for label, value in (
        ("Mismatches", attempt.get("mismatches", [])),
        ("Judge verdicts", (attempt.get("judge") or {}).get("verdicts", [])),
        ("Voice assets", attempt.get("voice_assets", [])),
        ("Final booking projection", attempt.get("booking")),
    ):
        lines.append(f"<h3>{_e(label)}</h3><pre>{_e(json.dumps(value, ensure_ascii=False, indent=2))}</pre>")
    if attempt.get("error_type"):
        lines.append(f"<p class=\"error\">Execution error type: {_e(attempt['error_type'])}</p>")
    lines.append("</details>")
    return "".join(lines)


def _counts(attempts):
    """Count passed, failed, and errored attempts for report summaries."""
    return {
        status: sum(item.get("status") == status for item in attempts)
        for status in ("passed", "failed", "error")
    }


def _provider_summary(receipts):
    """Summarize the interpretation, routing, and judge profiles used by the receipts."""
    profiles = {
        (
            item.get("profiles", {}).get("understanding", "unknown"),
            item.get("profiles", {}).get("routing", "unknown"),
            item.get("profiles", {}).get("judge_provider", "off"),
            item.get("profiles", {}).get("judge_model", ""),
        )
        for item in receipts
    }
    return "; ".join(
        f"{understanding} / {routing} / judge {judge} {model}".strip()
        for understanding, routing, judge, model in sorted(profiles)
    )


def _concurrency_summary(receipts):
    """Report requested versus effective worker counts and repetitions per run."""
    return "; ".join(
        f"{item.get('concurrency', {}).get('effective_jobs', 0)}/"
        f"{item.get('concurrency', {}).get('requested_jobs', 0)} workers, "
        f"{item.get('concurrency', {}).get('repetitions', 0)} repeats"
        for item in receipts
    ) or "not recorded"


def _working_tree_summary(receipts):
    """Show whether each run came from a clean tree or a hashed local change set."""
    labels = []
    for receipt in receipts:
        tree = receipt.get("working_tree", {})
        if not tree.get("available"):
            labels.append("identity unavailable")
        elif tree.get("dirty"):
            labels.append(
                f"modified ({tree.get('changed_path_count', 0)} paths, "
                f"sha256 {str(tree.get('sha256', ''))[:12]})"
            )
        else:
            labels.append("clean")
    return "; ".join(labels) or "not recorded"


def _judge_counts(attempts):
    """Aggregate criterion verdicts and enabled-judge execution errors."""
    verdicts = [
        verdict
        for attempt in attempts
        for verdict in (attempt.get("judge") or {}).get("verdicts", [])
    ]
    enabled = any(attempt.get("providers", {}).get("judge", "off") != "off" for attempt in attempts)
    errors = sum(
        attempt.get("status") == "error"
        and attempt.get("providers", {}).get("judge", "off") != "off"
        for attempt in attempts
    )
    return {
        "enabled": enabled,
        "total": len(verdicts),
        "passed": sum(bool(item.get("passed")) for item in verdicts),
        "failed": sum(not item.get("passed") for item in verdicts),
        "errors": errors,
    }


def _modality(attempt):
    """Classify one conversation as text, audio, mixed, or unknown input."""
    modalities = {turn.get("modality") for turn in attempt.get("turns", [])}
    return "mixed" if len(modalities) > 1 else next(iter(modalities), "unknown")


def _voice(attempt):
    """Identify the actual synthesis backend, model, and voice preset for grouping."""
    values = {
        f"{item.get('backend') or 'unknown'} / {item.get('model') or 'model unknown'} / {item.get('voice') or 'voice unknown'}"
        for item in attempt.get("voice_assets", [])
    }
    return ", ".join(sorted(values)) or "not applicable"


def _number(value):
    """Format a millisecond statistic or show that no sample was recorded."""
    return f"{value:.0f}" if value is not None else "n/a"


def _e(value):
    """Escape scenario-controlled text before inserting it into HTML markup."""
    return html.escape(str(value), quote=True)

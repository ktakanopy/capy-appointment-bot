# Make CAPY easier to learn and evaluate

This document describes an earlier design or implementation phase. See the architecture guide for the current webhook intake and worker flow. [Current Telegram architecture](../architecture.md#how-the-telegram-worker-works)

## Implementation brief for Sol High

Work in the existing capy-appointment-bot checkout on branch `portifolio`. The outcome is a developer who can run a synthetic booking conversation, trace how a voice note becomes an authorized booking, and reproduce an experiment that isolates Gemma understanding from Laya routing.

Read AGENTS.md and inspect `git status --short --branch` first. Work from the current files, including uncommitted README edits, the local model server, the booking refactor and the labeled SVG diagrams. A fresh checkout of the remote branch can lack these inputs. Review diffs before editing overlapping files and stage only intended changes if publication is requested.

### 1. Give the README two clear entry points

- Preserve the introduction, capabilities and existing artwork. Immediately below them, use the three turns from the `en-booking` case in `evals/data/smoke-text.jsonl`. Show the customer's input, expected decision, and visible outcome. “Book Massage” prompts for a date, “tomorrow” searches, and “1” selects an offered slot. The fixture expects one create write across the conversation. Label the example as a synthetic fixture.
- Put the offline path first. Show dependency setup and the existing command below. Explain that it runs the text corpus, prints a status per scenario and writes a JSON report. Point the reader to `en-booking` in that report. This path uses deterministic providers and fixture services and needs no Telegram token, database or model download.
- Keep the live voice path together, including Apple Silicon prerequisites, model setup, Telegram configuration, Compose startup, health checks and the limits of demo reminder delivery. Link model contracts, verification and migration guidance where relevant. Keep commands in one canonical place per task.
- **Acceptance.** From the top of the README a reader can find the offline command, a live voice demo and the code walkthrough. Every claimed model output is identified as an expected fixture value or an observed result.

### 2. Turn the architecture guide into a navigable code tour

- Move the existing “How to read the code” section ahead of the detailed worker and booking diagrams. Keep the heading anchor. Use the same booking conversation as the README and link to functions in reading order. Show where trust enters through the Telegram sender, where Gemma produces the interpreted message and facts, what Laya receives, where Python checks its proposal, and which transaction changes the booking.
- Pair three questions with executable examples. Link the model boundary to `tests/unit/test_gemma_laya.py`, the end-to-end booking lifecycle to `tests/integration/test_workflow_booking_journey.py`, and retry/concurrency guarantees to `tests/integration/test_postgres_booking_lifecycle.py`. Explain which dependencies each test exercises.
- Keep the SVGs, arrow IDs, matching descriptions, function names and source-line links. Refresh references after code edits. Use `docs/model-contracts.md` for payload details, `docs/database.md` for tables, and `docs/evaluation.md` for experiment instructions. Identify planning, historical and hypothetical scale documents as such in navigation.
- **Acceptance.** A reader can follow the reply “1” to the booking write or jump straight to voice transcription. Check changed relative links, heading anchors, SVG paths and the functions at referenced source lines. Each sequence arrow still has a matching explanation.

### 3. Simplify the Telegram date lookup without losing audio context

- In `app/telegram_worker.py`, inspect both callers of `_business_context()`. Today, `_run_turn()` loads a catalog to use only the local date. Separately, `_message_input()` supplies service names, professional names and that date to voice transcription. The graph loads its own catalog through `load_context()`.
- Give the turn's date-only lookup a descriptive name and use the existing `PostgresRepository.business()` for timezone access. Keep the full audio context in a clearly named audio-specific helper. Share the business-local date calculation if needed. Leave graph catalog loading and explicit evaluation dates intact. Avoid introducing a cache or changing checkpoint state for this refactor.
- Update worker fixtures that currently provide only `repository.catalog`. Exercise text, audio, and a missing business. Verify the text worker's date lookup avoids the catalog, the voice call receives its expected names, and a local date near midnight is calculated in the business timezone. Keep the existing guarantee that incomplete transcription cannot execute a turn.
- **Acceptance.** Ordinary text turns avoid the redundant full catalog fetch. Business-scoped lookup, localized dates, sanitized input, and user-facing error behavior remain covered.

### 4. Remove graph plumbing that obscures decisions

- Inspect callers of `interpret`, `help_reply`, and `persist` in `app/graph/steps.py`. Remove unused scheduling-actions parameters. Register state-only steps directly in `app/graph/builder.py`. Retain short adapters where a node actually needs the interpreter, decision engine, threshold or request-scoped actions.
- Preserve graph node names, edges, checkpoint compatibility, routing thresholds, confirmation/ownership checks and provider contracts. Keep helpers that express a business decision. Give new or changed functions readable names and docstrings explaining their purpose, how they work and the reason for consequential choices.
- **Acceptance.** The graph still accepts the same turn inputs and produces the same decisions, replies and writes on the regression corpus. Node registration exposes which steps depend on customer actions without a generic dispatcher.

### 5. Publish one reproducible model observation

- Use the existing synthetic speech cases and the documented local endpoints. In `docs/case-study.md`, record the case ID, clip, labeled facts/action, actual output available in the receipt, served checkpoint revisions, dataset hash and reproduction commands. Confirm the revisions against the actual model files or serving configuration.
- Run the audio corpus with Gemma and Laya at the workflow layer. Then run the same cases with `--layer extraction` and `--layer routing`. Routing uses the human-labeled interpreted message and draft, so the comparison helps locate errors. Keep receipts separate. Distinguish setup/endpoint errors from wrong model predictions.
- Prefer a reproducible failure with an explanation of which stage first diverged. If both cases pass, publish an observed success and identify the next harder case to test. If endpoints are unavailable, record the blocker, complete the other steps and provide the rerun command. Preserve the distinction between smoke results and evidence of fine-tuning gains.
- **Acceptance.** A reader can reproduce the reported result using synthetic data and identify the model, data and comparison used. Future training work remains clearly labeled.

## Verification and handoff

Run the offline baseline before and after the code edits. Compare case statuses, decisions and write counts. Run focused tests while editing and the existing disposable integration suite before handing back the implementation.

```sh
uv sync --locked --extra dev
uv run python -m app.evals.runner --understanding deterministic --routing deterministic
uv run --extra dev pytest -q tests/unit/test_telegram_turn.py tests/unit/test_audio_pipeline.py tests/unit/test_gemma_laya.py tests/unit/test_conversation_graph.py tests/unit/test_graph_workflow.py
docker compose -p capy-simplification-test -f docker-compose.test.yml run --build --rm checks
docker compose -p capy-simplification-test -f docker-compose.test.yml down
git diff --check
```

For live runs, set GEMMA_REV and LAYA_REV to the verified revisions. Use a separate output path for each experiment.

```sh
for layer in workflow extraction routing; do
  uv run python -m app.evals.runner --dataset evals/data/smoke-audio.jsonl \
    --understanding gemma --routing laya --layer "$layer" \
    --gemma-revision "$GEMMA_REV" --laya-revision "$LAYA_REV" \
    --output ".eval_runs/portfolio-audio-$layer.json"
done
```

Inspect each receipt and each command's outcome. The runner returns a nonzero status for failed cases. Report documentation changes, behavior preserved by the refactor, tests run, live results and any unavailable prerequisites.

## Separate follow-up decisions

The earlier schema proposal (retire unused audit_events and fold simulated reminder delivery into reminders) requires its own migration, existing-data checks, owner CLI verification and diagram updates. Likewise, changing Compose initialization or replacing the Redis checkpointer changes deployment or persistence behavior. Track these separately from this onboarding and model-evaluation implementation. Keep PostgreSQL-backed receipts and reminders, direct connections, and Telegram long polling for this work.

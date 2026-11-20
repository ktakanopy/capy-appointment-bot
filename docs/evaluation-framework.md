# CAPY evaluation framework

The scenario runner exercises real CAPY graph turns from YAML conversations. Each customer message is sent alone. The graph returns its actual reply and checkpoints it; that reply and the customer's input become history for the next scripted turn. `assistant_example` is a reference for the person reading the spec and is not passed to Gemma, Laya, or the judge.

The runner records three separate kinds of evidence:

- **Gemma output:** the validated `SchedulingInterpretation` captured before graph merging changes its fields.
- **Laya output:** the typed `DecisionOutput` proposed for that turn. Python still applies the workflow's safety rules.
- **Deterministic behavior:** reply intent, offered-slot count, exact write deltas for create/confirm/cancel/reschedule, and the saved booking's match to a previously offered slot.

Conversation playback is sequential: CAPY receives one current user input, produces its actual reply, and checkpoints that user/reply pair before the next scripted input. Gemma receives that earlier actual history as context; `assistant_example` is never inserted. Laya receives only Gemma's current interpreted message and the current `WorkflowDraft`, preserving the application's model contract. The report expands the prior history seen by Gemma for each turn.

The graph uses isolated in-memory checkpoints and synthetic scheduling services for each `(case, repetition)` attempt. Cases and repetitions can run concurrently; messages inside one case remain sequential. The scenario run does not create PostgreSQL rows. PostgreSQL behavior is covered separately by the integration suite, including the confirmation-before-create journey.

## Run the bilingual golden dataset

Install the locked development dependencies, then run the deterministic baseline. The first run uses text fixtures and requires no Telegram credentials, database, model server, voice model, judge server, or hosted API key.

```bash
uv sync --locked --extra dev
uv run --locked --extra dev python -m app.evals.scenarios run \
  --dataset evals/scenarios/golden.yaml \
  --profile offline \
  --repeat 1
```

The golden set contains the same booking journey in English and Brazilian Portuguese. A customer requests times, selects one, answers a separate confirmation question, and receives details after the booking is saved. The selection turn asserts zero writes. The approval turn asserts one create and checks that the saved booking matches the earlier offer.

Each invocation creates one timestamped JSON receipt and a self-contained HTML report under `.eval_runs/scenarios/`. The directory is ignored by Git. The command's exit code is `0` when deterministic checks pass, `1` when an assertion fails, and `2` for setup, provider, or execution errors.

The older JSONL runner remains available for extraction-only, fixed-input routing, and its smaller regression corpus. See [Evaluation](evaluation.md) for those commands and metrics.

## Author a scenario

YAML files live under `evals/scenarios/`. A dataset declares `schema_version`, `dataset_id`, and `scenarios`. Each case declares a stable `id`, a behavior `spec_id`, category, language, split, group, setup, example conversation, inline judge prompt and criteria, and final assertions.

Each conversation step has one executable `user` input and optional reference reply:

```yaml
- id: choose_time
  user:
    modality: text
    text: "1"
  assistant_example: "Please confirm the selected time before booking."
  assertions:
    gemma:
      slot_index: 1
    laya:
      next_action: BOOK
    intent: request_booking_confirmation
    writes: {}
```

`writes` is required for every turn. Omitted operation names mean zero, so an unexpected cancel or reschedule fails even if the expected create count is correct. A final `writes` map checks the whole conversation. Booking assertions can name an earlier `turn_id` and one-based offer position; the runner compares that slot's service, professional and start/end times with the saved booking.

Use `declared_coverage` to state which layers should eventually cover a behavior: `deterministic`, `model`, and `database`. The report shows this declaration separately from observed evidence. The scenario runner records database coverage as unobserved because its scheduling service is a fixture.

Render the same YAML as Markdown for review. The renderer includes setup, all user inputs, reference replies, assertions and the judge rubric.

```bash
uv run --locked --extra dev python -m app.evals.scenarios render-spec \
  --dataset evals/scenarios/golden.yaml \
  --output .eval_runs/scenarios/golden.md
```

The loader uses PyYAML's safe loader and rejects duplicate mapping keys, unknown fields, invalid actions, invalid audio combinations, unknown turn references, duplicate case/step/judge IDs, invalid dates or timezones, unsafe asset paths, and group IDs that cross train/dev/test splits. Version 1 only accepts synthetic provenance so receipts and external judge requests are not used as storage for production conversations. Related language or audio variants should share one group and one split.

## Run the challenge cases

The challenge dataset separates declining a pending offer from canceling a booking that was actually created. It also covers a changed time and spoken Portuguese consent.

Run a text-only case offline:

```bash
uv run --locked --extra dev python -m app.evals.scenarios run \
  --dataset evals/scenarios/challenge.yaml \
  --case decline-pending-booking \
  --profile offline \
  --repeat 1
```

For generated speech, prepare the audio before the run. The default local voice presets are `documentary` for English and `storyteller` for Brazilian Portuguese. `playful-host` is another Qwen3 preset. These use the local Qwen3-TTS VoiceDesign script; point `--local-python` at an environment with its MLX audio dependencies and model installed.

```bash
uv run --locked --extra dev python -m app.evals.scenarios prepare-audio \
  --dataset evals/scenarios/challenge.yaml \
  --backend local-qwen3 \
  --voice en=documentary \
  --voice pt=storyteller \
  --local-script ~/projects/voice-project/generate_voice.py \
  --local-python /path/to/qwen3-venv/bin/python \
  --manifest .eval_runs/scenarios/challenge-audio.json

uv run --locked --extra dev python -m app.evals.scenarios run \
  --dataset evals/scenarios/challenge.yaml \
  --profile offline \
  --audio-manifest .eval_runs/scenarios/challenge-audio.json \
  --repeat 1
```

Offline audio runs feed the prepared recording through the graph's audio path and use the YAML reference transcript as a deterministic transcription fixture. This checks scenario playback, routing and consent behavior without measuring Gemma speech recognition. Use the live profile to ask the configured Gemma model to transcribe and interpret the recording.

To use OpenRouter for English synthesis, the default model and voice are `microsoft/mai-voice-2.1-flash` and `en-US-Harper:MAI-Voice-2.1-Flash`. Set `OPENROUTER_API_KEY` in the shell. Brazilian Portuguese requires an explicit compatible model and voice because this repository does not define a verified default pair.

```bash
uv run --locked --extra dev python -m app.evals.scenarios prepare-audio \
  --dataset evals/scenarios/challenge.yaml \
  --backend openrouter \
  --model YOUR_MODEL_ID \
  --voice pt=YOUR_VERIFIED_PORTUGUESE_VOICE \
  --allow-remote \
  --manifest .eval_runs/scenarios/challenge-audio.json
```

The YAML supplies only synthetic speech scripts. OpenRouter generation requires the explicit `--allow-remote` flag and rejects cases not marked `provenance: synthetic`. The receipt records backend, model, voice, language, asset checksum, and cache reuse so results show exactly which speech input CAPY received. `prepare-audio --backend recorded` can also register an existing WAV fixture in a manifest.

## Select models and a judge

Use `--profile live` for the actual configured Gemma and Laya endpoints. Start the model service first using the [local model setup](../README.md#run-the-local-models-on-apple-silicon). By default, live cases run with two workers; offline cases use four. `--jobs 1` serializes the cases for diagnosis. A repeated attempt has a fresh graph checkpoint and booking fixture, but its turns remain ordered.

```bash
uv run --locked --extra dev python -m app.evals.scenarios run \
  --dataset evals/scenarios/golden.yaml \
  --profile live \
  --repeat 3 \
  --jobs 2
```

Each YAML scenario keeps its LLM judge prompt and named criteria beside the example conversation. CAPY sends one judge request per target turn, with only the conversation prefix through that turn. This prevents a later reply from influencing an earlier-turn score. The judge sees sanitized actual messages/replies, that turn's criteria, and allowlisted selected/saved booking facts. It does not see `assistant_example`. Returned evidence and explanations are sanitized before they enter a receipt. Judge results are advisory by default; add `--judge-gate` with an enabled judge provider to make a failed verdict fail the attempt. Provider errors are reported as execution errors. A conversation with criteria across several turns uses one judge request for each distinct target turn.

For an OpenAI-compatible local judge, configure the endpoint and model:

```bash
uv run --locked --extra dev python -m app.evals.scenarios run \
  --dataset evals/scenarios/golden.yaml \
  --profile live \
  --judge-provider local \
  --judge-model YOUR_LOCAL_MODEL \
  --judge-base-url http://localhost:8080/v1/chat/completions \
  --judge-gate
```

To send synthetic evaluation conversations to OpenRouter, set `OPENROUTER_API_KEY`, select a model, and opt in explicitly:

```bash
uv run --locked --extra dev python -m app.evals.scenarios run \
  --dataset evals/scenarios/golden.yaml \
  --profile live \
  --judge-provider openrouter \
  --judge-model YOUR_MODEL_ID \
  --allow-remote
```

Do not label real conversations synthetic. The remote judge guard accepts only scenarios whose provenance is synthetic. Judge credentials and provider error bodies are never written to receipts.

## Read, replay, and compare reports

The HTML report groups attempts by dataset, category, language, modality, backend and voice. It includes pass/fail/error and judge-criterion counts, pass rates, mean/p50/p95 latency, per-turn transcription/understanding/Laya timing, inconsistent results across repetitions, expected-versus-actual interpretation and Laya outputs, exact write deltas, judge evidence, safe booking projections, and declared versus observed behavior coverage. It records the source commit and a digest of the working tree so an uncommitted run can be distinguished later. Audio voice details include the backend, model, voice preset, checksum and whether a cached asset was reused. Transcript text is HTML-escaped before it is rendered.

Replay all failed and errored attempts from a receipt. A replay uses a new checkpoint and fixture state and keeps the original run/attempt IDs as parent links.

```bash
uv run --locked --extra dev python -m app.evals.scenarios replay \
  --from .eval_runs/scenarios/run-YYYYMMDDTHHMMSSffffffZ-abc12345.json \
  --attempts unsuccessful \
  --jobs 2
```

`--attempts` accepts `all`, `failed`, `error`, `unsuccessful`, or comma-separated exact attempt IDs. Replay preserves the scenario snapshot and reuses the run's audio manifest unless another is supplied.

Build one HTML view from existing receipts without running models or audio providers:

```bash
uv run --locked --extra dev python -m app.evals.scenarios report \
  .eval_runs/scenarios/run-FIRST.json .eval_runs/scenarios/run-SECOND.json \
  --output .eval_runs/scenarios/comparison.html
```

If a reply sounds right but the create-write assertion fails, open that attempt in the report. Compare the expected Gemma facts, observed interpretation, Laya action, prior offered slot, write deltas and saved booking projection. A judge pass cannot override a deterministic mismatch. Database coverage remains unobserved until a separate PostgreSQL check has run and attached evidence.

## Extend the datasets

Keep the golden dataset small and limited to core booking behavior. Put ambiguity, intent changes, spoken corrections, stale offers and lifecycle transitions in challenge cases. Use synthetic examples, stable behavior and criterion IDs, and keep related language/audio examples together in one split. Every booking success criterion should derive its expected service, professional, local date/time and timezone from a selected offer or saved booking rather than repeating model-generated details as ground truth.

Add database guarantees to the existing disposable PostgreSQL integration suite. The scenario runner's simulated counters make fast regressions easy to replay, but they do not prove PostgreSQL transactions, ownership checks or retry recovery.

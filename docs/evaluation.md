# Evaluation

The versioned JSONL corpus under `evals/data/` supports English and Portuguese text and audio. `smoke-text.jsonl` contains 16 synthetic task cases; `smoke-audio.jsonl` contains two synthesized speech cases. `smoke.jsonl` combines them. Speech was generated with installed macOS Eddy voices and converted to 16 kHz mono WAV. These samples validate the plumbing and provide a tiny regression baseline; they do not establish real-world speech/model quality.

Each case records ID, language, split, group ID and provenance. Turns contain text or a relative audio path, reference transcript for audio, expected extraction fields, proposed action and final response intent. Cases can specify initial bookings and expected write counts. Paired recordings/paraphrases must share a group across splits. Duplicate IDs, invalid schema, unsafe/missing audio paths and split leakage fail dataset validation.

```bash
# Offline workflow regression with fixture services:
uv run python -m app.evals.runner --understanding deterministic --routing deterministic
# Actual Gemma and Laya:
uv run python -m app.evals.runner --dataset evals/data/smoke.jsonl --understanding gemma --routing laya --gemma-revision CHECKPOINT --laya-revision CHECKPOINT
# Same corpus without Laya:
uv run python -m app.evals.runner --dataset evals/data/smoke.jsonl --understanding gemma --routing deterministic --gemma-revision CHECKPOINT
```

`--layer workflow` runs the real graph with isolated synthetic domain services. It checks fields, routing, final intent and writes across turns. PostgreSQL/Redis integration tests separately establish persistence, ownership and concurrency guarantees. `--layer extraction` checks interpretation against labels. `--layer routing` supplies labelled extraction and explicit draft state to the selected router to isolate its quality.

Audio always requires Gemma transcription. Deterministic text understanding cannot silently stand in for an audio model. Every complete recording is transcribed before its scheduling request; failures are errors. Word-level edit distance and reference-word counts are included for audio. No successful fallback is counted as a model prediction; the runner has no automatic fallback.

Results under `.eval_runs/` contain schema version, hash of dataset and audio assets, requested/actual providers, model revision labels, predictions, errors and transcription/understanding/routing latency. Summaries group language and modality. Nonzero exit means a failed case, provider error or invalid dataset. Use labelled revision identifiers: the runner cannot infer the checkpoint actually served by an external endpoint.

CI is configured to run offline cases, provider contracts, real audio decoding/chunk failure tests and real PostgreSQL/Redis integration checks. Integration tests cover populated legacy migration, account separation, scoped booking operations, overlap protection, concurrency, idempotency, atomic reminders, receipts, restart and expiry.

Live Gemma/Laya evaluations and a real Telegram walkthrough remain pending until compatible servers and an authorized synthetic-data test chat are available. Use examples with long recordings, noise and late corrections before claiming voice readiness. Preserve raw results with a case-study summary and sample counts; a tiny smoke corpus must not be presented as general model accuracy.

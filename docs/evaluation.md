# Evaluation

The versioned JSONL corpus under `evals/data/` supports English and Portuguese text and audio. `smoke-text.jsonl` contains 16 synthetic task cases; `smoke-audio.jsonl` contains two synthesized speech cases. `smoke.jsonl` combines them. Speech was generated with installed macOS Eddy voices and converted to 16 kHz mono WAV. These samples validate the plumbing and provide a tiny regression baseline; they do not establish real-world speech/model quality.

Each case records ID, language, split, group ID and provenance. Turns contain text or a relative audio path, reference transcript for audio, expected booking facts, a human-labelled `interpreted_message`, expected action and final response intent. Gemma's labels contain no intent or action. The interpreted message is a simple-language rendering that preserves uncertainty; it supplies fixed-input routing and can serve as a target for future Gemma training. Cases can specify initial bookings and expected write counts. Paired recordings/paraphrases must share a group across splits. Duplicate IDs, invalid schema, unsafe/missing audio paths and split leakage fail dataset validation.

```bash
# Offline workflow regression with fixture services:
uv run python -m app.evals.runner --understanding deterministic --routing deterministic
# Actual Gemma and Laya:
uv run python -m app.evals.runner --dataset evals/data/smoke.jsonl --understanding gemma --routing laya --gemma-revision CHECKPOINT --laya-revision CHECKPOINT
# Same corpus without Laya:
uv run python -m app.evals.runner --dataset evals/data/smoke.jsonl --understanding gemma --routing deterministic --gemma-revision CHECKPOINT
```

`--layer workflow` runs the real graph with isolated synthetic domain services. It checks booking facts, routing, final response intent and writes across turns. PostgreSQL/Redis integration tests separately establish persistence, ownership and concurrency guarantees. `--layer extraction` checks Gemma's booking facts against labels and records its interpreted message. `--layer routing` supplies the labelled `interpreted_message` and explicit draft context directly to Laya or the deterministic router. It invokes neither interpretation nor transcription, including for audio cases; missing interpretation labels are errors. Only the two-field message/context contract is routed.

Field accuracy does not establish whether a rewritten message preserved its meaning. Before a real quality benchmark or fine-tuning run, review interpretations for omitted constraints, negations, late corrections, ambiguity and English/Portuguese paraphrases. The small current corpus is a regression seed, not a semantic rewrite-quality benchmark.

Audio workflow and extraction evaluations require Gemma transcription. Deterministic text understanding cannot silently stand in for an audio model. Every complete recording is transcribed before its interpreted message can reach Laya; failures are errors. Word-level edit distance and reference-word counts are included when audio is transcribed. A routing-only result for an audio case measures routing of its labelled meaning, not transcription quality. No successful fallback is counted as a model prediction; the runner has no automatic fallback.

Results under `.eval_runs/` use schema version 2 and contain a hash of dataset and audio assets, requested providers, model revision labels, interpretations, actions, errors and transcription/understanding/routing latency. Per-case provider fields describe what ran: fixed-input understanding is `labelled`, and extraction-only routing is `not_run`. Summaries group language and source modality. Nonzero exit means a failed case, provider error or invalid dataset. Use labelled revision identifiers: the runner cannot infer the checkpoint actually served by an external endpoint. Older intent-labelled datasets and schema-version-1 receipts describe the earlier contract.

CI is configured to run offline cases, provider contracts, real audio decoding/chunk failure tests and real PostgreSQL/Redis integration checks. Integration tests cover populated legacy migration, account separation, scoped booking operations, overlap protection, concurrency, idempotency, atomic reminders, receipts, restart and expiry.

Live Gemma/Laya evaluations and a real Telegram walkthrough remain pending until compatible servers and an authorized synthetic-data test chat are available. Use examples with long recordings, noise and late corrections before claiming voice readiness. Preserve raw results with a case-study summary and sample counts; a tiny smoke corpus must not be presented as general model accuracy.

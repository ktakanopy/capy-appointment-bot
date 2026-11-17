# Portfolio cleanup: implementation evidence

## Local voice evaluation

Both synthetic confirmation clips passed with the pinned Gemma and Laya checkpoints on Apple Silicon. Gemma transcribed the recordings without word errors against their reference transcripts. Laya chose `CONFIRM` for each clip and the workflow recorded one confirmation per case using fixture services.

| Case and clip | Gemma’s interpreted message | Labeled and observed route | Transcription word errors | Workflow confirmation writes |
| --- | --- | --- | --- | --- |
| [en-audio-confirm](../evals/data/audio/en-confirm.wav) | “Please confirm my booking.” | `CONFIRM` | 0 of 4 words | 1 |
| [pt-audio-confirm](../evals/data/audio/pt-confirm.wav) | “Por favor, confirme meu agendamento.” | `CONFIRM` | 0 of 5 words | 1 |

Both cases start with an existing booking and expect no new booking facts. Extraction alone passed with the same interpreted messages and zero labeled fields. Routing alone passed for both human-labeled messages and their draft contexts. A harder next case is a voice note that names a service and corrects the appointment date at the end. That would exercise fact extraction and the effect of the correction on availability. The results above describe two synthetic clips using untuned checkpoints.

The run used `evals/data/smoke-audio.jsonl` and its two audio files. The evaluator’s combined SHA-256 was `2560d233435210da9a0a3e70123609d73bd2e9beadcf58dd06c0388764a80c31`. The local service used its default model paths, with revisions matching the download metadata for the Gemma bundle and Laya checkpoint. Start the pinned local server before reproducing the runs. [Model setup](../README.md#run-the-local-models-on-apple-silicon) · [Dataset labels](../evals/data/smoke-audio.jsonl)

```sh
for layer in workflow extraction routing; do
  GEMMA_BASE_URL=http://127.0.0.1:8081 LAYA_BASE_URL=http://127.0.0.1:8081 \
    uv run python -m app.evals.runner --dataset evals/data/smoke-audio.jsonl \
      --understanding gemma --routing laya --layer "$layer" \
      --gemma-revision 2d74ac41a33d7da82f39f7d5720a04c68514a103 \
      --laya-revision f2b4faf51023039425946074e2cf1361d2db11d5 \
      --output ".eval_runs/portfolio-audio-$layer.json"
done
```

## Earlier implementation checks

Verified locally during the simplification work. Normal application behavior retains Gemma text/audio and requires Laya routing. Evaluation explicitly selects Laya or deterministic routing through the same construction contracts.

## What changed

- Telegram numeric identity owns the customer; optional phone data cannot merge accounts. Booking changes recheck ownership in their write transaction.
- Customer capability tokens and public customer/owner HTTP mutation interfaces were retired. Owner inspection and reminder settings are local CLI operations.
- One bounded Redis checkpoint owns conversation state. Contact sanitization happens before interpretation/checkpointing; arbitrary names in free text remain a documented limitation.
- Full audio decoding/chunking/transcription remains active. Incomplete recordings cannot execute a partial request.
- Evaluations share provider construction, return failing exit status on failures/errors, and record dataset/asset hashes, revisions, fields, actions, writes, errors and latency.
- Obsolete implementations were removed from the checkout; observability is a separate optional Compose file. CI is configured to run real PostgreSQL/Redis checks and offline evaluations.

## Automated evidence

The initial simplification verification container ran Python 3.11 with PostgreSQL 17 and Redis 7: **70 tests passed**, with one upstream LangChain pending-deprecation warning. This included populated legacy migration, cross-account/business denial, idempotent/concurrent writes, exclusion constraints, rollback, reminder lifecycle, the real workflow-to-database journey, Telegram receipt recovery, Redis restart/expiry, privacy/history bounds and audio chunk failure/late-correction behavior.

After adding two bilingual unsupported-request cases, all **16 text task cases** and the seven evaluation-runner tests passed. The core behavior was unchanged by those additional corpus rows. Both main and observability Compose configurations passed validation. `git diff --check` passed.

Offline fixture-workflow measurements from this development machine:

| Group | Cases | Passes | Errors | p50 ms | p95 ms |
| --- | --- | --- | --- | --- | --- |
| en/text | 8 | 8 | 0 | 2.18 | 8.89 |
| pt/text | 8 | 8 | 0 | 2.09 | 6.31 |

Dataset SHA-256: `7f25378f9760cd1b3e1e5dcfaf0d0077ceccb4657eb486a34f7cddab97f1da52`. The local raw receipt is `.eval_runs/offline-cleanup.json` (ignored by Git). These are deterministic-provider, synthetic-service regression measurements; they do not measure real model quality or PostgreSQL request latency.

## Readability refactor verification

The follow-up readability refactor separates Telegram intake, customer provisioning, input handling, graph invocation and reply delivery. `CustomerSchedulingActions` implements the `SchedulingActions` protocol; operation preparation and evaluation input/scoring now have named functions. Health and the owner CLI construct only the storage dependencies they use. Telegram file metadata errors distinguish invalid files from oversized recordings.

The full disposable Python 3.11/PostgreSQL 17/Redis 7 suite passed **101 tests**, with the same upstream pending-deprecation warning. This includes **87 unit/API/evaluation tests** and **14 integration tests**. Added boundary checks cover Telegram command/contact/audio handling, provider failure without writes, persisted reply retries, safe HTTP errors, bounded responses, invalid file metadata and health startup without constructing the AI runtime.

All **16 bilingual text cases** passed separately in workflow, extraction and fixed-input routing modes using deterministic providers. Import/unused-name checks, Python formatting, both Compose configurations and `git diff --check` passed. These checks preserve the application requirement for Gemma and Laya; they do not add live model or Telegram evidence. The updated reading order is in [architecture](architecture.md#how-to-read-the-code).

## Interpreted-message routing verification

The next contract change removes Gemma intent classification and the normalized request object. Gemma returns a simple-language message plus booking facts for Python. Laya receives only `message` and draft `context`; it chooses the action, including `CLARIFY`. Checkpoints use `lg:v3`, while PostgreSQL data and booking transactions are unchanged. See [model contracts](model-contracts.md) for exact seed-based examples and external server requirements.

The disposable Python 3.11/PostgreSQL 17/Redis 7 suite passed **112 tests**, including **98 unit/API/evaluation tests** and **14 integration tests**, with the same upstream warning. Contract tests verify the actual HTTP payload shape, rejection of Gemma intent/action fields, sanitization of rewritten text, ambiguous-request clarification, numbered booking choices, and full audio transcription before one interpretation/routing/action sequence.

All **16 text cases** passed in offline workflow and extraction modes. All **18 labelled text/audio cases** passed in fixed-input routing mode without invoking Gemma or audio transcription. Those routing results measure the supplied interpretation/context labels, not speech recognition. The corpus now includes human-written interpreted-message labels and no Gemma intent labels; old receipt hashes and schema-version-1 measurements above describe the earlier contract.

## Remaining live evidence

A live Telegram text and audio walkthrough is still open. The local model runs above use fixture services. Larger datasets, fine-tuning and held-out quality comparisons are future work. [Evaluation guide](evaluation.md)

## Cutover

The existing application containers and database were not upgraded. Stop old workers and back up the database before deploying migration `0003_telegram_ownership`. It preserves bookings and verified identity links; rollback after token-column removal requires restoring the matching backup and application. Temporary conversations use a new checkpoint namespace. The disposable verification containers were removed after testing.

# Portfolio cleanup: implementation evidence

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

## Remaining live evidence

The configured host and Compose-resolved Gemma/Laya endpoints were unreachable during verification. Live model comparisons and a Telegram text/audio walkthrough were not performed. The corpus includes two synthesized English/Portuguese speech recordings, but their actual Gemma transcription accuracy remains unmeasured. No Telegram messages were sent by the cleanup.

Use the commands in [evaluation](evaluation.md) with available model endpoints and labelled revisions, then an explicitly authorized synthetic-data test chat. Report per-language/modality samples, errors, transcription quality and task outcomes. Large dataset expansion and fine-tuning remain future work.

## Cutover

The existing application containers and database were not upgraded. Stop old workers and back up the database before deploying migration `0003_telegram_ownership`. It preserves bookings and verified identity links; rollback after token-column removal requires restoring the matching backup and application. Temporary conversations use a new checkpoint namespace. The disposable verification containers were removed after testing.

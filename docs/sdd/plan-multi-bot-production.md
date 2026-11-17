# CAPY multi-bot production implementation plan

## Goal and release criteria

Run many businesses' Telegram bots on one CAPY backend. Share intake, conversation processing, inference, and delivery capacity across bots while keeping each business's data and each chat's turn order intact. Preserve the local Compose walkthrough, English and Portuguese text and voice input, deterministic booking policy, and the owner agenda.

Use 100,000 monthly active customers, 20 incoming turns per second at peak, and a five-second p95 text response as planning targets. Measure p95 from intake receipt to Telegram accepting the final reply. Validate voice latency separately by recording length. Track the daily 99.9% success objective with synthetic journeys and real request outcomes reported separately. Confirm the reminder lateness target with the business before release. Measure lateness from `send_at` to Telegram accepting the reminder. Capacity and availability targets require evidence from staging and production.

## 1. Establish the baseline

- Start from the latest `portifolio` commit and review the working tree. The audit migration and reminder worker cleanup were verified locally. Reconcile those changes explicitly before choosing the next Alembic revision. Run migrations and race tests against disposable databases.
- Add timings at intake, queue claim, transcription, Gemma, Laya, booking transaction, and outbound acceptance. Record the oldest eligible update, pending reminder age, per-bot send backlog, Telegram 429s, database connections, and lock-wait time. Keep message bodies, contact data, and bot secrets out of logs and metrics.
- Run the existing offline evaluations and booking, ownership, reminder, and Redis integration tests. Record a synthetic text and voice run with real models. Use a Telegram stub for load and failure tests. Keep live tests confined to configured test bots.

**Exit gate.** A baseline run shows where turns spend time. Existing booking and privacy guarantees have passing checks. [Evaluation guide](../evaluation.md) · [Runtime architecture](../architecture.md)

## 2. Add a trusted bot registry and split intake

- Use an additive Alembic migration for a registry with Telegram bot ID, business ID, an opaque webhook route ID, enabled status, and secret references. Resolve tokens and webhook secrets from deployment configuration. Backfill the existing `demo_business_id` mapping from a verified bot identity and preserve the current customer links. Bootstrap the same mapping from local configuration for the Compose demo.
- In `app/telegram_worker.py`, pass the resolved bot and business through identity lookup, local date, audio download and catalog, graph invocation, and reply creation. Remove the assumption that one process serves one business. Keep exactly one processing loop per bot until the concurrent claims in phase 4 are ready.
- Split receipt insertion from polling offset updates in `app/channels/telegram_repository.py`. Local `getUpdates` saves each receipt and advances its polling cursor in one transaction. The new FastAPI webhook validates payload size and authenticates the secret header for the resolved bot. Reuse CAPY's private-chat sender checks, then return success after committing the receipt. Record authenticated, identifiable updates of unsupported types as ignored without retaining their message bodies. A duplicate `(bot_id, update_id)` returns success without another turn. A transient database error returns a retryable response.
- Give polling intake and turn processing separate entry points and distinct per-bot locks during this phase. Leave transcription and model calls on the processing path. Define the inbox order using persisted receipt time and Telegram update IDs within a bounded reorder window. Handle a late slot selection by asking the customer to refresh the offer when its context is uncertain.
- Verify two test bots serving distinct businesses with different catalogs and time zones. Replay the same update ID in each bot, interleave voice and text, and test wrong secrets, oversized bodies, unsupported update types, duplicate deliveries, out-of-order arrivals, and database failure before acknowledgment.

**Exit gate.** An incoming update for a second bot can persist while the first bot transcribes audio. Local and webhook intake produce equivalent receipts. [Current Telegram worker](../../app/telegram_worker.py) · [Receipt repository](../../app/channels/telegram_repository.py) · [Telegram webhook API](https://core.telegram.org/bots/api#setwebhook)

## 3. Share delivery for replies and reminders

- Add one PostgreSQL outbound table for replies and real reminders. Store the business, bot, destination, job kind, stable deduplication key, eligibility time, attempts, and delivery outcome. Write the reply and complete its inbox turn in one transaction. Preserve one logical reply per processed update and its ordering within the chat. Keep the current demo inbox readable for rollout verification and preserve its historical entries.
- Keep reminder times in `reminders`. On create, cancel, and reschedule, write the schedule change alongside the booking and preserve the existing transaction and overlap constraint. Add a reminder generation and an explicit authorized bot and destination binding. When the binding is missing or ambiguous, record an inspectable failure. Materialize due reminders in bounded batches using `FOR UPDATE SKIP LOCKED`. Insert the outbox row and record materialization in one transaction, keyed by `(reminder_id, generation)`. Supersede unsent jobs for the old generation when an appointment moves or is canceled. Test the lock order shared by booking writes, claims, and cancellation.
- Run independent sender replicas. Claim in a short transaction with an expiry and claim generation. Authorize dispatch with an atomic transition that validates the reminder generation, booking status, and destination under the same locks used by cancellation and rescheduling. Once authorized, the send can race a later cancellation. Call Telegram outside the transaction and record acceptance or a bounded retry only under the current claim generation. Treat an expired dispatch as ambiguous and apply a bounded, documented retry policy that accounts for possible duplicate delivery. Honor Telegram's full `retry_after` through shared cooldown state.
- Share atomic rate-limit reservations across replicas for the bot and chat. Keep FIFO within a chat, allow an unrelated chat or bot to make progress, and preferentially serve eligible replies while reserving capacity for due reminders. Implement a small delivery adapter around the existing Telegram client. Keep queue claims, retries, and scheduling in their respective services. Add a second adapter when another delivery channel is implemented.
- Verify simultaneous scheduler and sender replicas, cancellation before and after dispatch authorization, rescheduling an already queued reminder, delivery throttling across replicas, and long `retry_after` values. Pause a sender after authorization, expire its claim, and verify it cannot overwrite a newer outcome. Test restart after an ambiguous send and isolated progress for another bot. Compare pending, retrying, active, and delivered job counts before and after reply backfill. Historical demo deliveries should remain visible to the owner without becoming new Telegram sends.

**Exit gate.** Each scheduled generation produces at most one outbound job. The correct bot delivers it. A slow or throttled chat leaves unrelated chats able to send. [Booking service](../../app/booking.py) · [Reminder worker](../../app/reminders.py) · [Telegram limits](https://core.telegram.org/bots/faq#broadcasting-to-users)

## 4. Enable safe conversation concurrency

- Use one active claim per `(business_id, bot_id, chat_id)`. Claim the oldest eligible turn in a short transaction. Allow different chats to run concurrently with bounded per-business capacity and separate text and audio capacity budgets. A later text turn in the same chat waits for its earlier voice turn. Keep inference and transcription outside database transactions.
- Add expiring leases with a monotonically increasing claim generation. Replace bot-wide recovery with expired-claim recovery. Check and lock the current claim in the same PostgreSQL transaction as each domain mutation and reply publication. Define a consistent lock order with existing booking locks. Preserve the update-derived idempotency key and overlap constraint.
- The current Redis saver updates a mutable latest checkpoint pointer. Give attempts private checkpoint writes and load only the last committed checkpoint reference. Publish the completed attempt's reference through the fenced PostgreSQL completion transaction. An expired worker may leave temporary Redis keys that expire, but its state stays unpublished. Treat a missing committed checkpoint as an expired draft and request a fresh slot selection.
- Keep the existing unknown-outcome response during the first rollout. Before enabling automatic recovery of a possibly committed write, persist the exact authorized action and its input before execution. Record the command outcome in the booking transaction. Recover from that outcome or replay the same command with its original key. The current idempotency ledger scopes keys by operation, so rerunning model routing could choose a different operation.
- Test two workers on one chat, independent chats during a slow voice turn, and a worker that resumes after another worker takes over its expired claim. Verify the stale worker cannot save a reply, overwrite the visible checkpoint, or change a booking. Inject a crash after booking commit and before reply publication. Verify one booking effect and coherent recovery.

**Exit gate.** Parallel processing improves independent chat progress while the stale-worker and committed-booking recovery tests pass. Measure business-row lock waits separately from model concurrency. [Redis checkpoint saver](../../app/graph/redis_checkpoint.py) · [Idempotency and recovery](../idempotency.md)

## 5. Roll out and measure production capacity

- Build on a single codebase with separate intake, processing, reminder scheduler, and sender entry points. Keep Compose for local use. In hosted environments run migrations once as a release job and seed demo data only in development. Use reachable Gemma and Laya endpoints, durable PostgreSQL and Redis, bounded database connection pools, and distinct readiness and liveness checks. Test backup restore, process drain, and dependency failover before claiming the availability target.
- Stage one bot, then a second business. Stop each bot's poller before registering its webhook and preserve Telegram's pending updates. Roll back by disabling the webhook and restarting polling with a cursor that preserves any unacknowledged backlog. Omit the cursor when necessary and rely on receipt deduplication. Keep schema changes compatible during the rollback window.
- Stop and drain the legacy reply sender and demo reminder worker before their replacements claim the same backlog. Reconcile pending, retrying, active, and delivered records before activation, then test the reverse cutover with a backlog. Only one delivery path per bot may own ready jobs during either transition.
- Load-test at 1, 5, and 20 turns per second. Use 100 turns per second as an internal stress case. Include a hot business, mixed test bots, slow models, repeated messages from one chat, and a burst of reminders due at the same time. Run a sustained test at the design peak. Collect p95 text response, voice latency by recording duration, oldest eligible work, reminder p95 lateness, Telegram 429s, database saturation, and model utilization. Use a Telegram stub for high-volume runs and controlled live tests for provider behavior.
- Scale from eligible queue age and measured processing capacity. Include warm model capacity for bursts, a cap per business, and backpressure when inference is saturated. Keep one rate budget across reply and reminder senders. Record observed results and update the architecture, database, and operational docs per release.

**Exit gate.** The canary preserves existing booking behavior and can be rolled back. The load report tests the design targets and identifies the limiting resource. [Docker Compose](../../docker-compose.yml) · [Database guide](../database.md)

## 6. Evaluate RabbitMQ when delivery needs it

- Compare the PostgreSQL delivery path against the measured reminder SLA. Consider RabbitMQ when dispatch polling, locks, or write load materially affect booking latency or when independent delivery services need routing. Compare under the same Telegram rate budgets. Record which bottleneck RabbitMQ addresses and how the team will operate it.
- Keep the booking schedule and authoritative outbox in PostgreSQL. Publish ready jobs by stable ID through a relay. Verify the route and use publisher confirms before recording publication. Senders handle possible redelivery, consult the latest job state and rate limits, and acknowledge after persisting the result or scheduled retry. Test a relay crash after publish and before recording it, a sender crash after send and before acknowledgment, and broker unavailability. Keep a rollback path that drains or reconciles in-flight jobs before returning to PostgreSQL dispatch.

**Exit gate.** Adopt the broker only if a measured improvement justifies operating a second durable service. [RabbitMQ publisher confirms and acknowledgements](https://www.rabbitmq.com/docs/confirms)

## Review and verification for every implementation phase

1. Run the focused tests for the changed boundary. Run the disposable PostgreSQL and Redis suite for migrations, ownership, concurrency, and delivery changes. Compare the offline evaluation decisions and write counts for any workflow change.
2. Request an independent **Astra Medium** code review using `gpt-6.2-astra` with medium reasoning. Provide the phase scope, the diff, test evidence, and known failure cases. Ask for concrete findings with severity, file, and line. Review tenant isolation, concurrency and restart safety, migration and rollback, secret handling, and whether each added abstraction has a current purpose.
3. Verify the findings against the code. Fix confirmed issues, rerun affected checks, and request an Astra Medium follow-up on the revised diff. Resolve blocking findings before the phase advances. Record the fixes and test evidence with the commit or pull request.
4. Commit and push the verified phase with any affected migration, documentation, and operational guidance. Keep phase commits small enough to review and roll back independently.

```sh
uv run python -m app.evals.runner --understanding deterministic --routing deterministic
docker compose -p capy-simplification-test -f docker-compose.test.yml run --build --rm checks
docker compose -p capy-simplification-test -f docker-compose.test.yml down
git diff --check
```

# Move Telegram work distribution to RabbitMQ

**Status — implemented for the local demo.** The PostgreSQL worker path supports rollback during the rollout window. Hosted cutover and cleanup follow deployment verification. [Cutover and recovery](../production.md#cutover-and-recovery) The goal is for a developer to follow one message from webhook to reply without first understanding a query that searches across every active chat.

## Follow one message in the proposed design

1. **Receive.** The webhook saves the Telegram update in PostgreSQL. In that same transaction it records that the chat needs processing. It returns HTTP 204 after commit. Telegram can repeat the same update, so keep the unique key on bot and update ID. An ignored update needs no job.
2. **Publish.** A small relay reads due publication records and publishes persistent messages to durable RabbitMQ queues. A publication record is a promise to send work after the database commits. Clear the pending record after the broker confirms acceptance and the route was valid. If the relay dies between confirmation and marking the record, it can publish twice. Consumers must tolerate that.
3. **Process.** A worker receives a signal for one bot and chat. It looks up the earliest unfinished receipt for **that chat** using the existing message-date and update-ID ordering. A signal can safely find no work. Keep the short reorder window and schedule another signal if the head is too early. This lets an audio request establish the available choices before a later “1” selects one. Gemma interprets the message, Laya routes it and the booking service enforces ownership and scheduling rules.
4. **Finish.** In one database transaction, finish the receipt, record the Redis checkpoint reference, save the outgoing reply and request its publication. Schedule the next eligible turn if the chat still has work. Then acknowledge the broker delivery. Acknowledging tells RabbitMQ the worker has taken durable responsibility. A crash after a booking commit but before turn completion still takes the existing interrupted-turn path.
5. **Send.** A sender receives the outgoing record ID, checks its current status and delivery permissions, reserves the send attempt, calls Telegram and records the outcome before acknowledging. Hosted reminders enter this same path when due. The local owner-inbox demo continues to exercise its own reminder destination.

## Broker layout and explicit trade-offs

Use a direct exchange with a fixed number of incoming queues. Compute a stable partition from bot and chat IDs with a documented deterministic hash and route to its queue. Use one active consumer per partition and one unacknowledged message at a time. Start the demo with one partition. Test with at least two. Another chat in the same partition can wait behind slow inference. Partition count changes require a drain and coordinated routing change. Use a separate outgoing queue so inference cannot occupy sender capacity. See [RabbitMQ message ordering](https://www.rabbitmq.com/docs/queues#preserving-message-order) and [consumer and publisher acknowledgements](https://www.rabbitmq.com/docs/confirms).

Keep messages small. An incoming signal identifies a bot and chat, and an outgoing signal identifies an outbox row. Use a unique publication ID for relay bookkeeping. Keep transcripts, contacts, audio, tokens and reply bodies out of broker payloads, logs and failure queues. PostgreSQL owns business records, deduplication, send outcomes and the schedule of future reminders. Redis owns short-lived conversation checkpoints.

Use one combined text/audio consumer initially. A single queue per chat partition gives no per-business fairness guarantee. Preserve the existing business concurrency cap with a bounded reservation when starting a turn until a separate decision changes that contract. When capacity is full, schedule a delayed wake-up and release the partition. Document the removal of dedicated lanes and the change from least-recently-served chat selection before cutover. Measure noisy-business and long-audio cases.

## Implementation sequence

| Stage | Work and acceptance gate |
| --- | --- |
| 1. Capture the baseline | Trace the webhook in `app/main.py` and `app/channels/telegram_intake.py`, then `TelegramTurns.claim`, `process_turn`, `TelegramTurns.finish` and `send_next`. Extend `tests/integration/test_telegram_production.py` with observable ordering, takeover and delivery outcomes. Run `scripts/benchmark_telegram_queue.py` against the existing path and save workload, worker count and latency for comparison. |
| 2. Add the transport | Add one maintained Python AMQP client to the dependency and lock files. Configure broker URL, credentials from environment, health checks and persistent broker storage in the demo and isolated test Compose stacks. Keep offline model evaluations independent. Add an additive Alembic migration for publication ID, target, due time, claim expiry and a due-record index. Exercise publish, confirm, unroutable message, disconnect and restart with a real broker. |
| 3. Migrate incoming turns | Write the receipt and publication from `save_update` on one connection. Build a bounded relay for due records with short claims recoverable after a crash. Split the processing entry point in `app/telegram_service.py` into a broker consumer and a readable turn handler. Replace the global selection in `app/channels/telegram_turns.py` with a chat-scoped lookup and a short transaction to reserve capacity and start the attempt. Keep generation checks around booking writes and checkpoint publication, lease renewal during inference and stale-worker rejection. Exercise this stage with the existing PostgreSQL sender. |
| 4. Migrate outgoing replies and reminders | Extend `TelegramTurns.finish`, registration-time reply reconciliation, and `enqueue_telegram_reminders` in `app/reminders.py` so each new outgoing record gets a publication in the same transaction. Change `app/channels/telegram_delivery.py` to authorize and reserve by outgoing ID. Preserve per-chat send order, bot and chat rate budgets, Telegram retry-after delays, bot enablement and reminder generation checks. For a blocked outgoing ID, persist its next wake-up before acknowledging. Keep expired-send recovery as scheduled maintenance. |
| 5. Switch and remove obsolete paths | Switch the process and send roles independently using the cutover below. After the rollback window, remove the old global claim queries, unused lane options, idle polling for broker consumers and obsolete indexes in a separate migration. Retain due-time scans for the relay and reminder scheduler. Review each lease and index against surviving write guards before removal. Keep webhook registration, stats, retention and graceful shutdown working through the normal CLI. |

Name handlers after the work they do, for example `process_chat_turn` and `deliver_outgoing_message`. Give each new function a docstring covering its purpose and how it achieves it. At transaction boundaries explain why an early acknowledgement or an automatic retry could lose work or duplicate a side effect.

## Failure tests and release gates

| Exercise | Required observation |
| --- | --- |
| Broker offline during webhook or publisher restart after confirm | An accepted receipt survives. The relay catches up and duplicate signals produce one completed turn. |
| Voice followed by “1”, reversed webhook arrival, or two bots sharing a numeric chat ID | Text uses the right offer, late turns follow the refresh rule and bots remain isolated. Another partition progresses while audio is running. |
| Retry before inference, exhausted business capacity or consumer shutdown | The chat head stays ahead of later turns. Delayed work has a durable wake-up. The worker exits cleanly or another consumer resumes. |
| Worker loses its connection mid-inference or crashes immediately after booking | A stale attempt cannot publish a checkpoint or change a booking. The recovered turn produces one safe result with no second booking. |
| Sender crashes after Telegram may have accepted a message | The expired dispatch becomes ambiguous. Redelivery cannot silently send it again. Explicit rejections and rate limits follow the existing bounded retry policy. |
| Booking cancelled or rescheduled while a reminder is queued | Sender checks the current generation and permission before dispatch. Duplicate signals cannot create another reminder send. |
| Malformed broker message or repeatedly failing handler | Bound retries, record a sanitized failure and isolate the message in a dead-letter queue for inspection. Document which IDs are safe to replay. |

Run the full disposable Compose test suite and deterministic bilingual evaluations. Repeat the baseline benchmark with the same stubs, including a burst and slow audio. Report queue depth, oldest due unpublished record, time to reply, redeliveries and ambiguous sends. Gate rollout on zero lost accepted receipts, duplicate booking effects or stale checkpoint writes in recovery tests. Compare latency with the baseline before choosing the deployment partition count.

## Cutover and rollback

1. Apply additive migrations and deploy the broker and relay with publication disabled. Keep the old workers active. Verify broker connectivity, durable queue declarations and database backups.
2. For each stage, pause its old workers and wait for in-flight attempts to finish or enter documented recovery. Switch producers to transactional publication. Backfill unfinished eligible records in bounded batches using stable unique keys. Include receipts accepted during the switch. Reconcile pending rows against pending publications before enabling the relay and consumers.
3. Enable the relay and new consumers for that stage. Keep a single owner for each processing path. Inspect outstanding receipts, unacknowledged deliveries, expired attempts and send outcomes after a broker restart. Rehearse inbound cutover before switching the sender.
4. To roll back, pause publication and new consumers, settle in-flight attempts, and resume old workers from PostgreSQL status. Preserve terminal and ambiguous outcomes. Isolate stale broker messages before a second cutover and rebuild required signals from unfinished database records. Run this rehearsal before removing legacy schema or workers.

## Documentation to ship with the implementation

- **README and source map.** Show the runnable demo command, required services and one reading path from webhook through relay, worker and sender. Update links to actual functions and lines after code settles.
- **Architecture and SVGs.** Update the overview shared by the README and architecture guide. Add labeled sequence diagrams for processing, outgoing delivery and hosted reminders, with one description per arrow and links to implementation lines. Explain publish confirmation, consumer acknowledgement, partition, redelivery and ambiguous send using one booking example.
- **Database, idempotency and security guides.** Update the relationship diagram and describe receipt, publication, conversation and outgoing records, retention, duplicate detection and the booking/turn transaction boundary. Record what is visible to RabbitMQ and how credentials are provided.
- **Deployment and capacity guides.** Update `docs/production.md` and `docs/system-design-100k.md` with worker commands, scaling limits, stats, outage recovery, cutover and rollback. Record measured results with their workload. Keep historical plans labeled and the deprecated production Compose file outside the demo path.
- **Verification.** Start from an empty demo stack, follow the published commands, deliver a text turn, an audio turn and a reminder, and repeat the documented recovery drill. Check source links and render every changed SVG before declaring the migration complete.

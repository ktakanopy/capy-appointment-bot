# Telegram processors and sender

The API commits accepted work to PostgreSQL before attempting a RabbitMQ notification. Five initial processors share one notification queue; each runs one active turn across normal notifications and direct database recovery. One independent sender delivers already-saved replies even while inference is slow or the broker is down. [Architecture overview](codebase-atlas/index.html#architecture-overview) · [Independent progress](codebase-atlas/index.html#independent-progress)

## Start reading the code

| Step | Code | What happens |
| --- | --- | --- |
| Receive | [Webhook](../app/main.py) · [IntakeResult](../app/channels/telegram_intake.py) | Authenticate, deduplicate and commit supported input and initial chat state. Unsupported updates get completed receipts. |
| Notify | [TelegramQueue](../app/channels/telegram_queue.py) | Publish only bot/chat IDs after the session has closed, with one bounded attempt on a reused lifecycle connection. |
| Schedule | [process_pending](../app/telegram_worker.py) | One executor thread shares notification and recovery work; startup recovery runs without connecting to RabbitMQ. |
| Claim | [ChatClaimResult](../app/channels/telegram_turns.py) | PostgreSQL returns claimed, busy, not_ready (with delay), or empty. A hint grants no ownership. |
| Execute | [process_turn](../app/telegram_worker.py) | Renew the claim during slow inference; guarded booking/customer writes reject stale attempts. |
| Save | [TelegramTurns.finish](../app/channels/telegram_turns.py) | Commit completed receipt, optional reply, checkpoint reference and chat release together. |
| Deliver | [run_sender](../app/telegram_sender.py) · [TelegramOutbox](../app/channels/telegram_delivery.py) | Hold the bot's sender lock, claim replies in chat order, pace sends, classify outcomes and maintain history. |

## Tables used by this runtime

| Physical table | What it holds |
| --- | --- |
| `telegram_inbox` | Durable update receipts with status `received`, `processing` or `processed`. The key `(bot_id, update_id)` deduplicates Telegram retries. |
| `telegram_chat_processing_state` | Current chat claim, generation, last processed position and last completed checkpoint reference. |
| `telegram_outbox` | Reply content, attempt, dispatch lease, retry/cooldown and terminal result. |

Redis stores the checkpoint contents. Turn completion clears the raw intake payload. Terminal send results clear the reply content. The sender prunes old records in bounded batches. [Schema](database.md)

## Full call sequence

1. `python -m app.telegram_service process` starts one processor; Compose launches five replicas. `python -m app.telegram_service send` starts the independent singleton sender.
2. The authenticated webhook commits an inbox record, initial chat coordination if needed, and the existing 0.3-second readiness window. An `IntakeResult` carries only a validated optional `ChatReady`.
3. The webhook attempts publication for at most one second, including connection/confirm wait. A failure is logged and still returns HTTP 204. Duplicate or unsupported updates do not need new hints.
4. RabbitMQ distributes hints to available consumers, manually acknowledged with prefetch one. PostgreSQL chooses the earliest unfinished stored message and grants a lease. Its existing late-update policy remains; RabbitMQ does not provide chronological Telegram ordering.
5. For readiness within 0.5 seconds, the processor waits once asynchronously, holding no transaction, row lock or claim, then rechecks PostgreSQL. Busy, empty or longer-deferred hints are acknowledged with durable work intact.
6. The processor forks the last completed Redis draft, runs Gemma/Laya and deterministic scheduling under database ownership checks, then commits turn completion. Broker acknowledgment follows completion or durable pre-execution deferral. A reconnect does not revoke the database claim or transfer an old delivery to a new channel.
7. A free processor also claims directly from PostgreSQL on startup and every 1–1.1 seconds, scheduled from the end of each recovery opportunity. Each opportunity takes at most one turn. Busy/duplicate hints do not postpone recovery; recovery backlog does not monopolize notification handling. [Lost notification](codebase-atlas/index.html#notification-recovery)
8. The sender reads replies independently. It retains roughly one send per second, persisted 429 cooldown and retry_after, at most five attempts, and ambiguous outcomes for uncertain sends or expired dispatches. A second sender cannot enter the loop; loss of its direct/session-pooled lock connection stops it.
9. SIGTERM stops new turns and drains the active synchronous turn with lease renewal. Cancelling an executor await also drains it; it cannot free a second execution slot. Forced termination relies on lease expiry and conservative interrupted-turn handling.

Booking commit, turn completion and Telegram acceptance are distinct boundaries. Redis contents and RabbitMQ operations are outside PostgreSQL transactions. If a booking commits before a crash, recovery preserves it and asks the customer to check appointments rather than automatically replaying the business action. A lost broker acknowledgment can redeliver a harmless hint without duplicating the completed turn or saved reply. [Transaction examples](codebase-atlas/index.html#notification-recovery)

## Follow a message in the logs

Use `CAPY_LOG_LEVEL=DEBUG` for per-update detail. Application logs exclude message bodies, contact values, tokens and broker URLs.

| Event | Meaning |
| --- | --- |
| `telegram.notification.published` / `publish_failed` | The post-commit hint was confirmed or failed within its deadline. |
| `telegram.broker.connected` / `unavailable` / `disconnected` | Transport lifecycle, independently of database recovery. |
| `telegram.turn.claim_result` | Work source and explicit claim outcome. |
| `telegram.turn.started` | Inbox wait measured before processing; `telegram.turn.finished` separates wait, processing and elapsed time. |
| `telegram.turn.claim_lost` | Ownership was superseded or expired; guarded writes remain fenced. |
| `booking.saved` / `telegram.turn.saved` | Separate booking and turn-completion commits. |
| `telegram.outbox.finished` | Wait measured before sending, provider result, retry delay and total elapsed time. |

Queue depth counts notifications rather than unfinished turns. Monitor PostgreSQL backlog and oldest-work age, plus outbox age. [Startup, changeover, monitoring and benchmark commands](production.md)

## Run the example without Telegram

This example uses scripted model outputs and simulated booking services to exercise the local graph. The Telegram demo above lets you observe the webhook, chat claim, and delivery.

```bash
CAPY_LOG_LEVEL=DEBUG uv run --locked python -m app.evals.runner \
  --understanding deterministic --routing deterministic
```

![Four messages in en-booking. V1 supplies tomorrow and gets an offer. V2 chooses 1 and receives a consent question. V3 approves, allowing a simulated booking write.](assets/telegram-worker-offline.svg)

**V1** supplies the date needed to offer times. **V2** selects the first time in turn three; the reply has intent `request_booking_confirmation` and makes no booking write. **V3** sends “Yes, book it.” in turn four; only this approval allows the create operation. Find `eval.turn.started` with `number: 3`, then follow turn four to compare selection with creation. [Test conversation](../evals/data/smoke-text.jsonl#L1) · [Set up the local environment](../README.md#run-the-offline-example)

| Event | What to check in `en-booking` |
| --- | --- |
| `workflow.draft.operation` | `reason: time_choice_for_new_booking` identifies a selection for a new booking. |
| `workflow.route.booking_choice` | `reason: action_does_not_target_existing_booking` indicates that the choice refers to offered times. |
| `workflow.route.rule_applied` and `workflow.route.selected` | `reason: choose_from_offered_times` and `route: select` lead to the node that reads the offer. |
| `workflow.slot.matched` and `workflow.reply.prepared` | Turn three matches `option_number: 1`, then returns intent `request_booking_confirmation` without a booking write. |
| `workflow.action.executing` | Turn four reaches `operation: create` after explicit approval and calls the simulated booking service. |
| `workflow.turn.completed` | `has_booking: True` means the graph returned a booking. The runner prints `PASS  en-booking`. |

If routing fails during the demo, the `stage` field of `workflow.route.failed` shows where the error occurred. [Routing code](../app/graph/steps.py#L266)

For database-backed consent behavior, read the [booking journey test](../tests/integration/test_workflow_booking_journey.py). For worker checkpoint recovery, read `test_completed_checkpoint_carries_slot_offer_into_next_attempt`; it uses a stub workflow to isolate checkpoint publication, expiry, and interrupted-turn recovery rather than exercising the real consent logic. [Checkpoint integration test](../tests/integration/test_telegram_production.py#L159-L221)

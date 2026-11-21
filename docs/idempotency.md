# Idempotency and recovery

Telegram turns use a stable key derived from bot ID and update ID. Booking request hashes include the trusted customer identity and requested operation. PostgreSQL scopes idempotency records by business and operation; reusing a key for different input returns a conflict. Replays recheck ownership.

Booking writes lock the business row to serialize the retry boundary, then validate the customer or lock the owned booking. Slot validation locks the professional and rechecks service assignment, hours, notice/horizon, blocks and active conflicts. An exclusion constraint independently rejects overlapping occupied intervals, including buffers.

The booking change and retry result commit together. A failed move preserves the old appointment.

Webhook intake commits the receipt before publishing an identifier notification and returning HTTP 204. It deduplicates by bot ID and update ID. Publication is best effort within one second. A failed or lost hint leaves accepted work in PostgreSQL; an available processor checks recovery on startup and every 1–1.1 seconds between turns. RabbitMQ acknowledgment settles a hint, not an inbox turn. Duplicate, busy and deferred hints can be acknowledged without completing durable work. [Lost notification example](codebase-atlas/index.html#notification-recovery).

A claim generation fences booking writes and publication of replies and Redis checkpoint references. When an interrupted claim expires, its replacement asks the customer to check their appointments. It discards the draft and preserves every committed booking. Automatic replay after an uncertain booking result remains gated on durable action commands.

The shared outbox stores one logical reply per update. The independent sender throttles sends and records Telegram cooldowns in the outbox. It retries definitive transient errors and marks lost responses or expired dispatches ambiguous for inspection. [Classify the send outcome](../app/telegram_sender.py#L15)

## Interrupted work

The webhook confirms receipt after PostgreSQL commits the update. After a restart,
processors recover pending messages directly from PostgreSQL; the independent sender selects replies from the outbox. Before a
booking or reply write, PostgreSQL checks whether the attempt still owns the chat.

Booking commit and turn completion use separate transactions. If an attempt is
interrupted, CAPY asks the customer to check their appointments. A send whose
lease expires becomes ambiguous because Telegram may already have accepted it.
[Turn claim](../app/channels/telegram_turns.py) · [Send recovery](../app/channels/telegram_delivery.py)

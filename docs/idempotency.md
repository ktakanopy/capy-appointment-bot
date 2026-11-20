# Idempotency and recovery

Telegram turns use a stable key derived from bot ID and update ID. Booking request hashes include the trusted customer identity and requested operation. PostgreSQL scopes idempotency records by business and operation; reusing a key for different input returns a conflict. Replays recheck ownership.

Booking writes lock the business row to serialize the retry boundary, then validate the customer or lock the owned booking. Slot validation locks the professional and rechecks service assignment, hours, notice/horizon, blocks and active conflicts. An exclusion constraint independently rejects overlapping occupied intervals, including buffers.

Creation and reminder insertion commit together. Cancellation cancels pending reminders in the same transaction. Rescheduling changes the booking and reminder atomically; a failed move preserves the old appointment.

Webhook intake saves the receipt before acknowledging the update. It deduplicates by bot ID and update ID.

A claim generation fences booking writes and publication of replies and Redis checkpoint references. When an interrupted claim expires, its replacement asks the customer to check their appointments. It discards the draft and preserves every committed booking. Automatic replay after an uncertain booking result remains gated on durable action commands.

The shared outbox stores one logical reply per update and one send per reminder generation. A reminder moved or canceled before dispatch authorization is skipped. A later cancellation can race an authorized send. Sender replicas share bot and chat rate limits. They retry definitive transient errors and mark lost responses or expired dispatches ambiguous for inspection. [Delivery worker](../app/channels/telegram_delivery.py)

## RabbitMQ redelivery

A webhook or scheduler saves its work and its publication in the same PostgreSQL transaction. A relay can publish twice if it crashes after broker confirmation. A consumer that sees an already finished receipt or send can acknowledge the repeat. When there is more work for the chat, the same wake-up may process its next eligible turn. PostgreSQL records and checks ownership of each attempt.

The worker acknowledges after durable completion or after saving a replacement wake-up. Booking commit and turn completion use separate transactions. Recovery from an interrupted attempt reports that the outcome is unknown and directs the customer to check their appointments. A redelivered send observes the dispatch status. An expired dispatch becomes ambiguous through maintenance. [Relay storage](../app/channels/telegram_publications.py) · [Consumer](../app/channels/telegram_broker.py#L105)

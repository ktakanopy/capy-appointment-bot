# Idempotency and recovery

Telegram turns use a stable key derived from bot ID and update ID. Booking request hashes include the trusted customer identity and requested operation. PostgreSQL scopes idempotency records by business and operation; reusing a key for different input returns a conflict. Replays recheck ownership.

Booking writes lock the business row to serialize the retry boundary, then validate the customer or lock the owned booking. Slot validation locks the professional and rechecks service assignment, hours, notice/horizon, blocks and active conflicts. An exclusion constraint independently rejects overlapping occupied intervals, including buffers.

Creation and reminder insertion commit together. Cancellation cancels pending reminders in the same transaction. Rescheduling changes the booking and reminder atomically; a failed move preserves the old appointment.

Webhook intake saves the receipt before acknowledging the update. It deduplicates by bot ID and update ID.

A claim generation fences booking writes and publication of replies and Redis checkpoint references. When an interrupted claim expires, its replacement asks the customer to check their appointments. It discards the draft and preserves every committed booking. Automatic replay after an uncertain booking result remains gated on durable action commands.

The shared outbox stores one logical reply per update and one send per reminder generation. A reminder moved or canceled before dispatch authorization is skipped. A later cancellation can race an authorized send. Sender replicas share bot and chat rate limits. They retry definitive transient errors and mark lost responses or expired dispatches ambiguous for inspection. [Delivery worker](../app/channels/telegram_delivery.py)

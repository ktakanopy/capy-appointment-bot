# Idempotency and recovery

Telegram turns use a stable key derived from bot ID and update ID. Booking request hashes include the trusted customer identity and requested operation. PostgreSQL scopes idempotency records by business and operation; reusing a key for different input returns a conflict. Replays recheck ownership.

Booking writes lock the business row to serialize the retry boundary, then validate the customer or lock the owned booking. Slot validation locks the professional and rechecks service assignment, hours, notice/horizon, blocks and active conflicts. An exclusion constraint independently rejects overlapping occupied intervals, including buffers.

Creation and reminder insertion commit together. Cancellation cancels pending reminders in the same transaction. Rescheduling changes the booking and reminder atomically; a failed move preserves the old appointment.

Telegram intake stores each update once with the polling offset. A claimed turn becomes a saved reply before delivery. Delivery retries use that reply without rerunning the graph. On restart, interrupted processing is marked uncertain and asks the customer to inspect their appointments. An external send timeout can still produce a duplicate reply; business idempotency prevents a duplicate booking.

Reminder delivery is simulated in a database inbox with a unique reminder ID. This makes the demo delivery transactional. Connecting a real external sender would require reviewing that delivery boundary separately.

# Database

PostgreSQL stores the catalog, working schedules, customers, bookings, reminders and Telegram update receipts. Redis holds the temporary conversation draft, offered slots and conversation history. The schema below reflects the [Alembic migrations](../alembic/versions).

Migration `0004_remove_audit_events` removes the unused `audit_events` table. If the table contains records, the migration stops and leaves them in place for review. Booking write results are stored in `idempotency_records`. [Audit table migration](../alembic/versions/0004_remove_audit_events.py)

## Tables and relationships

![Core PostgreSQL relationships for availability, bookings, the local reminder demo and Telegram intake.](assets/database-relationships.svg)

The diagram covers the scheduling tables. The broker publication table references either the conversation or its outgoing job.

```mermaid
erDiagram
    telegram_bots ||--o{ telegram_conversations : owns
    telegram_conversations ||--o{ telegram_updates : groups
    telegram_conversations |o--o| telegram_publications : wakes
    telegram_outbox |o--o| telegram_publications : wakes
    reminders |o--o{ telegram_outbox : stages
```

PostgreSQL enforces the publication relationships. Updates are grouped into conversations by bot and chat IDs.

| Table | Role | Relationships and key constraints |
| --- | --- | --- |
| `businesses` | Time zone, booking notice, booking horizon and reminder lead time | One business can own many rows in each business-scoped table. |
| `professionals` | Staff who can take appointments | Referenced by assignments, availability rules, blocks and bookings. |
| `services` | Duration, buffers, price and description | Referenced by assignments and bookings. |
| `professional_services` | Services each professional can offer | Joins `professionals` and `services`. The pair `(professional_id, service_id)` is the primary key. |
| `weekly_availability_rules` | Recurring local working hours | Each rule references one professional. |
| `blocked_periods` | Explicit intervals when a professional is unavailable | Each block references one professional. |
| `customers` | Customer records with optional phone and email | A customer can have many bookings. Telegram ownership uses an identity link. |
| `bookings` | Appointment times, occupied interval including buffers, and status | Each booking references one customer, service and professional. An exclusion constraint prevents overlapping occupied intervals for the same professional when bookings are scheduled or confirmed. |
| `reminders` | Reminder schedule, generation, and delivery state | Each reminder references a booking. A booking has at most one pending reminder. A reschedule supersedes its queued generation. |
| `reminder_inbox` | Simulated reminder deliveries | Each entry references one reminder and one booking. The unique `reminder_id` allows at most one inbox entry per reminder. |
| `idempotency_records` | Request hash and saved result for each booking write key | The primary key is `(business_id, operation, idempotency_key)`. The app stores the booking ID in `resource_id` as plain text. |
| `telegram_identities` | Links numeric Telegram senders to customer records | The primary key is `(business_id, bot_id, telegram_user_id)`. `customer_id` is optional and unique within a business and bot when present. |
| `telegram_bots` | Trusted mapping from Telegram bot to business | Maps each bot to one business, a webhook route, and environment variable references for credentials. |
| `telegram_updates` | Incoming update receipts | The primary key is `(bot_id, update_id)`. Processed message bodies are cleared. Legacy ready replies migrate to the outbox. |
| `telegram_poll_offsets` | Historical Telegram cursor | One row per `bot_id`. The table is retained for migration compatibility after switching to webhook intake. |
| `telegram_conversations` | One active turn and published checkpoint per chat | Each row belongs to a registered bot and its business. A claim generation authorizes booking writes and state publication. |
| `telegram_outbox` | Replies and due hosted reminders | One reply per bot and update. One send job per reminder and generation. Dispatch status records the delivery outcome. |
| `telegram_publications` | Pending broker wake-ups | References one conversation or one outgoing row. The unique target coalesces pending publications. The relay claims due records briefly and deletes them after confirmation. |
| `telegram_bot_delivery` | Shared per-bot rate and priority state | One row per registered bot controls when its next send is eligible. |
| `telegram_chat_delivery` | Shared per-chat rate state | One row per registered bot and destination chat controls its next send. |

## Booking and reminder workflow

![Booking creation data flow. The webhook saves the update before acknowledging it. A worker resolves the customer and offers available slots. When the customer selects a slot, a transaction checks the retry key and revalidates the slot before committing the booking and pending reminder together. Reply delivery and reminder delivery run separately after the commit.](assets/database-workflow.svg)

1. **Receive and resolve.** The webhook saves the update before responding. The worker claims the chat and resolves its customer's identity. [Intake](../app/channels/telegram_intake.py) · [Turn claims](../app/channels/telegram_turns.py)
2. **Read availability.** The app calculates slots from eligible professionals, working hours, blocks and active bookings. It applies service duration and buffers, minimum notice and the booking horizon. Offered slots are temporary references held in Redis. Appointment lookup reads the linked customer’s future scheduled and confirmed bookings. [Availability service](../app/scheduling.py) · [PostgreSQL repository](../app/repositories.py)
3. **Commit a write.** The booking service locks the business row and checks the idempotency key and request hash. New bookings and moves also lock the professional and revalidate the requested slot. PostgreSQL rejects overlapping active occupied intervals. A rejected write rolls back the transaction. [Booking service](../app/booking.py) · [Idempotency and recovery](idempotency.md)
4. **Save the reply and queue reminders.** Completing the turn saves its reply in the outbox. The hosted scheduler locks due reminders with `FOR UPDATE SKIP LOCKED` and queues the current generation. Sender replicas authorize each send before calling Telegram. The local demo writes to its owner inbox. [Outbound delivery](../app/channels/telegram_delivery.py) · [Reminders](../app/reminders.py)

| Booking write | Reminder effect in the same transaction |
| --- | --- |
| Create | Insert a pending reminder timed from the appointment start and the business’s reminder lead time. |
| Confirm | Keep the existing reminder schedule. |
| Cancel | Cancel the pending reminder, if present. |
| Reschedule | Move the pending reminder to the new time, or insert one if there is no pending reminder. |

See the architecture page’s [booking sequence diagrams](architecture.md#booking-sequence-diagrams) and [reminder delivery sequence](architecture.md#deliver-an-appointment-reminder) for the function-by-function walkthroughs.

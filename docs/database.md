# Database

PostgreSQL stores the massage catalog, working hours, customers, bookings, and Telegram work. Redis stores the temporary conversation draft, offered slots, and history. Alembic defines the schema and SQLAlchemy maps its runtime columns. [Migrations](../alembic/versions) · [Mappings](../app/db_models.py)

## Business tables

| Table | Purpose |
| --- | --- |
| `businesses` | Studio name, timezone, booking notice, and booking horizon. |
| `massagistas` | The professional who takes appointments. A business has one massagista. |
| `services` | One row per massage type and duration, with price and buffers. PostgreSQL generates the name from the type and minutes. |
| `weekly_availability_rules` | Recurring local work intervals. A day can have multiple intervals. |
| `blocked_periods` | Dates and times when the professional is unavailable. |
| `customers` | Customer records linked to verified Telegram identities. |
| `bookings` | Customer, service, actual appointment times, occupied interval including buffers, and status. |
| `idempotency_records` | Request hash and result for each booking write key. |

A unique index prevents duplicate service type and duration combinations. All services share the massagista's calendar. The booking stores its interval at creation, so later catalog changes do not change an appointment's recorded time. PostgreSQL excludes overlapping occupied intervals for scheduled and confirmed bookings.

## Telegram work

`telegram_bots` maps the configured bot to its studio and credentials. `telegram_identities` links a Telegram sender to a customer. `telegram_updates` records incoming updates, and `telegram_conversations` coordinates each chat and its checkpoint. `telegram_outbox` stores replies until delivery. `telegram_publications` coordinates broker wake-ups, and the bot and chat delivery tables hold rate limits. [Telegram intake](../app/channels/telegram_intake.py) · [Sender](../app/channels/telegram_delivery.py)

## Booking transaction

1. The webhook saves an update, and the worker resolves the customer.
2. Availability uses the selected duration and buffers with the professional's hours, blocks, and active bookings. The customer selects a slot and approves the displayed appointment.
3. The booking transaction checks the retry key and customer, locks the professional, and validates the slot again. The database prevents overlapping bookings. The booking and retry result commit together.
4. The worker saves a reply in the outbox for the sender. The owner can inspect upcoming appointments with `python -m app.cli`.

[Availability](../app/scheduling.py) · [Booking service](../app/booking.py) · [Reply delivery](../app/channels/telegram_delivery.py)

## Start with a fresh database

Migration `0007_massage_studio` requires an empty business database. It refuses an upgrade when business data exists. Create a separate empty database for the massage demo and retain the previous database with its matching application for rollback. The seed is repeatable and supplies illustrative services and prices. See the [demo instructions](../README.md#try-the-telegram-voice-demo).

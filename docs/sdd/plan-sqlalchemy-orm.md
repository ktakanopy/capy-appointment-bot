# Migrate CAPY’s PostgreSQL access to SQLAlchemy ORM

**Status — implemented.** Start with a catalog lookup that a new developer can follow from Python to PostgreSQL. Then migrate booking and Telegram operations one transaction at a time. The finished application should read in terms of finding available services, reserving a chat and queuing a reply. A reader should also be able to see exactly when a write commits and which conditions allow it.

## Two examples to guide implementation

**Read a service.** Today [PostgresRepository.service](../../app/repositories.py) reads a row and converts it into the public Pydantic `Service`. Map the database table to a class such as `ServiceRow`. Express the lookup in SQLAlchemy 2.x as follows:

```python
statement = select(ServiceRow).where(
    ServiceRow.business_id == business_id,
    ServiceRow.id == service_id,
    ServiceRow.active.is_(True),
)
service = session.scalar(statement)
```

The business filter protects tenant isolation. Convert the result to the existing public type while the session is open and return `None` for a missing or inactive service. Use this example in onboarding to explain the difference between a stored row and the data the application returns.

**Renew a worker’s lease.** In [TelegramTurns.renew](../../app/channels/telegram_turns.py), a worker may extend its time only while it owns the same attempt. Use a single ORM-enabled update against the mapped class. A read followed by assigning an attribute would leave a gap where another worker could take over.

```python
statement = (
    update(TelegramConversation)
    .where(
        TelegramConversation.bot_id == claim.bot.bot_id,
        TelegramConversation.chat_id == claim.row["chat_id"],
        TelegramConversation.active_update_id == claim.row["update_id"],
        TelegramConversation.generation == claim.generation,
        TelegramConversation.lease_until > func.clock_timestamp(),
    )
    .values(lease_until=func.clock_timestamp() + timedelta(seconds=LEASE_SECONDS))
    .returning(TelegramConversation.generation)
)
return session.scalar(statement) is not None
```

This example assumes the caller owns the transaction. PostgreSQL checks every condition as part of the write. Keep the existing regression test where an expired worker tries to renew its lease.

## Implementation order

| Stage | Change | Evidence needed before proceeding |
| --- | --- | --- |
| 1. Establish the baseline | Run the disposable integration suite. Inventory database entry points, their transaction owners, return types, PostgreSQL error handling and supported worker transports. Use Alembic revisions through `0006_telegram_publications` as the starting schema and check for later revisions when implementing. | Record baseline results. Trace a catalog read, a booking and a Telegram reply from their normal entry points. |
| 2. Introduce the ORM with the catalog | In [app/database.py](../../app/database.py), introduce one reusable SQLAlchemy engine per database configuration, using the existing Psycopg 3 driver. A session is a short-lived object for one unit of database work. Start one per operation, close it on exit, and use a transaction context for writes. Add typed declarative mappings alongside the first migrated queries. Convert catalog, business and service reads in [app/repositories.py](../../app/repositories.py). | Both supported database URL spellings work. Preserve `CAPY_DB_POOL_MAX=0` with `NullPool`, the configured cap with bounded pooling and zero overflow, timeouts and shutdown cleanup. Avoid stacking pools and account for the combined connection budget while both paths exist. Catalog ordering, active filters and missing records match the baseline. |
| 3. Migrate booking and scheduling together | Migrate the remaining repository reads, [slot validation](../../app/scheduling.py) and [booking operations](../../app/booking.py). Include the worker ownership guard in this slice wherever booking invokes it. Pass the same session through ownership, retry lookup, slot validation, booking mutation, reminder and retry-result writes. | Simultaneous overlapping bookings yield one winner. A repeated request returns the original result. A failed reschedule preserves both the old booking and reminder. Map PostgreSQL exclusion violations through SQLAlchemy’s wrapped driver error to the existing domain error. Other integrity errors keep their own meaning. |
| 4. Migrate Telegram intake and turns | Convert bot registration, identity, webhook receipts, conversations and their publication writes. Migrate [claim_chat, renew and finish](../../app/channels/telegram_turns.py) with [publication scheduling](../../app/channels/telegram_publications.py). Keep the session-level registration lock on a dedicated connection, with explicit release or physical close. Preserve the transaction-level quota lock inside the claim. | Webhook duplicates create one receipt. An old worker cannot change a booking or publish a checkpoint. The reply, completed receipt, checkpoint reference and next wake-up commit together. Test both RabbitMQ and the supported PostgreSQL fallback. |
| 5. Migrate outgoing delivery and supporting commands | Convert [delivery](../../app/channels/telegram_delivery.py), [reminders](../../app/reminders.py), relay database access, owner reads, seed, readiness, and worker maintenance, backfill and stats. Check webhook, worker and relay error handlers for SQLAlchemy exceptions. Keep the database transaction that reserves delivery separate from the network call to Telegram. | Rate limits apply across workers, cancelled reminders stay cancelled, duplicate signals cause one send, and a crash after a possible send retains the ambiguous outcome. Broker confirmation cannot erase a newer wake-up. Seed remains repeatable and CLI output stays compatible. |
| 6. Finish the switch | Retire superseded runtime connection helpers and duplicate queries once all callers have migrated. Update benchmark code that calls changed internals. Review remaining SQL individually. Historical Alembic scripts and independent SQL assertions in integration tests are valid. | The complete suite passes against a migrated PostgreSQL database. Exercise demo startup and each supported worker role. Document the few operations that still need handwritten SQL and why. |

Each stage should be a reviewable change that can be reverted against the same schema. Convert all collaborators sharing a transaction together. During the transition a legacy path may use its own connection for a separate operation. Opening a second connection inside a booking or turn transaction would break rollback guarantees.

## Readability and schema decisions

- Keep persistence classes separate from the existing [Pydantic contracts](../../app/models.py). Use SQLAlchemy’s `DeclarativeBase`, `Mapped` and `mapped_column`. Introduce relationships where a caller uses them. For lists such as an owner’s agenda, select or eagerly load the required data in bounded queries. Return plain application data before closing the session.
- Keep transaction ownership visible in the top-level operation. Helpers take a session and perform one named task. Use short docstrings to explain the rule each helper enforces and why it matters. Never share a session between the heartbeat thread and the thread running inference.
- Preserve composite keys, business-scoped foreign keys, server defaults, time zones, JSONB, exclusion constraints, partial indexes and the publication table’s `NULLS NOT DISTINCT` uniqueness. Alembic owns database changes. Keep partial model metadata out of autogeneration until all relevant objects have been mapped or intentionally excluded, including historical tables. An ORM conversion alone requires no schema migration.
- Use mapped objects for ordinary reads and writes. For atomic updates, upserts, and row locks, use SQLAlchemy statements against those same mappings. Preserve PostgreSQL lock strength, locked tables, lock order, `SKIP LOCKED` and database time. If translating the legacy global queue query makes it harder to review, keep one named, parameterized SQL statement for that fallback. Retire the transport only in a separate compatibility decision.
- Keep SQL parameters and mapped-object contents out of logs and errors that reach users or traces. Configure the engine to hide parameters, leave SQL echo disabled and verify existing redaction expectations. Redis checkpoints, RabbitMQ messages and the Gemma/Laya contracts are outside this persistence migration.

## Verification and documentation

Run the following baseline and release checks from the repository root:

```sh
docker compose -f docker-compose.test.yml run --build --rm checks
uv run python -m app.evals.runner --understanding deterministic --routing deterministic
```

Use the existing [booking lifecycle](../../tests/integration/test_postgres_booking_lifecycle.py), [Telegram ownership](../../tests/integration/test_telegram_ownership.py), [worker concurrency](../../tests/integration/test_telegram_production.py), [broker recovery](../../tests/integration/test_telegram_broker.py), [reminder](../../tests/integration/test_postgres_reminders.py) and [migration preservation](../../tests/integration/test_migration_preservation.py) tests as release gates. Keep fixture setup and assertions independent of the new mappings where they validate persisted outcomes. Add targeted coverage for gaps such as a lock leaking through the pool or an ORM exception exposing bound values. Compare query counts on catalog and agenda reads across small and larger result sets to catch a query per returned row.

Update the [source map](../source-map.md) with a reading order from repository method to mapping to transaction test. Update the [database guide](../database.md) with the actual mapping locations and the [architecture guide](../architecture.md) with the booking and Telegram transaction boundaries. Refresh affected function references and SVG labels when code moves. In the README, link to one worked query once the ORM version runs through the normal demo.

## Implementation verification

The disposable Compose suite passed 134 tests, and all 16 deterministic bilingual evaluations passed. With bounded pooling enabled, the integration, API and reminder query tests passed 37 checks. Fresh databases also passed the owner CLI and queue stats walkthrough. Each queue transport completed eight synthetic updates at four updates per second with ten idle chats and stubbed inference and delivery.

Availability used six SQL statements for one or two professionals. The catalog used three for one or eleven services, and the agenda used one for zero or ten bookings.

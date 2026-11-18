# CAPY system design study board

This document describes an earlier design or implementation phase. See the architecture guide for the current webhook intake and worker flow. [Current Telegram architecture](../architecture.md#how-the-telegram-worker-works)

Open `capy-system-design.excalidraw` in Excalidraw using **Open**, or drag the file onto the canvas. This is an editable scene with native text, shapes and arrows, not a screenshot. The SVG is a matching reading copy. Read the numbered sections from top to bottom and zoom into each section.

The reference image informed the requirements, architecture and sequence layout. This board adds capacity calculations, the current schema, table decisions, concrete code-review findings, and study questions. Its language is English to match this discussion.

## What is current versus proposed?

- Blue identifies current facts and data structures.
- Green identifies useful safeguards to keep or proposed directions; the text distinguishes them.
- Yellow identifies assumptions, qualifications and trade-offs.
- Red identifies risks to address before scaling.

The review describes the source in this working tree on November 12, 2026. It did not inspect production data, execute database migrations, or benchmark traffic. The application has not been modified by this study-board task. See [the longer design](../system-design-100k.md) for the deployment proposal.

## Schema verdict

There are **15 application tables** after migrations 0001–0003, excluding Alembic's own version table. Most are justified. Eight model the domain: businesses, professionals, services, professional-service assignments, recurring hours, dated blocks, customers, and bookings.

The strongest simplification candidates are:

1. **audit_events:** no application reader or writer was found. Either implement a real audit feature or remove it after checking existing data and external consumers.
2. **reminder_inbox:** this is a useful demo/test delivery destination. Replace it when implementing real Telegram reminders; a production outbox can take its place, leaving the count unchanged.
3. **telegram_poll_offsets:** remove only after polling is retired and the webhook rollback window has passed.

Do not remove idempotency records because Telegram updates already deduplicate delivery. One protects a transport update; the other protects a business operation. Do not merge weekly hours and blocked periods merely to save a table: recurring rules and dated exceptions have different semantics.

Removing audit_events and retiring polling offsets takes 15 to 13. Replacing reminder_inbox with an outbox still leaves 13. Durable ownership, command recovery or tenant configuration may introduce new tables later. A lower count is not inherently a better design.

## Complete relationship reference

These describe the current migrations, not proposed composite tenant constraints. `0..1` means optional; `N` means many. All `business_id` fields are declared references to `businesses.id`.

| Parent | Child / field | Cardinality and qualification |
| --- | --- | --- |
| businesses | professionals.business_id | 1:N |
| businesses | services.business_id | 1:N |
| businesses | professional_services.business_id | 1:N |
| businesses | weekly_availability_rules.business_id | 1:N |
| businesses | blocked_periods.business_id | 1:N |
| businesses | customers.business_id | 1:N |
| businesses | bookings.business_id | 1:N |
| businesses | reminders.business_id | 1:N |
| businesses | reminder_inbox.business_id | 1:N |
| businesses | idempotency_records.business_id | 1:N |
| businesses | audit_events.business_id | 1:N |
| businesses | telegram_identities.business_id | 1:N |
| businesses | telegram_updates.business_id | 1:N |
| professionals | professional_services.professional_id | 1:N; composite PK with service_id |
| services | professional_services.service_id | 1:N; together these implement professionals N:M services |
| professionals | weekly_availability_rules.professional_id | 1:N |
| professionals | blocked_periods.professional_id | 1:N |
| professionals | bookings.professional_id | 1:N; active occupied ranges cannot overlap |
| services | bookings.service_id | 1:N |
| customers | bookings.customer_id | 1:N |
| customers | telegram_identities.customer_id | Identity links to 0..1 customer; at most one identity per business/bot/customer; customer can link across bots |
| bookings | reminders.booking_id | 1:N historically; at most one pending reminder |
| reminders | reminder_inbox.reminder_id | 1:0..1 because reminder_id is unique |
| bookings | reminder_inbox.booking_id | 1:N; independently stored FK |
| bookings | audit_events.booking_id | 1:N; nullable, cleared when booking is deleted |
| bookings | idempotency_records.resource_id | Logical application reference only; no declared FK |
| telegram_identities | telegram_updates sender fields | Logical association; no declared identity FK |
| Bot configuration | telegram_poll_offsets.bot_id | No bot registry table or FK in the current schema |

## Findings and evidence

| Priority | Finding | Evidence | Suggested next step |
| --- | --- | --- | --- |
| Before parallel workers | One worker per bot and sequential processing | `app/telegram_worker.py`, `run` and `_process_pending_updates`; `app/channels/telegram_repository.py`, `acquire_worker_lock` | Separate durable ingress, turn workers and senders; preserve per-conversation ordering |
| Before parallel senders | One delayed reply blocks unrelated chats | `app/channels/telegram_repository.py`, `ready_replies` | Per-chat ordering and atomic delivery claims |
| Integrity hardening | Independent FKs do not enforce same-tenant links | Migrations 0001 and 0002 | Composite FKs such as `(customer_id, business_id)` plus corresponding parent uniqueness; clean existing rows first |
| Before concurrent recovery | Bot-wide recovery and unfenced checkpoint writes | `recover_interrupted`; `RedisCheckpointSaver.put` / `put_writes` | Durable leases, stale-owner fencing, atomic checkpoint version checks, crash tests |
| Measure hot tenants | Business-wide write serialization | `app/booking.py`, `_lock_business_and_check_retry` | Measure lock wait before narrowing the lock; retain transaction and exclusion guarantees |
| Measure database overhead | New connection per operation; per-professional query loop | `app/database.py`, `connect`; `app/repositories.py`, `scheduling_data` | Bounded connection pools; batch schedule reads if profiling warrants it |
| Retention semantics | Thread deletion only removes latest default-namespace keys | `app/graph/redis_checkpoint.py`, `delete_thread` | Implement full cleanup if immediate deletion is required; older keys currently expire by TTL |
| Production deployment | Migrations and seed run on API startup | `docker-compose.yml`, `api.command` | Keep local convenience; use a single migration job in production |

The tenant constraint gap does **not** demonstrate an exploitable API vulnerability. Important application paths already validate ownership. Database constraints protect against future code bugs, imports and additional writers. For example, independent FKs permit a business-A booking referencing a business-B customer because both individual rows exist. A composite FK verifies the pair.

The availability query count is approximately `3 + 3P` inside `scheduling_data` for P selected professionals; surrounding callers can issue additional queries. This is a scaling risk, not evidence that current response times are slow. Similarly, business-level locking is a deliberate correctness/throughput trade-off, not automatically an error.

## Useful features already present

- Active booking overlap is prevented by a PostgreSQL exclusion constraint.
- Booking operations validate customer ownership and current availability inside transactions.
- Update receipts and final replies are durable; reply retries do not rerun booking operations.
- Unknown committed outcomes use a conservative recovery response.
- Scheduling handles local time, buffers, notice windows and booking horizons.
- Reminder claiming uses `SKIP LOCKED`.
- Model interpretation/routing are separate from deterministic booking authorization.

## Answers to the study questions

1. **Why acknowledge before replying?** The acknowledgment confirms durable receipt, not completion. Long inference should not hold the webhook request open.
2. **Why two idempotency boundaries?** A transport update and a booking command are different units with different keys, payloads and retry lifetimes.
3. **Why is MAU insufficient?** It omits turns per user, burst shape, processing time, voice duration and downstream limits.
4. **Why is Redis not booking truth?** Conversation state expires. Committed appointments must survive conversation loss.
5. **Why global blocking?** The current reply query only allows the oldest waiting reply for the bot to advance.
6. **Why composite FKs?** Separate keys verify each entity independently. A composite key verifies that the entity and tenant match.
7. **What to remove first?** Investigate audit_events first because no app usage was found. Verify external consumers and stored data before a forward migration.
8. **What if a worker dies after commit?** Today the user gets a check-your-appointments response. A future immutable command/outcome record can recover the reply without repeating the mutation.

## Verification and regeneration

Run `python3 scripts/generate_study_board.py` to regenerate the editable scene and SVG. The generator checks unique IDs, valid dimensions and grouped card text containment. Layout QA also checks that card rectangles do not overlap. The SVG reading copy uses standard fonts; Excalidraw uses a handwriting font for headings and may render those slightly differently.

This is a static design review, not a tested production implementation. No application regression suite is needed for these documentation-only additions; load tests and failure-injection tests remain prerequisites for capacity claims.

## External references

- [PostgreSQL 17 constraints](https://www.postgresql.org/docs/17/ddl-constraints.html)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Telegram delivery-limit FAQ](https://core.telegram.org/bots/faq)
- [KEDA scaling semantics](https://keda.sh/docs/2.21/reference/scaledobject-spec/)
- [Excalidraw file format](https://github.com/excalidraw/excalidraw/blob/master/dev-docs/docs/codebase/json-schema.mdx)

# One massage studio and one calendar

One bot books appointments for one massagista. Service types and durations are data. The demo starts with a fresh database and uses the existing Gemma/Laya conversation.

## Catalog and bookings

| Massage type | Example duration |
| --- | ---: |
| Shiatsu | 30 min |
| Shiatsu | 60 min |
| Liberação miofascial | 60 min |

Store one row per bookable type and duration, with a stable ID and a price. Expose a distinct service name such as “Shiatsu 30 min” through the existing catalog. The current graph matches named services. A new option such as “Shiatsu 45 min” therefore needs only a data row. Specific examples and prices are demo choices.

The massagista record holds their name and scheduling settings. Weekly hours can have several intervals on a day, and blocked periods cover exceptions. Bookings record the customer, selected option, start, end, occupied interval and status. All options share one calendar.

## Implementation

1. **MS-01 · Set up the massagista, services, and calendar.** One active professional, one row per type and duration, stable IDs and repeatable seed data. Keep blocked periods available. This task owns the fresh schema for the professional, options and calendar. No booking backfill is required.
2. **MS-02 · Book each option on the shared calendar.** Return the same catalog and slot fields the graph consumes. Snapshot the booked interval, enforce ownership and reject overlaps during the booking transaction. MS-01 owns the business schema.
3. **MS-03 · Remove scheduled reminders and finish the demo.** Remove reminder-only code, tables, jobs, metrics and CLI settings. Retain ordinary reply delivery and the owner agenda. Update affected tests and walkthrough. This task owns retirement of reminder storage and dependent columns on the fresh schema.

MS-02 follows MS-01. MS-03 follows MS-02. The [business requirements](prd-massage-studio.md) and SDD feature `massage-studio` contain the acceptance criteria and task details.

## Interfaces and scope

Reuse SQLAlchemy, PostgreSQL, Alembic and the current repository interface. Supply the business, one professional, service list and slot fields that callers expect today. Use one bot through its current registration flow. Preserve the existing graph, prompts, Gemma and Laya contracts, text and voice processing, evaluation strategy, reply delivery and booking protections. Retire only scheduled reminder behavior and its storage.

## Demonstrate completion

- Seed an empty database twice. Show one massagista and one row per option. Add a 45-minute option and read it through the existing catalog.
- With 45 minutes free before lunch and a 15-minute buffer, offer the 30-minute session. Exclude the 60-minute session.
- Book Shiatsu, then request an overlapping liberação miofascial appointment. Only one can succeed. Confirm, look up, reschedule and cancel through the existing flow. Check ownership and repeat a Telegram update to verify one booking.
- Verify the immediate replies and owner agenda. Check that there are no scheduled reminders. Run the current booking journey, graph and model tests with updated business fixtures.

## Rollout

Initialize and seed a separate empty demo database. Existing bookings require no backfill. Keep the former database intact if it exists. A rollback uses the prior application with its corresponding database. Update the demo walkthrough with the current menu. No changes to the live application are part of this planning request.

# CAPY massage studio requirements

CAPY is an AI engineering portfolio project. One Telegram bot helps customers book one massagista. The scheduling example should be easy to explain while exercising the existing text and voice workflow.

## What the customer can book

The service table defines each type and duration the studio offers. The following rows are example demo data. The studio can add a different duration by adding a row.

| Massage type | Duration in minutes | Name shown to the existing graph |
| --- | ---: | --- |
| Shiatsu | 30 | Shiatsu 30 min |
| Shiatsu | 60 | Shiatsu 60 min |
| Liberação miofascial | 60 | Liberação miofascial 60 min |

## Business requirements

1. The demo has one configured bot, one bookable professional, and one calendar. The catalog and owner agenda refer to the same massagista.
2. Each option has a stable ID, a massage type, a positive duration in minutes and a demo price in BRL. Each type and duration combination appears once. The existing service name presented to the graph includes the duration. Prices and specific seed rows are illustrative.
3. Availability respects the selected option's duration, buffers, weekly working intervals and existing blocks. Every service occupies the same professional's time. An existing appointment retains its booked start and end when catalog data changes.
4. Customers can book, look up, confirm, reschedule and cancel using the existing flow. Current approval, ownership, retry and double-booking protections apply.
5. Customers receive immediate replies to their actions. The business sends no scheduled reminders. The owner can still read the agenda.

## Delivery boundary

Start from a fresh demo database. Existing bookings do not need a migration or backfill. This is a planning decision about compatibility. The plan calls for initializing a separate empty database.

Use the current graph, prompts, model contracts, Gemma/Laya roles, voice and text handling, and evaluation approach as they stand. The repository supplies the existing catalog format. Scope covers business tables, their immediate readers and writers, reminder retirement, affected fixtures, and the demo guide.

## Evidence of completion

A clean database produces the same menu when seeded twice. A new duration appears in the catalog after adding its row. A booking for one service blocks an overlapping booking for another. A booking journey produces one appointment and an immediate reply with no scheduled notification. Run the existing graph and model-contract tests against the revised business fixtures.

See the [implementation plan](plan-massage-studio.md) and the [existing model contracts](../model-contracts.md).

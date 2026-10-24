# Spec compliance

Checked: 2026-10-24, after the workflow graph landed. Read `docs/architecture.md` for the code that runs. This file compares that code with the target specification.

The scheduling core still matches the specification. The product architecture is now split in code, but several target pieces are ports or local adapters rather than live providers.

## Follows

| Requirement | Evidence |
|---|---|
| Domain model is a generic service business, demoed as a massage studio. | `businesses`, `services`, `professionals`, and the synthetic seed. |
| Availability is computed, not invented by the model. | `AvailabilityService` returns slots. Chat offers only those slots. |
| A selected slot is revalidated in the booking transaction. | `validate_slot_in_transaction` and the PostgreSQL overlap exclusion. |
| Create, confirm, cancel, and reschedule are idempotent. | `idempotency_records` in `BookingService`. |
| A failed reschedule leaves the original booking unchanged. | Reschedule updates the booking and reminder in one transaction. |
| Duration, weekly hours, professional-service assignment, buffers, blocked periods, notice, horizon, and business timezone are enforced. | `app/scheduling.py` and migration `0001_general_scheduling`. |
| PostgreSQL is the booking source of truth. Redis holds short-lived chat state. | Repository and `RedisChatStore`. |
| The model cannot emit SQL, generate the management token, or authorize a write. | Interpreter returns `SchedulingExtraction` only. |
| Booking changes write audit events and omit the raw token from normal logs. | `BookingService._write_audit` and `app/observability.py`. |
| A second business can be seeded without a schema rewrite. | Seed command supports another synthetic business. |
| Healthcare, live voice calls, marketplace discovery, and unrestricted tool use are absent. | Matches the V1 exclusions. |
| FastAPI is the backend. | `app/main.py`. |

## Does not follow

| Requirement | Current gap |
|---|---|
| A trained Laya model and a live Gemma audio model. | The decision port is deterministic and can be swapped. Audio understanding is a fixture, not Gemma. |
| Live WhatsApp delivery and short audio messages. | The webhook verifies a signature, dedupes provider message ids, and calls the same workflow. It does not call the Meta send API. |
| Reminder delivery to the customer's channel. | The worker uses a sender port. The default sender still writes the demo inbox. A reply in Streamlit is a new `/chat` turn. |
| Locations, deposits, cancellation windows, and fees. | Not in the schema or booking policy. Cancellation is allowed until the booking starts. |
| Locations, deposits, cancellation windows, and fees. | Not in the schema or booking policy. Cancellation is allowed until the booking starts. |
| Data model includes Location, Conversation, Payment, and AgentDecision. | Those tables do not exist. Conversation state is Redis, not PostgreSQL. |
| Calendar and payment adapters. | Not present. |
| Queue-backed confirmation, reminder, and retry jobs. | Reminder scheduling is a row inserted in the booking transaction. Delivery is a polling loop. |
| Mobile-first booking UI that conversation can pre-filter, plus an owner dashboard for configuration. | Streamlit has a customer form, chat, a read-only agenda, and a demo inbox. The owner cannot configure services, hours, or agent settings. Chat does not filter the form. |
| Strict contracts for normalized request, decision output, and tool result. | Only the extraction schema is strict. It has no modality, confidence, location, missing fields, or booking reference. |
| Workflow state remembers the operation independently of the latest utterance. | Offered slots and one pending slot are stored. There is no explicit draft operation. |
| Evaluation compares routing models, audio transcription, task completion, wrong-slot offers, latency, and cost. | Current evals score extraction fields only. |
| Observability covers modality, routing, workflow transitions, fallback, and cost. | Logs and optional Langfuse events cover a few chat and booking outcomes. |
| Conversation retention is defined. | Redis TTL exists. There is no documented retention policy for model calls or message content. |

## Partial

- **Provider replacement:** the text interpreter can be deterministic or OpenAI, with fallback. The decision and audio ports can be swapped in tests. Neither is a live Laya or Gemma service.
- **Confirmation:** a booking can be marked confirmed. The product does not send a reminder and then collect attendance confirmation on a channel.
- **Business scoping:** rows are scoped by `business_id`. Owner routes require `X-Owner-Token` only when `OWNER_API_TOKEN` is set.
- **Privacy:** tokens and contact fields are redacted from logs. The owner endpoint still returns customer names and phone numbers to any local caller.
- **Clean-room constraint:** the implementation uses generic scheduling concepts and synthetic demo data. This check did not audit prompts or history for copied employer material.

## Bottom line

The repository follows the domain rule: the model interprets language, and application code computes availability and commits bookings. The graph now owns the turn, and the decision port is separate from understanding. The largest remaining gaps are live WhatsApp delivery, Gemma audio, a trained Laya model, real reminder delivery, and the owner configuration dashboard.

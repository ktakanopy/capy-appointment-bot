# Spec compliance

Checked: 2026-10-23, against the architecture and project specification in this directory. No application code was changed.

The project follows the scheduling-core idea. It does not yet follow the target product architecture. The current app is a local demo with deterministic booking rules, a Streamlit UI, and a one-node LangGraph extractor. The specifications describe a channel-independent product with separate understanding, decision, workflow, and domain layers.

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
| Four separate layers: understanding, Laya decision, workflow execution, domain validation. | Understanding is OpenAI or a deterministic parser. There is no Laya model, confidence score, or human-escalation decision. Routing is Python branching in `app/main.py`. The graph is `START → interpret → END`. |
| LangGraph, or an equivalent state machine, owns allowed transitions. | The graph does not load a booking, collect missing fields, present slots, revalidate, or commit. |
| Text and audio are separate understanding inputs. Gemma 3n E2B transcribes short audio and emits structured intent. | No audio input, transcription, or Gemma integration. |
| WhatsApp is the first messaging channel, with text and short audio. | No webhook, signature check, outbound adapter, or delivery status. |
| A customer can ask about an existing appointment without pasting a secret. | Management requires a capability token. Sessions are not bound to a verified customer. |
| Reminder delivery and reminder-triggered reschedule. | The worker writes a demo inbox row. It does not send a message. A reply is a manual Streamlit action that still needs the token. |
| Basic FAQ from business-owned duration, price, location, preparation, and cancellation rules. | Help text lists service names. It does not answer those questions from catalog data. |
| Low-confidence or unsupported requests escalate to a human and preserve context. | Unsupported text becomes a generic clarification. There is no handoff state. |
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

- **Provider replacement:** the text interpreter can be deterministic or OpenAI, with fallback. Audio and decision providers cannot be swapped because they do not exist.
- **Confirmation:** a booking can be marked confirmed. The product does not send a reminder and then collect attendance confirmation on a channel.
- **Business scoping:** rows are scoped by `business_id`, but owner and reminder endpoints are unauthenticated.
- **Privacy:** tokens and contact fields are redacted from logs. The owner endpoint still returns customer names and phone numbers to any local caller.
- **Clean-room constraint:** the implementation uses generic scheduling concepts and synthetic demo data. This check did not audit prompts or history for copied employer material.

## Bottom line

The repository is aligned with the specification's domain rule: the model may interpret language, and application code must compute availability and commit bookings. It is not aligned with the specification's product architecture. The largest missing pieces are decision routing, workflow ownership, WhatsApp and audio channels, customer identity, real reminder delivery, and the dashboard/configuration surface.

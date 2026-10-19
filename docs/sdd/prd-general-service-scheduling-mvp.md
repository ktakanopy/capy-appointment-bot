# General Service Scheduling MVP

## Intent Checkpoint

- **User intent:** Reframe the clinic appointment assistant as a general scheduling product for small appointment-based service businesses and make a first portfolio release.
- **Why this matters:** The portfolio should demonstrate a useful product and a clear engineering boundary between AI interpretation and deterministic booking rules. The current project proves a protected clinic workflow, but it does not yet offer availability or create bookings.
- **Desired outcome:** A runnable local demo lets a customer book, confirm, reschedule, and cancel a service appointment through chat; the business owner can inspect the schedule; a demo reminder can lead into a rescheduling conversation.
- **Current problem:** The implementation is clinic-specific. It models patients, date-of-birth verification, doctors, and existing appointment status changes. It has no business catalog, availability engine, booking operation, durable booking store, or reminder delivery flow.
- **Scope:** One configurable demo business; generic business, professional, service, customer, availability, blocked-time, booking, reminder, and audit concepts; deterministic availability and booking services; PostgreSQL persistence; FastAPI and LangGraph conversation flow; Streamlit customer and owner views; a local reminder worker with a demo inbox; traces and workflow evaluations.
- **Acceptance:** The demo completes the full scheduling journey; never offers a slot outside deterministic availability; rechecks a slot at commit; rejects conflicts and duplicate retries safely; preserves the existing booking after a failed reschedule; supports the same behavior for a second business configuration in fixtures without workflow code changes; and can be run from the documented local setup.
- **Constraints and ownership boundaries:** Keep Python 3.11+, FastAPI, LangGraph, Pydantic, and Streamlit. Replace clinic-specific API payloads and synthetic clinic fixtures; there is no durable patient-data migration because current business data is in memory. The database migration owner is T002. Use synthetic data only. Customer-facing model output cannot authorize or mutate bookings. The existing API routes may retain their paths, but compatibility with clinic response fields is not required.
- **Assumptions:** Use the previously recommended defaults for the questions not separately answered: a local Docker demo, customer chat with clickable slot selection, a basic owner agenda, simulated reminder delivery, and automated workflow/data-integrity checks. Laya training and benchmarking follow this release. One demo business is seeded; the domain remains configuration-driven. A per-booking HMAC capability token controls customer access. The owner view is demo-only and has no production authentication.

## Problem Statement

Small service businesses often coordinate appointments manually in messaging apps and calendars. This project should demonstrate how a customer can express a scheduling request naturally while the system keeps availability and booking changes correct. The existing clinic assistant protects access to existing records but has no booking workflow and carries clinic-specific concepts throughout the domain, graph, user interface, and evaluations.

## Why It Matters

The portfolio value is a working, reviewable end-to-end product. Its central engineering point is that AI can interpret language and request clarification, while application code computes valid slots, checks policy, and commits booking changes. A second business fixture should show that the domain is general without trying to support every kind of business in the first release.

## Solution

Add a configurable service scheduling core for one seeded business. A customer can ask for a service and time, inspect available slots returned by the scheduling service, select one, and receive a booking plus private management token. The customer can later confirm, cancel, or reschedule using that token. A scheduled reminder is delivered through a local demo inbox; a simulated reply is submitted as a new chat API request and starts a reschedule flow. The owner view presents the demo business's bookings and reminder state.

The LLM returns a typed intent and constraints. It does not calculate availability, select an authoritative slot, make policy decisions, generate management tokens, or perform writes. The scheduling service revalidates before commit. PostgreSQL persists business records, bookings, reminders, and audit events. A small polling worker delivers due reminders through a replaceable demo adapter. The worker does not call LangGraph or access conversation checkpoints; the inbox reply UI submits the reply to the API as a new chat turn.

## User Stories and Requirements

### US1 — Find and book a valid slot

As a customer, I want to describe a service and preferred time conversationally, choose from real available slots, and receive a booking confirmation.

- **FR-001:** The catalog must model businesses, professionals, services, and which professionals provide each service. Business and service configuration includes timezone, service duration, price, buffers, and basic advance-booking limits.
- **FR-002:** Availability must derive from the business's weekly professional schedules, blocked periods, existing active bookings, service duration, buffers, minimum notice, and maximum booking horizon.
- **FR-003:** The assistant may offer only slot identifiers returned by the scheduling service. Each slot selection is revalidated at commit time.
- **FR-004:** A successful booking returns its summary and an unguessable customer management token. Derive a stable per-booking token using HMAC-SHA-256 from `BOOKING_TOKEN_SECRET`, booking ID, and token version; store only a one-way digest of the token. The same token can be re-derived for an idempotent retry without persisting its raw value.
- **FR-005:** Repeating a create request with the same idempotency key returns the original outcome and does not create a duplicate booking.

### US2 — Manage an existing booking

As a customer, I want to confirm, reschedule, or cancel my booking without exposing other customers' records.

- **FR-006:** Customer management actions require a valid private token; database IDs alone are not a customer authorization mechanism.
- **FR-007:** Rescheduling atomically changes the booking only after the new slot has been revalidated and protected against conflicts. A failed reschedule leaves the original booking unchanged.
- **FR-008:** Confirmation and cancellation are explicit, deterministic, and idempotent. A canceled booking cannot be confirmed or rescheduled.
- **FR-009:** Booking state changes create an audit event with the action, booking ID, result, and timestamp. Do not log raw management tokens or unnecessary customer contact data.

### US3 — Inspect the demo schedule

As the demo business owner, I want to inspect upcoming appointments for the configured business.

- **FR-010:** The owner view shows bookings, status, professional, service, local start/end time, and reminder delivery status for the seeded business.
- **FR-011:** The MVP supports one configured business at a time in the UI. Domain and scheduling services receive business-scoped data and must work with a second business fixture without workflow-code changes.
- **FR-012:** The owner view is a synthetic-data portfolio demo and is not represented as production-authenticated.

### US4 — Send and act on a reminder

As a customer, I want a reminder before my booking and to be able to reply that I need another time.

- **FR-013:** A successful booking creates one durable reminder record at the configured lead time.
- **FR-014:** A polling worker claims due reminders idempotently and records delivery using a demo inbox adapter. No live messaging credentials or third-party send are required.
- **FR-015:** A simulated reminder reply such as “I can’t make it; anything after 6?” is submitted by the client as a new `/chat` request with a session ID and booking management token. The client may continue an active session or start a fresh one; the API validates the token and supplies authorized booking context, so the reply does not rely on old conversation history. It requests rescheduling; the prior booking remains active until the replacement slot commits. The worker never invokes the graph. Raw management tokens are never written to LangGraph state/checkpoints, conversation history, logs, traces, or model input.

### US5 — Understand the request safely

As a customer, I want the assistant to understand common scheduling language and ask for missing information when needed.

- **FR-016:** Typed model output may include operation, service, preferred professional, date/time constraints, customer contact fields, selected slot reference, and confirmation intent. Missing or ambiguous values lead to a bounded clarification prompt.
- **FR-017:** The model cannot invent slots, alter booking state, bypass management-token checks, or select a slot not present in the latest deterministic availability result.
- **FR-018:** Provider calls are isolated behind the existing LLM provider boundary. Tests use a deterministic provider and do not require an external API key.

### Quality requirements

- **NFR-001:** Persist booking and reminder data in PostgreSQL; provide versioned schema migrations owned by T002.
- **NFR-002:** Enforce professional/time overlap safety in the database transaction boundary so concurrent requests cannot double-book.
- **NFR-003:** Respect business timezone and store instants unambiguously; display local times using the configured business timezone.
- **NFR-004:** Preserve structured traces and add offline evaluation scenarios for booking, ambiguity, conflicts, cancellation, rescheduling, reminder replies, and prompt-injection attempts.
- **NFR-005:** Document local setup, synthetic demo credentials/flows, database migration and seed behavior, worker operation, and evaluation commands.

### Lifecycle policy for the demo

- Customers may confirm, cancel, or reschedule a future `scheduled` or `confirmed` booking until its start time. Past or in-progress bookings cannot be changed. There are no cancellation fees or deposit rules in this release.
- A booking has at most one pending reminder at a time. Cancellation marks its pending reminder canceled in the same transaction. Rescheduling updates an existing pending reminder; if the prior reminder was already delivered, it creates a new pending reminder for the replacement time. Previously delivered reminder/inbox history is retained. The seeded demo business uses a five-minute reminder lead time so the local reminder flow can be demonstrated quickly; lead time remains business configuration.

### HTTP contract baseline

- Keep `POST /sessions/new`, `POST /chat`, and `GET /health` with new generic DTOs; clinic payload compatibility is not required.
- Add `GET /businesses/{business_id}/catalog`, `POST /availability/search`, `POST /bookings`, `GET /bookings/me`, `POST /bookings/me/confirm`, `POST /bookings/me/cancel`, `POST /bookings/me/reschedule`, `GET /owner/bookings`, and `GET /demo/reminders`.
- `POST /chat` accepts `session_id`, `message`, and optional `manage_token` as separate fields. The API validates the token and only supplies the authorized booking ID/current booking summary and permitted action to that turn. Do not put the raw token in graph state, checkpoints, chat history, model context, logs, traces, URLs, or error messages. The demo UI keeps it in protected session state and never appends it to chat text.
- `POST /availability/search` accepts business/service IDs, optional professional ID, local date range, and optional local time bounds; returns ordered slots with opaque per-result `slot_id`, professional, offset-aware start/end, local display label, duration, and price.
- `POST /bookings` accepts the selected `slot_id` plus business/service/professional IDs and exact offset-aware start instant returned with that slot, customer contact fields, and requires `Idempotency-Key`; the slot reference is not authority and the server re-resolves/revalidates the exact slot before committing.
- Customer management endpoints accept `X-Booking-Token` and `Idempotency-Key` where a mutation can be retried. Create returns the derived token so the customer can retain it. Never put a token in a URL. Invalid tokens and unknown bookings share a generic response.
- `GET /owner/bookings` and `GET /demo/reminders` expose only the seeded synthetic business and are explicitly local-demo endpoints without production authentication.

### Persistence contract baseline

- T002 defines and owns all Alembic schema. Persist service duration/buffers, business timezone, professional-service assignments, weekly hours, blocked periods, bookings, reminder delivery state, audit events, and idempotency outcomes.
- A booking stores `start_at`, `end_at`, `occupied_from`, and `occupied_until` as aware instants. Active states are `scheduled` and `confirmed`; canceled rows do not occupy time. Enable PostgreSQL `btree_gist` and add a GiST exclusion constraint preventing overlapping occupied ranges per professional for active bookings. This is the final concurrency guard even when availability is rechecked in application code.
- Persist an idempotency scope/key, canonical request hash, and booking/result reference under a unique constraint on `(business_id, operation, idempotency_key)`. Reusing a key with a different request returns a typed conflict. Derive the booking token with HMAC-SHA-256 from a required `BOOKING_TOKEN_SECRET` of at least 32 random bytes, booking ID, and token version, then store only its one-way digest; same-key create retries can re-derive and return the same token. Keep idempotency retention for the lifetime of the associated booking in this demo. Secret rotation invalidates existing tokens; document this local-demo limitation.
- Creating a booking and its pending reminder are one transaction. A booking has at most one pending reminder, enforced by a partial unique constraint on booking ID where reminder state is pending. Cancellation cancels the pending reminder in the same transaction. Rescheduling changes the booking and either updates its pending reminder or creates a replacement pending reminder if the previous one was delivered; a failed reschedule changes neither booking nor reminder. T007 claims/delivers pending reminders; inbox rows have a unique reminder ID so retries cannot duplicate visible messages.

## Boundaries and Ownership

- T001 owns the general Pydantic domain types and operation contracts.
- T002 owns SQL persistence, schema migrations, repositories, seed loading, and the migration from the current in-memory runtime. No existing database backfill is needed.
- T003 and T004 own deterministic availability and booking lifecycle rules respectively.
- T005 owns FastAPI contracts/routes and runtime wiring.
- T006 owns LLM extraction, LangGraph state/nodes, and chat behavior.
- T007 owns durable reminders, worker lifecycle, and demo notification adapter.
- T008 owns Streamlit customer and owner surfaces.
- T009 owns evaluation, observability changes, local deployment, and product documentation.

## Implementation Decisions

- Keep the existing Python/FastAPI/LangGraph/Pydantic/Streamlit stack for the first release.
- Replace patient, doctor, and date-of-birth verification concepts with customer, professional, service, and private booking management-token concepts.
- Keep a single-business demo configuration while making business scope an explicit input to catalog, availability, and repository operations.
- Treat all customer-facing times as business-local, timezone-aware requests; persist timezone-aware instants and calculate availability against the configured timezone.
- Use PostgreSQL transactions and a database overlap constraint for active bookings. Idempotency keys protect create and mutation retries.
- Use a persisted reminder table and one polling worker process for this demo. The notification adapter writes to a demo inbox; live WhatsApp, SMS, email, Redis, and a general-purpose queue are deferred.
- Keep conversation checkpoints in the current in-memory checkpointer for the single API demo process. Booking, customer-token digest, reminders, idempotency outcomes, and audit events are durable. Do not run multiple API replicas in this MVP; the worker does not read the checkpointer.
- Keep the current `/sessions/new`, `/chat`, and `/health` routes where useful, but replace clinic-specific fields and operations. Add explicit catalog, availability, management, and owner booking endpoints needed by the UI.
- Use existing structured observability and evaluation patterns. The new scenarios use a deterministic test provider and assert system outcomes before optional model judging.
- Run focused pytest areas for each task and the full documented suite at completion. Local manual verification covers the full customer and reminder journey in Docker Compose.

## Testing and Quality Decisions

- Unit tests cover value validation, service duration and buffers, timezone conversion, working-hour boundaries, booking conflicts, policy windows, and status transitions.
- Repository integration tests use a disposable PostgreSQL database to exercise migrations, transaction behavior, uniqueness, overlap protection, and idempotency.
- Graph and API tests replay multi-turn booking, clarification, slot selection, token-protected management, and reminder-reschedule scenarios using a deterministic provider.
- Evaluation scenarios assert structured outcomes and verify that all offered slots came from the scheduling service. An LLM judge may summarize conversation quality but cannot determine booking correctness.
- Run `uv run --extra dev pytest` for the full test suite. Run the Docker Compose walkthrough from the README as the manual system check.

## Out of Scope

- Fine-tuning or serving Laya; model benchmarking is a follow-up project milestone.
- Live WhatsApp, SMS, email, calendar, or payment integrations.
- Public hosting, production customer authentication, business signup, tenant administration, and a production security claim for the owner view.
- Marketplace discovery, voice, healthcare/clinical workflows, travel scheduling, multi-location businesses, complex recurrence, deposits, and unrestricted tool use.
- Redis, a general-purpose distributed queue, or production worker autoscaling.
- Migration or compatibility with the clinic demo's seeded patient and appointment records.

## Further Notes

- Source specification: `docs/Service Scheduling Agent — Project Specification.md`.
- Source architecture: `docs/Service Scheduling Agent — Architecture.md`.
- Existing implementation notes to replace or archive: `README.md`, `docs/architecture.md`, `docs/graph.md`, `docs/security.md`, and `docs/evaluation.md`.
- The two service-scheduling source docs are currently untracked. Preserve them while implementing this plan.
- No production authentication or privacy guarantee should be implied by use of management capability tokens in a synthetic local demo.

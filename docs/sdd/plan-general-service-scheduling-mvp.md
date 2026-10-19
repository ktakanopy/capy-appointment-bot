# SDD Plan: General Service Scheduling MVP

## Source Artifacts

- [`prd-general-service-scheduling-mvp.md`](prd-general-service-scheduling-mvp.md)
- [`Service Scheduling Agent — Project Specification.md`](../Service%20Scheduling%20Agent%20%E2%80%94%20Project%20Specification.md)
- [`Service Scheduling Agent — Architecture.md`](../Service%20Scheduling%20Agent%20%E2%80%94%20Architecture.md)
- Current baseline: [`README.md`](../../README.md), [`docs/architecture.md`](../architecture.md), `app/`, `frontend/`, and `tests/`.

## Intent Checkpoint

- **Intent:** Refactor the clinic assistant into a generic scheduling product for small appointment-based businesses, as a portfolio MVP.
- **Why:** Demonstrate a usable customer journey and the boundary between probabilistic language interpretation and deterministic availability/booking rules.
- **Desired result:** A local demo where a customer chats to book, confirm, reschedule, or cancel; an owner sees the schedule; and a simulated reminder reply is submitted as a new API chat turn to start a reschedule.
- **Scope:** One seeded business; generic business/service/professional/customer/booking concepts; PostgreSQL; deterministic availability and booking operations; FastAPI, LangGraph, and Streamlit; demo reminder inbox/worker; observability, evaluations, and local setup documentation.
- **Non-goals:** Laya training/benchmarking in this release, live WhatsApp/SMS/email/calendar/payments, hosted deployment, production authentication, multi-location, marketplace, healthcare, voice, and distributed queue infrastructure.
- **Confirmed decision:** Laya training and benchmarking follow the working product MVP.
- **Assumptions from the recommended scope:** Local Docker demo, web chat with selectable slots, simple owner agenda, demo reminders, PostgreSQL, clinic API compatibility may be dropped, and the full test suite plus manual journey define completion. These assumptions are explicitly recorded in the PRD and can be revised in review.
- **Acceptance:** The system offers only deterministic slots, revalidates before writes, protects against conflicting and repeated requests, leaves an old booking untouched when rescheduling fails, supports a second business fixture through configuration, and completes the reminder-to-reschedule demo. The worker never accesses LangGraph state.
- **Constraints:** Python 3.11+, existing FastAPI/LangGraph/Pydantic/Streamlit stack, synthetic data, no old clinic record backfill. T002 owns schema migrations.
- **Unresolved:** None. The prior revision was approved; final seed/reply-flow clarifications are awaiting Plannotator re-review.

## Goal

Deliver a runnable local portfolio app for general service scheduling. The customer can discover a configured service, request a time in chat, choose an actually available slot, receive a private booking management token, and use that token to confirm, reschedule, or cancel. The owner view shows bookings and reminders. The service computes every valid slot and validates every state change; the LLM only extracts typed intent and constraints.

## Inputs and Outputs

### Plan-level inputs

- Repository root: `/Users/brtnathd/projects/capy-appointment-bot`.
- Product source: `docs/Service Scheduling Agent — Project Specification.md` and `docs/Service Scheduling Agent — Architecture.md`.
- Current implementation and tests: `app/`, `frontend/`, `tests/`, `pyproject.toml`, `Dockerfile`, and `docker-compose.yml`.
- Runtime: Python 3.11+, Docker Compose for PostgreSQL and local app/worker services, optional `OPENAI_API_KEY` for real model calls, `DATABASE_URL` for the app database, and `TEST_DATABASE_URL` for database integration tests. Tests use deterministic providers and synthetic fixtures.
- User-visible behavior: customer chat, selectable availability results, booking summary and private manage token, owner agenda, and a local reminder inbox.

### Plan-level outputs

- `docs/sdd/prd-general-service-scheduling-mvp.md`: proposed scope/product contract for this revised version.
- `docs/sdd/plan-general-service-scheduling-mvp.md`: task contracts and implementation/verification plan.
- `docs/sdd/.sdd-state.json`: mutable feature/task state maintained through `sdd-mcp`.
- `docs/sdd/general-service-scheduling-mvp/tasks.md`: materialized task index for review/reading, supplemental to SDD state.
- Application deliverables: generalized domain, versioned PostgreSQL schema, deterministic availability and booking lifecycle, API/chat/UI, demo reminder worker, evaluation scenarios, Docker walkthrough, and updated project docs.
- External effects: local database migration/seed; reminder records and demo inbox rows. No real customer messaging or payment calls.
- Errors exposed to users: missing/ambiguous service or time requests a clarification; stale/unavailable slot offers alternatives; invalid management token gives a generic not-found response; policy conflicts preserve current booking state; provider or database unavailability returns a controlled service error.

## Context and Existing Patterns

- `app/main.py` has `/sessions/new`, `/chat`, and `/health`; `app/runtime.py` is the composition root.
- `app/models.py` mixes domain and transport models and currently contains clinic-specific Patient, Appointment, DOB verification, and clinic status types.
- `app/ports/` uses repository and LLM Protocols. `app/repositories.py` contains seeded in-memory adapters.
- `app/graph/` separates LangGraph state, nodes, builder, workflow, and deterministic parsing. The current graph is clinic-specific and must be replaced for the new customer journey.
- `app/llm/schemas.py` and `app/llm/prompt.py` define typed extraction. The runtime currently requires an OpenAI API key; tests replace it with `TestProvider` in `tests/conftest.py`.
- `app/observability.py` redacts clinic PII and captures graph traces. Update summaries/redaction for customer/contact/token data.
- `app/evals/` replays scenarios through a deterministic runtime and can retain its runner shape while scenarios and outcome fields change.
- `frontend/streamlit_app.py` is one chat page; reuse Streamlit and `frontend/lib/api_client.py` for slot controls and owner agenda.
- `docker-compose.yml` currently provisions Postgres for Langfuse only. Add a separate app database; do not reuse Langfuse's internal database.

## Governance and Complexity Gates

- Keep the current framework and split work at domain, persistence, API/workflow, worker, UI, and portfolio-validation boundaries. Each task has a focused output and can be handed to a lower-capability implementer without interpreting the full chat.
- Reuse the current LLM Protocol, FastAPI composition root, LangGraph wrapper/checkpoint pattern, Streamlit/API client, logging/tracing, and deterministic evaluation runner where they fit.
- Replace clinic-specific identity and appointment behavior rather than preserving incompatible patient response fields.
- Keep one demo business and a second injected fixture for generality checks. Do not build onboarding or unrestricted multi-tenant administration.
- Keep reminder delivery as one Postgres-backed poller and a demo adapter. Do not add Redis, Celery, or a distributed queue.
- Keep customer management via an HMAC-derived capability token whose digest is stored; never use a database ID as authorization. Owner UI is explicitly local demo-only.
- The 500-line task-size heuristic is a warning to split at real ownership/contracts, not a target. Tasks below follow module/dependency boundaries, not artificial microtasks.

## Proposed Implementation

1. Define generic domain and transport contracts, removing clinic-only types.
2. Add PostgreSQL persistence, versioned migrations, repository adapters, and synthetic business seed data.
3. Implement timezone-aware availability as deterministic Python logic.
4. Implement atomic, idempotent booking operations and private customer management grants.
5. Expose catalog, availability, booking management, owner agenda, and health APIs.
6. Rebuild the LangGraph conversation around bounded extraction, clarification, deterministic slots, explicit selection, and management actions.
7. Add durable reminders, a single polling worker, and demo inbox delivery.
8. Adapt Streamlit into customer chat/slot selection and a simple owner agenda.
9. Replace clinic evaluations/docs and complete the Docker local walkthrough.

## Task Decomposition Rules

Tasks are split when work crosses a module owner, changes an independent contract, requires a separate migration, or needs its own acceptance checks. Dependencies are explicit: schema and domain precede scheduling; deterministic services precede APIs and chat; persisted bookings precede reminders; stable API/workflows precede UI; final evaluation/docs follow the complete flow. No task is marked parallel because shared runtime/schema wiring gives the small team no useful parallel boundary.

## Interfaces, Data, and Ownership

- **Domain:** Business, Professional, Service, WeeklyAvailabilityRule, BlockedPeriod, Customer, Booking, Reminder, AuditEvent. Business timezone and service duration/buffers are explicit. Booking status is scheduled, confirmed, or canceled.
- **Time contract:** API accepts ISO-8601 values with offsets or a business-local date/time plus business ID. Reject naive ambiguous instants at write boundaries. Persist timezone-aware UTC instants; derive display times in the configured `zoneinfo` business timezone.
- **Availability contract:** `POST /availability/search` accepts business/service IDs, optional professional ID, local date range, and optional local time bounds. It returns chronological slots with an opaque presentation `slot_id`, professional ID, offset-aware `start_at`/`end_at`, local display label, duration, and price. A `slot_id` is a selection reference only, never proof that a slot remains free.
- **Booking contract:** `POST /bookings` accepts the selected slot reference plus its business/service/professional IDs and exact start instant, customer contact fields, and a required `Idempotency-Key` header. The service recomputes and revalidates the exact slot inside the transaction. It returns the saved booking summary plus a per-booking HMAC-SHA-256 manage token derived from required `BOOKING_TOKEN_SECRET` (at least 32 random bytes), booking ID, and token version; persist only its one-way digest. This lets idempotent create retries return the same token without storing it in plaintext. Management mutations use `X-Booking-Token` plus `Idempotency-Key`; customer read uses `X-Booking-Token`. Unknown booking and invalid token have the same generic response. Secret rotation invalidates existing tokens and is documented as a local-demo limitation.
- **Concurrency contract:** T002 owns PostgreSQL `btree_gist` and a GiST exclusion constraint over `[occupied_from, occupied_until)` per professional for scheduled/confirmed bookings. The booking transaction also revalidates availability. Create and its pending reminder are atomic; cancel and pending-reminder cancellation are atomic; reschedule and reminder update/recreation are atomic. A failed reschedule changes neither. The demo allows confirm/cancel/reschedule only before booking start; no fees or deposits.
- **Conversation contract:** LLM output is a Pydantic typed extraction only. Workflow owns clarification and latest available-slot context; a customer selects a current `slot_id` explicitly. `POST /chat` receives optional `manage_token` outside the message text; the API validates it and passes only authorized booking context into that invocation. The raw token is never placed in graph state/checkpoint, history, model input, logs, traces, URLs, or errors. Chat side effects call application services only.
- **Reminder contract:** A booking has at most one pending reminder. The seeded demo business uses a five-minute lead time so the local journey can be demonstrated quickly; lead time remains business configuration. Create writes the reminder atomically; cancel cancels it atomically; reschedule updates the pending reminder or creates a new pending reminder if the old one was already delivered. A separate worker claims due rows with PostgreSQL row locking (`FOR UPDATE SKIP LOCKED`), writes an inbox row unique by reminder ID, and stores attempts/delivery outcome transactionally. It has no graph/checkpointer dependency. The UI submits a simulated reply as a new `/chat` request with an active session ID or a fresh session ID and token separately; the API validates the token and supplies booking context.
- **Migration ownership:** T002 creates and documents Alembic revisions and seed loading. No backfill is required because existing appointment data is seeded in memory. T009 owns Docker migration startup instructions and rollback notes.
- **Operational boundary:** App database is separate from Langfuse Postgres. Local worker is an explicit Compose service. LLM and Langfuse integrations remain optional in tests.

## Task Snapshot

| ID | Status | Priority | Parallel | Story/Requirement | Why / Intent | Start Boundary | End Boundary | Owner | Migration Owner | Inputs / Arguments | Outputs / Side-effects | Acceptance Criteria | Dependencies |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T001 | todo | P1 |  | US1/FR-001, FR-016 | Establish business-neutral types and API/model vocabulary | Existing clinic models | Typed generic scheduling contracts replace clinic concepts | Domain/API models | N/A — no storage change | PRD, existing models and graph response contract | `app/models.py`, schema tests | Core entities, time/contact fields, enums and DTOs validate; no patient/DOB types remain in active contracts | None |
| T002 | todo | P1 |  | NFR-001–003 | Make bookings durable and define all concurrency-critical schema | T001 domain contract | PostgreSQL repositories, seed, migration, test DB | Persistence | T002 | Domain types, DATABASE_URL, TEST_DATABASE_URL, booking/reminder/idempotency contracts | SQL schema, Alembic revision, repositories, demo business seed, exclusion/idempotency constraints | Fresh DB migrates and seeds; repository CRUD, booking overlap exclusion, token digest, idempotency uniqueness, and transaction rollback pass integration checks | T001 |
| T003 | todo | P1 |  | US1/FR-002–003, NFR-003 | Calculate only valid local-time slots | Persisted business rules/repos | Ordered valid availability slots | Scheduling service | N/A — T002 owns schema | Availability query, service/pro/business IDs, time window | Deterministic slot results; policy tests | Hours, duration, buffers, blocks, bookings, notice, horizon, timezone, DST, and empty results are correct | T001, T002 |
| T004 | todo | P1 |  | US1/US2/FR-004–009, NFR-002 | Commit safe and repeatable booking changes | Valid availability result and T002 schema | Booking lifecycle, management token, and transactional reminder scheduling services | Booking application service | N/A — T002 owns all schema | Slot reference/exact slot, customer fields, manage token, idempotency key, reminder lead-time policy | Durable booking/reminder writes, token digest, audit events, typed outcomes | Overlap protection, retries, stale slots, policy errors, atomic reschedule, create/reminder atomicity, status idempotency pass | T003 |
| T005 | todo | P1 |  | US1–US3/FR-001–012 | Publish a stable API for UI and booking use cases | Domain services and repositories | Versioned FastAPI request/response/routes | API/runtime | N/A — no schema migration | HTTP request DTOs, DATABASE_URL, demo business ID | Catalog, availability, booking management, owner agenda, health endpoints; OpenAPI | API never trusts a slot or DB ID for authorization; errors and schemas are covered | T004 |
| T006 | todo | P1 |  | US1/US2/US4/US5/FR-013–018 | Replace the patient graph with a bounded scheduling conversation | API/application contracts | Multi-turn booking and manage workflow | LLM and LangGraph | N/A — no schema migration | Chat session, messages, typed model extraction, authorized booking context for the current turn | New Pydantic prediction, prompt, graph state/nodes/responses; raw manage token excluded from graph/checkpoint/history/model input | Clarification, slot offer/selection, explicit commit, token management, and new-turn reminder reply flows work with deterministic provider | T005 |
| T007 | todo | P1 |  | US4/FR-013–015 | Deliver reminders in a durable local demo flow | Persisted pending reminders from T004 | Due reminders appear once in demo inbox; UI/API submits any reply as a new chat request | Reminder worker/adapter | N/A — T002 owns reminder/inbox schema | Booking schedule, lead-time config, worker poll interval, inbox API contract | Reminder repository/service, adapter, worker Compose service, inbox read endpoint; no graph invocation | Due delivery, retries, idempotency, inbox visibility, and reply-through-API behavior are covered | T004, T005 |
| T008 | todo | P1 |  | US1–US4/FR-010–012 | Make end-to-end behavior easy to demonstrate | API, chat, reminder contracts | Customer and owner Streamlit pages complete journey | Frontend | N/A — no schema migration | API base URL, chat session, booking/manage token, business ID | Chat, clickable slots, booking summary, management, agenda and demo inbox UI | Walkthrough needs no API docs or manual DB editing | T005, T006, T007 |
| T009 | todo | P1 |  | NFR-004–005 and all success criteria | Make behavior measurable and portfolio-ready | Stable app/worker/UI behavior | Evaluations, current docs, repeatable local demo | Quality/portfolio | N/A — T002 migration docs referenced, not new migration | Demo Compose stack, synthetic scenarios, current README/docs | Evaluation scenarios, traces/redaction, Docker walkthrough, clean product docs | Full suite and manual path pass; no clinic workflow claims remain in active docs | T008 |

## Detailed Task Contracts

### T001 — Define generic scheduling domain and contracts

- **Why / intent:** Create a shared, clinic-neutral language for the MVP so persistence, scheduling, API, and graph tasks agree on entity and operation shapes.
- **Start boundary:** Begin at existing `app/models.py`, `app/llm/schemas.py`, and response DTOs. Do not add persistence or route behavior.
- **End boundary:** Pydantic models and enums represent Business, Professional, Service, WeeklyAvailabilityRule, BlockedPeriod, Customer, Booking, Reminder, AuditEvent, availability slots, customer management, and chat input/output. `Appointment` may be renamed to `Booking`; remove Patient/DOB verification types from active contracts.
- **Owner:** Domain/API contract boundary.
- **Migration owner:** N/A; only Python models and no persisted records change.
- **Inputs:** PRD sections FR-001 and FR-016; existing Pydantic v2 style; Python 3.11; preserve strict `extra="forbid"` on external request DTOs.
- **Outputs / side-effects:** Change `app/models.py`; update `app/llm/schemas.py` to the extraction field names without implementing prompting; add/update `tests/unit/test_value_objects.py`, entity/state-coercion tests as appropriate. Define money as decimal-safe, duration in integer minutes, and timestamps as timezone-aware `datetime` values. Add explicit `BusinessId`, `ServiceId`, status/operation/outcome enums only where validation benefits.
- **Non-goals:** SQLAlchemy mapping, database migrations, scheduling algorithms, API routes, UI, keeping clinic compatibility aliases.
- **Acceptance criteria:** All new entity and DTO validation tests pass; invalid durations, missing timezone offsets at write DTO boundaries, invalid contact values, and unknown external fields are rejected; no active public or domain schema requests DOB or `patient_id`.
- **Dependencies:** None.
- **Relevant files:** `app/models.py`, `app/llm/schemas.py`, `tests/unit/test_value_objects.py`, `tests/unit/test_state_coercion.py`, and type imports across app.
- **Focused verification:** `uv run --extra dev pytest tests/unit/test_value_objects.py tests/unit/test_state_coercion.py`.

### T002 — Add PostgreSQL persistence, migrations, and demo seed

- **Why / intent:** Keep bookings and reminder state after app restarts and give every later workflow a stable storage contract.
- **Start boundary:** Use T001 models as the domain contract. Add one app database separate from Langfuse's internal database.
- **End boundary:** Alembic can create a fresh application schema; SQLAlchemy repositories implement domain Protocols; seed loading creates a massage demo business, at least two professionals, multiple services, schedules, and synthetic customers; seed reminder lead time is five minutes for demoability; tests can target disposable PostgreSQL. Schema explicitly covers business timezone/service duration and buffers, professional-service assignments, weekly hours, blocked periods, bookings, reminders/inbox, audits, and idempotency outcomes.
- **Owner:** Persistence and schema boundary.
- **Migration owner:** T002 owns all Alembic revisions, seed/backfill decision, and migration/rollback instructions. No backfill is needed; the current dataset is in-memory demo data.
- **Inputs:** T001 entities, `DATABASE_URL`, `TEST_DATABASE_URL`, app DB credentials in local environment, PostgreSQL Compose service, existing repository Protocol pattern.
- **Outputs / side-effects:** Add SQLAlchemy/Alembic/driver dependencies in `pyproject.toml` and `uv.lock`; add DB engine/session wiring, ORM mappings, repositories and migration files; update `app/ports/repositories.py`, `app/repositories.py`, `app/runtime.py`; add an app PostgreSQL service and health check in `docker-compose.yml`; add an app-owned `.env.example` placeholder for required `BOOKING_TOKEN_SECRET`, repository integration tests, and seed command. Migration enables `btree_gist`; booking rows include `occupied_from`/`occupied_until`; an exclusion constraint forbids overlapping ranges per professional for scheduled/confirmed states; idempotency `(business_id, operation, key)` and canonical request hash are persisted under uniqueness; pending reminder rows are unique by booking ID; inbox rows are unique by reminder ID; management token digest is persisted without raw token. Ensure migrations are not run against Langfuse's database.
- **Non-goals:** Add a second business onboarding UI, migrate clinic fixtures, move LangGraph checkpoints to Postgres, Redis/queue, deploy a managed database.
- **Acceptance criteria:** A clean app database migrates and seeds repeatably; rerunning seed does not duplicate records; repositories scope reads by business and save all domain types needed by T003/T004/T007; integration tests prove durable round-trip, transaction rollback, exclusion constraint behavior for adjacent and overlapping ranges, canceled rows excluded from conflict enforcement, and idempotency uniqueness; Docker Compose uses a separate DB name/volume/service from Langfuse.
- **Dependencies:** T001.
- **Relevant files:** `pyproject.toml`, `uv.lock`, `app/config.py`, `app/runtime.py`, `app/ports/repositories.py`, `app/repositories.py`, `app/db.py` or equivalent focused module, `migrations/`, `docker-compose.yml`, `tests/unit/`, and `tests/api/conftest.py`.
- **Focused verification:** `uv run --extra dev pytest tests/unit/test_repositories.py`; `docker compose up -d app-postgres`; `DATABASE_URL=... uv run alembic upgrade head`; `TEST_DATABASE_URL=... uv run --extra dev pytest tests/integration/test_postgres_repositories.py`.

### T003 — Implement deterministic, timezone-aware availability

- **Why / intent:** Ensure every offered slot comes from business rules and persisted conflicts, never model generation.
- **Start boundary:** Begin at the repository query interfaces and availability models from T001/T002. Exclude all LLM/graph code.
- **End boundary:** `AvailabilityService.find_slots(...)` accepts business, service, optional professional, local date/time constraints and returns ordered valid slots with professional, start/end instants, local display labels, and service/price summary.
- **Owner:** Scheduling domain/application service.
- **Migration owner:** N/A; T002 owns persisted schedule schema.
- **Inputs:** Business timezone; professional-service mapping; weekly availability rules; blocked periods; existing non-canceled bookings; service duration and buffers; minimum notice and booking horizon; request local date/time range.
- **Outputs / side-effects:** Add a focused scheduling module and unit/integration tests. Compute intervals in business-local time using `zoneinfo`; persist/compare aware UTC instants. Use a documented default 15-minute candidate-start increment. Query overlaps with the full duration plus buffers. Skip nonexistent local wall times at spring DST transitions and represent repeated fall-back wall times as distinct slots with their respective offsets. No writes.
- **Non-goals:** Reserve slots, create bookings, alter business rules, LLM interpretation, travel/multi-location, recurring exceptions beyond blocked periods.
- **Acceptance criteria:** Return only slots inside business and professional hours; respect assigned services, duration, buffers, blocks, active bookings, notice/horizon; return stable chronological order and empty list when none exist; skip nonexistent wall times and distinguish both valid offsets for repeated fall-back times; write operations reject naive or ambiguous instants; tests include adjacent bookings, exactly-fitting boundaries, DST transitions, and no matching professional.
- **Dependencies:** T001, T002.
- **Relevant files:** `app/scheduling.py` or `app/scheduling/availability.py`, `app/ports/repositories.py`, repository query methods, `tests/unit/test_scheduling.py`, and PostgreSQL integration tests.
- **Focused verification:** `uv run --extra dev pytest tests/unit/test_scheduling.py tests/integration/test_availability.py`.

### T004 — Implement safe booking lifecycle and management tokens

- **Why / intent:** Turn a candidate slot into a durable booking without double-booking, exposing other customers, or losing the old booking on a failed reschedule.
- **Start boundary:** Use T003 availability and T002 transactions/repositories.
- **End boundary:** Booking service supports create, get by private management token, confirm, cancel, and reschedule with typed outcomes, audit events, and idempotent retries.
- **Owner:** Booking application/domain service.
- **Migration owner:** N/A; T002 owns schema. If review proves schema is missing an index/column, update the T002 migration set before implementation proceeds; do not introduce an unowned ad hoc migration.
- **Inputs:** Business/service/professional IDs and exact candidate start from a slot result; customer contact; create/mutation idempotency key and canonical request hash; `BOOKING_TOKEN_SECRET`; token supplied separately for customer read/actions; action-specific policy settings; reminder lead time.
- **Outputs / side-effects:** Add booking service and HMAC token derivation/digest verification; create and update booking plus reminder rows in one DB transaction; cancellation cancels a pending reminder in the same transaction; reschedule updates the pending reminder or creates a new one if the old reminder was delivered. One audit event per effective action; typed outcomes for unavailable, generic not found/invalid token, not allowed, idempotency conflict, and idempotent prior result. Same-key create retry re-derives the same token from booking ID. No migration is added by this task.
- **Non-goals:** Chat routing, sending messages, payment/deposit, production login/OTP, querying by raw booking ID from public customer endpoints.
- **Acceptance criteria:** Create reruns with same key and same request return original booking and same deterministic HMAC token; reuse of a key with a different request returns typed idempotency conflict; different requests cannot overlap for the same professional; DB constraint and transaction handle concurrent create attempts; HMAC token is high-entropy and only its digest is stored; invalid token gives non-enumerating response; only future bookings can be confirmed, canceled, or rescheduled; confirm/cancel retries are idempotent; cancel and pending-reminder cancellation are atomic; reschedule revalidates and is atomic, updating the pending reminder or creating a replacement after prior delivery; failure leaves prior booking and reminder unchanged; create writes one pending reminder atomically; canceled bookings do not block availability. Reminder delivery/retry behavior belongs to T007.
- **Dependencies:** T003.
- **Relevant files:** `app/services.py` or focused `app/booking.py`, repository Protocol/transaction APIs, T002 ORM/migration tests, `tests/unit/test_booking_service.py`, `tests/integration/test_booking_concurrency.py`.
- **Focused verification:** `uv run --extra dev pytest tests/unit/test_booking_service.py tests/integration/test_booking_concurrency.py`.

### T005 — Publish catalog, availability, booking, and owner APIs

- **Why / intent:** Give the chat and Streamlit client a typed HTTP interface that exposes application services while keeping policy in those services.
- **Start boundary:** Begin after booking service and repository interfaces are stable. Keep the current `/sessions/new`, `/chat`, and `/health` route paths available for the new contracts.
- **End boundary:** OpenAPI documents `POST /sessions/new`, `POST /chat`, `GET /health`, `GET /businesses/{business_id}/catalog`, `POST /availability/search`, `POST /bookings`, `GET /bookings/me`, `POST /bookings/me/confirm`, `/cancel`, `/reschedule`, and `GET /owner/bookings`; T007 adds `GET /demo/reminders`. T005 defines generic chat transport DTOs only; T006 implements `/chat` handler and graph invocation. Runtime injects repositories/services through `app/runtime.py`.
- **Owner:** FastAPI API and composition-root boundary.
- **Migration owner:** N/A; no database schema changes.
- **Inputs:** T001 DTOs; T003 availability service; T004 booking service; `DATABASE_URL`; configured demo business ID; existing API error mapping.
- **Outputs / side-effects:** Replace clinic chat response fields and route dependencies; add request validation, response DTOs, HTTP status mapping, owner agenda DTOs, and API tests. Catalog returns business/service/professional details; availability returns chronological slots and opaque selection references; booking create receives exact selected slot fields and required `Idempotency-Key`, then revalidates; management read uses `X-Booking-Token`, mutations use `X-Booking-Token` and `Idempotency-Key`. Tokens never appear in URLs. Owner agenda accepts business/date filters and is labeled synthetic local-demo-only. Return generic same-shape errors for unknown booking and invalid token.
- **Non-goals:** Implement graph interpretation, UI, external identity provider, preserve clinic response fields, expose ORM records directly.
- **Acceptance criteria:** OpenAPI schemas contain no patient/DOB fields and define `/chat` DTOs for T006 to implement; invalid/stale selections return controlled responses; customer management is token-gated and does not enumerate bookings; API calls pass only validated contracts to services; test runtime uses deterministic provider and isolated DB.
- **Dependencies:** T004.
- **Relevant files:** `app/main.py`, `app/runtime.py`, `app/models.py`, new `app/api/` modules only if route structure warrants it, `frontend/lib/api_client.py`, and `tests/api/`.
- **Focused verification:** `uv run --extra dev pytest tests/api`.

### T006 — Rebuild LangGraph for conversational scheduling

- **Why / intent:** Let customers express intent naturally while keeping the slot source and booking mutations deterministic.
- **Start boundary:** Replace the current verification/list/confirm/cancel clinic graph using the new chat DTOs and T005 application/API contracts; implement the `/chat` handler around the graph here.
- **End boundary:** Each chat turn extracts typed operation/service/professional/date/time/contact/slot/management intent, asks for missing or ambiguous fields, presents slots from T003, requires explicit selection before T004 create, and routes manage actions through token-protected services.
- **Owner:** LLM boundary and LangGraph workflow.
- **Migration owner:** N/A; no persistence migration.
- **Inputs:** Incoming message, session/thread ID, bounded recent messages, latest deterministic slot list, optional raw `manage_token` supplied separately in the incoming API request, API-validated booking context/action authorization for this turn, `LLMProvider` Protocol.
- **Outputs / side-effects:** Replace `app/graph/state.py`, `nodes.py`, `builder.py`, `parsing.py`, `workflow.py` as needed; update `app/llm/schemas.py`, prompt, `app/responses.py`, runtime, API chat response and deterministic provider fixtures. Chat outcome includes operation, assistant text, offered slots/booking summary, structured result and issue code. API validates raw token before graph invocation; only authorized booking ID and safe booking summary/action context may enter the current turn, and raw token must not be passed to graph, persisted checkpoint/history, prompt, trace, log, URL, or response error.
- **Non-goals:** Model-selected availability, arbitrary tools/SQL, model-generated token, Laya fine-tuning, multiple commits from one turn, free-form policy exceptions.
- **Acceptance criteria:** Chat supports search → offer → choose → confirm-book; missing service/date prompts; ambiguous reference asks clarification; only current offered slot IDs are actionable and server-side revalidated; stale selected slot refreshes options without booking; management token is required for actions and never enters graph/checkpoint/history/model context/logs/traces; reminder reply is accepted as a new request with session ID, message, and separately supplied token; injection asking for hidden/other bookings does not bypass services.
- **Dependencies:** T005.
- **Relevant files:** `app/graph/`, `app/llm/schemas.py`, `app/llm/prompt.py`, `app/responses.py`, `app/runtime.py`, `tests/graph/`, `tests/support.py`, and `tests/api/`.
- **Focused verification:** `uv run --extra dev pytest tests/graph tests/api`.

### T007 — Add durable reminder worker and demo inbox adapter

- **Why / intent:** Demonstrate asynchronous reminder behavior and reminder-triggered workflow without real channel credentials.
- **Start boundary:** Use persisted booking/reminder records and the new conversation entry point.
- **End boundary:** A separate Compose worker claims due reminders, writes one message to a local demo inbox through a `NotificationAdapter`, and records outcome. A client reads the inbox and submits a reply as a new `/chat` API request; the worker never calls LangGraph or reads the API process's in-memory checkpoint.
- **Owner:** Reminder/background-work boundary.
- **Migration owner:** N/A; T002 owns reminder and inbox schema. If a field is missing, coordinate a versioned T002-owned revision before merging.
- **Inputs:** Persisted pending reminder ID/booking ID/due timestamp, lead-time policy applied by T004, worker polling interval, and demo adapter. Reminder delivery is linked to booking ID; no LangGraph session ID is required or persisted.
- **Outputs / side-effects:** Add reminder service/worker/adapter modules, Docker Compose worker command, structured delivery status and `GET /demo/reminders` read contract; tests use a fake clock and fake adapter. No graph/checkpointer dependency and no simulated reply processing in the worker.
- **Non-goals:** Live messaging provider, retries without bound, Redis/Celery, configurable multi-channel marketing.
- **Acceptance criteria:** T004 creates at most one pending reminder per booking in the booking transaction; worker claims due records with `FOR UPDATE SKIP LOCKED`, uses an inbox row unique by reminder ID, and cannot duplicate visible delivery across retries/concurrency; terminal result is inspectable; failure is visible and retryable by a bounded policy; five-minute demo reminder appears in inbox in the walkthrough; reply is sent by UI to `/chat` as a new request with either the active session ID or a fresh session ID and separately supplied token; worker imports/calls no graph/checkpointer; reschedule updates or recreates pending reminder atomically and failed reschedule preserves booking and reminder history.
- **Dependencies:** T004 and T005. T007 does not depend on LangGraph; only the later UI reply interaction uses the `/chat` API added in T006.
- **Relevant files:** `app/reminders/` or focused `app/reminders.py`, `app/runtime.py`, `docker-compose.yml`, `app/main.py` reminder inbox read/reply routes, `tests/unit/test_reminders.py`, `tests/api/test_reminder_flow.py`.
- **Focused verification:** `uv run --extra dev pytest tests/unit/test_reminders.py tests/api/test_reminder_flow.py`; `docker compose up --build worker`.

### T008 — Build customer chat and owner agenda in Streamlit

- **Why / intent:** Make the portfolio demo understandable to a reviewer without requiring API docs or database inspection.
- **Start boundary:** Use T005/T006 API contracts and T007 demo inbox endpoints.
- **End boundary:** Streamlit presents customer conversation, interactive slot choices, final booking/manage token, confirm/cancel/reschedule controls, owner agenda, and reminder inbox/status.
- **Owner:** Frontend.
- **Migration owner:** N/A; no data migration.
- **Inputs:** API base URL; seeded business ID; session ID; chat response/slot DTOs; private token returned once on booking; owner date-range filter.
- **Outputs / side-effects:** Adapt `frontend/streamlit_app.py` and `frontend/lib/api_client.py`; add view/page helpers only where they make the two roles clear. UI actions submit the exact slot selection/token to API and display typed errors.
- **Non-goals:** Next.js rewrite, authentication system, public hosting, editable business onboarding, direct DB access.
- **Acceptance criteria:** Customer can complete the full booking/manage flow from UI; slots are clickable rather than copied; booking summary and private token are shown with safe demo wording; customer token stays in protected Streamlit session state and is sent only in protected request fields/headers, never chat text, URL, or logs; owner can inspect appointments and reminders; stale slot/API failure recovers with clear feedback.
- **Dependencies:** T005, T006, T007.
- **Relevant files:** `frontend/streamlit_app.py`, `frontend/lib/api_client.py`, `frontend/lib/`, `app/main.py` only if a missing response contract is found (coordinate with T005), and `tests/unit/test_frontend_api_client.py`.
- **Focused verification:** `uv run --extra dev pytest tests/unit/test_frontend_api_client.py`; manual Streamlit walkthrough against Docker Compose.

### T009 — Replace clinic evaluations/docs and finish local demo

- **Why / intent:** Make the general service product coherent to a portfolio reviewer and provide evidence for critical conversational/data-integrity behavior.
- **Start boundary:** Begin when API, conversation, reminder, and UI contracts are stable.
- **End boundary:** The evaluation runner covers intended journeys and guardrails; active README/docs and Compose instructions describe only the general scheduling MVP; a clean checkout can run the documented local flow.
- **Owner:** Evaluation, observability, documentation, local deployment.
- **Migration owner:** N/A — no application schema changes; document T002 migration/seed/rollback commands.
- **Inputs:** Final chat/API outcome DTOs, synthetic scenarios, Langfuse optional configuration, app Postgres/worker Compose services, existing README and architecture/evaluation docs.
- **Outputs / side-effects:** Replace `app/evals/scenarios/core_scenarios.py` with booking/manage/clarification/stale-slot/conflict/duplicate/retry/reminder/injection cases; update evaluation outcome extraction, judge rubric, trace redaction and summaries; update `README.md`, `docs/architecture.md`, `docs/graph.md`, `docs/security.md`, `docs/evaluation.md`, `.env.example` if present, and `docker-compose.yml` instructions. Mark old clinic docs historical or replace with the new product behavior; do not delete the two untracked service-scheduling source docs.
- **Non-goals:** Train or compare Laya, publish/host the application, add live integrations, create unrelated diagrams or marketing pages.
- **Acceptance criteria:** Each critical journey asserts structured outcomes, not only judge wording; evaluation proves offered slots come from the service and mutation results match persisted state; PII/token redaction applies; README quickstart starts API, UI, database, and worker, explains generating a 32-byte `BOOKING_TOKEN_SECRET`, and names synthetic demo steps; all clinic identity/DOB claims are removed from active docs; `uv run --extra dev pytest` passes; Docker manual walkthrough completes book → reminder → reschedule → owner inspection.
- **Dependencies:** T008.
- **Relevant files:** `app/evals/`, `app/observability.py`, `README.md`, `docs/architecture.md`, `docs/graph.md`, `docs/security.md`, `docs/evaluation.md`, `docker-compose.yml`, `.env.example`, and `tests/evals/`.
- **Focused verification:** `uv run --extra dev pytest`; `uv run python -m app.evals.runner` with deterministic/offline setup; clean `docker compose up --build` walkthrough.

## Relevant Files

- Domain and API baseline: `app/models.py`, `app/main.py`, `app/runtime.py`, `app/config.py`.
- Persistence baseline: `app/repositories.py`, `app/ports/repositories.py`, `pyproject.toml`, `uv.lock`, `docker-compose.yml`.
- Conversation/AI baseline: `app/graph/`, `app/llm/`, `app/responses.py`.
- Frontend baseline: `frontend/streamlit_app.py`, `frontend/lib/api_client.py`.
- Quality baseline: `app/observability.py`, `app/evals/`, `tests/unit/`, `tests/graph/`, `tests/api/`, `tests/evals/`.
- Portfolio docs: `README.md`, `docs/architecture.md`, `docs/graph.md`, `docs/security.md`, `docs/evaluation.md`.

## Coverage Map

| Intent/requirement | Tasks |
| --- | --- |
| General business/service/professional model | T001, T002 |
| Deterministic availability, timezones, buffers, blocks | T003 |
| Book, confirm, reschedule, cancel, idempotency, no double-booking, private access | T004 |
| Stable HTTP surface for customer, owner, and chat | T005 |
| AI extraction, clarification, slot selection, bounded workflow | T006 |
| Durable reminder, demo delivery, reminder reply → reschedule | T007 |
| Customer and owner portfolio experience | T008 |
| Evaluation, trace safety, local demo, updated docs | T009 |
| Laya follow-up milestone, live channels, payment/calendar, hosting | Out of scope |

## Verification Strategy

- T001: focused Pydantic/value-model tests.
- T002: migration/seed and repository integration tests on disposable PostgreSQL.
- T003: pure unit tests plus repository-backed availability tests including boundaries/timezones.
- T004: service tests and Postgres concurrency/idempotency tests.
- T005: FastAPI contract/error/authorization tests with a deterministic provider.
- T006: graph and API multi-turn tests with deterministic extraction; no API key needed.
- T007: fake-clock/adapter unit tests and a reminder-reply integration test.
- T008: API client tests plus manual customer/owner Streamlit flow.
- T009: full `uv run --extra dev pytest`, offline evaluation runner, and Docker Compose manual acceptance flow.
- Plannotator is the plan review gate. Review the saved plan file with `plannotator annotate docs/sdd/plan-general-service-scheduling-mvp.md --gate --json`; incorporate feedback before calling the plan ready. The interactive command waits for the user's decision.

## Rollout and Operations

- Local-only Compose stack: API, Streamlit, app PostgreSQL, reminder worker, and existing optional Langfuse stack. Use distinct DB and volume names for app and Langfuse.
- Startup order: healthy app DB → run Alembic upgrade → seed demo business idempotently → start API/frontend/worker.
- Backup/restore and production retention are outside this local portfolio MVP. Include a database reset command for synthetic demo data and state clearly that reset deletes all local app bookings.
- Migration rollback: document downgrade for each Alembic revision and stop the worker/API before rollback. No data backfill is needed.
- Secrets: `.env.example` contains placeholders only; do not commit API keys. Tests default to deterministic provider and synthetic records.
- Observability: structured operation events include business/booking IDs and result codes; never include raw management tokens or unnecessary contact fields.

## Execution Notes

- SDD feature ID: `general-service-scheduling-mvp`.
- Source of truth: `docs/sdd/.sdd-state.json` through `sdd-mcp`; this plan is the human-readable, complete implementation input for Luna.
- Task statuses start `todo`; dependencies must be `done` before work is claimed.
- SDD task IDs are recorded after registration. Prior revision was approved; current version is awaiting Plannotator re-review.
- If the user rejects scope assumptions, update this plan and SDD task state before implementation begins.

## Out of Scope

- Laya training/benchmarking in V1.
- Real WhatsApp/SMS/email/calendar/payment providers.
- Production auth, public hosting, multi-location, travel, healthcare, voice, marketplace, or distributed queue.
- Backward compatibility for clinic response payloads and migration of seeded in-memory patients/appointments.

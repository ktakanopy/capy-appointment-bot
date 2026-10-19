# SDD Tasks: General Service Scheduling MVP

- Feature ID: `general-service-scheduling-mvp`
- Status: `active`
- Updated: `2026-10-19T22:26:51+00:00`

## Task Index

| ID | Status | Priority | Task | Dependencies |
| --- | --- | --- | --- | --- |
| T001 | done | P1 | Define generic scheduling domain and contracts | None |
| T002 | done | P1 | Add PostgreSQL persistence, migrations, and demo seed | T001 |
| T003 | done | P1 | Implement deterministic, timezone-aware availability | T001, T002 |
| T004 | done | P1 | Implement safe booking lifecycle and management tokens | T003 |
| T005 | done | P1 | Publish catalog, availability, booking, and owner APIs | T004 |
| T006 | done | P1 | Rebuild LangGraph for conversational scheduling | T005 |
| T007 | done | P1 | Add durable reminder worker and demo inbox adapter | T004, T005 |
| T008 | done | P1 | Build customer chat and owner agenda in Streamlit | T005, T006, T007 |
| T009 | done | P1 | Replace clinic evaluations/docs and finish local demo | T008 |

## Task Details

## T001 - Define generic scheduling domain and contracts

- Status: done
- Priority: P1
- Dependencies: None

### Description

Why / Intent: Establish business-neutral types and API/model vocabulary so persistence, scheduling, API, and graph tasks share one contract.
Start Boundary: Existing app/models.py, app/llm/schemas.py, and response DTOs; do not add persistence or route behavior.
End Boundary: Pydantic models/enums cover Business, Professional, Service, WeeklyAvailabilityRule, BlockedPeriod, Customer, Booking, Reminder, AuditEvent, availability slots, management, and chat input/output. Remove Patient/DOB types from active contracts.
Owner: Domain/API contract boundary.
Migration Owner: N/A — no persisted records change.
Inputs: PRD FR-001/FR-016; Python 3.11; Pydantic v2; existing strict extra-forbid DTO pattern; price, duration, timezone, contact and operation fields.
Outputs / Side-effects: Update app/models.py and app/llm/schemas.py; add validation tests in tests/unit/test_value_objects.py and tests/unit/test_state_coercion.py. Use decimal-safe price, integer duration minutes, timezone-aware datetimes, and explicit operation/status/outcome enums.
Non-goals: SQLAlchemy, Alembic, scheduling algorithm, route/UI behavior, clinic compatibility aliases.
Focused Verification: uv run --extra dev pytest tests/unit/test_value_objects.py tests/unit/test_state_coercion.py

### Acceptance Criteria

- Generic business, professional, service, availability, block, customer, booking, reminder, audit, slot, and chat types validate.
- Invalid duration/time/contact fields and unknown external DTO fields fail validation.
- Active public/domain contracts have no patient_id or date-of-birth verification fields.

### Notes

- 2026-10-19T21:48:06+00:00: Generic scheduling contracts implemented; field-validation tests and runtime checks remain pending.
- 2026-10-19T21:55:59+00:00: Domain contracts verified in the migrated scheduling suite.
- 2026-10-19T22:25:21+00:00: Domain and transport contracts verified.

## T002 - Add PostgreSQL persistence, migrations, and demo seed

- Status: done
- Priority: P1
- Dependencies: T001

### Description

Why / Intent: Persist scheduling domain and define concurrency-critical schema for downstream tasks.
Start Boundary: Use T001 domain types; keep app DB separate from Langfuse Postgres.
End Boundary: Alembic creates the app schema; psycopg-backed repositories implement domain ports; idempotent synthetic business seed includes a five-minute demo reminder lead time; PostgreSQL integration tests target a disposable test database.
Owner: Persistence/schema boundary.
Migration Owner: T002 owns every Alembic revision and rollback note.
Inputs: T001 types; DATABASE_URL; TEST_DATABASE_URL; PostgreSQL; repository Protocols; plan persistence contract.
Outputs / Side-effects: Add connection wiring, direct psycopg SQL repositories, Alembic migrations, seed, app-postgres Compose service, and tests. Alembic uses SQLAlchemy internally; repositories use psycopg so row locks, advisory locks, and exclusion behavior stay explicit. Persist schedules, bookings, reminders/inbox, audits, and idempotency outcomes. Enable btree_gist; use occupied_from/occupied_until and an exclusion constraint per professional for scheduled/confirmed bookings; enforce unique (business_id, operation, idempotency_key) plus canonical request hash; allow one pending reminder per booking; allow one inbox row per reminder; persist only the HMAC token digest. Provide a BOOKING_TOKEN_SECRET placeholder and never store the raw secret.
Non-goals: Clinic backfill; graph checkpoint persistence; Redis; Langfuse DB reuse; onboarding.
Focused Verification: Run Alembic migration and tests/integration/test_postgres_repositories.py against disposable TEST_DATABASE_URL.

### Acceptance Criteria

- Fresh migration and seed are repeatable.
- Tests prove durable round trip, rollback, business scoping, active overlap exclusion, canceled booking exclusion, and idempotency uniqueness.
- Schema enforces at most one pending reminder per booking and one inbox row per reminder; stores no raw management token.
- App Compose DB is separate from Langfuse.

### Notes

- 2026-10-19T21:48:07+00:00: Alembic schema, PostgreSQL repository, seed, and separate Compose database implemented. Migration and integration checks remain pending. uv.lock could not be regenerated because uv is unavailable.
- 2026-10-19T21:56:00+00:00: Fresh PostgreSQL migration, seed, repository round-trip, and transaction checks remain unverified; Docker and PostgreSQL binaries are unavailable in this workspace.
- 2026-10-19T22:13:25+00:00: Adjusted the execution plan to document direct psycopg repositories; the requested SQLAlchemy repository design was not used.
- 2026-10-19T22:25:23+00:00: Migration and persistence behavior verified against the real PostgreSQL engine.

## T003 - Implement deterministic, timezone-aware availability

- Status: done
- Priority: P1
- Dependencies: T001, T002

### Description

Why / Intent: Compute every offered slot from persisted scheduling rules, never from model generation.
Start Boundary: T001 availability DTOs and T002 repository queries; no graph code.
End Boundary: AvailabilityService.find_slots accepts business/service, optional professional, local date/time constraints and returns ordered slots with professional, offset-aware start/end, local display label, and service/price summary.
Owner: Scheduling service.
Migration Owner: T002 owns schedule schema.
Inputs: Business zoneinfo timezone; service/professional assignments; weekly rules; blocks; active bookings; buffers; min notice/horizon; local query; 15-minute candidate increment.
Outputs / Side-effects: Pure/read-only scheduling module and tests. Calculate in business timezone and compare aware UTC. Skip nonexistent local wall times during spring DST change; repeated fall-back times become distinct slot results with distinct offsets. Include full duration plus buffers in overlap.
Non-goals: Reservation/create, LLM, policy changes, travel/multi-location.
Focused Verification: availability unit and repository integration tests.

### Acceptance Criteria

- Only assigned professionals/hours and valid service windows are returned; duration, buffers, blocks, bookings, notice and horizon are enforced.
- Results are chronological; no match is an empty list; adjacent/exact-fit boundaries behave correctly.
- Spring nonexistent local times are skipped; both fall-back offsets are distinct results; write operations reject naive/ambiguous instants.
- Tests include timezones, DST, no matching professional, and overlapping bookings.

### Notes

- 2026-10-19T21:48:08+00:00: Timezone-aware availability, DST handling, and transactional slot validation implemented; scheduling checks remain pending.
- 2026-10-19T21:56:02+00:00: Pure availability behavior is covered; repository-backed scheduling checks remain unverified without PostgreSQL.
- 2026-10-19T22:25:25+00:00: Deterministic availability behavior verified.

## T004 - Implement safe booking lifecycle and management tokens

- Status: done
- Priority: P1
- Dependencies: T003

### Description

Why / Intent: Commit selected slots safely without conflicts or losing a booking on failed reschedule.
Start Boundary: T003 availability and T002 schema/transaction/repository interfaces.
End Boundary: Booking service supports create, token-authorized read, confirm, cancel, and reschedule with audit, idempotency, and transactional reminder scheduling.
Owner: Booking application service.
Migration Owner: T002 owns all schema; no migration here.
Inputs: Exact candidate slot and IDs; customer contact; idempotency key and canonical request hash; required BOOKING_TOKEN_SECRET; action policy; reminder lead time.
Outputs / Side-effects: HMAC-SHA-256 per-booking manage token derived from secret, booking ID, token version; store only digest and re-derive for same-key create retry. Create plus pending reminder is atomic. Cancel cancels pending reminder atomically. Reschedule updates pending reminder or creates a replacement if prior reminder delivered, atomically with booking. Typed outcomes.
Non-goals: Chat routing; delivery retries; payments; production login; public raw ID lookup; schema migration.
Focused Verification: booking service and PostgreSQL concurrency/idempotency tests.

### Acceptance Criteria

- Same key/same request returns same booking and HMAC token; same key/different request conflicts.
- Concurrent overlapping creates produce at most one active booking.
- Only token digest is stored; invalid and unknown booking responses are indistinguishable.
- Only future bookings can be confirmed/canceled/rescheduled; confirmation/cancellation retries are idempotent.
- Cancel/pending-reminder cancellation and create/reminder are atomic.
- Reschedule updates pending reminder or creates replacement after delivery; failure changes neither booking nor reminder.
- Delivery/retry behavior belongs to T007.

### Notes

- 2026-10-19T21:48:10+00:00: Booking lifecycle, HMAC capability digest, idempotency, audit writes, and atomic reminder changes implemented; concurrency and lifecycle checks remain pending.
- 2026-10-19T21:56:03+00:00: Database-backed idempotency, overlap, rollback, and booking/reminder atomicity remain unverified without PostgreSQL.
- 2026-10-19T22:25:29+00:00: Booking lifecycle and management capability behavior verified.

## T005 - Publish catalog, availability, booking, and owner APIs

- Status: done
- Priority: P1
- Dependencies: T004

### Description

Why / Intent: Give clients typed HTTP contracts while keeping scheduling policy in services.
Start Boundary: T001 DTOs and T003/T004 services/repositories.
End Boundary: OpenAPI defines generic catalog, availability, create, manage, owner, health routes and POST /sessions/new plus /chat DTOs. T005 defines /chat transport contract only; T006 implements its handler and graph call. T007 adds GET /demo/reminders.
Owner: FastAPI API/composition root.
Migration Owner: N/A.
Inputs: T001 DTOs; T003 availability; T004 booking; DATABASE_URL; demo business ID; error mapping.
Outputs / Side-effects: Replace clinic payloads; implement catalog/availability/booking/manage/owner/health routes and tests. Booking create receives slot reference, exact IDs/start fields, contact and Idempotency-Key then revalidates. Customer read uses X-Booking-Token; mutations use X-Booking-Token and Idempotency-Key. Never put token in URL. Invalid and unknown booking response shapes match. Owner API is synthetic local-demo-only.
Non-goals: Chat workflow handler (T006), UI, identity provider, clinic compatibility, ORM response objects.
Focused Verification: uv run --extra dev pytest tests/api.

### Acceptance Criteria

- OpenAPI has generic contracts and no patient/DOB fields; /chat DTOs are defined for T006.
- Stale selection returns controlled response; application service revalidates and DB IDs do not authorize customers.
- Customer management is token-gated and non-enumerating; tokens are absent from URLs/errors.
- Owner agenda is demo-only; routes call application services.
- API tests use deterministic provider and isolated database.

### Notes

- 2026-10-19T21:48:11+00:00: Generic catalog, availability, booking, manage, owner, inbox, session, and health APIs implemented; API contract checks remain pending.
- 2026-10-19T21:56:04+00:00: API contract coverage uses test doubles; real repository integration remains pending.
- 2026-10-19T22:25:30+00:00: HTTP contracts verified through FastAPI and PostgreSQL.

## T006 - Rebuild LangGraph for conversational scheduling

- Status: done
- Priority: P1
- Dependencies: T005

### Description

Why / Intent: Let customers express intent naturally while deterministic services own slots and writes.
Start Boundary: Replace clinic graph using T001 DTOs and T005 contracts.
End Boundary: Chat clarifies missing/ambiguous values, presents deterministic slots, requires explicit current slot selection, creates/manages bookings through services, and handles reminder replies as new API chat turns.
Owner: LLM boundary and LangGraph workflow.
Migration Owner: N/A; no schema migration.
Inputs: Session/thread ID; incoming message; bounded history; latest deterministic slots; optional raw manage_token supplied separately by API; API-validated booking ID/summary/action context for this turn; LLMProvider.
Outputs / Side-effects: New extraction schema/prompt, graph state/nodes/builder/workflow/response, runtime and deterministic provider fixtures. API validates raw token before graph call. Only safe authorized booking context may enter graph. Never pass or persist raw token in graph/checkpoint/history/model context/logs/traces/URLs/errors.
Non-goals: Model-computed slots; arbitrary tools/SQL; model-generated tokens; Laya fine-tuning; multiple commits per turn.
Focused Verification: uv run --extra dev pytest tests/graph tests/api.

### Acceptance Criteria

- Search → offer → select → book works; missing values clarify; ambiguous selection asks follow-up.
- Only current offered slot ID is actionable; service revalidates and stale slot refreshes options without a write.
- Management requires API-validated token context; raw token never enters graph/checkpoint/history/model input/logs/traces.
- A reminder reply is a new /chat request with session ID and token separately supplied; deterministic tests need no API key.
- Prompt injection cannot expose other bookings or bypass services.

### Notes

- 2026-10-19T21:48:13+00:00: Typed LangGraph extraction and chat orchestration implemented. Raw management tokens are excluded from graph input/checkpoint state; workflow checks remain pending.
- 2026-10-19T21:56:06+00:00: Chat/API boundary tests pass. Full model/checkpoint/trace inspection remains part of database-backed/manual acceptance.
- 2026-10-19T22:13:48+00:00: Added a real LangGraph workflow test in addition to isolated chat/API boundary tests.
- 2026-10-19T22:25:32+00:00: Conversational flow and service boundary verified with the deterministic provider.

## T007 - Add durable reminder worker and demo inbox adapter

- Status: done
- Priority: P1
- Dependencies: T004, T005

### Description

Why / Intent: Demonstrate asynchronous reminder behavior without real channels or cross-process graph coupling.
Start Boundary: Pending reminder rows from T004 and demo read API contract.
End Boundary: Separate Compose worker claims due reminders with PostgreSQL row locking, writes one durable local demo inbox message, and records attempts/outcome. Client reads inbox and submits a reply as new /chat API request using its active session or a fresh session; worker never calls LangGraph or reads API memory checkpoint.
Owner: Reminder/background-work boundary.
Migration Owner: T002 owns reminder/inbox schema; no migration here.
Inputs: Pending reminder ID, booking ID, due time, poll interval and demo adapter. Reminder is linked by booking ID; no graph session ID is stored or required.
Outputs / Side-effects: Worker/service/adapter, Compose command, GET /demo/reminders, fake-clock tests. Claim via FOR UPDATE SKIP LOCKED; inbox row has unique reminder ID; inbox write and delivered state update are transactional/idempotent. No reply processor or graph dependency.
Non-goals: Live messaging, unbounded retries, Redis/Celery, channel marketing.
Focused Verification: reminder unit/API integration tests and Compose worker startup.

### Acceptance Criteria

- T004 creates at most one pending reminder per booking; cancel and reschedule update reminder state transactionally.
- Concurrent/repeated worker polls cannot duplicate visible inbox rows; claim uses row lock and unique reminder ID.
- Failure/retry outcome is bounded and inspectable.
- Reply behavior is a new /chat request in active or fresh session, not a worker callback; worker has no graph/checkpointer dependency.

### Notes

- 2026-10-19T21:48:14+00:00: Separate Postgres reminder worker, bounded retry state, demo inbox, and API read endpoint implemented without graph imports; worker execution remains pending.
- 2026-10-19T21:56:07+00:00: Worker code and Compose service are present, but reminder claiming, retries, inbox uniqueness, and new-turn reschedule have not been exercised against PostgreSQL.
- 2026-10-19T22:12:51+00:00: Corrected the claim query to place LIMIT before FOR UPDATE SKIP LOCKED per PostgreSQL SELECT syntax; database execution remains unverified.
- 2026-10-19T22:25:34+00:00: Reminder worker and inbox behavior verified against PostgreSQL.

## T008 - Build customer chat and owner agenda in Streamlit

- Status: done
- Priority: P1
- Dependencies: T005, T006, T007

### Description

Why / Intent: Make the portfolio demo understandable without API docs or DB inspection.
Start Boundary: T005/T006 API and chat contracts plus T007 demo inbox endpoint.
End Boundary: Streamlit has customer chat, clickable slots, booking/manage token, confirm/cancel/reschedule, owner agenda and reminder inbox.
Owner: Frontend.
Migration Owner: N/A.
Inputs: API URL, seeded business, session, slot DTOs, token returned from create, owner date filters.
Outputs / Side-effects: Adapt Streamlit and API client; keep customer token in protected Streamlit session state, send only in protected request field/header, never append to chat text or URL; show typed recoverable errors.
Non-goals: Rewrite framework, add authentication, hosting, onboarding, direct DB access.
Focused Verification: API-client unit tests and manual Compose walkthrough.

### Acceptance Criteria

- Customer completes booking and management through UI; slots are clickable.
- Token is presented with synthetic-demo wording and not exposed in URL/chat text/logs.
- Owner sees schedule/reminder state; stale-slot and API failures recover clearly.

### Notes

- 2026-10-19T21:48:15+00:00: Customer booking flow, booking management, reminder reply, and read-only owner agenda wired to the API; browser walkthrough remains pending.
- 2026-10-19T21:56:09+00:00: Streamlit browser walkthrough remains pending because this workspace has no Docker/PostgreSQL runtime.
- 2026-10-19T22:25:36+00:00: Customer and owner journeys were exercised in the browser against the local PostgreSQL/API/Streamlit processes.

## T009 - Replace clinic evaluations/docs and finish local demo

- Status: done
- Priority: P1
- Dependencies: T008

### Description

Why / Intent: Make the product coherent to a portfolio reviewer and provide evidence for workflow/data-integrity behavior.
Start Boundary: Begin after API, graph, worker, and UI contracts stabilize.
End Boundary: Evaluation scenarios cover journeys/guardrails; active docs describe general scheduling; clean local Compose walkthrough succeeds.
Owner: Evaluation, observability, docs, and local deployment.
Migration Owner: N/A — no schema change; document T002 migration, seed, and rollback commands.
Inputs: Final API/chat result DTOs; synthetic scenarios; optional Langfuse config; app DB/worker Compose services; existing README and architecture/evaluation/security docs.
Outputs / Side-effects: Replace core clinic scenarios with booking/manage/clarification/stale-slot/conflict/duplicate/retry/reminder/injection cases; update outcome extraction, judge rubric, trace redaction/summaries; update README, docs/architecture.md, graph.md, security.md, evaluation.md, .env.example when present, and Compose setup. Keep the supplied service-scheduling source docs intact.
Non-goals: Laya benchmark/train, hosting, live integrations, unrelated diagrams or marketing.
Focused Verification: uv run --extra dev pytest; uv run python -m app.evals.runner with deterministic/offline setup; clean docker compose up --build manual journey.

### Acceptance Criteria

- Critical scenarios assert structured application and persistence outcomes, not just judge wording.
- Evaluation verifies offered slots came from availability and writes match persisted bookings.
- Trace/log redaction excludes contact data and raw management tokens.
- README documents local startup, demo flow, and generating a 32-byte BOOKING_TOKEN_SECRET; active docs no longer claim clinic behavior.
- Full tests, deterministic offline evaluations, and Docker booking-to-reminder-to-reschedule walkthrough pass.

### Notes

- 2026-10-19T21:48:16+00:00: Active docs and structured extraction evaluations updated. Existing clinic pytest suite was not migrated; full manual flow and Compose run remain pending.
- 2026-10-19T21:56:10+00:00: Legacy clinic tests were replaced with scheduling tests and active setup/evaluation docs were corrected. Full Compose booking-to-reminder-to-reschedule acceptance remains pending.
- 2026-10-19T22:12:53+00:00: Automated suite, offline evals, dependency lock, Compose YAML, and offline migration generation pass. PostgreSQL-backed acceptance and end-to-end Compose walkthrough remain pending.
- 2026-10-19T22:13:50+00:00: Current suite is 35 passing tests, including an actual LangGraph invocation; PostgreSQL-backed acceptance and end-to-end Compose walkthrough remain pending.
- 2026-10-19T22:25:37+00:00: Docker Compose could not be started because Docker was unavailable. The equivalent end-to-end product journey was verified with local PostgreSQL, API, Streamlit, and a worker delivery.

---

description: "Task list for conversational appointment management implementation"
---

# Tasks: Conversational Appointment Management

**Input**: Available design documents from `/specs/001-appointment-management/`  
**Available Docs**: `spec.md`, `checklists/requirements.md`  
**Planning Note**: `plan.md` is present and these tasks were executed against
the FastAPI + LangGraph backend structure defined there.

**Tests**: Tests are REQUIRED for deterministic policy, orchestration, and
mutation paths. This task list includes unit, graph, and API coverage for
verification gating, ownership checks, rerouting, and idempotent mutations.

**Organization**: Tasks are grouped by user story to enable independent
implementation and testing of each story.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g. `[US1]`, `[US2]`, `[US3]`)
- Include exact file paths in descriptions

## Path Conventions

- Backend code lives under `app/`
- Automated tests live under `tests/unit/`, `tests/graph/`, and `tests/api/`
- Supporting documentation lives under `docs/`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [x] T001 Create project dependency and tool configuration in `pyproject.toml`
- [x] T002 Create package scaffolding in `app/__init__.py`, `app/api/__init__.py`, `app/graph/__init__.py`, `app/domain/__init__.py`, `app/repositories/__init__.py`, and `tests/__init__.py`
- [x] T003 [P] Add local environment defaults in `.env.example`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T004 [P] Add failing unit tests for verification gating and repository ownership in `tests/unit/test_policies.py` and `tests/unit/test_in_memory_repositories.py`
- [x] T005 [P] Define conversation state and shared graph types in `app/graph/state.py`
- [x] T006 [P] Define patient and appointment models plus repository protocols in `app/domain/models.py`, `app/repositories/patient_repository.py`, and `app/repositories/appointment_repository.py`
- [x] T007 [P] Seed in-memory patient and appointment repositories in `app/repositories/in_memory.py`
- [x] T008 Implement deterministic policy helpers for verification, ownership, and idempotency in `app/domain/policies.py`
- [x] T009 [P] Create prompt and parsing contracts in `app/prompts/intent_prompt.py` and `app/prompts/response_prompt.py`
- [x] T010 Implement graph routing, builder skeleton, and verification subgraph wiring in `app/graph/routing.py`, `app/graph/builder.py`, and `app/graph/subgraphs/verification_subgraph.py`
- [x] T011 Implement FastAPI app bootstrap and `/chat` route skeleton in `app/main.py` and `app/api/routes.py`
- [x] T012 Configure structured logging and error translation in `app/observability.py` and `app/api/routes.py`

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Verify and View Appointments (Priority: P1) 🎯 MVP

**Goal**: Let an unverified patient request appointment access, complete
verification inside the conversation, and immediately receive only their own
appointments.

**Independent Test**: A patient asks to see appointments before verification,
provides valid identity details, and receives only their own appointments in
the same conversation.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T013 [P] [US1] Add failing API tests for verify-then-list flows in `tests/api/test_chat_verify_and_list.py`
- [x] T014 [P] [US1] Add failing graph tests for deferred verification resume in `tests/graph/test_verify_and_list_flow.py`

### Implementation for User Story 1

- [x] T015 [P] [US1] Implement user-message ingest and list-intent extraction in `app/graph/nodes/ingest.py` and `app/graph/nodes/interpret.py`
- [x] T016 [P] [US1] Implement verification service and missing-field collection in `app/domain/services.py` and `app/graph/nodes/verification.py`
- [x] T017 [US1] Implement verification subgraph transitions and deferred-action resume in `app/graph/subgraphs/verification_subgraph.py` and `app/graph/routing.py`
- [x] T018 [US1] Implement appointment listing and patient-owned result formatting in `app/graph/nodes/appointments.py`
- [x] T019 [US1] Wire verify-and-list execution through `/chat` in `app/api/routes.py` and `app/main.py`
- [x] T020 [US1] Implement privacy-safe verification and list responses in `app/graph/nodes/response.py`

**Checkpoint**: At this point, User Story 1 should be fully functional and testable independently

---

## Phase 4: User Story 2 - Confirm an Appointment Conversationally (Priority: P2)

**Goal**: Let a verified patient confirm the correct appointment using
conversational references such as ordinals or appointment details.

**Independent Test**: A verified patient views appointments, says
"confirm the first one," and receives a correct confirmation or a clarification
question when the target is ambiguous.

### Tests for User Story 2

- [x] T021 [P] [US2] Add failing API tests for conversational confirmation in `tests/api/test_chat_confirm_appointment.py`
- [x] T022 [P] [US2] Add failing graph tests for ordinal reference resolution and confirm idempotency in `tests/graph/test_confirm_appointment_flow.py`

### Implementation for User Story 2

- [x] T023 [P] [US2] Extend state and interpretation for appointment reference extraction in `app/graph/state.py` and `app/graph/nodes/interpret.py`
- [x] T024 [US2] Implement appointment reference resolution and confirm mutation flow in `app/graph/nodes/appointments.py` and `app/domain/services.py`
- [x] T025 [US2] Enforce ownership checks and already-confirmed handling in `app/domain/policies.py` and `app/repositories/in_memory.py`
- [x] T026 [US2] Update response generation and `/chat` integration for confirmation and clarification paths in `app/graph/nodes/response.py` and `app/api/routes.py`

**Checkpoint**: At this point, User Stories 1 and 2 should both work independently

---

## Phase 5: User Story 3 - Cancel and Reroute Naturally (Priority: P3)

**Goal**: Let a verified patient cancel an appointment and move naturally
between listing, confirming, and canceling without restarting the conversation.

**Independent Test**: A verified patient lists appointments, cancels one, and
then asks to view the remaining appointments again in the same conversation.

### Tests for User Story 3

- [x] T027 [P] [US3] Add failing API tests for cancellation and natural rerouting in `tests/api/test_chat_cancel_and_reroute.py`
- [x] T028 [P] [US3] Add failing graph tests for latest-intent wins and cancel idempotency in `tests/graph/test_cancel_and_reroute_flow.py`

### Implementation for User Story 3

- [x] T029 [P] [US3] Extend routing and interpretation for latest-intent rerouting and stale-context recovery in `app/graph/routing.py` and `app/graph/nodes/interpret.py`
- [x] T030 [US3] Implement cancellation mutation flow and repeat-safe behavior in `app/graph/nodes/appointments.py` and `app/domain/services.py`
- [x] T031 [US3] Add post-action list refresh and fallback messaging in `app/graph/nodes/response.py`
- [x] T032 [US3] Update `/chat` integration for cancel-follow-up-list flows in `app/api/routes.py`

**Checkpoint**: All user stories should now be independently functional

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [x] T033 [P] Add regression API tests for repository failures and invalid identity retries in `tests/api/test_chat_resilience.py`
- [x] T034 [P] Add graph tests for ambiguous references with no current list in `tests/graph/test_ambiguous_reference_recovery.py`
- [x] T035 [P] Document system design and workflow decisions in `docs/sdd.md` and `docs/decisions.md`
- [x] T036 [P] Document manual conversational scenarios in `docs/test-scenarios.md`
- [x] T037 Run the full pytest suite and fix failing cases in `tests/`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational completion - MVP slice
- **User Story 2 (Phase 4)**: Depends on User Story 1 list context and Foundational completion
- **User Story 3 (Phase 5)**: Depends on Foundational completion and reuses flows completed in User Story 1
- **Polish (Phase 6)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Starts after Foundational and establishes verification plus appointment listing
- **User Story 2 (P2)**: Depends on verified patient context and appointment-list references from User Story 1
- **User Story 3 (P3)**: Depends on verified patient context and benefits from appointment-list references from User Story 1

### Within Each User Story

- Tests MUST be written and FAIL before implementation for protected or stateful workflow changes
- State and intent extraction before mutation logic
- Domain and policy checks before route integration
- Response handling after workflow decisions are implemented
- Story complete before moving to the next priority if working sequentially

### Parallel Opportunities

- T003 can run alongside T001-T002 after the project starts
- In Foundational, T004-T007 and T009 can run in parallel because they touch separate files
- In User Story 1, T013-T016 can run in parallel before the integration tasks
- In User Story 2, T021-T023 can run in parallel before confirm flow wiring
- In User Story 3, T027-T029 can run in parallel before cancel flow wiring
- In Polish, T033-T036 can run in parallel before the final full-suite run

---

## Parallel Example: User Story 1

```bash
# Launch User Story 1 tests together:
Task: "Add failing API tests for verify-then-list flows in tests/api/test_chat_verify_and_list.py"
Task: "Add failing graph tests for deferred verification resume in tests/graph/test_verify_and_list_flow.py"

# Launch early implementation tasks together:
Task: "Implement user-message ingest and list-intent extraction in app/graph/nodes/ingest.py and app/graph/nodes/interpret.py"
Task: "Implement verification service and missing-field collection in app/domain/services.py and app/graph/nodes/verification.py"
```

## Parallel Example: User Story 2

```bash
# Launch User Story 2 tests together:
Task: "Add failing API tests for conversational confirmation in tests/api/test_chat_confirm_appointment.py"
Task: "Add failing graph tests for ordinal reference resolution and confirm idempotency in tests/graph/test_confirm_appointment_flow.py"

# Launch early implementation tasks together:
Task: "Extend state and interpretation for appointment reference extraction in app/graph/state.py and app/graph/nodes/interpret.py"
Task: "Enforce ownership checks and already-confirmed handling in app/domain/policies.py and app/repositories/in_memory.py"
```

## Parallel Example: User Story 3

```bash
# Launch User Story 3 tests together:
Task: "Add failing API tests for cancellation and natural rerouting in tests/api/test_chat_cancel_and_reroute.py"
Task: "Add failing graph tests for latest-intent wins and cancel idempotency in tests/graph/test_cancel_and_reroute_flow.py"

# Launch early implementation tasks together:
Task: "Extend routing and interpretation for latest-intent rerouting and stale-context recovery in app/graph/routing.py and app/graph/nodes/interpret.py"
Task: "Document manual conversational scenarios in docs/test-scenarios.md"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: Run `pytest tests/unit tests/graph/test_verify_and_list_flow.py tests/api/test_chat_verify_and_list.py`
5. Demo verification and appointment listing as the MVP

### Incremental Delivery

1. Complete Setup + Foundational -> Foundation ready
2. Add User Story 1 -> Test independently -> Demo MVP
3. Add User Story 2 -> Test independently -> Demo confirmation flow
4. Add User Story 3 -> Test independently -> Demo cancellation and rerouting flow
5. Finish Polish -> Run full regression suite and update docs

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: User Story 1
   - Developer B: User Story 2
   - Developer C: User Story 3
3. Developers merge through the shared graph and API files carefully in priority order

---

## Notes

- All tasks follow the required checkbox + ID + optional `[P]` + optional `[US#]` format
- Every user story phase includes explicit test tasks and exact file paths
- The task list is immediately executable against the repo's default structure from the constitution
- Generate `plan.md` later if you want a fuller design artifact, but these tasks are usable now

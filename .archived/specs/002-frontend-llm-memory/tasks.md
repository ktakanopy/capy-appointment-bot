---

description: "Task list for frontend, LLM, tracing, evaluation, and remembered identity"
---

# Tasks: Frontend, LLM, and Returning Memory

> Historical planning artifact: tasks related to remembered identity reflect an explored scope that was intentionally removed from the final delivered product.

**Input**: Design documents from `/specs/002-frontend-llm-memory/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/chat-api.yaml`, `quickstart.md`

**Tests**: Tests are required for provider safety, remembered-identity restore,
protected-action gating, tracing resilience, and offline evaluation behavior.

**Organization**: Tasks are grouped by user story so each story can be built,
tested, and demonstrated independently.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no incomplete dependency)
- **[Story]**: Which user story the task belongs to (`[US1]`, `[US2]`, `[US3]`, `[US4]`)
- Every task includes exact file paths

## Path Conventions

- Backend code: `app/`
- Streamlit UI: `frontend/`
- Automated tests: `tests/unit/`, `tests/graph/`, `tests/api/`, `tests/evals/`
- Docs: `docs/`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add the new package dependencies, frontend directory, and shared
configuration surface for the feature.

- [X] T001 Update Python dependencies for Streamlit, OpenAI, Langfuse, and SQLite support in `pyproject.toml`
- [X] T002 [P] Create frontend and evaluation package scaffolding in `frontend/`, `frontend/lib/__init__.py`, `app/llm/__init__.py`, `app/evals/__init__.py`, `app/evals/scenarios/__init__.py`, and `tests/evals/__init__.py`
- [X] T003 [P] Extend environment examples and ignore rules for provider, tracing, and SQLite files in `.env.example` and `.gitignore`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared backend infrastructure that all user stories depend on

**⚠️ CRITICAL**: No user story work should begin until this phase is complete

- [X] T004 [P] Add failing unit tests for SQLite persistence and remembered-identity storage in `tests/unit/test_sqlite_persistence.py` and `tests/unit/test_remembered_identity_repository.py`
- [X] T005 [P] Add failing API tests for new-session defaults and schema changes in `tests/api/test_session_lifecycle.py`
- [X] T006 [P] Add runtime settings and configuration loading in `app/config.py`
- [X] T007 [P] Replace in-memory thread checkpoints with SQLite-backed persistence in `app/graph/builder.py` and `app/graph/state.py`
- [X] T008 [P] Define remembered-identity models and repository interface in `app/domain/models.py` and `app/repositories/remembered_identity_repository.py`
- [X] T009 Implement SQLite remembered-identity repository and shared persistence helpers in `app/repositories/sqlite_identity.py` and `app/domain/services.py`
- [X] T010 [P] Define the provider abstraction and factory boundary in `app/llm/base.py` and `app/infrastructure/llm/factory.py`
- [X] T011 [P] Add shared tracing and redaction helpers in `app/observability.py` and `app/config.py`
- [X] T012 [P] Extend request and response schemas for session bootstrap and remembered identity in `app/api/schemas.py`
- [X] T013 Implement `/sessions/new` and `/remembered-identity/forget` route skeletons in `app/api/routes.py` and `app/main.py`

**Checkpoint**: Shared persistence, provider boundary, and session bootstrap are ready

---

## Phase 3: User Story 1 - Chat Through a Simple Patient UI (Priority: P1) 🎯 MVP

**Goal**: Let a patient use a simple UI instead of manual API calls while preserving the existing protected workflow.

**Independent Test**: Start the backend and Streamlit app, open a fresh session, complete verification, and perform one appointment action entirely through the UI.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T014 [P] [US1] Add unit tests for the frontend API client request flow in `tests/unit/test_frontend_api_client.py`
- [X] T015 [P] [US1] Add API integration tests for chat transcript and session bootstrap behavior in `tests/api/test_ui_chat_flow.py`

### Implementation for User Story 1

- [X] T016 [P] [US1] Implement the frontend API client for `/sessions/new` and `/chat` in `frontend/lib/api_client.py`
- [X] T017 [P] [US1] Build the Streamlit app shell and session-state initialization in `frontend/streamlit_app.py`
- [X] T018 [US1] Render transcript, verification status, and appointment results in `frontend/streamlit_app.py`
- [X] T019 [US1] Wire new-session creation and chat turn submission in `frontend/streamlit_app.py` and `app/api/routes.py`
- [X] T020 [US1] Add UI-level retry, error, and clarification handling in `frontend/streamlit_app.py`

**Checkpoint**: User Story 1 is usable as a demoable MVP through the frontend

---

## Phase 4: User Story 2 - Use a Default Model While Keeping Provider Choice Open (Priority: P2)

**Goal**: Introduce real LLM-backed interpretation and response generation with OpenAI as the default provider behind a replaceable abstraction.

**Independent Test**: Configure the default provider, execute the protected workflow safely, then swap provider configuration or simulate provider failure without weakening deterministic gating.

### Tests for User Story 2

- [X] T021 [P] [US2] Add unit tests for provider factory behavior and structured provider outputs in `tests/unit/test_llm_provider_factory.py` and `tests/unit/test_openai_provider.py`
- [X] T022 [P] [US2] Add graph and API tests for provider failure fallback and protected-action safety in `tests/graph/test_llm_fallback_flow.py` and `tests/api/test_llm_provider_failures.py`

### Implementation for User Story 2

- [X] T023 [P] [US2] Define provider request or response schemas and prompt contracts in `app/llm/schemas.py`, `app/prompts/intent_prompt.py`, and `app/prompts/response_prompt.py`
- [X] T024 [US2] Implement the default OpenAI adapter in `app/llm/openai_provider.py`
- [X] T025 [US2] Integrate provider selection into interpretation and presenter-based response generation in `app/graph/nodes/interpret.py`, `app/application/presenters/chat_presenter.py`, and `app/infrastructure/llm/factory.py`
- [X] T026 [US2] Wire provider configuration into application startup in `app/main.py` and `app/config.py`
- [X] T027 [US2] Add deterministic provider-error fallback handling in `app/graph/nodes/interpret.py`, `app/graph/nodes/response.py`, and `app/api/routes.py`

**Checkpoint**: User Stories 1 and 2 both work, with the UI backed by a replaceable model provider

---

## Phase 5: User Story 3 - Review Traceable Conversations and Quality Signals (Priority: P3)

**Goal**: Make model-backed conversations observable and add a lightweight offline evaluation suite with an LLM judge.

**Independent Test**: Run a curated evaluation suite, inspect per-scenario judgments, and review trace records for one protected conversation without breaking the live workflow.

### Tests for User Story 3

- [X] T028 [P] [US3] Add unit tests for trace redaction and judge result parsing in `tests/unit/test_observability_traces.py` and `tests/unit/test_eval_judge.py`
- [X] T029 [P] [US3] Add evaluation-runner and tracing-resilience tests in `tests/evals/test_runner.py` and `tests/api/test_tracing_resilience.py`

### Implementation for User Story 3

- [X] T030 [P] [US3] Add Langfuse tracing hooks around workflow and provider calls in `app/observability.py` and `app/llm/openai_provider.py`
- [X] T031 [P] [US3] Define evaluation result and judge schemas in `app/evals/models.py` and `app/evals/judge.py`
- [X] T032 [US3] Implement the offline evaluation runner in `app/evals/runner.py`
- [X] T033 [US3] Add curated scenario definitions for gating, rerouting, ambiguity, idempotency, and provider failure in `app/evals/scenarios/core_scenarios.py`

**Checkpoint**: User Stories 1-3 are functional, traceable, and reviewable through offline evals

---

## Phase 6: User Story 4 - Remember Verified Identity Across a New Session (Priority: P4)

**Goal**: Restore a previously verified patient context in a fresh session with bounded retention and explicit revocation.

**Independent Test**: Verify in one session, start a fresh session, restore the same patient context without re-entering identity details, then revoke remembered identity and confirm the next session falls back to normal verification.

### Tests for User Story 4

- [X] T034 [P] [US4] Add unit tests for remembered-identity retention, expiry, and revocation in `tests/unit/test_remembered_identity_service.py`
- [X] T035 [P] [US4] Add API and graph tests for restore and forget flows in `tests/api/test_remembered_identity.py` and `tests/graph/test_remembered_identity_restore.py`

### Implementation for User Story 4

- [X] T036 [P] [US4] Extend remembered-identity lifecycle services in `app/domain/models.py` and `app/domain/services.py`
- [X] T037 [US4] Implement create, restore, and revoke operations in `app/repositories/sqlite_identity.py` and `app/repositories/remembered_identity_repository.py`
- [X] T038 [US4] Restore verified patient context during session bootstrap in `app/api/routes.py` and `app/graph/builder.py`
- [X] T039 [US4] Expose remembered-identity status and forget actions in `frontend/lib/api_client.py` and `frontend/streamlit_app.py`

**Checkpoint**: All four user stories are independently functional

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Finish docs, regression coverage, and end-to-end validation

- [X] T040 [P] Update environment and run instructions for the new frontend, provider, tracing, and eval flow in `README.md` and `.env.example`
- [ ] T041 [P] Add regression scenarios for remembered-identity expiry and duplicate-date ambiguity in `app/evals/scenarios/core_scenarios.py` and `tests/evals/test_runner.py`
- [X] T042 [P] Refresh system design documentation for the new architecture in `docs/sdd.md` and `docs/decisions.md`
- [X] T043 [P] Update manual scenario coverage for frontend, restore, and tracing flows in `docs/test-scenarios.md` and `specs/002-frontend-llm-memory/quickstart.md`
- [X] T044 Run the full validation suite across `tests/`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies, can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion and blocks all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational completion and is the MVP
- **User Story 2 (Phase 4)**: Depends on Foundational completion and can be validated independently via backend tests even before the UI is polished
- **User Story 3 (Phase 5)**: Depends on Foundational completion and benefits from the provider boundary in User Story 2
- **User Story 4 (Phase 6)**: Depends on Foundational completion and uses the session lifecycle established earlier
- **Polish (Phase 7)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Starts after Foundational completion with no dependency on later stories
- **User Story 2 (P2)**: Starts after Foundational completion; integrates with the UI but remains independently testable through backend calls
- **User Story 3 (P3)**: Best implemented after User Story 2 because tracing and offline judging rely on the provider boundary
- **User Story 4 (P4)**: Starts after Foundational completion and can be demonstrated independently through session bootstrap and restore flows

### Within Each User Story

- Tests MUST be written and fail before implementation for protected or stateful workflow changes
- Shared schemas and helpers before service logic
- Service logic before route or UI integration
- Route integration before documentation and polish
- Complete one story checkpoint before treating the story as done

### Parallel Opportunities

- T002 and T003 can run in parallel after T001 starts
- In Foundational, T004-T012 have high parallelism across separate files before T013 ties the pieces together
- In User Story 1, T014-T017 can run in parallel before the frontend integration tasks
- In User Story 2, T021-T024 can run in parallel before graph and startup wiring
- In User Story 3, T028-T031 can run in parallel before the runner and scenario tasks
- In User Story 4, T034-T036 can run in parallel before restore-flow integration
- In Polish, T040-T043 can run in parallel before the final full-suite run

---

## Parallel Example: User Story 1

```bash
# Launch User Story 1 tests together:
Task: "Add unit tests for the frontend API client request flow in tests/unit/test_frontend_api_client.py"
Task: "Add API integration tests for chat transcript and session bootstrap behavior in tests/api/test_ui_chat_flow.py"

# Launch early implementation tasks together:
Task: "Implement the frontend API client for /sessions/new and /chat in frontend/lib/api_client.py"
Task: "Build the Streamlit app shell and session-state initialization in frontend/streamlit_app.py"
```

## Parallel Example: User Story 2

```bash
# Launch User Story 2 tests together:
Task: "Add unit tests for provider factory behavior and structured provider outputs in tests/unit/test_llm_provider_factory.py and tests/unit/test_openai_provider.py"
Task: "Add graph and API tests for provider failure fallback and protected-action safety in tests/graph/test_llm_fallback_flow.py and tests/api/test_llm_provider_failures.py"

# Launch early implementation tasks together:
Task: "Define provider request or response schemas and prompt contracts in app/llm/schemas.py, app/prompts/intent_prompt.py, and app/prompts/response_prompt.py"
Task: "Implement the default OpenAI adapter in app/llm/openai_provider.py"
```

## Parallel Example: User Story 3

```bash
# Launch User Story 3 tests together:
Task: "Add unit tests for trace redaction and judge result parsing in tests/unit/test_observability_traces.py and tests/unit/test_eval_judge.py"
Task: "Add evaluation-runner and tracing-resilience tests in tests/evals/test_runner.py and tests/api/test_tracing_resilience.py"

# Launch early implementation tasks together:
Task: "Add Langfuse tracing hooks around workflow and provider calls in app/observability.py and app/llm/openai_provider.py"
Task: "Define evaluation result and judge schemas in app/evals/models.py and app/evals/judge.py"
```

## Parallel Example: User Story 4

```bash
# Launch User Story 4 tests together:
Task: "Add unit tests for remembered-identity retention, expiry, and revocation in tests/unit/test_remembered_identity_service.py"
Task: "Add API and graph tests for restore and forget flows in tests/api/test_remembered_identity.py and tests/graph/test_remembered_identity_restore.py"

# Launch early implementation tasks together:
Task: "Extend remembered-identity lifecycle services in app/domain/models.py and app/domain/services.py"
Task: "Implement create, restore, and revoke operations in app/repositories/sqlite_identity.py and app/repositories/remembered_identity_repository.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: Launch the backend and Streamlit UI, then complete one protected appointment flow through the frontend
5. Demo the product as a real user-facing app

### Incremental Delivery

1. Complete Setup + Foundational -> shared infrastructure ready
2. Add User Story 1 -> demo usable frontend MVP
3. Add User Story 2 -> demo real model-backed interaction with safe fallback
4. Add User Story 3 -> demo tracing and offline evaluation
5. Add User Story 4 -> demo remembered identity across a new session
6. Finish Polish -> run the full suite and refresh docs

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is ready:
   - Developer A: User Story 1
   - Developer B: User Story 2
   - Developer C: User Story 4
3. Implement User Story 3 after the provider boundary is stable
4. Finish Polish and full validation together

---

## Notes

- All tasks follow the required checkbox + ID + optional `[P]` + optional `[US#]` format
- The task list assumes the existing backend from feature `001` remains in place and is extended rather than replaced
- Protected-action safety stays in the backend even when UI, model, tracing, and remembered identity are added

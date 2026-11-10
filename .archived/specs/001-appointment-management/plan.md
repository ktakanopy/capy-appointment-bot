# Implementation Plan: Conversational Appointment Management

**Branch**: `001-build-appointment-service` | **Date**: 2026-04-13 | **Spec**: `/Users/kevin.takano/projects/appointment-bot/specs/001-appointment-management/spec.md`
**Input**: Feature specification from `/Users/kevin.takano/projects/appointment-bot/specs/001-appointment-management/spec.md`

**Note**: This plan covers Phase 0 research and Phase 1 design artifacts for the
verification-gated appointment workflow.

## Summary

Build a FastAPI backend that exposes a single conversational `/chat` endpoint,
uses LangGraph `StateGraph` for deterministic workflow routing, persists
short-term conversation state per session thread, and separates intent parsing
from authorization and appointment mutations. The design favors explicit policy
gates, a reusable verification subgraph, in-memory repositories for the v1
exercise, and strong automated coverage for protected flows.

## Technical Context

**Language/Version**: Python 3.11+  
**Primary Dependencies**: FastAPI, LangGraph, Pydantic, pytest  
**Storage**: LangGraph `InMemorySaver` for thread-scoped checkpoints; in-memory patient and appointment repositories for v1  
**Testing**: pytest with unit, graph, and API tests; FastAPI `TestClient` for endpoint coverage  
**Target Platform**: Linux server or containerized API runtime  
**Project Type**: Conversational backend web-service  
**Performance Goals**: Complete the verify-then-list happy path within 4 conversational turns and keep protected-flow responses predictable under retries  
**Constraints**: Deterministic authorization, minimal PII in logs, idempotent confirm/cancel behavior, graceful fallback on ambiguous intent or repository failure  
**Scale/Scope**: Single-clinic prototype with one active patient per session and no cross-session long-term memory

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Initial Gate Review**

- [x] Protected actions are enumerated and gated in deterministic backend code.
  Listing, confirming, and canceling are blocked behind verification and routed
  through explicit policy helpers.
- [x] Conversation state, `session_id` to `thread_id` mapping, deferred intent,
  and rerouting behavior are explicit.
  The design uses a persisted `ConversationState` keyed by `session_id`.
- [x] The LLM boundary is documented, including deterministic fallback when
  parsing is ambiguous.
  The model is limited to intent extraction and response wording; policy and
  mutation decisions remain in backend code.
- [x] Unit, graph, and API tests cover verification, ownership, idempotency,
  and failure recovery.
  These are required artifacts in `tests/unit/`, `tests/graph/`, and
  `tests/api/`.
- [x] Structured logs or traces are defined with privacy-aware handling of
  patient data.
  Logs capture decision paths without exposing unnecessary raw identity fields.

**Post-Design Gate Review**

- [x] `research.md` resolves architecture and persistence choices without
  violating deterministic safety gates.
- [x] `data-model.md` defines explicit workflow state, patient ownership, and
  appointment status transitions.
- [x] `contracts/chat-api.yaml` exposes a single `/chat` endpoint with a stable
  session identifier and safe response contract.
- [x] `quickstart.md` exercises verification, protected access, rerouting, and
  repeated mutation safety.

## Project Structure

### Documentation (this feature)

```text
specs/001-appointment-management/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── chat-api.yaml
└── tasks.md
```

### Source Code (repository root)

```text
app/
├── main.py
├── observability.py
├── api/
│   ├── routes.py
│   └── schemas.py
├── graph/
│   ├── builder.py
│   ├── routing.py
│   ├── state.py
│   ├── nodes/
│   │   ├── appointments.py
│   │   ├── ingest.py
│   │   ├── interpret.py
│   │   ├── response.py
│   │   └── verification.py
│   └── subgraphs/
│       └── verification_subgraph.py
├── domain/
│   ├── models.py
│   ├── policies.py
│   └── services.py
├── repositories/
│   ├── appointment_repository.py
│   ├── in_memory.py
│   └── patient_repository.py
└── prompts/
    ├── intent_prompt.py
    └── response_prompt.py

tests/
├── api/
├── graph/
└── unit/

docs/
├── decisions.md
├── sdd.md
└── test-scenarios.md
```

**Structure Decision**: Use the default single-backend structure from the
constitution. It keeps API transport, workflow orchestration, domain logic, and
repository access separate without introducing unnecessary project boundaries.

## Complexity Tracking

No constitution violations or justified exceptions are required for this plan.

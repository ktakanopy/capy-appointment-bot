# Implementation Plan: Frontend, LLM, and Returning Memory

> Historical planning artifact: this plan includes exploratory remembered-identity work that was later removed from the delivered product to stay closer to the original exercise scope.

**Branch**: `002-frontend-llm-memory` | **Date**: 2026-04-13 | **Spec**: `/Users/kevin.takano/projects/appointment-bot/specs/002-frontend-llm-memory/spec.md`
**Input**: Feature specification from `/Users/kevin.takano/projects/appointment-bot/specs/002-frontend-llm-memory/spec.md`

**Note**: This plan covers Phase 0 research and Phase 1 design artifacts for a
model-backed version of the appointment workflow with a simple frontend,
traceability, offline evaluation, and bounded remembered identity across
sessions.

## Summary

Extend the existing FastAPI + LangGraph appointment service with a simple
Streamlit patient interface, an internal LLM provider abstraction with OpenAI
as the default adapter, Langfuse tracing at the workflow and model boundaries,
an offline evaluation harness that runs curated scenarios with an LLM judge,
and a bounded remembered-identity store that restores verified patient context
across a newly started session. Deterministic policy gates remain authoritative
for verification, ownership, and protected mutations.

## Technical Context

**Language/Version**: Python 3.11+  
**Primary Dependencies**: FastAPI, LangGraph, Pydantic, Streamlit, OpenAI Python SDK, Langfuse Python SDK, pytest  
**Storage**: SQLite-backed LangGraph checkpointer for thread-scoped session state; SQLite remembered-identity repository for bounded cross-session identity restore; in-memory patient and appointment repositories remain acceptable for v1 domain data  
**Testing**: pytest for unit, graph, API, and evaluation harness coverage; curated offline scenarios with an LLM-based judge for qualitative review  
**Target Platform**: Local demo workstation or Linux server running the FastAPI backend and a companion Streamlit UI  
**Project Type**: Conversational backend web-service with a lightweight companion frontend  
**Performance Goals**: Keep the patient chat loop responsive for local/demo use, preserve safe completion of protected workflows, and keep evaluation runs small enough to execute offline as part of review  
**Constraints**: Deterministic authorization, provider-agnostic model boundary, minimal PII in traces, revocable remembered identity, graceful fallback when model/tracing/evaluation services fail, and no hidden safety logic inside prompts  
**Scale/Scope**: Single-clinic prototype with one active patient per UI session, one default model provider, and bounded remembered identity rather than full long-term conversation memory

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Initial Gate Review**

- [x] Protected actions are enumerated and gated in deterministic backend code.
  The current backend policy layer remains authoritative for verification,
  ownership checks, and appointment mutations.
- [x] Conversation state, `session_id` to `thread_id` mapping, deferred intent,
  and rerouting behavior are explicit.
  The design keeps LangGraph workflow state explicit and adds a bounded
  remembered-identity record outside live thread state.
- [x] The LLM boundary is documented, including deterministic fallback when
  parsing is ambiguous.
  Model usage is limited to interpretation, user-facing language, evaluation,
  and judging; protected-action approval remains deterministic.
- [x] Unit, graph, and API tests cover verification, ownership, idempotency,
  and failure recovery.
  This feature expands coverage with provider-failure, remembered-identity, UI
  integration, and evaluation-harness tests.
- [x] Structured logs or traces are defined with privacy-aware handling of
  patient data.
  Langfuse traces will capture workflow and model spans while redacting or
  minimizing raw identity data.

**Post-Design Gate Review**

- [x] `research.md` justifies the provider abstraction, Streamlit UI, Langfuse
  tracing, and SQLite-backed remembered identity without weakening policy
  enforcement.
- [x] `data-model.md` separates thread-scoped conversation state from bounded
  cross-session remembered identity.
- [x] `contracts/chat-api.yaml` keeps protected actions behind deterministic
  backend control and exposes explicit session-restoration behaviors.
- [x] `quickstart.md` covers UI startup, backend startup, environment
  configuration, and offline evaluation execution.

## Project Structure

### Documentation (this feature)

```text
specs/002-frontend-llm-memory/
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
│   └── subgraphs/
├── domain/
├── repositories/
├── llm/
│   ├── base.py
│   ├── openai_provider.py
│   └── factory.py
└── evals/
    ├── judge.py
    ├── runner.py
    └── scenarios/

frontend/
├── streamlit_app.py
└── lib/
    └── api_client.py

tests/
├── api/
├── evals/
├── graph/
└── unit/

docs/
```

**Structure Decision**: Preserve the existing FastAPI + LangGraph backend and
add a small `frontend/` directory for the Streamlit demo UI. This keeps the
protected workflow inside the backend while providing a simple patient-facing
surface and avoiding a heavier frontend stack for this feature.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| Bounded cross-session remembered identity beyond thread-scoped memory | The feature explicitly requires the system to remember verified identification after a new session starts | Thread-scoped checkpoints only persist within one conversation thread and cannot safely restore identity across a fresh session |

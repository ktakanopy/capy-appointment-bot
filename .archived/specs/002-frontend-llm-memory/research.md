# Research: Frontend, LLM, and Returning Memory

> Historical planning artifact: the remembered-identity research below documents an explored direction, not part of the shipped product scope.

## Decision 1: Use Streamlit for the simple patient-facing UI

- **Decision**: Build the new interface as a small Streamlit app that calls the
  existing FastAPI backend.
- **Rationale**: The feature asks for a simple interface, and Streamlit's chat
  primitives plus session state are a good fit for a fast, local demo UI. This
  keeps the frontend lightweight while leaving the authoritative workflow logic
  in the backend.
- **Alternatives considered**:
  - React/Vite frontend: rejected for this feature because it adds more setup
    and frontend infrastructure than needed for a simple local/demo interface.
  - Extending FastAPI with server-rendered HTML: rejected because it would mix
    UI and backend workflow concerns more tightly.

## Decision 2: Introduce an internal LLM provider interface with OpenAI as the default adapter

- **Decision**: Define a local `LLMProvider` boundary and implement OpenAI as
  the first concrete provider.
- **Rationale**: The feature requires OpenAI as the default while keeping the
  code easy to extend to other providers. A small internal interface keeps the
  application decoupled without bringing in a multi-provider abstraction layer
  that the project does not yet need.
- **Alternatives considered**:
  - Direct OpenAI calls scattered through workflow nodes: rejected because it
    would create provider lock-in and weaken testability.
  - LiteLLM or another cross-provider framework: rejected for now because a
    local adapter is simpler and enough for the requested scope.

## Decision 3: Use OpenAI structured outputs for bounded interpretation and evaluation judging

- **Decision**: Use the OpenAI Python SDK with structured parsing for intent
  extraction and LLM-judge outputs.
- **Rationale**: Structured outputs reduce brittle string parsing and support a
  clear contract between model calls and deterministic backend logic. They also
  work well for offline evaluation scorecards.
- **Alternatives considered**:
  - Free-form text parsing: rejected because it increases ambiguity and weakens
    deterministic fallback behavior.
  - Tool calling for every model interaction: rejected because the current use
    cases only need small structured outputs and concise responses.

## Decision 4: Trace workflow and provider calls with Langfuse

- **Decision**: Use Langfuse's Python SDK with `@observe` spans and the
  Langfuse OpenAI wrapper for model calls.
- **Rationale**: Langfuse supports function-level tracing, OpenAI call tracing,
  and evaluation-related observability without changing the core backend
  control-flow architecture.
- **Alternatives considered**:
  - Custom JSON logs only: rejected because the feature explicitly calls for
    Langfuse and richer LLM observability.
  - LangSmith: rejected because the requested tracing system is Langfuse.

## Decision 5: Keep deterministic safety gates and use the LLM only in bounded steps

- **Decision**: Limit model usage to intent extraction, assistant phrasing,
  and offline judging while preserving deterministic policy checks for
  verification, ownership, rerouting safety, and appointment mutation.
- **Rationale**: The constitution requires bounded AI responsibility. The new
  model layer must improve UX, not become the authorization authority.
- **Alternatives considered**:
  - Letting the model route protected actions end to end: rejected because it
    would make access control probabilistic.
  - Replacing deterministic fallback logic with prompt instructions: rejected
    because that violates the constitution directly.

## Decision 6: Persist short-term session memory with SQLite-backed LangGraph checkpoints

- **Decision**: Replace the in-memory checkpointer with a local SQLite-backed
  LangGraph checkpointer.
- **Rationale**: Thread-scoped short-term memory remains central to the design,
  and SQLite keeps that persistence local, simple, and restart-safe for demo
  use.
- **Alternatives considered**:
  - Keep `InMemorySaver`: rejected because session continuity disappears on
    process restart and weakens the demo UX.
  - Postgres or Redis checkpointers: rejected because they add infrastructure
    complexity beyond the scope of this feature.

## Decision 7: Store remembered identity separately from live conversation state

- **Decision**: Add a dedicated remembered-identity repository with bounded
  retention and explicit revoke or clear behavior.
- **Rationale**: The feature asks to remember successful identification across a
  new session, which is different from thread-scoped memory. Keeping this store
  separate from the conversation thread state makes the boundary explicit and
  easier to reason about.
- **Alternatives considered**:
  - Reuse the active conversation state as cross-session memory: rejected
    because thread state should not silently become durable identity storage.
  - Store entire long-term chat histories: rejected because the feature only
    requires remembered identification, not full conversation memory.

## Decision 8: Build a small custom offline evaluation harness with an LLM judge

- **Decision**: Implement a lightweight evaluation runner that executes curated
  scenarios, stores expected outcomes, and calls a model judge for structured
  pass/fail reasoning.
- **Rationale**: The feature asks for a simple offline evaluation flow. A small
  in-repo harness is easier to control than introducing a larger evaluation
  framework immediately.
- **Alternatives considered**:
  - DeepEval or another dedicated framework: rejected for now because the scope
    only requires a small, understandable scenario runner.
  - Pure rule-based evaluation only: rejected because the feature explicitly
    asks for LLM-as-judge.

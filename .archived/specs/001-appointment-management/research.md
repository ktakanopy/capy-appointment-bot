# Research: Conversational Appointment Management

## Decision 1: Use LangGraph StateGraph with thread-scoped checkpoints

- **Decision**: Build the workflow around `StateGraph` and compile it with
  `InMemorySaver` for v1 persistence.
- **Rationale**: LangGraph documents thread-level checkpointing with
  `InMemorySaver`, which matches the need to resume multi-turn conversations by
  `thread_id` without adding cross-session memory complexity. The graph model is
  also a better fit than a free-form agent loop because the protected actions
  follow predictable business gating.
- **Alternatives considered**:
  - Stateless request handling without checkpoints: rejected because deferred
    verification and rerouting would become brittle.
  - A dynamic ReAct-style agent loop: rejected because authorization and
    mutation safety need explicit routing rather than autonomous tool choice.

## Decision 2: Model verification as a reusable subgraph

- **Decision**: Implement identity verification as its own LangGraph subgraph
  that can collect missing fields, perform lookup, and resume deferred actions.
- **Rationale**: LangGraph subgraph guidance supports reusable stateful flow
  segments. Verification is the main reusable control point for every protected
  action in this service.
- **Alternatives considered**:
  - Inline verification logic inside every protected node: rejected because it
    duplicates policy handling and makes rerouting harder to reason about.
  - A single monolithic graph node for all verification behavior: rejected
    because it hides intermediate state and weakens testability.

## Decision 3: Keep the LLM on bounded interpretation and response duties only

- **Decision**: Allow the model to infer intent, extract conversational fields,
  and phrase responses, but keep authorization, ownership checks, and mutation
  decisions deterministic in backend code.
- **Rationale**: The constitution requires deterministic safety gates.
  Restricting the model to non-authoritative steps preserves conversational UX
  without turning protected actions into probabilistic behavior.
- **Alternatives considered**:
  - Letting the model decide whether verification is complete: rejected because
    access control cannot depend on non-deterministic interpretation.
  - Avoiding the model entirely: rejected because conversational references such
    as ordinals and mixed identity input are easier to support with bounded NLP.

## Decision 4: Use a single POST /chat endpoint with session continuity

- **Decision**: Expose one POST `/chat` endpoint that receives `session_id` and
  `message`, then maps the session to a persistent workflow thread.
- **Rationale**: The exercise asks for a conversational endpoint, not a set of
  action-specific APIs. A single entry point keeps the transport layer simple
  while preserving state through the graph.
- **Alternatives considered**:
  - Multiple endpoints for verify, list, confirm, and cancel: rejected because
    it breaks the conversational contract and complicates rerouting.
  - WebSocket-only transport: rejected because it adds complexity that the spec
    does not require.

## Decision 5: Start with repository interfaces and in-memory implementations

- **Decision**: Define repository protocols for patients and appointments, then
  back them with seeded in-memory data for v1.
- **Rationale**: The exercise is about orchestration, safety boundaries, and
  testability. Repository interfaces keep the design migration-ready without
  dragging database setup into the prototype.
- **Alternatives considered**:
  - Direct hard-coded data inside graph nodes: rejected because it mixes domain
    logic with control flow and hurts testing.
  - A real database: rejected because it adds infrastructure complexity without
    improving the evaluation target for this exercise.

## Decision 6: Test at unit, graph, and API levels

- **Decision**: Use pytest to cover domain policies, graph routing, and the
  `/chat` endpoint. Use FastAPI `TestClient` for API tests.
- **Rationale**: FastAPI's testing guidance fits a straightforward endpoint test
  setup, and the constitution explicitly requires coverage for verification,
  ownership, idempotency, and recovery behavior.
- **Alternatives considered**:
  - API-only tests: rejected because graph and policy bugs would be harder to
    isolate.
  - Unit-only tests: rejected because the most important behavior spans the
    request layer and the workflow graph.

# Feature Specification: Frontend, LLM, and Returning Memory

> Historical planning artifact: this spec includes an exploratory remembered-identity/session-bootstrap scope that was intentionally not kept in the delivered product. The shipped scope is the core verification-gated appointment flow plus the frontend/LLM/tracing/evaluation additions.

**Feature Branch**: `002-frontend-llm-memory`  
**Created**: 2026-04-13  
**Status**: Draft  
**Input**: User description: "Things that I want in my application:1. simple front-end ui to interact with bot. 2. LLM usage, and use openai as default. But make the code to be decoupled to be easy to add a new provider. 3. Add langfuse tracing to the code. 4. Add a simple offline evaluation that tests different scenarions with llm-as-judge. 5. The code should has short term memory and should remember my identification after I started a new session."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Chat Through a Simple Patient UI (Priority: P1)

As a clinic patient, I want a simple interface where I can chat with the
assistant, review replies, and continue my appointment workflow without using
manual API calls.

Verification gate: protected appointment actions remain unavailable until the
patient is identified. Rerouting behavior: the interface must let the patient
continue naturally from verification to listing, confirming, and canceling.
Failure recovery: if the assistant cannot complete the request, the interface
shows the response clearly and keeps the conversation intact.

**Why this priority**: A usable interface is the most visible improvement and
is the main way a reviewer or patient will interact with the system.

**Independent Test**: A user opens the application, starts a conversation,
completes identity verification, and performs at least one appointment action
without calling the backend manually.

**Acceptance Scenarios**:

1. **Given** a patient opens the application for the first time, **When** the
   patient sends a message, **Then** the interface shows the full conversation
   history for that active session and displays assistant replies clearly.
2. **Given** a patient has completed identity verification, **When** the
   patient asks to list, confirm, or cancel appointments, **Then** the
   interface reflects the updated assistant response without forcing a reset.
3. **Given** the assistant returns an error or clarification request, **When**
   the patient continues the conversation, **Then** the interface preserves the
   prior messages and supports the next turn.

---

### User Story 2 - Use a Default Model While Keeping Provider Choice Open (Priority: P2)

As a product owner, I want the assistant to use a default language model while
keeping the provider choice replaceable, so the product can launch quickly now
and still adapt to future provider changes.

Verification gate: model-generated language must not bypass deterministic access
controls. Rerouting behavior: model-powered interpretation and responses must
still honor the latest valid patient request. Failure recovery: if the selected
provider is unavailable or produces an unusable result, the system falls back
to a safe conversational response instead of performing an unsafe action.

**Why this priority**: The product now needs actual model-backed conversation,
but long-term maintainability depends on avoiding provider lock-in.

**Independent Test**: The system runs with a default model provider, completes
the protected workflow safely, and can switch to a second provider through the
same application boundary without changing business behavior.

**Acceptance Scenarios**:

1. **Given** the application is configured with a default model provider,
   **When** a patient sends a conversational request, **Then** the assistant
   uses model-backed interpretation or response generation while preserving the
   same protected action rules.
2. **Given** the application is reconfigured to use a different provider,
   **When** the same patient scenario is executed, **Then** the workflow still
   follows the same gating and action outcomes.
3. **Given** the provider cannot complete a request, **When** the patient sends
   a new turn, **Then** the system returns a safe fallback response and does not
   perform an unauthorized mutation.

---

### User Story 3 - Review Traceable Conversations and Quality Signals (Priority: P3)

As an engineer or reviewer, I want conversation traces and a small offline
evaluation suite, so I can inspect behavior, compare scenarios, and detect
quality regressions before changes are accepted.

Verification gate: traces and evaluations must capture protected-flow decisions
without exposing unnecessary patient data. Rerouting behavior: trace records
must show how the system handled intent changes and resumed actions. Failure
recovery: if tracing or evaluation fails, the production workflow remains safe
and the failure is clearly visible to maintainers.

**Why this priority**: Observability and evaluation make the model-backed
version explainable and easier to trust in an interview or review setting.

**Independent Test**: A maintainer runs curated scenarios, obtains quality
judgments for each one, and can inspect traces for a protected workflow without
blocking end-user conversations.

**Acceptance Scenarios**:

1. **Given** a patient conversation completes, **When** an engineer reviews the
   recorded trace, **Then** the engineer can inspect the key decisions,
   verification status, and action outcomes for that conversation.
2. **Given** a curated offline scenario set exists, **When** a maintainer runs
   the evaluation suite, **Then** the suite produces per-scenario pass or fail
   judgments and an overall summary.
3. **Given** tracing or evaluation infrastructure is temporarily unavailable,
   **When** the assistant handles a patient request, **Then** the patient flow
   remains available and the observability failure is surfaced separately.

---

### User Story 4 - Remember Verified Identity Across a New Session (Priority: P4)

As a returning patient, I want the application to remember my successful
identification when I start a new session, so I do not need to re-enter the
same identity details every time.

Verification gate: remembered identity must only restore the already verified
patient context and must never let one patient inherit another patient's data.
Rerouting behavior: a returning patient can begin a fresh session and continue
directly into protected actions if remembered identification is still valid.
Failure recovery: if the remembered identity is missing, expired, or cannot be
trusted, the system falls back to normal verification.

**Why this priority**: Remembered identification improves convenience while
still preserving the safety boundary of the workflow.

**Independent Test**: A patient verifies in one session, starts a new session,
and the system restores the same identified patient context without re-entering
identity details, unless the remembered context has expired or been cleared.

**Acceptance Scenarios**:

1. **Given** a patient successfully verified in a prior recent session,
   **When** the patient starts a new session, **Then** the system recognizes the
   returning patient context and allows the next protected action to continue
   without repeating full identification.
2. **Given** remembered identity is no longer valid, **When** the patient starts
   a new session, **Then** the system requires normal verification before any
   protected action.
3. **Given** a remembered identity exists, **When** the patient chooses to stop
   using it or the system detects a mismatch, **Then** the system clears the
   remembered context and reverts to normal verification.

### Edge Cases

- The model provider is unavailable during a protected workflow turn.
- A provider produces an answer that conflicts with deterministic access rules.
- The frontend loses connectivity after the patient has already started a
  conversation.
- A returning patient opens a new session after remembered identification has
  expired or been cleared.
- Two appointments share the same date and the patient uses an ambiguous
  reference.
- Offline evaluation scenarios disagree with expected behavior or the judge
  cannot produce a usable result.
- Tracing fails while the assistant is serving live user traffic.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a simple patient-facing interface for sending
  messages and reviewing the active conversation.
- **FR-002**: System MUST preserve conversation continuity within an active
  session, including prior messages, verification state, and current workflow
  context.
- **FR-003**: System MUST use a default language model provider for
  conversation-related tasks while keeping provider selection replaceable behind
  a stable application boundary.
- **FR-004**: System MUST keep deterministic backend control over verification,
  authorization, ownership checks, and protected mutations regardless of which
  model provider is selected.
- **FR-005**: System MUST fall back to a safe user-facing response when the
  configured model provider cannot produce a usable result.
- **FR-006**: System MUST record traceable conversation runs that let
  maintainers inspect workflow decisions, model usage, and outcomes without
  exposing unnecessary sensitive data.
- **FR-007**: System MUST provide an offline evaluation flow that runs curated
  conversation scenarios and produces scenario-level quality judgments plus an
  overall summary.
- **FR-008**: System MUST include scenarios in the offline evaluation flow for
  verification, protected-action gating, rerouting, ambiguous references,
  repeated mutations, and provider failure handling.
- **FR-009**: System MUST remember a patient's successful identification across
  a newly started session for a bounded retention period.
- **FR-010**: System MUST make remembered identification revocable, so the
  remembered patient context can be cleared manually or automatically when it is
  no longer trustworthy.
- **FR-011**: System MUST continue to require safe fallback verification when a
  returning-session identity cannot be restored with confidence.
- **FR-012**: System MUST keep the existing appointment management workflow
  available through the new interface without weakening any protected-action
  gates.

### Key Entities *(include if feature involves data)*

- **Patient Session**: The active user interaction shown in the interface,
  including visible messages and current workflow state.
- **Remembered Identity**: A bounded reusable record of a previously verified
  patient identity that may be restored in a new session.
- **Model Provider Configuration**: The runtime selection of the default model
  provider and its replaceable alternatives.
- **Conversation Trace**: An inspectable record of the workflow path, model
  calls, and action outcomes for one conversation.
- **Evaluation Scenario**: A curated conversation test case with an expected
  workflow outcome and a quality judgment result.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of covered patient demo scenarios can be completed through
  the user interface without direct manual API calls.
- **SC-002**: 100% of covered protected-action scenarios remain safely gated in
  the model-backed version of the product.
- **SC-003**: At least 90% of curated offline evaluation scenarios produce a
  usable judgment result and a clear pass or fail outcome.
- **SC-004**: Reviewers can inspect trace records for 100% of covered
  successful protected-flow scenarios without needing raw application logs.
- **SC-005**: Returning patients with valid remembered identification can begin
  a new session and reach their first protected action in fewer turns than a
  first-time patient in equivalent covered scenarios.

## Assumptions

- The first release only needs a simple single-user interface for local or demo
  use, not a full multi-role clinic portal.
- Remembered identification is limited to previously verified identity context,
  not full long-term conversation history.
- Remembered identification is retained for a bounded period and may be cleared
  manually or automatically.
- Offline evaluation uses a model-based judge for scenario review, but live
  production decisions still remain under deterministic safety controls.
- Strong healthcare-grade authentication, full compliance workflows, and real
  clinic system integrations remain out of scope unless introduced separately.

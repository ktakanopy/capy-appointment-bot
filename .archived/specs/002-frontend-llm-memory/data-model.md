# Data Model: Frontend, LLM, and Returning Memory

> Historical planning artifact: this document still records a remembered-identity model from an exploratory scope that was not shipped in the delivered product.

## Patient Session

**Purpose**: Represents the active patient interaction in the UI and backend.

**Fields**:

- `session_id`: external session identifier used by the frontend
- `thread_id`: backend workflow thread identifier
- `messages`: visible conversation history for the active session
- `verified`: whether the current session has an identified patient
- `patient_id`: resolved patient identifier when verification succeeds
- `current_operation`: latest requested operation or verification step
- `appointments`: current patient appointment view when relevant
- `last_action_result`: structured outcome of the latest completed action

**Validation Rules**:

- a session may only expose protected appointments when `verified` is true
- `session_id` must map to exactly one active thread at a time
- protected actions require patient ownership checks even when identity was
  restored from remembered context

## RememberedIdentity

**Purpose**: Bounded reusable record that restores a previously verified patient
context in a newly started session.

**Fields**:

- `remembered_identity_id`: internal identifier for the remembered record
- `patient_id`: verified patient identifier
- `display_name`: optional patient-facing label for the remembered identity
- `verification_fingerprint`: bounded fingerprint or lookup key tied to the
  verified identity record
- `issued_at`: when the remembered identity was created
- `expires_at`: bounded expiry time
- `revoked_at`: optional revocation timestamp
- `status`: active, expired, or revoked

**Validation Rules**:

- only verified identities may create remembered records
- expired or revoked records cannot restore patient context
- restoring a remembered identity must resolve back to the same patient record
  before protected actions proceed

## LLMProviderConfig

**Purpose**: Runtime configuration for the selected model provider.

**Fields**:

- `provider_name`: logical provider identifier
- `model_name`: default model used for interpretation or response tasks
- `enabled_capabilities`: interpretation, response generation, judging
- `timeout_seconds`: request timeout budget
- `fallback_mode`: behavior when the provider call fails

**Validation Rules**:

- provider config must resolve to exactly one default provider at runtime
- protected-flow execution cannot depend on provider-specific response shapes

## ConversationTrace

**Purpose**: Inspectable trace of one conversation run or evaluation run.

**Fields**:

- `trace_id`: trace identifier
- `session_id`: associated patient session identifier when applicable
- `thread_id`: associated workflow thread identifier
- `span_events`: workflow, provider, and evaluator observations
- `verification_status`: high-level verification state at key steps
- `action_outcomes`: protected-action results recorded during the trace
- `error_summary`: optional failure summary

**Validation Rules**:

- traces must not contain unnecessary raw PII
- traces must remain useful even if provider calls fail mid-run

## EvaluationScenario

**Purpose**: Curated offline test case for workflow and quality review.

**Fields**:

- `scenario_id`: stable scenario identifier
- `title`: short scenario name
- `input_turns`: ordered conversation inputs to execute
- `expected_outcomes`: required workflow outcomes
- `judge_rubric`: evaluation criteria for the LLM judge
- `category`: verification, rerouting, ambiguity, idempotency, provider failure,
  remembered identity, or tracing

**Validation Rules**:

- every scenario must define at least one expected workflow outcome
- protected scenarios must include a safety or authorization expectation

## EvaluationResult

**Purpose**: Output of one offline evaluation scenario.

**Fields**:

- `scenario_id`: associated scenario identifier
- `status`: pass, fail, or error
- `judge_summary`: concise judge explanation
- `score`: optional numeric or categorical score
- `observed_outcomes`: workflow results seen during execution
- `trace_id`: optional linked trace

**Validation Rules**:

- a result marked `pass` must satisfy all required expected outcomes
- a result marked `error` must explain whether execution or judging failed

## Relationships

- One `Patient Session` maps to one active backend thread
- One `RememberedIdentity` points to one patient and may restore many future
  sessions until it expires or is revoked
- One `LLMProviderConfig` is used by many sessions and many evaluation runs
- One `ConversationTrace` may be linked to one patient session or one
  evaluation run
- One `EvaluationScenario` produces one `EvaluationResult` per execution

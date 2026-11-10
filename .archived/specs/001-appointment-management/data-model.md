# Data Model: Conversational Appointment Management

## Patient

**Purpose**: Represents the verified clinic patient allowed to access protected
appointment actions.

**Fields**:

- `id`: stable patient identifier
- `full_name`: full legal or registered name used for verification
- `phone`: clinic-recorded phone number in normalized string form
- `date_of_birth`: date of birth in ISO `YYYY-MM-DD`

**Validation Rules**:

- `full_name` is required for verification matching
- `phone` is required and must be normalized before lookup
- `date_of_birth` is required and must parse as a valid calendar date

## Appointment

**Purpose**: Represents a patient-owned clinic appointment that may be listed,
confirmed, or canceled.

**Fields**:

- `id`: stable appointment identifier
- `patient_id`: owning patient identifier
- `date`: appointment date
- `time`: appointment time
- `doctor`: clinician or provider display name
- `status`: one of `scheduled`, `confirmed`, or `canceled`

**Validation Rules**:

- `patient_id` must match the verified patient before any mutation
- only `scheduled` appointments may transition to `confirmed`
- only `scheduled` or `confirmed` appointments may transition to `canceled`
- repeated confirm or cancel requests must return stable outcomes

**State Transitions**:

- `scheduled -> confirmed`
- `scheduled -> canceled`
- `confirmed -> canceled`
- `confirmed -> confirmed` returns idempotent success with no destructive change
- `canceled -> canceled` returns idempotent success with no destructive change

## ConversationState

**Purpose**: Holds thread-scoped short-term memory for the conversational
workflow.

**Fields**:

- `thread_id`: stable workflow thread identifier derived from `session_id`
- `messages`: accumulated user and assistant messages for the current thread
- `requested_operation`: latest requested high-level operation such as list,
  confirm, cancel, help, or unknown
- `deferred_operation`: protected operation paused until verification succeeds
- `verified`: boolean gate for protected actions
- `verification_status`: current verification progress or failure status
- `patient_id`: resolved patient identifier after successful verification
- `provided_full_name`: captured verification value
- `provided_phone`: captured verification value
- `provided_dob`: captured verification value
- `missing_verification_fields`: remaining required fields
- `listed_appointments`: current appointment list available for follow-up
  references
- `selected_appointment_id`: resolved appointment target for confirm or cancel
- `last_action_result`: structured outcome of the latest completed action
- `response_text`: final response text returned to the caller
- `issue`: structured internal recovery signal

**Validation Rules**:

- protected actions require `verified == true`
- `deferred_operation` may only be set for protected actions blocked by policy
- `selected_appointment_id` must resolve from the current patient's known
  appointments
- `listed_appointments` must be refreshed after state-changing actions when the
  conversation depends on current list context

## Relationships

- One `Patient` owns many `Appointment` records
- One `ConversationState` may resolve to exactly one `Patient` at a time
- `ConversationState.listed_appointments` is a thread-local projection of the
  verified patient's appointments
- `ConversationState.selected_appointment_id` points to one appointment from the
  verified patient's list

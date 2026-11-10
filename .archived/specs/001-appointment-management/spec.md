# Feature Specification: Conversational Appointment Management

**Feature Branch**: `001-build-appointment-service`  
**Created**: 2026-04-13  
**Status**: Draft  
**Input**: User description: "Create a conversational service that verifies clinic patients before allowing them to list, confirm, cancel, and naturally reroute between appointment actions."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Verify and View Appointments (Priority: P1)

As a clinic patient, I want the service to guide me through identity verification
and then show my appointments, so I can safely access my schedule without using
clinic-specific system terms.

Verification gate: appointment details remain unavailable until the patient has
provided full name, phone number, and date of birth that match a valid record.
If the patient asks to view appointments before verification, the service pauses
that request, gathers the missing identity details, and resumes the request only
after successful verification. If verification fails, the service asks the
patient to try again without revealing whether any partial details were correct.

**Why this priority**: Verification and appointment visibility are the minimum
valuable journey and the required entry point for every protected action.

**Independent Test**: A patient asks to see appointments before verification,
provides valid identifying details, and receives only their own appointments in
the same conversation.

**Acceptance Scenarios**:

1. **Given** an unverified patient asks to see appointments, **When** the
   patient provides valid identity details, **Then** the service verifies the
   patient and returns that patient's appointment list in the same conversation.
2. **Given** an unverified patient asks to see appointments, **When** the
   patient has not yet provided all required identity details, **Then** the
   service asks only for the missing details and does not show appointment data.
3. **Given** an unverified patient provides identity details that do not match a
   valid patient, **When** verification is attempted, **Then** the service
   returns a generic retry message and does not reveal any appointment data.

---

### User Story 2 - Confirm an Appointment Conversationally (Priority: P2)

As a verified clinic patient, I want to confirm one of my appointments using
natural conversational references such as "the first one" or a date and time,
so I can complete the action quickly without re-entering all appointment
details.

Verification gate: confirmation remains unavailable until the patient is already
verified. Rerouting behavior: after viewing appointments, the patient may move
straight into confirmation without restarting the conversation. Failure
recovery: if the appointment reference is ambiguous or invalid, the service asks
for clarification instead of confirming the wrong appointment.

**Why this priority**: Confirmation is a core protected action and depends on
the verified appointment-listing journey established in User Story 1.

**Independent Test**: A verified patient views their appointments, says
"confirm the first one," and receives confirmation for the correct appointment
or a clarification request if the reference is unclear.

**Acceptance Scenarios**:

1. **Given** a verified patient has an appointment list in the current
   conversation, **When** the patient asks to confirm an appointment using a
   clear conversational reference, **Then** the service confirms the correct
   appointment and returns a completion message.
2. **Given** a verified patient requests confirmation with an ambiguous
   reference, **When** the service cannot confidently determine the target
   appointment, **Then** the service asks for clarification and makes no change.
3. **Given** a verified patient requests confirmation for an appointment that is
   already confirmed, **When** the request is processed, **Then** the service
   returns a stable response that keeps the appointment status unchanged.

---

### User Story 3 - Cancel and Reroute Naturally (Priority: P3)

As a verified clinic patient, I want to cancel an appointment and move between
listing, confirming, and canceling naturally within one conversation, so the
experience feels helpful instead of rigid.

Verification gate: cancellation remains unavailable until the patient is
verified. Rerouting behavior: the patient may change direction at any time,
such as asking to list appointments, then cancel one, then return to the list.
Failure recovery: if the selected appointment is unclear, unavailable, or was
already canceled, the service responds safely and keeps the conversation going.

**Why this priority**: Natural rerouting is a stated requirement of the
exercise and demonstrates that the service can manage protected actions without
forcing patients to restart the flow.

**Independent Test**: A verified patient lists appointments, asks to cancel one,
then asks to view the remaining appointments again without restarting the
conversation.

**Acceptance Scenarios**:

1. **Given** a verified patient has access to their appointments, **When** the
   patient asks to cancel one and uses a valid appointment reference, **Then**
   the service cancels the correct appointment and returns a completion message.
2. **Given** a verified patient changes intent mid-conversation, **When** the
   patient moves from listing to canceling and back to listing, **Then** the
   service follows the latest valid request without requiring a restart.
3. **Given** a verified patient repeats a cancellation request for an
   appointment that is already canceled, **When** the request is processed,
   **Then** the service returns a stable response and does not create an
   inconsistent outcome.

### Edge Cases

- A patient asks to confirm or cancel an appointment before identity has been
  verified.
- A patient provides verification details across multiple messages and changes
  goals during collection.
- A patient refers to an appointment using a vague reference such as
  "the first one" when no current list is available.
- A patient tries to confirm or cancel an appointment that does not belong to
  them or no longer exists.
- A patient repeats the same confirmation or cancellation request after already
  receiving a completion message.
- The service cannot complete a request because appointment data is temporarily
  unavailable.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The service MUST provide a conversational experience for clinic
  patients seeking help with appointments.
- **FR-002**: The service MUST collect full name, phone number, and date of
  birth before allowing any protected appointment action.
- **FR-003**: The service MUST block appointment listing, confirmation, and
  cancellation until identity verification succeeds.
- **FR-004**: The service MUST remember a protected request made before
  verification and resume that request after successful verification.
- **FR-005**: The service MUST preserve conversation continuity within the same
  patient session so the patient does not need to restart after each action.
- **FR-006**: The service MUST list only the appointments that belong to the
  verified patient.
- **FR-007**: The service MUST allow a verified patient to confirm an
  appointment using a conversational reference from the current context, such
  as list position or appointment details.
- **FR-008**: The service MUST allow a verified patient to cancel an
  appointment using a conversational reference from the current context, such
  as list position or appointment details.
- **FR-009**: The service MUST let the patient switch between supported actions
  naturally and follow the latest valid request.
- **FR-010**: The service MUST ask clarifying questions when a patient request
  is ambiguous or incomplete instead of taking a risky action.
- **FR-011**: The service MUST return stable, non-destructive responses when a
  patient repeats a confirmation or cancellation request.
- **FR-012**: The service MUST avoid revealing whether another patient's data or
  appointments exist when verification or appointment selection fails.

### Key Entities *(include if feature involves data)*

- **Patient**: A clinic patient who must be identified before protected
  appointment actions are allowed.
- **Appointment**: A scheduled clinic visit associated with one patient and a
  current status such as scheduled, confirmed, or canceled.
- **Conversation Session**: The active patient interaction that holds context
  such as collected identity details, current goal, and recent appointment
  references.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In acceptance testing, patients who provide valid identity details
  can complete verification and view their appointments within 4 conversational
  turns after their first protected request.
- **SC-002**: 100% of covered test scenarios attempting to list, confirm, or
  cancel appointments before verification are blocked and redirected to
  verification.
- **SC-003**: 95% of scripted confirmation and cancellation journeys that use
  references from the current appointment list complete without the patient
  restarting the conversation.
- **SC-004**: 100% of repeated confirmation and cancellation test scenarios
  return stable outcomes without creating duplicate or contradictory changes.
- **SC-005**: 100% of covered failure scenarios involving invalid identity data
  or wrong-patient appointment references return safe responses that do not
  expose another patient's information.

## Assumptions

- Each conversation is associated with one patient at a time.
- Patients can provide full name, phone number, and date of birth during the
  conversation.
- Appointment management in this feature is limited to listing, confirming, and
  canceling existing clinic appointments.
- Rescheduling, payments, insurance steps, and human handoff are outside the
  scope of this feature.
- Patients remain in the same conversation session while completing a given
  appointment-management task.

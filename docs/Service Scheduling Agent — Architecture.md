# Service Scheduling Agent — Architecture

Mixed architecture for conversational scheduling, reminders, and deterministic execution

# 1. Architecture Goal

The system should use AI where language is ambiguous and software where correctness is mandatory.

The architecture separates four responsibilities:

Understanding: What does the customer mean?

Decision: Which workflow should run next?

Execution: What deterministic steps are allowed?

Domain logic: Is the requested operation valid in the real scheduling state?

# 2. High-Level Architecture

```text
Customer
   |
   +---------------------------+
   |                           |
WhatsApp                   Web Booking UI
   |                           |
   |                 structured booking actions
   |                           |
   +-------------+-------------+
                 |
             API Gateway
                 |
          Conversation Service
                 |
        +--------+--------+
        |                 |
      Text              Audio
        |                 |
Low-cost text       Gemma 3n E2B
understanding       transcription + intent extraction
        |                 |
        +--------+--------+
                 |
          NormalizedIntent / State
                 |
          Decision Engine
          Fine-tuned Laya
   |
   +-- confidence high --> Workflow Engine
   |
   +-- confidence low  --> General-model fallback / human
                              |
                              v
                        Workflow Engine
                              |
                 Deterministic domain workflows
                    /       |        \
                 Book   Reschedule   Cancel
                    \       |        /
                     Scheduling Engine
                              |
          +-------------------+-------------------+
          |                   |                   |
      Calendar Adapter   Messaging Adapter   Payment Adapter
          |                   |                   |
      Calendar/API       WhatsApp/SMS/etc.   Payment provider
```

Shared infrastructure:

PostgreSQL = durable business state

Redis = short-lived conversation/session/cache state

Queue/Worker = reminders, delayed jobs, retries, event processing

Observability = traces, metrics, model decisions, tool calls, audit events

# 3. Mixed Architecture Responsibilities

Text understanding model

Receives text messages and produces structured facts instead of controlling the workflow. It can use a low-cost general model or deterministic parsing for simple cases.

Audio understanding model — Gemma 3n E2B

Receives short customer audio messages. Gemma 3n E2B performs two bounded tasks: speech-to-text transcription and extraction of a compact scheduling intent/state. Gemma does not select graph nodes and does not execute tools. Its output becomes input to the Laya decision engine.

Audio flow:

`audio message → Gemma 3n E2B → transcript + structured intent/state → Laya → next node`

Example input:

“Can we move tomorrow’s massage to after 7 with Ana?”

Example normalized state:

intent: reschedule

preferred_professional: Ana

date_reference: tomorrow

time_constraint: after 19:00

existing_booking_required: true

Laya decision engine

Receives the normalized intent/state plus workflow context and returns the typed routing decision. The normalized state may come from a text understanding model or from Gemma 3n E2B after processing audio. Laya owns node routing; the upstream understanding model does not.

Example:

next_action = CHECK_RESCHEDULE_AVAILABILITY

needs_human = false

confidence = 0.97

The Laya model should be fine-tuned on project-specific decision examples. It is intentionally small because it is not responsible for open-ended generation.

Workflow engine

LangGraph or an equivalent explicit state machine owns allowed transitions.

Examples:

BOOK

CHECK_AVAILABILITY -> PRESENT_SLOTS -> CONFIRM_SLOT -> REVALIDATE_SLOT -> CREATE_BOOKING

RESCHEDULE

LOAD_BOOKING -> COLLECT_NEW_CONSTRAINTS -> CHECK_AVAILABILITY -> CONFIRM_NEW_SLOT -> REVALIDATE_SLOT -> COMMIT_RESCHEDULE

CANCEL

LOAD_BOOKING -> CHECK_POLICY -> CONFIRM_CANCELLATION -> CANCEL_BOOKING

Domain services

Business logic is plain application code. The AI does not calculate authoritative availability or decide whether a database mutation is valid.

# 4. Request Contracts

NormalizedRequest

conversation_id

customer_id

input_modality

transcript

intent

service

professional

location

date_constraints

time_constraints

booking_reference

free_text_summary

missing_fields

confidence

DecisionInput

normalized_request

workflow_state

available_actions

business_policy_flags

DecisionOutput

next_action

confidence

needs_human

optional_reason_code

ToolResult

success

error_code

domain_payload

retryable

Contracts should use strict schemas and validation.

# 5. State Ownership

Conversation state

Owns recent messages, normalized customer intent, clarification context, and channel metadata.

Workflow state

Owns progress inside one operation: booking, rescheduling, cancellation, reminder confirmation, or FAQ.

Scheduling state

PostgreSQL is the source of truth for services, availability rules, blocked periods, and bookings.

AI state

Model outputs are evidence for decisions, not authoritative business state.

# 6. Booking Flow

1. Customer sends a text or audio request.
2. For text, the text understanding layer extracts scheduling constraints. For audio, Gemma 3n E2B transcribes the message and extracts a compact intent/state.
3. Laya receives that normalized intent/state and selects the correct booking workflow node or another bounded action.
4. Workflow loads service and professional rules.
5. Scheduling engine computes valid slots.
6. UI or conversation presents only returned slots.
7. Customer chooses one.
8. Workflow revalidates the slot.
9. Booking is committed in an idempotent transaction.
10. Confirmation and reminder jobs are scheduled.

# 7. Reminder-to-Reschedule Flow

Reminder worker sends a message before the appointment.

Customer replies:

“I can’t make it. Anything after 6 tomorrow?”

The conversation service normalizes the reply. If the reminder response is audio, Gemma 3n E2B first transcribes it and extracts the scheduling intent/state.

Laya receives that state and routes to RESCHEDULE.

The workflow loads the referenced booking.

The scheduling engine computes valid alternatives.

The old booking remains active until the new slot is revalidated and the reschedule commit succeeds.

This flow demonstrates why reminders, conversational understanding, and deterministic scheduling should share state without giving the model direct database control.

# 8. Front-End Architecture

Customer booking UI

Mobile-first web/PWA experience.

The AI can generate constraints and open the UI already filtered. Example: a customer asks for Friday after 18:00, then receives a booking page containing only matching times.

Business dashboard

Calendar view

Appointment detail

Customers

Professionals

Services

Availability rules

Reminder status

Conversation/audit history

Agent configuration

WhatsApp should be treated as a channel, not as the core product. Meta-native appointment experiences can coexist with the scheduling backend.

# 9. Model Strategy

Default experimental architecture:

Text: low-cost understanding model -> normalized state -> fine-tuned Laya -> deterministic workflow

Audio: Gemma 3n E2B -> transcription + structured intent/state -> fine-tuned Laya -> deterministic workflow

Alternative A:

Fine-tuned Laya directly on raw text customer messages -> workflow

Alternative B:

Fine-tuned Qwen small model -> structured decision -> workflow

Alternative C:

General API model directly returns the typed action -> workflow

The benchmark determines which version becomes the production default.

# 10. Laya Deployment

Laya is small enough that serving it should not require a high-VRAM GPU.

Preferred sequence:

Development and fine-tuning: local GPU or short-lived rented GPU

Inference benchmark: CPU vs small GPU

Production: choose CPU, serverless small GPU, or always-on small GPU based on measured p95 and utilization

Avoid renting 24–48 GB VRAM solely for a decision router unless benchmarking proves a real throughput requirement.

# 11. Async Architecture

Reminder scheduling and external retries should not block conversation requests.

```text
BookingCommitted
   |
   +--> schedule reminder jobs
   +--> send confirmation
   +--> update calendar adapter
   +--> emit analytics event
```

Use a queue with idempotency keys and retry policies.

# 12. Reliability Rules

Never trust a previously shown slot at commit time; revalidate it.

Use idempotency keys for create, reschedule, cancel, message-send, and payment operations.

Do not let an LLM construct arbitrary SQL or arbitrary external API calls.

Tool errors must become explicit workflow states.

Fallback does not mean retrying indefinitely with larger models.

A human escalation path must preserve conversation and workflow context.

# 13. Observability

Trace every turn across:

channel request

input modality

audio transcription when applicable

intent/state extraction

normalization

model/provider

Laya decision

workflow transition

domain service

external adapter

final response

Core metrics:

transcription_accuracy

audio_intent_accuracy

route_accuracy

task_success_rate

wrong_slot_offer_rate

tool_failure_rate

fallback_rate

human_escalation_rate

p50_latency

p95_latency

model_cost

cost_per_completed_booking

# 14. Security and Privacy

Use least-privilege credentials for adapters.

Encrypt secrets outside application code.

Separate tenant/business data by business ID.

Redact sensitive fields from model traces where possible.

Define retention for conversation content.

Treat model/provider calls as external data-processing boundaries.

# 15. A2A / Multi-Agent Evolution

A2A is not required for the MVP.

Introduce A2A only if independent agents become real deployment boundaries, for example:

Scheduling Agent

Payments Agent

Customer Support Agent

Each agent should own a distinct capability and lifecycle. Do not split one small workflow into multiple agents only to demonstrate A2A.

# 16. Deployment Shape for V1

Frontend

Next.js or equivalent web application for customer booking and business dashboard.

Backend

Python/FastAPI application with conversation, workflow, scheduling, and adapter modules.

Workflow

LangGraph or an explicit state-machine abstraction.

Data

PostgreSQL + Redis.

Async

Queue/worker for reminders and retries.

AI

Pluggable text-understanding provider, Gemma 3n E2B audio-understanding service, and a fine-tuned Laya decision/routing service.

# 17. Architecture Principles

AI interprets; domain code validates.

Models propose transitions; workflows constrain transitions.

The database is the source of truth.

Availability is computed, never hallucinated.

Critical side effects are deterministic and idempotent.

Models and providers are replaceable.

Audio understanding and workflow routing are separate: Gemma produces transcript/intent state; Laya chooses the node.

Channels are replaceable.

Every important decision is observable and evaluable.

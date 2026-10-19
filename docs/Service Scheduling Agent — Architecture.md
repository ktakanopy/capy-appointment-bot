# Service Scheduling Agent — Architecture

Mixed architecture for conversational scheduling, reminders, and deterministic execution

# 1\. Architecture Goal

The system should use AI where language is ambiguous and software where correctness is mandatory.

The architecture separates four responsibilities:  
Understanding: What does the customer mean?  
Decision: Which workflow should run next?  
Execution: What deterministic steps are allowed?  
Domain logic: Is the requested operation valid in the real scheduling state?

# 2\. High-Level Architecture

Customer  
   |  
   \+--------------------+  
   |                    |  
WhatsApp            Web Booking UI  
   |                    |  
   \+---------+----------+  
             |  
         API Gateway  
             |  
      Conversation Service  
             |  
     Structured State Builder  
             |  
   \+---------+-----------------------+  
   |                                 |  
Understanding Model             Fast-path rules  
Qwen / DeepSeek /               deterministic cases  
Kimi / OpenAI  
   |  
NormalizedRequest  
   |  
Decision Engine  
Fine-tuned Laya  
   |  
   \+-- confidence high \--\> Workflow Engine  
   |  
   \+-- confidence low  \--\> General-model fallback / human  
                              |  
                              v  
                        Workflow Engine  
                              |  
                 Deterministic domain workflows  
                    /       |        \\  
                 Book   Reschedule   Cancel  
                    \\       |        /  
                     Scheduling Engine  
                              |  
          \+-------------------+-------------------+  
          |                   |                   |  
      Calendar Adapter   Messaging Adapter   Payment Adapter  
          |                   |                   |  
      Calendar/API       WhatsApp/SMS/etc.   Payment provider

Shared infrastructure:  
PostgreSQL \= durable business state  
Redis \= short-lived conversation/session/cache state  
Queue/Worker \= reminders, delayed jobs, retries, event processing  
Observability \= traces, metrics, model decisions, tool calls, audit events

# 3\. Mixed Architecture Responsibilities

Understanding model  
Receives natural language and produces structured facts instead of controlling the workflow.

Example input:  
“Can we move tomorrow’s massage to after 7 with Ana?”

Example normalized state:  
intent: reschedule  
preferred\_professional: Ana  
date\_reference: tomorrow  
time\_constraint: after 19:00  
existing\_booking\_required: true

Laya decision engine  
Receives structured state plus workflow context and returns typed decisions.

Example:  
next\_action \= CHECK\_RESCHEDULE\_AVAILABILITY  
needs\_human \= false  
confidence \= 0.97

The Laya model should be fine-tuned on project-specific decision examples. It is intentionally small because it is not responsible for open-ended generation.

Workflow engine  
LangGraph or an equivalent explicit state machine owns allowed transitions.

Examples:  
BOOK  
CHECK\_AVAILABILITY \-\> PRESENT\_SLOTS \-\> CONFIRM\_SLOT \-\> REVALIDATE\_SLOT \-\> CREATE\_BOOKING

RESCHEDULE  
LOAD\_BOOKING \-\> COLLECT\_NEW\_CONSTRAINTS \-\> CHECK\_AVAILABILITY \-\> CONFIRM\_NEW\_SLOT \-\> REVALIDATE\_SLOT \-\> COMMIT\_RESCHEDULE

CANCEL  
LOAD\_BOOKING \-\> CHECK\_POLICY \-\> CONFIRM\_CANCELLATION \-\> CANCEL\_BOOKING

Domain services  
Business logic is plain application code. The AI does not calculate authoritative availability or decide whether a database mutation is valid.

# 4\. Request Contracts

NormalizedRequest  
conversation\_id  
customer\_id  
intent  
service  
professional  
location  
date\_constraints  
time\_constraints  
booking\_reference  
free\_text\_summary  
missing\_fields  
confidence

DecisionInput  
normalized\_request  
workflow\_state  
available\_actions  
business\_policy\_flags

DecisionOutput  
next\_action  
confidence  
needs\_human  
optional\_reason\_code

ToolResult  
success  
error\_code  
domain\_payload  
retryable

Contracts should use strict schemas and validation.

# 5\. State Ownership

Conversation state  
Owns recent messages, normalized customer intent, clarification context, and channel metadata.

Workflow state  
Owns progress inside one operation: booking, rescheduling, cancellation, reminder confirmation, or FAQ.

Scheduling state  
PostgreSQL is the source of truth for services, availability rules, blocked periods, and bookings.

AI state  
Model outputs are evidence for decisions, not authoritative business state.

# 6\. Booking Flow

1\. Customer sends a request.  
2\. Understanding layer extracts scheduling constraints.  
3\. Laya selects the booking workflow or asks for another bounded action.  
4\. Workflow loads service and professional rules.  
5\. Scheduling engine computes valid slots.  
6\. UI or conversation presents only returned slots.  
7\. Customer chooses one.  
8\. Workflow revalidates the slot.  
9\. Booking is committed in an idempotent transaction.  
10\. Confirmation and reminder jobs are scheduled.

# 7\. Reminder-to-Reschedule Flow

Reminder worker sends a message before the appointment.

Customer replies:  
“I can’t make it. Anything after 6 tomorrow?”

The conversation service normalizes the reply.  
Laya routes to RESCHEDULE.  
The workflow loads the referenced booking.  
The scheduling engine computes valid alternatives.  
The old booking remains active until the new slot is revalidated and the reschedule commit succeeds.

This flow demonstrates why reminders, conversational understanding, and deterministic scheduling should share state without giving the model direct database control.

# 8\. Front-End Architecture

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

# 9\. Model Strategy

Default experimental architecture:  
General low-cost model \-\> normalized state \-\> fine-tuned Laya \-\> deterministic workflow

Alternative A:  
Fine-tuned Laya directly on raw customer messages \-\> workflow

Alternative B:  
Fine-tuned Qwen small model \-\> structured decision \-\> workflow

Alternative C:  
General API model directly returns the typed action \-\> workflow

The benchmark determines which version becomes the production default.

# 10\. Laya Deployment

Laya is small enough that serving it should not require a high-VRAM GPU.

Preferred sequence:  
Development and fine-tuning: local GPU or short-lived rented GPU  
Inference benchmark: CPU vs small GPU  
Production: choose CPU, serverless small GPU, or always-on small GPU based on measured p95 and utilization

Avoid renting 24–48 GB VRAM solely for a decision router unless benchmarking proves a real throughput requirement.

# 11\. Async Architecture

Reminder scheduling and external retries should not block conversation requests.

BookingCommitted  
   |  
   \+--\> schedule reminder jobs  
   \+--\> send confirmation  
   \+--\> update calendar adapter  
   \+--\> emit analytics event

Use a queue with idempotency keys and retry policies.

# 12\. Reliability Rules

Never trust a previously shown slot at commit time; revalidate it.

Use idempotency keys for create, reschedule, cancel, message-send, and payment operations.

Do not let an LLM construct arbitrary SQL or arbitrary external API calls.

Tool errors must become explicit workflow states.

Fallback does not mean retrying indefinitely with larger models.

A human escalation path must preserve conversation and workflow context.

# 13\. Observability

Trace every turn across:  
channel request  
normalization  
model/provider  
Laya decision  
workflow transition  
domain service  
external adapter  
final response

Core metrics:  
route\_accuracy  
task\_success\_rate  
wrong\_slot\_offer\_rate  
tool\_failure\_rate  
fallback\_rate  
human\_escalation\_rate  
p50\_latency  
p95\_latency  
model\_cost  
cost\_per\_completed\_booking

# 14\. Security and Privacy

Use least-privilege credentials for adapters.  
Encrypt secrets outside application code.  
Separate tenant/business data by business ID.  
Redact sensitive fields from model traces where possible.  
Define retention for conversation content.  
Treat model/provider calls as external data-processing boundaries.

# 15\. A2A / Multi-Agent Evolution

A2A is not required for the MVP.

Introduce A2A only if independent agents become real deployment boundaries, for example:  
Scheduling Agent  
Payments Agent  
Customer Support Agent

Each agent should own a distinct capability and lifecycle. Do not split one small workflow into multiple agents only to demonstrate A2A.

# 16\. Deployment Shape for V1

Frontend  
Next.js or equivalent web application for customer booking and business dashboard.

Backend  
Python/FastAPI application with conversation, workflow, scheduling, and adapter modules.

Workflow  
LangGraph or an explicit state-machine abstraction.

Data  
PostgreSQL \+ Redis.

Async  
Queue/worker for reminders and retries.

AI  
Pluggable general-model provider plus a Laya decision service.

# 17\. Architecture Principles

AI interprets; domain code validates.  
Models propose transitions; workflows constrain transitions.  
The database is the source of truth.  
Availability is computed, never hallucinated.  
Critical side effects are deterministic and idempotent.  
Models and providers are replaceable.  
Channels are replaceable.  
Every important decision is observable and evaluable.  

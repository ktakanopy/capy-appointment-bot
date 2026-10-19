# Service Scheduling Agent — Project Specification

AI-first scheduling and reminder platform for small service businesses

# 1\. Product Vision

Build an AI-assisted scheduling platform for small service businesses where customers can book, reschedule, cancel, confirm, and receive reminders through conversational channels or a lightweight booking UI.

The first demo vertical is an independent massage therapist or small massage studio, but the domain model should remain generic enough for barbers, personal trainers, tattoo artists, beauty professionals, private teachers, pet groomers, and similar appointment-based businesses.

The project is not “a WhatsApp bot.” WhatsApp is one possible channel. The product owns the scheduling rules, conversation state, workflows, reminders, integrations, and business dashboard.

# 2\. Core Problem

Small service businesses often coordinate appointments manually across messaging apps and calendars. This creates repeated work, slow responses, missed reminders, double-booking risk, and fragmented customer context.

The system should make common scheduling operations conversational while keeping the actual booking logic deterministic and auditable.

# 3\. MVP Users

Business owner / professional  
Configures services, working hours, availability rules, locations, buffers, deposits, and reminder policies.

Customer  
Books, reschedules, cancels, confirms, or asks about an existing appointment through WhatsApp or the web booking experience.

# 4\. MVP Use Cases

Book an appointment  
Customer expresses a service, preferred day/time, professional, or location. The system extracts constraints, finds valid slots, presents options, confirms the choice, and creates the booking.

Reschedule an appointment  
Customer changes an existing booking. The old booking is kept until the new slot is validated and the reschedule transaction succeeds.

Cancel an appointment  
Customer cancels an eligible booking. Cancellation policy and fees are evaluated in deterministic business logic.

Check availability  
Customer asks questions such as “Do you have anything Friday after 6?” The system returns only slots produced by the scheduling engine.

Reminder and confirmation  
The system sends configurable reminders before an appointment and can ask the customer to confirm attendance.

Reminder-triggered rescheduling  
A reminder reply such as “I can’t make it; can we do after 7?” becomes a new rescheduling workflow rather than a free-form chatbot exchange.

Basic service questions  
The assistant can answer bounded questions about duration, location, price, preparation, and cancellation rules from business-owned data.

Human escalation  
Low-confidence, unsupported, or policy-sensitive situations can be handed off instead of forcing an AI decision.

# 5\. Product Surfaces

Customer conversation channel  
WhatsApp is the first messaging integration. Additional channels should be possible without changing the scheduling core.

Customer booking web UI  
A mobile-first interface for selecting service, professional, date, time, and optional deposit. Conversational intent can pre-filter this UI.

Business dashboard  
Calendar, appointments, customers, services, professionals, availability, reminder status, and basic agent settings.

# 6\. Scheduling Rules

The scheduling engine must support:  
Service duration  
Professional working hours  
Professional-specific services  
Location or travel constraints  
Buffers between appointments  
Existing bookings and blocked periods  
Minimum booking notice  
Maximum booking horizon  
Cancellation and rescheduling windows  
Optional deposits  
Time zone handling

The AI must never invent availability. Available slots come from deterministic scheduling logic.

# 7\. Mixed Architecture

The project intentionally separates language understanding, decision routing, workflow execution, and domain logic.

Understanding layer  
A low-cost general model converts ambiguous user language into a compact structured request. Candidate providers include Qwen, DeepSeek, Kimi, OpenAI, or another interchangeable API/model.

Decision layer  
A specialized decision model such as a fine-tuned Laya model selects typed actions or next workflows. Example outputs: BOOK, RESCHEDULE, CANCEL, CHECK\_AVAILABILITY, FAQ, CONFIRM, ESCALATE.

Execution layer  
Deterministic graph workflows execute the selected operation. The workflow, not the language model, controls critical side effects.

Domain layer  
A scheduling service validates availability, business policies, conflicts, and booking transactions.

Fallback  
Low-confidence decisions can be routed to a stronger general model or human review.

# 8\. Initial Data Model

Business  
Professional  
Service  
Location  
AvailabilityRule  
BlockedPeriod  
Customer  
Booking  
Conversation  
Reminder  
Payment  
AgentDecision  
AuditEvent

# 9\. Integrations

Initial candidates:  
WhatsApp Business / Meta messaging APIs  
Google Calendar or a calendar adapter  
Payment provider for optional deposits  
Email/SMS adapter as future channels

External providers must be hidden behind adapters so the domain does not depend on one vendor.

# 10\. Non-Functional Requirements

Reliability  
Booking, rescheduling, and cancellation operations must be idempotent.

Concurrency safety  
The system must revalidate a slot immediately before committing a booking.

Auditability  
Every decision, tool call, state transition, and side effect should be traceable.

Latency  
Simple routing should be fast enough for interactive messaging. Expensive model calls should not sit on every workflow step.

Cost  
Track cost per conversation, cost per completed booking, model calls per task, and fallback rate.

Privacy  
Store only data required by the scheduling product. Avoid sensitive or unrelated customer profiling.

# 11\. Evaluation

The MVP should include an offline evaluation set with realistic and synthetic conversations.

Primary metrics:  
Intent / route accuracy  
Task completion rate  
Wrong-slot-offer rate  
Invalid action rate  
Booking correctness  
Fallback rate  
p50 / p95 decision latency  
Cost per 1,000 conversations  
Reminder-to-confirmation conversion

Compare at least:  
General API model only  
Fine-tuned small generative model  
Fine-tuned Laya decision model  
General model → Laya → deterministic workflow  
Laya directly on raw user input, if feasible

# 12\. MVP Boundaries

In scope  
Scheduling, rescheduling, cancellation, availability, reminders, confirmation, simple FAQs, web booking UI, business dashboard, and one messaging channel.

Out of scope for V1  
Voice calls, healthcare workflows, medical or clinical data, marketplace discovery, complex CRM automation, autonomous multi-agent swarms, and unrestricted tool use.

# 13\. Public-Safe / Clean-Room Constraint

This project must remain independent from prior employer systems.

Use only generic scheduling concepts, public APIs, original prompts, original schemas, synthetic/demo data, and independently designed workflows. Do not copy proprietary endpoints, internal names, prompts, tenant rules, implementation details, datasets, or business-specific logic from previous work.

The goal is to demonstrate the general engineering idea: probabilistic language understanding combined with specialized decision routing and deterministic execution.

# 14\. Success Criteria for V1

A customer can complete booking, rescheduling, cancellation, and reminder confirmation end-to-end.

The system never offers a slot not returned by the scheduling engine.

Critical actions are deterministic, idempotent, and auditable.

The AI layer can be benchmarked independently from the scheduling engine.

The architecture can swap model providers without rewriting business workflows.

The same core can support a second service-business vertical with configuration changes rather than a rewrite.  

# Plan: follow the scheduling specification

Checked against `docs/Service Scheduling Agent — Project Specification.md` and `docs/Service Scheduling Agent — Architecture.md`.

Yes. The repository can follow those documents without rewriting the scheduling engine. `AvailabilityService` and `BookingService` already match the domain rule: the model interprets language, and application code computes availability and commits bookings.

The gap is the product architecture. Understanding, decision routing, workflow execution, and domain validation are not separate. WhatsApp, audio, customer identity, real reminder delivery, FAQ, escalation, and the owner configuration surface are missing.

This plan closes that gap in dependency order. It does not train Laya or Gemma before the contracts and workflow exist. Those models plug into ports once the deterministic path is testable.

## Keep

- PostgreSQL as appointment truth, including overlap exclusion, slot revalidation, and idempotent create, confirm, cancel, and reschedule.
- Redis as short-lived conversation state.
- The rule that a model cannot emit SQL, generate a management token, or decide that a slot is free.
- Direct scheduling endpoints for the web UI. Chat is not the only way to book.
- Synthetic demo data and the clean-room boundary.

## Do not do in this plan

- Live voice calls, healthcare workflows, marketplace discovery, CRM automation, or multi-agent A2A.
- A Next.js rewrite before the API contracts are stable. Streamlit remains the demo UI until the dashboard work starts.
- Payments, deposits, and calendar sync before one messaging channel can complete a booking. Those are adapter candidates, not the first success criterion.
- Putting raw tokens, phone numbers, or token digests in graph state, model prompts, logs, or traces.

## Target shape

```text
Channel adapter
    -> Conversation service
        -> Text or audio understanding
        -> Decision port
        -> Workflow graph
        -> Scheduling and booking services
    -> Outbound messaging port
```

Understanding returns a normalized request. The decision port returns the next allowed action and a confidence score. The workflow graph is the only component that advances a booking, reschedule, cancel, reminder confirmation, or FAQ step. Domain services accept or reject the mutation.

On WhatsApp, the verified sender number identifies the customer. The customer never sees a management token. On the web demo, the app holds the token until a real login exists. Neither identity chooses create versus reschedule. The workflow draft does.

## Phase 1 — Make the workflow own the turn

Follow `docs/graph-conversation-plan.md`.

One inbound message invokes the graph once. The graph loads the draft, merges the extraction, and routes to clarify, search, select, help, or execute. `POST /chat` becomes an adapter. `InMemorySaver` is not conversation memory. Redis stores the draft.

Done when `app/main.py` has no chat step branch, a graph test can run a turn without HTTP, and a token or known customer cannot silently turn a new booking into a reschedule.

## Phase 2 — Split understanding from decision

Add the specification contracts, even while the decision port is still deterministic:

- `NormalizedRequest`: conversation, customer, modality, transcript, intent, service, professional, date and time constraints, booking reference, missing fields, and confidence.
- `DecisionOutput`: `next_action`, confidence, `needs_human`, and an optional reason code.
- `ToolResult`: success, error code, domain payload, and whether the failure is retryable.

The current OpenAI and deterministic interpreters implement text understanding only. They must not choose the graph node.

The first decision adapter is a deterministic router over the normalized request plus the workflow draft. It returns the same action set the specification names: `BOOK`, `RESCHEDULE`, `CANCEL`, `CHECK_AVAILABILITY`, `FAQ`, `CONFIRM`, and `ESCALATE`. Laya can replace that adapter later without changing the workflow.

Done when a unit test can swap the decision adapter and the workflow still constrains the transition. An understanding model cannot call a booking service directly.

## Phase 3 — Bind identity and workflow state

Add a durable conversation record scoped by business and channel. For a verified channel, resolve the sender to the existing `(business_id, phone)` customer and store `customer_id` on the draft.

Workflow state remembers the operation, missing field, offered slot ids, and active booking id. Appointment memory comes from PostgreSQL, not from chat history. Zero bookings offers creation. One booking can be the target. Several bookings ask which one and do not write.

The web demo continues to pass an authorized `booking_id` into the graph after validating the token outside it.

Done when "when is my appointment?" and "move it" work from a verified customer id, and an unverified typed phone still cannot manage a booking.

## Phase 4 — Add WhatsApp as a channel adapter

Add the Cloud API webhook behind an adapter. Verify the signature, map the receiving number to the business, map the sender to the customer, deduplicate the provider message id, and call the same workflow. Do not call `/chat` over HTTP and do not accept `business_id` from the customer payload.

Outbound messages go through a durable outbox with retries and delivery-status updates. Interactive replies map to offered slot ids or action ids, not to free-form booking ids invented by the model.

Done when a verified text message can search, book, confirm, cancel, and reschedule without a pasted token, and a duplicate webhook does not create a second booking.

## Phase 5 — Send reminders and accept their replies

Keep the existing reminder schedule and worker claim logic. Replace the demo inbox write with the messaging port. A reminder for an existing appointment and a suggestion to book a new one stay different message purposes, with opt-in and template category checks before anything is sent.

A customer reply resumes that customer's workflow and sets the operation from the decision, normally `RESCHEDULE` or `CONFIRM`. The old booking remains active until the new slot commits.

Done when a due reminder is delivered or retried durably, and a reply can confirm or move the referenced booking without a token in the message.

## Phase 6 — FAQ and escalation

Answer bounded questions from the business catalog and policy: duration, price, preparation text, and cancellation rules. Add only the catalog fields required for those answers. Do not add a general RAG bot.

If the decision returns `needs_human` or confidence stays below a configured threshold, stop the workflow, persist the draft, and record an escalation. Do not loop through larger models.

Done when a price or duration question does not start a booking, and an unsupported request is traceable as an escalation instead of a generic clarification.

## Phase 7 — Evaluate the layers separately

Extend the current extraction evals. Add sets for route accuracy, task completion, wrong-slot offers, invalid actions, fallback, and reminder confirmation. Keep understanding, decision, and workflow scores separate so a provider can be compared without changing the scheduling engine.

Audio metrics stay empty until phase 8. Laya and Gemma are benchmark candidates behind the same ports, not prerequisites for the deterministic demo.

Done when `uv run python -m app.evals.runner` reports layer-specific results and a wrong slot cannot be marked as a successful offer.

## Phase 8 — Add short audio

Accept a short audio message on the messaging channel. Gemma 3n E2B, or a fake in tests, returns a transcript plus a normalized request. It does not select a node or call a tool. The decision port and workflow are unchanged.

Done when the same reschedule workflow runs from text and from a transcribed audio fixture, and the transcript is not treated as booking authority.

## Phase 9 — Owner configuration

Expose authenticated owner operations for services, professionals, weekly hours, blocked periods, reminder policy, and agent settings. The current unauthenticated agenda and reminder endpoints must be protected or limited to demo mode before this is reachable.

The customer UI can remain Streamlit for the demo. Conversation-provided constraints should pre-filter the slot list. A Next.js dashboard is optional after these operations exist. It is not required to follow the domain specification.

## Later adapters

Locations, deposits, calendar sync, and payments get their own ports after the channel workflow is stable. They must not enter the model prompt as tools that can write directly. Cancellation fees, if added, belong in the booking service, not in the decision model.

## Success criteria

The specification's V1 outcome is met when:

- a customer can book, reschedule, cancel, and confirm from a verified channel and from the web UI
- every offered slot comes from the scheduling engine and is rechecked before commit
- create, reschedule, cancel, and outbound send are idempotent
- understanding, decision, and workflow can be swapped or benchmarked independently
- a second seeded business works through configuration rather than a code change
- no model output is appointment truth

## Suggested order

1. Workflow ownership.
2. Normalized contracts and a deterministic decision port.
3. Verified customer identity and explicit workflow state.
4. WhatsApp text.
5. Real reminder delivery and replies.
6. FAQ and escalation.
7. Layered evaluations.
8. Audio through the same contracts.
9. Authenticated owner configuration.
10. Calendar, payments, locations, and deposits only after the channel loop is proven.

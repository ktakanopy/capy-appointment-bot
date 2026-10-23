# Plan: make the graph control the conversation

## Problem

`build_graph` in `app/graph/builder.py` is `START → interpret → END`. It returns a `SchedulingExtraction` and stops. Clarification, search, slot selection, create, confirm, cancel, and reschedule are chosen by `_handle_chat_intent` in `app/main.py`.

The graph does not control the conversation. Another entry point, including WhatsApp, would have to duplicate those branches or call back into `/chat`.

## Outcome

One inbound message invokes the graph once. The graph loads the scheduling draft, interprets the message, updates the draft, chooses the next step, calls a domain port when a write is allowed, saves the draft, and returns a channel-neutral result.

`POST /chat` becomes an adapter. It validates the request, redacts token-shaped text, resolves authorization, invokes the graph, appends redacted history, and maps the result to `ChatResponse`. It no longer decides the step.

Direct scheduling endpoints stay outside the graph. Streamlit forms can still call `AvailabilityService` and `BookingService` directly.

## Boundaries

The graph controls:

- the active operation: `create`, `reschedule`, `confirm`, `cancel`, or `none`
- which field is missing
- whether to clarify, search, select, help, or execute
- which previously offered slot may be selected
- when a domain port is called

The graph does not control:

- whether a slot is free
- booking SQL, token generation, or idempotency
- WhatsApp payloads or webhook verification
- trusting a phone number typed in the message

A management token authorizes a web request. It never sets the operation. A verified WhatsApp sender is represented as `customer_id` before the graph runs. The raw token, phone number, and token digest never enter graph state, the model prompt, logs, or traces.

## Target turn

```text
load_context → interpret → merge_draft → route
route → clarify | search | select | help | prepare_action
prepare_action → execute
clarify | search | select | help | execute → persist → END
```

`route` is a conditional edge on the draft, not a model decision. `execute` calls the existing booking service through a port and must tolerate a replay of the same idempotency key.

## State

Replace the extraction-only `SchedulingGraphState` with a turn state that includes:

- `conversation_id`, sanitized message, business-local today, and catalog names
- draft: `operation`, `service_id`, `professional_id`, `local_date`, `earliest_local_time`, `offered_slot_ids`, `pending_slot_id`, `missing`, and `active_booking_id`
- authorization already resolved by the adapter: `anonymous`, `customer_id`, or an authorized `booking_id`
- extraction, chosen slot, and the channel-neutral result

Redis remains the cross-turn store for the draft and offered slots. `InMemorySaver` is not conversation memory. Do not add a second checkpointer as a competing source of truth in this change.

PostgreSQL remains appointment truth. The graph does not reconstruct an existing appointment from chat history.

## Routing rules

- A new availability request sets `operation=create` unless the customer explicitly asks to move an existing booking.
- `reschedule` runs only when the draft operation is `reschedule` and the adapter supplied an authorized booking or a verified customer.
- Zero matching bookings asks whether to create. One matching booking is the target. Several matching bookings ask which one and do not write.
- If the draft is waiting for a date and the reply contains a date, continue the search even when the interpreter returns `unknown`.
- Ask for one missing field at a time.
- Invalid extracted dates or times clear that field and ask again. They must not raise.
- A slot can be selected only if its id is in `offered_slot_ids`. Saving a pending slot does not reserve it.
- Confirm and cancel also require an explicit operation plus authorization. They do not run just because a token is present.

## Phases

### 1. Lock the current contract

Make the API test lifespan avoid constructing the production Redis client before the fake runtime is installed. Keep `tests/api/test_chat_contract.py` green.

Add graph tests that invoke the compiled graph with fake availability and booking ports. No HTTP server and no Redis server are required for those tests.

### 2. Add the graph ports and state

Add `app/graph/ports.py` with small protocols for catalog lookup, draft load/save, availability search, customer booking lookup, and booking execution.

Extend `app/graph/state.py` with the draft and result fields. Add `app/graph/result.py` for the channel-neutral outcome: text, intent, offered slots, optional booking summary, and optional management token for the trusted web caller only.

`app/runtime.py` builds those ports from the existing repository, Redis chat store, availability service, and booking service.

### 3. Move each step into a node

Move the behavior from `_handle_chat_*` and `app/chat_helpers.py` into graph nodes:

- `interpret` keeps the current interpreter call.
- `merge_draft` canonicalizes catalog names and fills only missing draft fields.
- `search` calls the availability port and stores the offered slot ids.
- `select` resolves a slot id or one-based index against those ids.
- `prepare_action` checks the draft operation and authorization.
- `execute` calls the booking port.
- `clarify`, `help`, and `persist` own the remaining response and draft write.

Update `LangGraphWorkflow` so its public method is `handle_turn`, not `interpret`.

### 4. Make the route an adapter

`POST /chat` resolves the session, redacts the message, and validates a web management token before invocation. On success it passes `booking_id`, never the raw token.

It invokes `handle_turn`, appends redacted history, and returns the existing `ChatResponse` shape. Delete `_handle_chat_intent` and the other chat handlers from `app/main.py`.

Do not change `POST /bookings`, availability search, or the token-protected management routes.

### 5. Fix operation control and multi-turn recovery

This phase changes behavior on purpose:

- a token or known customer plus a selected slot creates a booking when the operation is `create`
- the same selection reschedules only when the operation is `reschedule`
- "tomorrow" after a date question continues the draft under the deterministic interpreter
- an invalid model date returns clarification and writes nothing

Update `docs/graph.md` so its diagram matches the compiled graph. `scripts/generate_graph_diagram.py` is stale and should be updated only if it can compile the new `build_graph` signature. Otherwise leave it unused.

## Tests

Graph unit tests cover:

- search offers slots and stores only those ids
- an unoffered slot id does not call create or reschedule
- confirm and cancel do not run without both the operation and authorization
- token-authorized `create` does not call reschedule
- explicit `reschedule` with one authorized booking calls reschedule
- several bookings ask which one and write nothing
- a date reply continues a draft waiting for a date
- an invalid date asks again
- the raw token is absent from the invoked state and the interpreter input

The existing API contract tests prove `/chat` still returns the same response shape.

## Out of scope

- WhatsApp webhook, signature verification, and outbound delivery
- replacing the web demo token with a login
- owner-endpoint authentication
- proactive suggestions and real reminder delivery
- a durable LangGraph checkpointer

Those can call `handle_turn` later. They are not required for the graph to control the conversation.

## Done when

- the compiled graph contains the routing nodes, not only `interpret`
- `app/main.py` has no chat step branch
- a graph test can run a multi-step turn without `TestClient`
- authorization cannot choose `create` versus `reschedule`
- booking availability and overlap rules still live in the existing domain services
- no raw management token or phone number is written to graph state

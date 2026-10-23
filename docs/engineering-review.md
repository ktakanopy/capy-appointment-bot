# Engineering review

Reviewed: 2026-10-22. No application code was changed.
bouti
The repository has a solid scheduling core, but it is still a local demo—not yet a production WhatsApp bot. The deterministic booking services should be preserved. The conversation, identity, and messaging layers around them are what need to improve.

Direct answers:

- **WhatsApp:** yes, it fits this product, but it should be a channel adapter, not logic embedded in booking services.
- **Chat flow in `app/main.py`:** acceptable for a prototype, but it should move out as the product grows.
- **Endpoints:** yes, and several already exist. What is missing is primarily messaging integration and authenticated configuration.
- **Reactive conversations, appointment memory, reminders, and proactive suggestions:** all make sense, but they should be separate, configurable capabilities.

## 1. Important findings in the current code

| Priority | Finding | Why it matters |
|---|---|---|
| High before public deployment | Owner bookings and demo reminder endpoints have no authentication. See [`app/main.py`](../app/main.py) around the owner bookings route. | The owner response includes customer contact information. These endpoints must be protected or disabled outside demo mode. |
| High | Having a management token automatically makes slot selection a reschedule. See `_handle_chat_slot_selection` in [`app/main.py`](../app/main.py). | Authorization to manage a booking is not the same as an intention to reschedule it. A returning customer trying to make another appointment can modify the existing one instead. This behavior was reproduced. |
| High for the requested use cases | There is no durable customer-to-conversation identity mapping. Sessions are temporary and not bound to a business or customer identity. See `create_session` in [`app/chat_history.py`](../app/chat_history.py). | WhatsApp replies, returning customers, and replies to reminders need reliable business, customer, conversation, and booking associations. |
| Medium | Conversation history exists, but there is no explicit scheduling draft or state machine. See [`app/graph/state.py`](../app/graph/state.py). | The bot should remember which information it is collecting independently of whether the LLM understands the latest message. The deterministic fallback does not use history. A "tomorrow" clarification reply was reproduced falling back to a generic prompt. |
| Medium | Extracted dates and times are strings, then parsed directly in handlers. See [`app/llm/schemas.py`](../app/llm/schemas.py) and the search handler in [`app/main.py`](../app/main.py). | Structured output guarantees a shape, not a valid calendar date. Invalid values should produce clarification rather than an unhandled parsing error. |

Foundations worth preserving:

- Booking policy remains outside the LLM.
- Availability is revalidated inside the booking transaction.
- PostgreSQL prevents overlapping bookings.
- Booking actions support idempotency.
- Management tokens are kept out of model input and normal observability.

Evolve this design. Do not rewrite the scheduling engine.

## 2. Should the chat flow live in `main.py` or the graph?

Not in `main.py`. That does not mean moving every rule into LangGraph.

The graph is effectively:

```text
START → interpret message → END
```

That is explicit in [`app/graph/builder.py`](../app/graph/builder.py). The actual conversation routing, clarification, slot selection, creation, and management live in [`app/main.py`](../app/main.py).

For the expanded requirements, use these boundaries:

| Layer | Responsibility |
|---|---|
| FastAPI routes / channel adapters | Validate transport input, authenticate, normalize events, and return acknowledgments or responses. |
| Conversation application service + graph | Load conversation context, determine the current step, collect missing fields, route actions, and construct replies. |
| Domain services | Validate availability, authorize booking operations, create, cancel, or reschedule, and enforce business rules and idempotency. |
| Scheduler / messaging workers | Trigger reminders and outreach, deliver messages, retry failures, and track delivery. |

A possible conversation workflow:

```text
Load context
    ↓
Interpret message + update scheduling draft
    ↓
Route by explicit conversation state
    ├── Collect missing information
    ├── Search availability
    ├── Process offered-slot selection
    ├── Prepare/confirm booking action
    └── Help or clarification
    ↓
Call deterministic services when authorized
    ↓
Persist state + produce channel-neutral reply
```

The graph should track explicit fields such as:

- Current operation: new booking versus reschedule.
- Selected service, date, and professional.
- Offered slots and their offer context.
- Pending customer information.
- Active booking reference.
- Whether an action requires confirmation.

A token must never determine the operation. It supplies authorization; the workflow supplies intent.

LangGraph is not mandatory. A plain application-level state machine is valid for a simple scheduling bot. With multiple entry points, proactive messages, and resumable conversations, a real graph becomes more useful than the current one-node wrapper.

## 3. WhatsApp: what to add

Start with the official WhatsApp Business Platform Cloud API, behind an adapter.

```text
WhatsApp webhook
    ↓
Verify signature → persist/deduplicate inbound event → acknowledge
    ↓
Conversation worker
    ↓
Shared conversation workflow
    ↓
Booking services
    ↓
Durable outbound queue → WhatsApp adapter
```

The same workflow should also support Streamlit or a future web frontend. The WhatsApp handler should call the shared application service. It should not duplicate the flow or make an HTTP call back into this app's `/chat` endpoint.

Essential pieces:

1. Webhook verification and signature validation.
2. Business and customer mapping from the configured receiving WhatsApp number and sender identity, not from a user-supplied business ID.
3. Inbound deduplication using provider message IDs. Booking-level idempotency alone does not prevent repeated replies.
4. Per-conversation serialization or concurrency control, so two quick messages cannot overwrite each other's state.
5. Durable outbound delivery, with retries and delivery-status callbacks.
6. Interactive button and list responses mapped to stable, scoped offer or action IDs.
7. Server-side booking authorization. Customers should not need to copy management tokens into WhatsApp messages.

The current reminder worker only writes to a demo inbox. It does not send messages. See [`app/reminders.py`](../app/reminders.py). Its scheduling logic is a useful starting point, but external delivery needs a separate adapter and delivery lifecycle. An API accepting a message is not proof that the customer received it.

### WhatsApp constraints affect the product

Meta's documentation requires approved templates outside the 24-hour customer-service window and opt-in for messaging.

Therefore:

- A reminder about an existing appointment and a suggestion to make a new booking are different message purposes.
- Proactive booking suggestions may require a marketing template rather than a utility template, depending on their content.
- Consent, opt-out, quiet hours, and frequency limits must be enforced before sending.
- Configuration must not bypass channel-policy checks.

These are product requirements, not just API integration details.

Sources: [WhatsApp messaging windows](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages) and [template categorization](https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/template-categorization).

## 4. Should this project expose endpoints?

Yes. It already does.

Existing endpoints include:

- `POST /sessions/new`
- `POST /chat`
- `GET /businesses/{business_id}/catalog`
- `POST /availability/search`
- `POST /bookings`
- `GET /bookings/me`
- Booking confirmation, cancellation, and rescheduling endpoints
- Owner agenda and demo reminders

Keep the conversation API and the direct scheduling API separate. A UI, partner integration, or owner tool should be able to book without pretending to chat.

Suggested additions, not implemented:

| Endpoint | Purpose |
|---|---|
| `GET /webhooks/whatsapp` | Verify the webhook subscription. |
| `POST /webhooks/whatsapp` | Receive messages and delivery-status events. |
| `GET /businesses/{id}/bot-settings` | Read authenticated business configuration. |
| `PATCH /businesses/{id}/bot-settings` | Update allowed bot behavior. |
| `POST /outreach-jobs` | Authorize and enqueue proactive outreach. Restrict this to owners or internal services. |
| `GET /outreach-jobs/{id}` | Inspect outreach and delivery progress. |

Do not expose individual graph nodes as endpoints. The API should describe product operations, not internal implementation details.

## 5. Supporting the different use cases

Separate four capabilities rather than making everything one bot mode:

| Capability | Example | Required state |
|---|---|---|
| Reactive booking | "I want a massage tomorrow." | Current scheduling draft and offered slots. |
| Remember existing appointments | "When is my appointment?" | Verified customer identity and a database lookup. |
| Appointment reminders | "Your appointment is tomorrow at 10." | Booking-linked scheduled event and delivery record. |
| Proactive suggestions | "Would you like to book again? Here are some options." | Consent, trigger policy, preferences, offer context, and expiry. |

These capabilities can coexist.

### Remembering must not depend on the LLM

The current Redis history is limited to 24 messages, with a default session TTL of 60 minutes. The graph checkpointer is also in memory. See [`app/runtime.py`](../app/runtime.py).

That is useful short-term context, but it is not durable appointment memory.

Distinguish:

- **Conversation state:** the current workflow step and scheduling draft.
- **Customer profile:** verified identity, consent, and explicitly provided preferences.
- **Appointment truth:** PostgreSQL bookings, queried when needed.

If a customer asks about an appointment a week later, the bot should retrieve it from the database, not reconstruct it from old messages. If the customer has several appointments, it should ask which one.

### Example configuration

Illustrative business-level configuration:

```yaml
conversation:
  locale: pt-BR
  remember_bookings: true

reminders:
  enabled: true
  minutes_before: [1440, 60]

booking_suggestions:
  enabled: false
  trigger: manual
  max_suggestions: 3
  offer_ttl_minutes: 30
  cooldown_days: 14
```

Keep provider credentials in secret configuration. Business behavior belongs in validated, versioned database settings.

For proactive suggestions, save the offer context before delivery: customer, business, suggested slots, message reference, and expiry. That lets "the second option" resolve correctly. Availability must still be rechecked when the customer books. An offer is not a reservation.

A scheduler should initiate this work through the same application layer. The graph should not remain running or sleep until the next outreach time.

## 6. Recommended implementation order

1. Fix correctness and public-exposure risks: authentication, explicit booking operation, business and customer scoping, and input validation.
2. Extract conversation handling from `main.py` behind a channel-neutral application interface.
3. Introduce typed conversation state and durable identity, with a deliberate persistence and retention policy.
4. Add reactive WhatsApp integration, including durable inbox and outbox processing.
5. Replace simulated reminders with real delivery.
6. Add configurable proactive suggestions, consent enforcement, and offer correlation.
7. Expand evaluations beyond extraction: multi-turn clarification, returning customers, multiple bookings, stale offers, duplicate webhooks, concurrent replies, opt-out, and provider outages.

## Validation performed

- Unit and evaluation tests: 26 passed.
- Normal API test startup: 9 setup errors, because the existing local virtual environment lacks the newly declared `redis` dependency.
- With production-runtime creation mocked out, isolating the existing fake-runtime API tests: 9 passed.
- The token-driven rescheduling behavior and the deterministic clarification gap were reproduced.
- PostgreSQL integration, live OpenAI, and WhatsApp delivery were not tested.

No source or configuration files were changed during the review. The recommendation is to make the bot channel-independent, stateful, and event-driven, while keeping booking truth, authorization, and policy deterministic. That supports WhatsApp, returning customers, reminders, and configurable first-message suggestions without turning `main.py` into an increasingly fragile conversation controller.

Other documentation consulted: [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence).

## Plan to solve item 2

Item 2 is the conversation-control move: get turn routing out of `app/main.py` without moving booking policy into LangGraph or into the model.

Do this in four phases. Do not start WhatsApp, reminder delivery, or proactive suggestions inside this refactor.

### Phase 0 — Lock current behavior

The API tests currently fail during FastAPI lifespan because it constructs the real Redis client before the fake runtime override is used. Fix that test seam first.

Keep [`tests/api/test_chat_contract.py`](../tests/api/test_chat_contract.py) as characterization coverage. Add one explicit test for the current token-plus-slot reschedule behavior and mark it as the behavior phase 2 will change. Do not fix that bug while moving code.

Exit criteria: `tests/unit`, `tests/api`, and `tests/evals` pass without a running Redis server.

### Phase 1 — Extract the conversation service without changing decisions

Create a channel-neutral service and move the existing handlers behind it:

```text
app/conversation/service.py     ConversationService.handle_turn
app/conversation/routing.py     current next-step decision
app/conversation/replies.py     current response copy
app/conversation/models.py      turn context and outcome
```

Move catalog resolution, slot choice, customer resolution, search, selection, confirm, cancel, reschedule, help, and clarification out of [`app/main.py`](../app/main.py) and [`app/chat_helpers.py`](../app/chat_helpers.py).

`POST /chat` should only require the session, redact token-shaped text, call the service, append redacted history, and return `ChatResponse`. The public chat contract stays the same. Direct scheduling endpoints continue to call `AvailabilityService` and `BookingService` directly.

Leave the graph as `START → interpret → END` in this phase. It is the interpreter port, not the workflow.

Exit criteria: no chat handler remains in `app/main.py`, service tests call `handle_turn` without HTTP, and booking SQL, token format, and idempotency are unchanged.

### Phase 2 — Add an explicit scheduling draft

Extend Redis session state beyond offered slots and one pending slot:

```text
operation: search | create | reschedule | confirm | cancel | none
service_id
professional_id
local_date
earliest_local_time
offered_slot_ids
pending_slot_id
missing: service | date | professional | customer | slot | none
active_booking_id
```

Rules:

- A management token authorizes an action. It never sets the operation.
- A new search uses `operation=create` unless the customer explicitly asked to move an existing booking.
- Reschedule requires both a validated token and an explicit reschedule intent or an already active reschedule draft.
- If the draft is waiting for a date and the reply contains a date, continue the search even when the interpreter returns `unknown`.
- Ask for one missing field at a time.
- Parse dates and times defensively. Invalid values ask again and write nothing.
- Selecting a slot still only works for a slot the service just offered. Saving a pending slot does not reserve it.

This is the phase that changes the reproduced token-driven reschedule bug. Add tests for a new booking with a token present, an explicit move request, a "tomorrow" clarification under the deterministic interpreter, and an invalid extracted date.

Do not put the raw management token, phone number, or token digest into the draft, model prompt, logs, or traces.

### Phase 3 — Expand the graph only if the service boundary is already tested

A plain state machine in the conversation service is a valid end state for this MVP. Expand LangGraph only when interrupts, retries, or multiple workers must resume the same turn.

If expanded, the nodes are:

```text
load_context → interpret → merge_draft → route
route → clarify | search | select | prepare_action | respond
prepare_action → execute_domain_action → respond
```

`execute_domain_action` calls `BookingService`. It does not contain SQL or token generation, and it must tolerate being run again. Keep one source of conversation truth. Prefer the application draft in Redis, with PostgreSQL as booking truth. Do not also treat `InMemorySaver` as authoritative.

The graph must not call back into `POST /chat`.

### Phase 4 — Leave a channel-neutral turn

The service should accept a business id, conversation id, text, optional selected slot, optional customer, optional management authorization, and an idempotency key. It should return text, intent, offered slots, an optional booking summary, and an optional management token for the trusted caller.

`POST /chat` maps `ChatRequest` to that turn. A later WhatsApp worker maps a verified provider event to the same turn. It must not supply `business_id` from the customer payload.

### Acceptance for item 2

- Turn routing lives in `app/conversation/`, not in the FastAPI module.
- `POST /chat` has no booking or availability policy of its own.
- The deterministic interpreter can continue a draft after a short clarification reply.
- A management token cannot turn a new-booking selection into a reschedule.
- Invalid extracted dates do not raise an unhandled exception.
- Raw management tokens still cannot reach model input, graph state, logs, traces, or error bodies.
- Existing direct scheduling endpoints still call the domain services directly.
- No WhatsApp or proactive-sending behavior was added under this refactor.

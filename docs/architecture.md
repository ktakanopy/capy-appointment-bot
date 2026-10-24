# Architecture

This note describes the code that runs today. The target product shape is in `docs/Service Scheduling Agent — Architecture.md`. Where the two differ, this file is the one to trust for reading the repository.

## What runs

```mermaid
flowchart LR
  Browser[Streamlit] --> API[FastAPI adapters]
  WhatsApp[WhatsApp webhook] --> API
  API --> Graph[LangGraph turn]
  Graph --> Decision[Deterministic decision]
  Graph --> Interpreter[Text interpreter]
  Graph --> Availability[Availability service]
  Graph --> Booking[Booking service]
  Availability --> DB[(PostgreSQL)]
  Booking --> DB
  Graph --> Redis[(Redis draft and history)]
  Worker[Reminder worker] --> DB
  Worker --> Inbox[Demo inbox sender]
  Interpreter -. optional .-> LLM[OpenAI]
  API -. optional .-> Trace[Langfuse]
```

PostgreSQL is appointment truth. Redis is short-lived conversation state. The model never decides that a slot is free and never writes a booking.

## Where a chat message goes

1. `POST /chat` in `app/main.py` validates the HTTP request and calls `handle_chat` in `app/chat_adapter.py`.
2. The adapter checks the session, redacts a token pasted into the message, and, if a management token was sent separately, resolves it to a `booking_id`. The raw token does not enter graph state.
3. `LangGraphWorkflow.handle_turn` runs the graph in `app/graph/builder.py`.
4. The graph loads the draft, interprets the text, merges fields, asks the decision port for a next action, and then constrains that action.
5. A domain port is called only from the `execute` node. Availability and booking rules stay in `app/scheduling.py` and `app/booking.py`.
6. The draft and offered slots are saved. The adapter maps the result back to `ChatResponse`.

The direct endpoints `POST /availability/search` and `POST /bookings` do not enter the graph. The Streamlit forms use them. Chat and a future channel must not be the only way to book.

## Compiled conversation graph

Generated from `app/graph/builder.py` by `python scripts/generate_graph_diagram.py`. Solid arrows always run. Dotted arrows are choices made by the draft.

<!-- compiled-graph:start -->
```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	load_context(load_context)
	interpret(interpret)
	merge_draft(merge_draft)
	decide(decide)
	clarify(clarify)
	search(search)
	select(select)
	prepare_action(prepare_action)
	execute(execute)
	help(help)
	lookup(lookup)
	escalate(escalate)
	persist(persist)
	__end__([<p>__end__</p>]):::last
	__start__ --> load_context;
	clarify --> persist;
	decide -.-> clarify;
	decide -.-> escalate;
	decide -.-> help;
	decide -.-> lookup;
	decide -.-> prepare_action;
	decide -.-> search;
	decide -.-> select;
	escalate --> persist;
	execute --> persist;
	help --> persist;
	interpret --> merge_draft;
	load_context --> interpret;
	lookup --> persist;
	merge_draft --> decide;
	prepare_action -.-> clarify;
	prepare_action -.-> execute;
	prepare_action -.-> persist;
	search --> persist;
	select -.-> clarify;
	select -.-> persist;
	select -.-> prepare_action;
	persist --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```
<!-- compiled-graph:end -->

## Who owns what

| Code | Owns | Does not own |
|---|---|---|
| `app/main.py` | HTTP routes, owner-token check, webhook entry | Which conversation step runs |
| `app/chat_adapter.py` | Web chat translation, token resolution, ports for this request | Booking SQL |
| `app/llm/` | Extract intent, service, date, time, and customer fields from text | Choosing a graph node or claiming a slot is free |
| `app/graph/decision.py` | Propose `BOOK`, `RESCHEDULE`, `CANCEL`, `CHECK_AVAILABILITY`, `FAQ`, `CONFIRM`, `ESCALATE`, or `LOOKUP_BOOKING` | Forcing a transition the workflow does not allow |
| `app/graph/steps.py` | Draft merge, clarification, search, selection, FAQ, lookup, escalation, and the execute call | Overlap policy and token generation |
| `app/scheduling.py` | Which times are valid | Conversation copy |
| `app/booking.py` | Create, confirm, cancel, reschedule, idempotency, audit, token digest | Reading the customer's sentence |
| `app/channels/whatsapp.py` | Signature check, sender mapping, dedupe, same workflow call | A second copy of the booking flow |
| `app/reminders.py` | Claim due reminders and send through a sender port | LangGraph |

## Conversation rules that matter

The draft remembers the operation: `create`, `reschedule`, `confirm`, `cancel`, or `none`. It also remembers the service, date, offered slot ids, and the missing field.

A management token authorizes the web caller. A verified WhatsApp sender authorizes that customer. Neither one changes the operation. Saying "book another" while a token is present creates a booking. Saying "move it" is what starts a reschedule.

If the bot asked for a date and the reply contains a date, the draft continues even when the interpreter returns `unknown`. An invalid date asks again and writes nothing.

"When is my appointment?" uses the verified customer and PostgreSQL. A phone number typed in the message is not proof of identity. On the web demo, the page holds the management token and sends it outside the chat text.

## State

| State | Where | Lifetime |
|---|---|---|
| Services, hours, bookings, reminders, audit | PostgreSQL | Durable |
| Scheduling draft, offered slots, and redacted messages | LangGraph checkpoint in Redis, keyed by the session id | `SESSION_TTL_MINUTES` |
| Request-scoped customer details and raw token | `ChatPorts` object | One turn |

Restarting the API does not delete a booking. It can drop an in-progress Redis draft after the TTL. The web demo can still manage the booking with the saved token.

## Messaging channels

The customer channels are WhatsApp and Telegram. Streamlit is only a local demo surface. A customer authorizes themself by messaging from a verified account. They do not paste a token into the chat.

Telegram is not connected yet. The WhatsApp webhook is the channel that exists in code. Both channels should call the same graph.

`BookingService` creates the booking token. The graph and the channel do not. It happens only after the customer picks an offered slot and the create write is allowed. The service signs `booking-manage:v1:{booking_id}:1` with `BOOKING_TOKEN_SECRET`, saves the SHA-256 digest on the new booking row, and keeps the raw token out of the reply.

```mermaid
flowchart TD
  User[Customer on WhatsApp or Telegram] -->|text| Adapter[Channel adapter]
  Adapter --> Verify{Provider proof valid?}
  Verify -->|no| Reject[Reject and do not reply]
  Verify -->|yes| Dedupe{Provider message id already seen?}
  Dedupe -->|yes| Ack[Acknowledge and stop]
  Dedupe -->|no| Identity[Resolve sender to customer_id]
  Identity --> Graph[LangGraph turn]
  Graph --> Checkpoint[(Redis checkpoint)]
  Graph --> Choice{Draft operation}
  Choice -->|create and date missing| AskDate[Ask for a date and write nothing]
  Choice -->|create and date present| Search[AvailabilityService returns slots]
  Search --> Offer[Reply with those times only]
  Offer --> Pick[Customer picks one offered slot]
  Pick --> Recheck[BookingService rechecks the slot in the write transaction]
  Recheck -->|taken| Conflict[Reply that the time is gone]
  Recheck -->|free| Insert[Insert the booking row]
  Insert --> Mint["BookingService._token creates bk1.booking_id.1.signature"]
  Mint --> Digest[(Save SHA-256 digest on the booking)]
  Mint --> Hide[Drop the raw token before the channel reply]
  Choice -->|lookup| List[List this customer's bookings]
  Choice -->|cancel or reschedule| Count{How many upcoming bookings?}
  Count -->|none| OfferNew[Offer to create one]
  Count -->|one| Mutate[BookingService rebuilds the same token from id and version]
  Count -->|several| Ask[Ask which one and write nothing]
  Mutate --> Apply[Confirm, cancel, or reschedule that booking]
  Hide --> Reply[Channel reply with no token]
  Conflict --> Reply
  AskDate --> Reply
  List --> Reply
  OfferNew --> Reply
  Ask --> Reply
  Apply --> Reply
  Reply --> User
```

## Tokens the application creates

A booking still gets a management token. The chat does not show it. The token is an internal capability for that booking, not the customer's login.

```mermaid
flowchart LR
  Secret[BOOKING_TOKEN_SECRET] --> Mint[BookingService._token]
  Create[Booking created] --> Mint
  Mint --> Raw[Raw token bk1.booking_id.version.signature]
  Raw --> Digest[SHA-256 digest saved on the booking]
  Raw --> Internal[Used only inside BookingService for confirm, cancel, and reschedule]
  Digest --> DB[(PostgreSQL)]
  Raw -. never .-> Chat[WhatsApp or Telegram reply]
  Raw -. never .-> Graph[Graph state, logs, or model prompt]
```

| Token | Created when | Who sees it | What it authorizes |
|---|---|---|---|
| Booking management token | Every successful `BookingService.create`, and again on an idempotent replay of that create | No customer. The service rebuilds it from the booking id, version, and `BOOKING_TOKEN_SECRET` when a verified sender changes that booking. | One booking. Stored as a digest. |
| Idempotency key | One per create, confirm, cancel, or reschedule call | The channel adapter, not the customer | Replaying the same write does not create a second booking |
| Conversation id | First message from a sender on a channel | Nobody. It is a hash of business, channel, and sender. | Redis checkpoint for that thread |
| Provider message id | Assigned by WhatsApp or Telegram | The adapter uses it once | Deduplicate the webhook |
| Webhook verify token | Configured in `WHATSAPP_VERIFY_TOKEN`, not created per user | Meta during webhook setup | Proves this server owns the callback URL |
| App secret | Configured in `WHATSAPP_APP_SECRET` | Nobody. It checks `X-Hub-Signature-256`. | Proves the webhook body came from Meta |
| Owner token | Configured in `OWNER_API_TOKEN` | The business owner, on owner routes only | Read the agenda and change reminder settings |

`BOOKING_TOKEN_SECRET` is not a customer token. It is the signing key. Rotating it invalidates every management token already issued. The booking rows remain. A verified WhatsApp or Telegram sender can still manage their own booking because the service checks `customer_id` before it rebuilds the internal token.

## What is not built

WhatsApp signature verification and dedupe exist, but there is no live Meta send. Gemma and Laya are the default understanding and routing adapters. They call local servers and fall back to deterministic parsing when those servers are down. The model weights are not bundled here. Reminders still land in the demo inbox unless a different sender is injected. Owner routes stay open unless `OWNER_API_TOKEN` is set. There is no calendar, payment, location, or deposit adapter. Telegram delivery is not connected.

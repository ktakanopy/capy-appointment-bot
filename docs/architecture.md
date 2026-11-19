# Architecture

![CAPY runtime architecture. The webhook commits Telegram updates and the relay publishes wake-ups through RabbitMQ. The turn worker handles text and audio and runs the LangGraph workflow. Gemma transcribes and interprets messages, Laya proposes an action, and Python services enforce booking rules. Redis stores conversation state. PostgreSQL stores bookings, Telegram receipts and demo reminders.](assets/architecture.svg)

Telegram is the customer interface. Separate services receive updates, process conversations and deliver replies. PostgreSQL keeps business records and pending work. RabbitMQ signals that work is ready. Redis holds the conversation state used by the graph.

The graph's `understand_input` node waits for a complete transcript so a correction at the end of a recording can affect the next action. The audio chunker splits decoded speech near pauses and enforces size and time limits. The node redacts recognizable sensitive data from text and transcripts, then asks Gemma for a plain-language interpretation and booking facts. Laya receives the interpreted message and the draft context. It proposes an action, which Python checks against required facts, valid references and booking authorization. The app loads Gemma and Laya through the same constructors used by evaluations. [Runtime](../app/runtime.py#L33) · [Graph flow](graph.md) · [Model contracts](model-contracts.md)

The workflow passes raw input through invocation context in memory. Redis stores draft details, offered slots and bounded sanitized history. Private booking results stay in request-scoped scheduling actions. Each turn loads its business catalog once and shares it across transcription, local date calculation and the graph. [Workflow](../app/graph/workflow.py#L13) · [Data handling](security.md)

`SchedulingActions` defines the operations the graph can request. `CustomerSchedulingActions` implements that contract for the trusted business and customer identity. Evaluation fixtures implement the same contract with simulated booking writes. The booking service checks ownership and current policy inside transactions. The availability service handles local time, service assignment, notice, horizon, buffers and conflicts. The business-row lock serializes writes per business for a simple idempotency boundary. PostgreSQL's exclusion constraint independently prevents overlapping bookings. [Actions](../app/graph/actions.py) · [Booking service](../app/booking.py) · [Availability service](../app/scheduling.py)

The local reminder worker saves due reminders without a Telegram destination to a demo inbox in PostgreSQL. The owner CLI shows upcoming bookings and recent inbox messages. Reminders with a Telegram destination go through the shared outbox. [Database and reminders](database.md) · [Owner CLI](../app/cli.py#L12)

Evaluations run the workflow with simulated scheduling services or isolate extraction and routing. PostgreSQL and Redis integration tests cover persistence. [Evaluation guide](evaluation.md) · [Source map](source-map.md) · [Project history](history.md)

## How the Telegram worker works

Follow one message through the webhook, RabbitMQ, the conversation worker and the sender, with Python excerpts and the numbered sequence diagram. [Telegram worker guide](telegram-worker.md)

## How to read the code

Start with the customer replying “1” to an offer. The offline `en-booking` case supplies the model outputs and checks that this choice creates exactly one booking. [Offline case](../evals/data/smoke-text.jsonl#L1) · [Run the example](../README.md#run-the-offline-example)

1. **Match the number to an offered time.** The graph uses the saved conversation state to resolve “1” to the first time it offered. [Understand input](../app/graph/steps.py#L97) · [Choose an action](../app/graph/steps.py#L252) · [Select the time](../app/graph/steps.py#L569)
2. **Write the booking.** The booking service checks the customer and whether the slot still fits the schedule. One SQLAlchemy session commits the booking, pending reminder and retry result together. [Execute the action](../app/graph/steps.py#L735) · [Create the booking](../app/booking.py#L152)

| Question | Executable example |
| --- | --- |
| What can Laya see? | The unit test captures the outgoing model request and verifies that it contains only the interpreted message and context. It uses a stubbed HTTP response. [Laya contract test](../tests/unit/test_gemma_laya.py#L59) |
| What can the customer do after booking? | The journey uses real PostgreSQL services to confirm, deliver the demo reminder, reschedule, and cancel. [Booking journey test](../tests/integration/test_workflow_booking_journey.py#L17) |
| Can two retries create two appointments? | The test runs concurrent booking writes against PostgreSQL and checks replay and overlap protection. [Concurrent booking test](../tests/integration/test_postgres_booking_lifecycle.py#L256) |

## Booking sequence diagrams

### Find a time and create a booking

![Two Telegram turns labeled C01 through C21, from asking for available times to receiving the booking reply.](assets/booking-create-sequence.svg)

Follow “Book a relaxation massage tomorrow,” then “1.” Read the arrows from top to bottom and use each label to find its explanation below. The worker guide details how the Telegram steps reach each service. [Telegram sequence](telegram-worker.md#full-call-sequence)

#### Find a time

| Call | What happens and why | Source |
| --- | --- | --- |
| **C01** | The customer asks for times in a private Telegram chat. Telegram sends the message to the bot’s configured webhook. | [Webhook](../app/main.py#L77) |
| **C02** | CAPY records the incoming update. The conversation worker uses the Telegram user ID to find the customer's account. | [Worker guide](telegram-worker.md#start-reading-the-code) |
| **C03** | The graph uses the customer's text or complete transcript. It redacts the text, asks Gemma to interpret it and gives Laya the interpreted message and draft. Python checks the details required to proceed. | [`understand_input()` · L97](../app/graph/steps.py#L97) · [`route_turn()` · L252](../app/graph/steps.py#L252) |
| **C04** | The workflow requests slots for the selected service and date, applying any staff or earliest-time preference. This narrows the search to choices that address the customer’s request. | [`search()` · L534](../app/graph/steps.py#L534) · [`CustomerSchedulingActions.search()` · L49](../app/customer_scheduling_actions.py#L49) |
| **C05** | The availability service reads working hours, blocked periods and active bookings for eligible staff. These records supply the current schedule for conflict checks. | [`scheduling_data()` · L123](../app/repositories.py#L123) |
| **C06** | The service returns slots that fit working hours, duration, setup and cleanup buffers, and the business’s notice and booking horizon rules. The slots represent current availability, which may change before the booking is written. | [`AvailabilityService.search()` · L190](../app/scheduling.py#L190) |
| **C07** | The workflow formats the slots as numbered choices and saves the offered slots in the Redis checkpoint. This lets the following turn resolve a reply such as “1” to the exact time it offered. | [`_slot_message()` · L34](../app/chat_helpers.py#L34) · [Offered slots · L551–565](../app/graph/steps.py#L551-L565) · [Redis checkpoint setup · L72](../app/runtime.py#L72) |
| **C08** | The worker saves the offer reply for delivery. | [Finish the turn](../app/channels/telegram_turns.py#L384) |
| **C09** | The sender delivers the offered times through the originating bot. | [Sender](../app/channels/telegram_delivery.py#L350) |

#### Create the booking

| Call | What happens and why | Source |
| --- | --- | --- |
| **C10** | The customer replies with an offered choice such as “1.” This separate turn lets the customer choose the time before CAPY attempts the booking. | [Webhook](../app/main.py#L77) |
| **C11** | CAPY records this choice as a new update for the same chat. The worker loads the conversation state from the previous turn. | [Worker guide](telegram-worker.md#start-reading-the-code) |
| **C12** | The workflow retrieves the offered slots and matches the selection against those choices. An unknown or expired offer requires a fresh selection. | [Workflow](../app/graph/workflow.py) · [Selection](../app/graph/steps.py#L569) |
| **C13** | The workflow hands the selected slot, trusted customer account and stable update key to the booking service. This gives the service concrete inputs it can verify before writing. | [Create action · L735–762](../app/graph/steps.py#L735-L762) · [`create_booking()` · L54](../app/customer_scheduling_actions.py#L54) |
| **C14** | The booking service locks the business and checks the key derived from the bot and update IDs against the request hash. If a booking committed before the caller received its reply, a matching retry can return the owned booking without creating it again. This is idempotency. The lock keeps concurrent attempts from both treating the key as new, and a changed request with that key is rejected. | [Update key · L157](../app/telegram_worker.py#L157) · [`_hash_booking_request()` · L36](../app/booking.py#L36) · [`_lock_business_and_check_retry()` · L90](../app/booking.py#L90) |
| **C15** | For a new booking, the service locks the professional and rechecks the slot because the offer may have gone stale. It verifies that the customer belongs to the business before linking that account as the owner. | [`validate_slot_in_transaction()` · L43](../app/scheduling.py#L43) · [`_require_customer_in_business()` · L246](../app/booking.py#L246) |
| **C16** | Write the booking, pending reminder and retry result. The reminder’s due time lets a worker deliver it later. PostgreSQL’s overlap constraint protects the professional’s occupied time, including buffers, even if another write conflicts. | [`_insert_scheduled_booking()` · L256](../app/booking.py#L256) · [`_queue_booking_reminder()` · L298](../app/booking.py#L298) · [`_save_retry_result()` · L129](../app/booking.py#L129) · [Overlap constraint · L101–105](../alembic/versions/0001_general_scheduling.py#L101-L105) |
| **C17** | The SQLAlchemy session commits the three writes together. A failure rolls them all back, so CAPY cannot end up with a reminder or saved successful outcome for a booking that failed. | [`create()` · L152](../app/booking.py#L152) · [`database_session()` · L45](../app/database.py#L45) |
| **C18** | The service returns the owned booking and outcome after the transaction exits. This gives the workflow the result of the verified write. | [Read and return the booking · L210](../app/booking.py#L210) |
| **C19** | The workflow returns a booking reply with the selected local time and professional, so the customer can check what was booked. | [Build the booking reply · L754–762](../app/graph/steps.py#L754-L762) |
| **C20** | The worker saves the booking reply for delivery. | [Finish the turn](../app/channels/telegram_turns.py#L384) |
| **C21** | The sender delivers the booking reply to the customer. | [Outbound sender](../app/channels/telegram_delivery.py#L350) |

### Confirm, cancel or reschedule

![Booking changes labeled M01 through M09, including ownership, retry checks, the operation-specific writes and commit.](assets/booking-change-sequence.svg)

This diagram starts when the workflow has identified the appointment and, for a move, the replacement slot. The same Telegram intake and reply steps apply.

| Call | What happens and why | Source |
| --- | --- | --- |
| **M01** | The workflow asks for clarification until it can identify the appointment and any required replacement slot. It then passes the requested operation, trusted customer account and stable update key to the service. The service can verify the target against database ownership. | [`prepare_action()` · L606](../app/graph/steps.py#L606) · [`_prepare_reschedule()` · L627](../app/graph/steps.py#L627) · [`_prepare_booking_status_change()` · L704](../app/graph/steps.py#L704) · [`execute()` · L735](../app/graph/steps.py#L735) |
| **M02** | The service locks the business before comparing the retry key and request hash. This gives competing attempts a single order and lets the service reject a key reused for different details. | [`_lock_business_and_check_retry()` · L90](../app/booking.py#L90) |
| **M03** | The service loads and locks a booking matching all three IDs. The lock holds the state steady through the write. A matching retry returns the current owned booking with its saved outcome. A new change must meet the appointment start-time cutoff. | [`_load_customer_booking()` · L62](../app/booking.py#L62) · [Replay and cutoff · L374](../app/booking.py#L374) · [`_require_upcoming_booking()` · L412](../app/booking.py#L412) |
| **M04** | Confirmation records the customer’s acceptance and keeps the scheduled reminder. It preserves an already confirmed status and rejects confirmation of a canceled booking. | [`confirm()` · L329](../app/booking.py#L329) · [`_write_booking_status()` · L419](../app/booking.py#L419) |
| **M05** | Cancellation frees the occupied time and marks pending reminders canceled so the worker will skip them. Both changes belong to the same transaction so they take effect together. | [`cancel()` · L341](../app/booking.py#L341) · [Status and reminder updates · L441](../app/booking.py#L441) |
| **M06** | Rescheduling validates the new slot while excluding the booking’s old interval so it can move into an otherwise free time that overlaps part of its original time. It keeps the booking ID and retimes or queues the reminder to match the new appointment. A failed move preserves both original records. | [`reschedule()` · L456](../app/booking.py#L456) · [`_revalidate_booking_slot()` · L225](../app/booking.py#L225) · [`_update_booking_slot()` · L525](../app/booking.py#L525) · [`_move_booking_reminder()` · L542](../app/booking.py#L542) |
| **M07** | Save the outcome for a new request in the write transaction. This lets a later retry report the saved outcome without repeating the change. | [`_save_retry_result()` · L129](../app/booking.py#L129) |
| **M08** | Commit the booking change, any reminder change and retry record together. This keeps the appointment state and the reported outcome in sync after a successful write or a rollback. | [`database_session()` · L45](../app/database.py#L45) · [Reschedule rollback test · L126](../tests/integration/test_postgres_booking_lifecycle.py#L126) |
| **M09** | The service returns the owned booking and outcome. The workflow uses the outcome to tell the customer whether the change succeeded or, where applicable, had already taken effect. | [Status outcome · L384](../app/booking.py#L384) · [Workflow replies · L764–800](../app/graph/steps.py#L764-L800) |

### Deliver an appointment reminder

A booking writes its reminder schedule in the same transaction. A cancellation invalidates pending work and a reschedule advances the reminder generation. Hosted reminder scheduling queues a due generation once. The sender checks that the generation and Telegram identity still match before authorizing a send. [Booking service](../app/booking.py) · [Reminder scheduler](../app/reminders.py) · [Sender](../app/channels/telegram_delivery.py)

The local demo still writes reminders to the owner inbox. The sequence diagram below describes this local path. [Owner CLI](../app/cli.py)

![Local reminder delivery from booking to the simulated owner inbox.](assets/reminder-delivery-sequence.svg)

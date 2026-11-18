# Source map

Start with one customer replying “1” to an offered appointment. Follow the message into the
conversation worker, through the booking transaction, and back to Telegram. [Booking walkthrough](architecture.md#how-to-read-the-code)

## Telegram and conversation state

| What are you looking for? | Where to read |
| --- | --- |
| The webhook and health checks | [main.py](../app/main.py) defines the HTTP endpoints. [Production runbook](production.md#hosted-configuration) |
| Which process runs each job | [telegram_service.py](../app/telegram_service.py) runs polling, conversation processing, reminder scheduling, sending, bot registration, and queue statistics. |
| Bot ownership and incoming messages | [bots.py](../app/channels/bots.py) maps credentials and routes to a business. [telegram_intake.py](../app/channels/telegram_intake.py) validates private messages and records update IDs. [telegram_repository.py](../app/channels/telegram_repository.py) links senders to customers and retains the legacy polling and reply methods. |
| How a worker gets its turn | [telegram_turns.py](../app/channels/telegram_turns.py) claims a chat temporarily, verifies which worker owns it, and saves the completed turn. [redis_checkpoint.py](../app/graph/redis_checkpoint.py) stores conversation drafts and saved offers in Redis. |
| Text, voice, and contact handling | [telegram_worker.py](../app/telegram_worker.py) prepares messages and handles supported commands. [audio_chunks.py](../app/channels/audio_chunks.py) decodes recordings and cuts them into overlapping clips. [phone.py](../app/channels/phone.py) normalizes shared contact numbers. [privacy.py](../app/privacy.py) redacts recognizable contact details before model input. [Data handling](security.md) |
| Telegram calls and outbound delivery | [telegram.py](../app/channels/telegram.py) calls the Bot API. [telegram_delivery.py](../app/channels/telegram_delivery.py) claims queued messages and shares rate limits across senders. [Retries and recovery](idempotency.md) |

## Models and the conversation graph

| What are you looking for? | Where to read |
| --- | --- |
| What Gemma and Laya receive | [gemma.py](../app/llm/gemma.py) calls Gemma for text and transcription. [schemas.py](../app/llm/schemas.py) defines the interpretation fields. [laya.py](../app/graph/laya.py) sends the interpretation and draft context to Laya. [Model contracts](model-contracts.md) |
| How the app connects to the models | [runtime.py](../app/runtime.py) assembles providers, the workflow, and booking services for a worker. [local_models.py](../app/local_models.py) serves the two model endpoints on Apple Silicon. [provider.py](../app/llm/provider.py) and [decision.py](../app/graph/decision.py) also supply the scripted evaluation providers. |
| How a conversation turn runs | [workflow.py](../app/graph/workflow.py) invokes the graph. [builder.py](../app/graph/builder.py) connects the steps. [steps.py](../app/graph/steps.py) verifies the proposed action and handles each route. [chat_helpers.py](../app/chat_helpers.py) formats choices and matches catalog names. [Conversation graph](graph.md) |
| What crosses each boundary | [contracts.py](../app/graph/contracts.py) defines the turn input, draft, and reply. [state.py](../app/graph/state.py) defines the graph fields and limits message history. [actions.py](../app/graph/actions.py) declares the scheduling operations and [context.py](../app/graph/context.py) binds them to the current turn. |

## Scheduling and the database

| What are you looking for? | Where to read |
| --- | --- |
| Available appointments | [scheduling.py](../app/scheduling.py) checks staff hours, time zones, buffers, blocks, and conflicts. It checks the chosen slot again when a booking is written. |
| Appointment changes | [customer_scheduling_actions.py](../app/customer_scheduling_actions.py) calls the booking service for the trusted customer. [booking.py](../app/booking.py) enforces ownership and saves each appointment change and its side effects together. |
| Database records and changes | [repositories.py](../app/repositories.py) reads the catalog, schedules, and bookings. [models.py](../app/models.py) defines the scheduling data contracts. [database.py](../app/database.py) manages PostgreSQL connections. [Alembic](../alembic) holds schema migrations. [Table guide](database.md) |
| Reminders and the owner demo | [reminders.py](../app/reminders.py) delivers to the local demo inbox or queues due hosted reminders. [owner.py](../app/owner.py) reads the owner agenda and inbox for [cli.py](../app/cli.py). [seed.py](../app/seed.py) creates the demo catalog and hours. |

## Running and checking changes

| What are you looking for? | Where to read |
| --- | --- |
| Configuration and logs | [config.py](../app/config.py) loads settings. [.env.example](../.env.example) shows local values. [observability.py](../app/observability.py) records structured events and initializes optional Langfuse tracing. |
| Local and hosted services | The [Dockerfile](../Dockerfile) builds the app and checks images. [Local Compose](../docker-compose.yml) runs the demo, [hosted Compose](../docker-compose.production.yml) defines deployment roles, and the [observability overlay](../docker-compose.observability.yml) adds Langfuse. |
| Tests | [Unit tests](../tests/unit) cover rules and adapters. [API tests](../tests/api) verify health responses. [Integration tests](../tests/integration) use disposable PostgreSQL and Redis. [CI](../.github/workflows/verify.yml) runs them along with the [offline evaluation tests](../tests/evals). [Local checks](../README.md#verify) |
| How the models and workflow are evaluated | [runner.py](../app/evals/runner.py) loads, runs, and summarizes cases. [models.py](../app/evals/models.py) defines the evaluation format. [fixtures.py](../app/evals/fixtures.py) supplies simulated scheduling operations, [judge.py](../app/evals/judge.py) scores extraction in isolation, and [routing.py](../app/evals/routing.py) compares proposed actions with labels. [Evaluation guide](evaluation.md) |
| What the support scripts do | The [queue benchmark](../scripts/benchmark_telegram_queue.py) measures worker and database overhead with synthetic Telegram messages. The [graph generator](../scripts/generate_graph_diagram.py) prints Mermaid from the compiled workflow. |

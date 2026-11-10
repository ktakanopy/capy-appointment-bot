# CAPY-bot — Service Scheduling MVP

A portfolio MVP for appointment-based service businesses. Customers find a real available slot, book it, and manage the booking with a private capability token. Owners review the agenda from the CLI, and a reminder worker simulates delivery into a demo inbox.

The design keeps a hard boundary: models interpret language and propose an action; deterministic application code decides availability and commits bookings. The repository also includes a scenario benchmark, provider fallbacks, and optional Langfuse traces.

[Demo](#local-demo) · [Capabilities](#what-the-bot-can-do) · [Workflow](#bot-workflow) · [Architecture](#architecture) · [AI interpretation](#ai-interpretation) · [API](#api-overview) · [Roadmap](#roadmap)

## Local demo

Requirements: Docker Compose. No LLM API key is required: the default Gemma/Laya providers fall back to deterministic parsing when their local servers are absent.

```bash
docker compose up --build
```

Open the API and OpenAPI page at [http://localhost:8000](http://localhost:8000) and [http://localhost:8000/docs](http://localhost:8000/docs). Then run the CLI in the API container:

```bash
docker compose run --rm api python -m app.cli
```

Compose starts the API, PostgreSQL, Redis, and the reminder worker, applies the Alembic migration, and seeds a synthetic business. The app database is separate from the optional Langfuse database.

To test through Telegram instead, set `TELEGRAM_BOT_TOKEN` and start the worker profile:

```bash
docker compose --profile telegram up --build
```

For a guaranteed offline run without provider timeouts:

```bash
UNDERSTANDING_PROVIDER=deterministic DECISION_PROVIDER=deterministic docker compose up --build
```

### Demo journey

1. Choose a service, date, and optional professional, then find availability.
2. Select a slot and enter a name and phone number.
3. Save the private management token the CLI prints with the booking.
4. Confirm, cancel, or move the booking. `/owner` prints the agenda and reminder status.
5. The demo business schedules a reminder five minutes before the booking. The worker writes it to the inbox; replying starts a new chat turn with the token attached.

The seeded business is a wellness studio with two professionals and one massage service. It is fixture data only; the model supports multiple businesses, services, and assignments. Pass `--include-second-business` to `uv run python -m app.seed` to add another synthetic business.

## What the bot can do

| The bot can | Example |
|---|---|
| Find real availability | "Do you have a massage tomorrow afternoon?" |
| Book a slot | "Book the 9:00 with Ana" |
| Confirm a booking | "Confirm my booking" |
| Cancel a booking | "Cancel it" |
| Reschedule a booking | "Move it to Friday at 10" |
| Answer catalog questions | "How much is a massage?" |
| Look up an appointment | "When is my appointment?" |
| Ask for one missing detail | "Which service would you like?" |
| Escalate unsupported requests | It does not invent data or policy |

A management token authorizes one booking; it does not change the operation. Availability is always rechecked before a write. The bot does not answer general business questions, use a RAG knowledge base, or deliver outbound email. WhatsApp and SMS are archived, not active.

## Bot workflow

```mermaid
flowchart TD
    MSG["Customer message<br/>Telegram text/voice or CLI"] --> INT["Interpret<br/>Gemma · OpenAI · deterministic"]
    INT --> MERGE["Merge scheduling draft<br/>service · date · time · professional"]
    MERGE --> DECIDE["Decide next action<br/>Laya · deterministic"]
    DECIDE --> ROUTE{"Route"}

    ROUTE -->|Missing field| CLARIFY["Ask one clarification"]
    ROUTE -->|Availability| SEARCH["Search real slots"]
    ROUTE -->|Manage booking| AUTH{"Valid token or<br/>verified identity?"}
    ROUTE -->|FAQ or lookup| READ["Answer from catalog<br/>or verified appointments"]
    ROUTE -->|Unsupported| ESCALATE["Bounded escalation"]

    AUTH -->|No| ESCALATE
    AUTH -->|Yes| MUTATE["Confirm · cancel · reschedule"]
    SEARCH --> SELECT["Customer selects a slot"]
    SELECT --> BOOK["Create booking in one transaction<br/>slot validation + reminder + idempotency"]
    MUTATE --> CHANGE["Apply change in one transaction"]
    BOOK --> TOKEN["Return booking and management token"]
    TOKEN --> SAVE["Persist draft and response in Redis"]
    CHANGE --> SAVE
    CLARIFY --> SAVE
    READ --> SAVE
    ESCALATE --> SAVE

    classDef model fill:#eef2ff,stroke:#c7d2fe;
    classDef policy fill:#fff7ed,stroke:#fed7aa;
    classDef store fill:#f0fdf4,stroke:#bbf7d0;
    class INT,MERGE,DECIDE model;
    class AUTH,SEARCH,BOOK,MUTATE,CHANGE policy;
    class SAVE store;
```

Models only interpret language and propose an action. `app/scheduling.py` and `app/booking.py` decide availability, authorize changes, and commit writes. Catalog answers are bounded; appointment lookup requires a verified Telegram contact or a valid token.

## Architecture

![Architecture overview](docs/architecture-overview.svg)

PostgreSQL is the source of truth; Redis holds short-lived drafts and checkpoints. The LangGraph turn may propose an action, but availability and booking rules live in `app/scheduling.py` and `app/booking.py`. Direct REST clients and the CLI use the same domain services without the graph.

> The WhatsApp, SMS, and Streamlit implementations are retained under [`.archived/`](.archived/) for reference and are not part of the running app.

Read more: [architecture](docs/architecture.md) · [conversation graph](docs/graph.md) · [idempotency](docs/idempotency.md) · [security](docs/security.md).

## AI interpretation

- **Understanding:** `UNDERSTANDING_PROVIDER=gemma` calls a local Gemma server (`GEMMA_BASE_URL`) and falls back to the deterministic interpreter on failure. `UNDERSTANDING_PROVIDER=deterministic` forces offline mode; `LLM_PROVIDER=openai` plus `OPENAI_API_KEY` switches text understanding to OpenAI with a deterministic fallback.
- **Routing:** `DECISION_PROVIDER=laya` calls a local Laya server (`LAYA_BASE_URL`) to propose the next action and falls back to deterministic routing. Set `DECISION_PROVIDER=deterministic` to skip it.
- **Audio:** voice notes are decoded, split at pauses, and transcribed by Gemma `/understand`. There is no audio fallback, so a failed chunk returns a retry/type message instead of a partial booking.
- **Boundary:** neither provider can authorize a booking or claim a slot is free. Redis stores the draft, offered slots, and redacted history for `SESSION_TTL_MINUTES`.
- **Tracing:** `docker compose --profile observability up --build` with `TRACING_ENABLED=true` adds Langfuse. Logs and trace metadata omit names, contact details, and tokens.

Gemma and Laya call configured local servers; model weights and servers are not bundled here. A successful fallback is not a successful model prediction, and provider failures are logged separately.

## Known limitations

- Telegram and Gemma audio are implemented but not validated end-to-end against a live bot, database migration, and real recordings.
- Gemma and Laya servers and weights are not bundled. A successful fallback is not a successful model prediction.
- Email, calendar sync, and payments are not connected. WhatsApp and SMS are archived, not active.

## API overview

- `GET /health`
- `GET /businesses/{business_id}/catalog`
- `POST /availability/search`
- `POST /bookings` with `Idempotency-Key`
- `GET /bookings/me` with `X-Booking-Token`
- `POST /bookings/me/confirm`, `/cancel`, and `/reschedule` with the token and idempotency key
- `POST /sessions/new` and `POST /chat`
- `GET /owner/bookings`, `GET /demo/reminders`, `PATCH /owner/settings` (also available through `/owner` in the CLI)

Slot results are references, not reservations: `/bookings` recalculates the slot inside the write transaction, and PostgreSQL rejects overlapping active bookings for the same professional. Rescheduling updates the booking and reminder in one transaction.

Management tokens are HMAC-SHA-256 capabilities derived from the booking ID and token version; the database stores only a digest. Set `BOOKING_TOKEN_SECRET` to at least 32 random bytes outside the demo (`openssl rand -hex 32`). Rotation invalidates existing tokens.

## Development

Python 3.11+, PostgreSQL, and Redis are required.

```bash
uv sync --extra dev
docker compose up -d app-postgres chat-redis
export DATABASE_URL=postgresql://scheduling:scheduling@localhost:5433/scheduling
export CHAT_REDIS_URL=redis://localhost:6380/0
export BOOKING_TOKEN_SECRET='use-at-least-32-random-bytes-here'
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --reload
```

In another terminal (with the same exports):

```bash
uv run python -m app.cli
```

Checks:

```bash
uv run python -m app.evals.runner
uv run --extra dev pytest -q
```

Set `TEST_DATABASE_URL` to a disposable PostgreSQL database whose name ends in `_test` to include migration, transaction, concurrency, reminder, and API integration coverage. Without it, those tests skip.

## Project map

- `app/main.py`: HTTP contracts, token boundary, request entry
- `app/cli.py`: interactive chat and owner console
- `app/owner.py`: owner agenda and demo reminder views
- `app/chat_adapter.py`, `app/chat_helpers.py`, `app/chat_history.py`: chat translation, token resolution, catalog matching, Redis history
- `app/graph/`: one conversation turn (interpret, merge draft, decide, execute, persist)
- `app/scheduling.py`: timezone-aware availability and transactional slot validation
- `app/booking.py`: idempotent booking lifecycle, HMAC tokens, reminders
- `app/repositories.py`: PostgreSQL catalog, schedule, owner, and booking reads
- `app/channels/`: Telegram, audio decoding and splitting
- `app/telegram_worker.py`: polling, identity checks, per-user orchestration
- `app/reminders.py`: reminder worker and replaceable sender (demo inbox by default)
- `alembic/`: versioned schema, including the overlap exclusion constraint

Archived code lives under [`.archived/`](.archived/) and is not imported by the app.

## Roadmap

Proposed next work; full detail and completion evidence live in the [AI engineering portfolio roadmap](docs/ai-engineering-portfolio-roadmap.md).

- **1. Task-level evaluations.** Run the same scenarios across deterministic, Gemma, and OpenAI understanding and across deterministic versus Laya routing; record task success, field accuracy, latency, cost, and fallback use separately; make failures exit nonzero for CI.
- **2. Real Telegram journey.** Validate text and voice against a live bot and Gemma server; cover duplicate updates, provider outages, identity boundaries, and restarts; record the walkthrough.
- **3. Observability.** Correlate turns, generations, workflow steps, and business outcomes; report p50/p95 latency, fallback frequency, and cost per completed task with redacted traces.
- **4. Evaluated FAQ.** Compare a curated in-context baseline with simple retrieval for policies and preparation instructions; add reranking only if measured retrieval failures justify it.
- **5. Human handoff.** Persist requests with reason, minimal context, status, and assigned operator; let an owner reply and return control without conflicting bot actions.
- **6. Deployment and recovery.** Require owner authentication outside demo mode, run database-backed integration checks in CI, and exercise timeouts, retries, concurrency, and restarts.
- **7. Case study.** Publish the architecture, evaluation results, a demo video, and measured tradeoffs; benchmark Laya fine-tuning against a held-out set only after a baseline.

## Scope

Local portfolio MVP with synthetic data. Owner routes stay open unless `OWNER_API_TOKEN` is set. Telegram polling requires `TELEGRAM_BOT_TOKEN`; live voice requires a compatible Gemma server. See [security](docs/security.md) and [evaluation](docs/evaluation.md).

The original [project specification](.archived/reference/Service%20Scheduling%20Agent%20%E2%80%94%20Project%20Specification.md) and [architecture source](.archived/reference/Service%20Scheduling%20Agent%20%E2%80%94%20Architecture.md) are archived design inputs.

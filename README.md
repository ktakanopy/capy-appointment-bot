# Service Scheduling MVP

A portfolio MVP for appointment based service businesses. Customers can discover a service, choose a real available slot, book it, and manage it with a private capability token. A local owner view shows the agenda, and a small reminder worker simulates delivery into an inbox.

The project demonstrates a clear boundary: language interpretation can identify a service, date, or requested action; deterministic application code decides availability and commits booking changes.

## Local demo

Requirements: Docker Compose. The default demo uses a deterministic interpreter and does not need an LLM API key.

```bash
docker compose up --build
```

Open the Streamlit app at [http://localhost:8501](http://localhost:8501). The API and OpenAPI page are at [http://localhost:8000](http://localhost:8000) and [http://localhost:8000/docs](http://localhost:8000/docs).

The Compose stack runs the API, Streamlit UI, a dedicated scheduling PostgreSQL database, and the reminder worker. Startup applies the Alembic migration and loads the synthetic demo business. The app database is separate from the optional Langfuse database.

### Demo journey

1. Choose a service, date, and optional professional, then find availability.
2. Select a displayed slot and enter a name and phone number.
3. Save the private management token shown with the booking.
4. Confirm, cancel, or move the booking. The owner tab shows the agenda.
5. The demo business schedules reminders five minutes before a booking. The worker places due reminders in the inbox; submitting a reply starts a new `/chat` request with the token sent separately.

The seeded business is a wellness studio with two professionals and one massage service. It is only fixture data; the domain model supports multiple businesses, services, professionals, and service assignments. Pass `--include-second-business` to the seed command to add a second synthetic business.

### What the bot can do

In the customer conversation, the bot can:

- Find available times for a service and date, optionally with a requested professional.
- Help complete a booking after the customer chooses a time and provides their name and phone number.
- Confirm, cancel, or reschedule an existing booking when given its private management token.
- Explain how to get started and list the services available in the business catalog.

The local demo uses synthetic business data: one studio, one massage service, and two professionals. The bot does not answer general business questions or use a RAG knowledge base. It does not send messages through WhatsApp or SMS; reminders are simulated in the demo inbox.

## AI interpretation

`LLM_PROVIDER=deterministic` is the default so the local demo works offline. To use model-assisted extraction, set `LLM_PROVIDER=openai` and `OPENAI_API_KEY` in a local `.env` file before starting Compose. The OpenAI interpreter uses the SDK's Pydantic structured-output parser and falls back to the deterministic interpreter on provider failure. Neither interpreter can authorize a booking or claim a time is free. Chat sessions, offered slots, and the recent redacted message history are stored in Redis and expire after `SESSION_TTL_MINUTES`.

The optional Langfuse stack is separate from the default demo:

```bash
docker compose --profile observability up --build
```

Set `TRACING_ENABLED=true` to enable traces. Logs and trace metadata omit names, contact details, and management tokens.

## API overview

- `GET /businesses/{business_id}/catalog`
- `POST /availability/search`
- `POST /bookings` with `Idempotency-Key`
- `GET /bookings/me` with `X-Booking-Token`
- `POST /bookings/me/confirm`, `/cancel`, and `/reschedule` with the token and idempotency key
- `POST /sessions/new` and `POST /chat`
- `GET /owner/bookings` and `GET /demo/reminders`
- `GET /health`

Slot results are references, not reservations. Booking writes recalculate the selected slot in a transaction. PostgreSQL also rejects overlapping active bookings for the same professional. Rescheduling updates the booking and reminder in one transaction, so a rejected move leaves the original booking intact.

Management tokens are HMAC-SHA-256 capabilities derived from the booking ID and token version. The database stores only a digest. For a new secret, run `openssl rand -hex 32` and set its output as `BOOKING_TOKEN_SECRET`; use at least 32 random bytes outside the local demo. Secret rotation invalidates existing demo tokens.

## Development

Python 3.11+ is required. The API expects PostgreSQL, and migrations are managed with Alembic.

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

In another terminal:

```bash
uv run streamlit run frontend/streamlit_app.py
```

Run the offline extraction benchmark with:

```bash
uv run python -m app.evals.runner
```

Run the automated scheduling checks with `uv run --extra dev pytest -q`. Set `TEST_DATABASE_URL` to a disposable PostgreSQL database whose name ends in `_test` to include migration, transaction, concurrency, reminder, and API integration coverage. Without it, the PostgreSQL integration tests skip.

## Project map

- `app/main.py`: HTTP contracts, chat orchestration, and token boundary
- `app/models.py`: generic scheduling entities and API DTOs
- `app/scheduling.py`: timezone-aware deterministic availability and transactional slot validation
- `app/booking.py`: idempotent booking lifecycle, HMAC capabilities, reminders, and audit events
- `app/repositories.py`: PostgreSQL catalog, schedule, and booking reads
- `app/graph/`: LangGraph extraction step; no booking authorization or raw token state
- `app/reminders.py`: Postgres-backed demo reminder worker; no LangGraph dependency
- `frontend/`: customer booking flow and read-only owner agenda
- `alembic/`: versioned PostgreSQL schema, including the overlap exclusion constraint
- `docs/sdd/`: product requirements and task contracts

The supplied [project specification](docs/Service%20Scheduling%20Agent%20%E2%80%94%20Project%20Specification.md) and [architecture source](docs/Service%20Scheduling%20Agent%20%E2%80%94%20Architecture.md) are retained as source material. The active implementation notes are [architecture](docs/architecture.md), [conversation flow](docs/graph.md), [security](docs/security.md), and [evaluation](docs/evaluation.md).

## Scope

This is a local portfolio MVP with synthetic data. It does not connect to WhatsApp, SMS, email, calendars, payments, or healthcare systems. It does not include production owner authentication, onboarding, multi-location scheduling, or a distributed job queue. Laya benchmarking and training are planned after the working product MVP.

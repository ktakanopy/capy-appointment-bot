# CAPY-bot — Service Scheduling MVP

A portfolio MVP for appointment-based service businesses. Customers find a real available slot, book it, and manage the booking with a private capability token. Owners see the agenda, and a reminder worker simulates delivery into a demo inbox.

The design keeps a hard boundary: models interpret language and propose an action; deterministic application code decides availability and commits bookings. The repository also includes a scenario benchmark, provider fallbacks, and optional Langfuse traces.

[Demo](#local-demo) · [Architecture](#architecture) · [AI interpretation](#ai-interpretation) · [API](#api-overview) · [Roadmap](#roadmap)

## Local demo

Requirements: Docker Compose. No LLM API key is required: the default Gemma/Laya providers fall back to deterministic parsing when their local servers are absent.

```bash
docker compose up --build
```

Open the Streamlit app at [http://localhost:8501](http://localhost:8501). The API and OpenAPI page are at [http://localhost:8000](http://localhost:8000) and [http://localhost:8000/docs](http://localhost:8000/docs).

Compose starts the API, Streamlit, PostgreSQL, Redis, and the reminder worker, applies the Alembic migration, and seeds a synthetic business. The app database is separate from the optional Langfuse database.

For a guaranteed offline run without provider timeouts:

```bash
UNDERSTANDING_PROVIDER=deterministic DECISION_PROVIDER=deterministic docker compose up --build
```

### Demo journey

1. Choose a service, date, and optional professional, then find availability.
2. Select a slot and enter a name and phone number.
3. Save the private management token shown with the booking.
4. Confirm, cancel, or move the booking. The owner tab shows the agenda.
5. The demo business schedules a reminder five minutes before the booking. The worker writes it to the inbox; replying starts a new `/chat` turn with the token sent separately.

The seeded business is a wellness studio with two professionals and one massage service. It is fixture data only; the model supports multiple businesses, services, and assignments. Pass `--include-second-business` to `uv run python -m app.seed` to add another synthetic business.

## What it does

- Interprets a free-text request, tracks a scheduling draft across turns, and asks one clarification at a time.
- Finds real availability and completes a booking after the customer picks a time and provides a name and phone number.
- Confirms, cancels, or reschedules with a capability token. A token authorizes the caller; it does not by itself make the next selected slot a reschedule.
- Answers bounded catalog questions such as price, duration, and the demo cancellation rule.
- Looks up appointments only for a verified channel identity. A phone number typed in chat is not enough.

The bot does not answer general business questions, use a RAG knowledge base, or deliver outbound WhatsApp/email. WhatsApp webhook verification is present, but outbound delivery is not connected.

## Architecture

![Architecture overview](docs/architecture-overview.svg)

PostgreSQL is the source of truth; Redis holds short-lived drafts and checkpoints. The LangGraph turn may propose an action, but availability and booking rules live in `app/scheduling.py` and `app/booking.py`. The Streamlit forms use direct REST endpoints that call the same domain services without the graph.

Read more: [architecture](docs/architecture.md) · [conversation graph](docs/graph.md) · [security](docs/security.md).

## AI interpretation

- **Understanding:** `UNDERSTANDING_PROVIDER=gemma` calls a local Gemma server (`GEMMA_BASE_URL`) and falls back to the deterministic interpreter on failure. `UNDERSTANDING_PROVIDER=deterministic` forces offline mode; `LLM_PROVIDER=openai` plus `OPENAI_API_KEY` switches text understanding to OpenAI with a deterministic fallback.
- **Routing:** `DECISION_PROVIDER=laya` calls a local Laya server (`LAYA_BASE_URL`) to propose the next action and falls back to deterministic routing. Set `DECISION_PROVIDER=deterministic` to skip it.
- **Audio:** voice notes are decoded, split at pauses, and transcribed by Gemma `/understand`. There is no audio fallback, so a failed chunk returns a retry/type message instead of a partial booking.
- **Boundary:** neither provider can authorize a booking or claim a slot is free. Redis stores the draft, offered slots, and redacted history for `SESSION_TTL_MINUTES`.
- **Tracing:** `docker compose --profile observability up --build` with `TRACING_ENABLED=true` adds Langfuse. Logs and trace metadata omit names, contact details, and tokens.

Gemma and Laya call configured local servers; model weights and servers are not bundled here. A successful fallback is not a successful model prediction, and provider failures are logged separately.

## Known limitations

- A booking created in the web chat can lose its management token before the Streamlit UI displays it (open P1 from the PR review). Use the direct booking form for the demo until the widget state is fixed.
- A later chat search that returns no availability keeps the previous slot list visible (open P2).
- Reminder text renders the stored instant in UTC rather than the business timezone, and the owner agenda has no per-booking reminder status (open P2).
- Telegram and Gemma audio are implemented but not validated end-to-end against a live bot, database migration, and real recordings.
- WhatsApp outbound delivery, email, calendar sync, and payments are not connected.

## API overview

- `GET /health`
- `GET /businesses/{business_id}/catalog`
- `POST /availability/search`
- `POST /bookings` with `Idempotency-Key`
- `GET /bookings/me` with `X-Booking-Token`
- `POST /bookings/me/confirm`, `/cancel`, and `/reschedule` with the token and idempotency key
- `POST /sessions/new` and `POST /chat`
- `GET /owner/bookings`, `GET /demo/reminders`, `PATCH /owner/settings`
- `GET|POST /webhooks/whatsapp`, `POST /webhooks/sms`

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

In another terminal:

```bash
uv run streamlit run frontend/streamlit_app.py
```

Checks:

```bash
uv run python -m app.evals.runner
uv run --extra dev pytest -q
```

Set `TEST_DATABASE_URL` to a disposable PostgreSQL database whose name ends in `_test` to include migration, transaction, concurrency, reminder, and API integration coverage. Without it, those tests skip.

## Project map

- `app/main.py`: HTTP contracts, token boundary, webhook entry
- `app/chat_adapter.py`, `app/chat_helpers.py`, `app/chat_history.py`: web chat translation, token resolution, catalog matching, Redis history
- `app/graph/`: one conversation turn (interpret, merge draft, decide, execute, persist)
- `app/scheduling.py`: timezone-aware availability and transactional slot validation
- `app/booking.py`: idempotent booking lifecycle, HMAC tokens, reminders, audit
- `app/repositories.py`: PostgreSQL catalog, schedule, and booking reads
- `app/channels/`: WhatsApp, Telegram, SMS, audio decoding and splitting
- `app/telegram_worker.py`: polling, identity checks, per-user orchestration
- `app/reminders.py`: reminder worker and replaceable sender (demo inbox by default)
- `frontend/`: customer booking flow and read-only owner agenda
- `alembic/`: versioned schema, including the overlap exclusion constraint

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

The original [project specification](docs/Service%20Scheduling%20Agent%20%E2%80%94%20Project%20Specification.md) and [architecture source](docs/Service%20Scheduling%20Agent%20%E2%80%94%20Architecture.md) are retained as design inputs.

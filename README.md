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
- Confirm, cancel, or reschedule an existing booking. The web demo sends the private management token separately from the chat text. The token authorizes the caller; it does not by itself mean the next selected time is a reschedule.
- Answer bounded catalog questions such as price, duration, and the demo cancellation rule.
- Look up an existing appointment only for a verified channel identity. A phone number typed in chat is not enough.

The local demo uses synthetic business data: one studio, one massage service, and two professionals. The bot does not answer general business questions or use a RAG knowledge base. WhatsApp webhook verification is present, but outbound WhatsApp delivery is not connected. Reminders are written to the demo inbox.

## AI interpretation

`LLM_PROVIDER=deterministic` is the default so the local demo works offline. To use model-assisted extraction, set `LLM_PROVIDER=openai` and `OPENAI_API_KEY` in a local `.env` file before starting Compose. The OpenAI interpreter uses the SDK's Pydantic structured-output parser and falls back to the deterministic interpreter on provider failure. Neither interpreter can authorize a booking or claim a time is free. Chat sessions, offered slots, and the recent redacted message history are stored in Redis and expire after `SESSION_TTL_MINUTES`.

The optional Langfuse stack is separate from the default demo:

```bash
docker compose --profile observability up --build
```

Set `TRACING_ENABLED=true` to enable traces. Logs and trace metadata omit names, contact details, and management tokens.

### Long voice recordings

`GemmaUnderstanding.transcribe_recording(audio_bytes, context)` decodes a
Telegram voice message or audio upload into mono 16 kHz WAV chunks before
calling the configured Gemma `/understand` endpoint. The endpoint must already
support mono 16 kHz WAV audio and return a nonempty `transcript`; this repository does not
implement that server.

The splitter looks for a pause of at least 300 ms between seconds 25 and 29,
then cuts in the middle of the last suitable pause. Without a pause it cuts at
30 seconds. Adjacent chunks overlap by one second to protect words near a cut.
The transcription method joins matching boundary phrases of at least three
words; without word timestamps, short or differently transcribed repetitions
can remain and intentional boundary repetitions cannot always be distinguished.

The method returns only the complete transcript. The Telegram worker interprets
it once and runs one workflow turn, after all chunks succeed, so a late
correction is not lost. Failed decoding, an empty chunk transcript, a provider
failure, or a processing/size limit raises a sanitized error instead of
returning a partial message.

FFmpeg is included in the Docker image. For host development, install FFmpeg
and put `ffmpeg` on `PATH`. Defaults allow a 20 MB upload, 512 MiB of decoded
temporary PCM, 120 seconds for decoding, 900 seconds for the complete
transcription, and 64,000 transcript characters. These are configurable resource
budgets, not a 30-second recording limit or a guarantee about model context.
Temporary audio is removed on completion or failure. The transcript is customer
data: do not log it, and check the final interpreter's context limit before use.

Telegram onboarding requires the user to share their own contact. Voice must
finish transcription before the graph runs, so a failure in any chunk cannot
trigger a booking. The update ledger saves a final reply before sending it and
retries delivery without rerunning the graph. If the process stops while a turn
is running, the worker does not rerun that ambiguous turn; it asks the customer
to check their appointments first.

### Telegram customer chat

The Telegram channel is an optional polling worker. It needs the PostgreSQL
migration applied (the Compose API service does this at startup), Redis,
`TELEGRAM_BOT_TOKEN`, and the existing business/model configuration. It does
not require the Streamlit service. For example, add the token to the ignored
`.env` file and run:

```bash
docker compose --profile telegram up --build -d app-postgres chat-redis api telegram-worker
```

The worker rejects startup when Telegram reports an active webhook or another
worker already holds its database lock; it never changes webhook settings.
Users must share their own Telegram contact before chatting. A self-contact is
verified by matching Telegram's contact user ID with the message sender. This
may link an existing customer account by phone; a username or typed/spoken
phone alone cannot link an account. The username is display metadata; the
numeric Telegram user ID is the account key.

If text messages work but voice messages fail, verify that the existing Gemma
server accepts audio at `GEMMA_BASE_URL/understand` and returns a transcript.
The worker will keep text available and ask the user to retry/type when audio
processing fails. Adjust `TELEGRAM_AUDIO_CHUNK_TIMEOUT_SECONDS` and
`TELEGRAM_AUDIO_PROCESSING_TIMEOUT_SECONDS` only after measuring the target
server's response time.

The database retains only Telegram identity data and update receipts needed for
deduplication. Raw update payloads are deleted once a reply is saved; delivered
receipt IDs are retained for 30 days. A queued reply can contain customer data
until delivery succeeds; replies that Telegram permanently rejects are cleared
while their dedupe receipts remain. No audio bytes or Telegram file URL are persisted.
External replies can be duplicated if Telegram accepted a send but its response
was lost; booking writes remain protected by their update-derived idempotency
key.

## API overview

- `GET /businesses/{business_id}/catalog`
- `POST /availability/search`
- `POST /bookings` with `Idempotency-Key`
- `GET /bookings/me` with `X-Booking-Token`
- `POST /bookings/me/confirm`, `/cancel`, and `/reschedule` with the token and idempotency key
- `POST /sessions/new` and `POST /chat`
- `GET /owner/bookings`, `GET /demo/reminders`, and `PATCH /owner/settings`
- `GET /webhooks/whatsapp` and `POST /webhooks/whatsapp`
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
- `app/graph/`: one inbound conversation turn. The interpreter extracts fields, the decision port proposes an action, and the graph constrains the transition.
- `app/chat_adapter.py`: web `/chat` adapter. It resolves a management token before calling the graph.
- `app/channels/`: WhatsApp signature and dedupe, Telegram API/identity persistence, the audio understanding port, and pause-aware recording splitting.
- `app/telegram_worker.py`: Telegram polling and per-user conversation orchestration.
- `app/reminders.py`: Postgres-backed reminder worker and a replaceable sender. The default sender writes the demo inbox.
- `frontend/`: customer booking flow and read-only owner agenda
- `alembic/`: versioned PostgreSQL schema, including the overlap exclusion constraint
- `docs/sdd/`: product requirements and task contracts

The supplied [project specification](docs/Service%20Scheduling%20Agent%20%E2%80%94%20Project%20Specification.md) and [architecture source](docs/Service%20Scheduling%20Agent%20%E2%80%94%20Architecture.md) are retained as source material. The active implementation notes are [architecture](docs/architecture.md), [conversation flow](docs/graph.md), [security](docs/security.md), and [evaluation](docs/evaluation.md).

The [AI Engineering portfolio roadmap](docs/ai-engineering-portfolio-roadmap.md) records proposed priorities, the reviewed baseline, and the evidence needed to demonstrate model quality, real channel integration, observability, and reliable operation.

## Scope

This is a local portfolio MVP with synthetic data. WhatsApp/SMS outbound delivery and email are not connected; it does not sync a calendar or take payment. Set `OWNER_API_TOKEN` to require a token on owner routes; when it is unset, those routes stay open for the local demo. Telegram polling and audio transcription require the configured Bot API and an existing compatible Gemma server; real voice behavior still needs end-to-end validation.
